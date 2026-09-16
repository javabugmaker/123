"""Load-order lock: which backtest implementation actually wins.

Context (see REFACTOR_PLAN §9.5)
--------------------------------
A runtime probe over the production entry points showed that five rebinding
targets are *contested*: more than one overlay installs them, and the winner is
decided purely by import order.  Four of them decide the behaviour of every
backtest:

* ``analytics_core._backtest_one_ticker`` — currently ``backtest_alignment``'s
  wrapper; ``sample_acceleration_v80`` and ``vectorization_v98`` both try.
* ``analytics_core._backtest_one_ticker_cached`` — currently
  ``institution_scanner.point_in_time_backtest``; ``cache_acceleration_v80`` and
  ``incremental_v78`` both try.
* ``analytics_core._signal_evaluations`` — currently ``fastscore_v80``;
  ``fastpath_v78`` tries.
* ``backtest_fastscore_v80._fast_score_matrix`` — currently
  ``scoring_consistency_v94``; ``vectorization_v98`` tries.

Two ways this bites:

1. someone edits ``cache_acceleration_v80`` / ``incremental_v78`` / ``fastpath_v78``
   believing they are changing production, and nothing changes;
2. somebody reorders an import and the backtest silently switches executor.

This file pins the winners so case 2 becomes a red test instead of a quiet
change.  It runs in a **child process** because importing the production entry
points installs overlays process-wide, which would pollute the rest of the suite.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

CONTESTED = (
    ("analytics_core", "_backtest_one_ticker"),
    ("analytics_core", "_backtest_one_ticker_cached"),
    ("analytics_core", "_signal_evaluations"),
    ("backtest_fastscore_v80", "_fast_score_matrix"),
)

_EXPECTED_WINNERS = {
    "analytics_core._backtest_one_ticker": "backtest_alignment",
    "analytics_core._backtest_one_ticker_cached": (
        "institution_scanner.point_in_time_backtest"
    ),
    "analytics_core._signal_evaluations": "backtest_fastscore_v80",
    "backtest_fastscore_v80._fast_score_matrix": "scoring_consistency_v94",
}

# The losers, for the negative assertions that make the lock non-vacuous: a
# winner that is only asserted positively could still be satisfied by a probe
# that never ran.
_LOSERS = {
    "analytics_core._backtest_one_ticker": (
        "backtest_sample_acceleration_v80",
        "backtest_vectorization_v98",
    ),
    "analytics_core._backtest_one_ticker_cached": (
        "backtest_cache_acceleration_v80",
        "backtest_incremental_v78",
    ),
    "analytics_core._signal_evaluations": ("backtest_fastpath_v78",),
    "backtest_fastscore_v80._fast_score_matrix": ("backtest_vectorization_v98",),
}

_CHILD = '''
import sys, types, json, importlib

# asyncio cannot initialise on some Windows builds (WinError 10106 on
# _overlapped), which would otherwise abort the import chain at tickflow /
# curl_cffi.aio.  The import graph is what we measure, not the vendor clients.
class _Dummy(int):
    def __call__(self, *a, **k): return self
    def __getattr__(self, n): return _Dummy()

_ov = types.ModuleType("_overlapped")
_ov.__getattr__ = lambda name: _Dummy()
sys.modules["_overlapped"] = _ov

_tf = types.ModuleType("tickflow")
_tf.TickFlow = object
_tf.AsyncTickFlow = object
sys.modules["tickflow"] = _tf

import main
import daily_pipeline
import historical_backtest

targets = json.loads(sys.argv[1])
out = {}
for module_name, attr in targets:
    try:
        obj = getattr(importlib.import_module(module_name), attr)
        out[f"{module_name}.{attr}"] = getattr(obj, "__module__", None) or repr(obj)[:80]
    except Exception as exc:
        out[f"{module_name}.{attr}"] = f"<error {type(exc).__name__}: {exc}>"
print("@@@" + json.dumps(out))
'''


def _probe() -> dict[str, str]:
    """Import the production entry points in a child and report each winner."""
    with tempfile.TemporaryDirectory() as tmp:
        child = pathlib.Path(tmp) / "probe.py"
        child.write_text(_CHILD, encoding="utf-8")
        env = {
            "PYTHONPATH": str(ROOT),
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        }
        proc = subprocess.run(
            [sys.executable, str(child), json.dumps(list(CONTESTED))],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    blob = (proc.stdout or "") + (proc.stderr or "")
    for line in blob.splitlines():
        if line.startswith("@@@"):
            return json.loads(line[3:])
    raise AssertionError(
        "the overlay probe did not run; a winner lock that cannot see the "
        "import graph protects nothing:\n" + blob[-2000:]
    )


@pytest.fixture(scope="module")
def winners() -> dict[str, str]:
    resolved = _probe()
    assert set(resolved) == set(_EXPECTED_WINNERS), (
        f"probe covered {sorted(resolved)}, expected {sorted(_EXPECTED_WINNERS)}"
    )
    broken = {k: v for k, v in resolved.items() if v.startswith("<error")}
    assert not broken, f"probe could not resolve: {broken}"
    return resolved


@pytest.mark.parametrize("symbol", sorted(_EXPECTED_WINNERS))
def test_contested_symbol_resolves_to_the_expected_implementation(
    winners: dict[str, str], symbol: str
) -> None:
    expected = _EXPECTED_WINNERS[symbol]
    assert winners[symbol] == expected, (
        f"{symbol} now resolves to {winners[symbol]}, expected {expected}. "
        "Either the overlay load order changed on purpose (update this lock and "
        "say so in the commit) or an overlay started losing the race."
    )


@pytest.mark.parametrize("symbol", sorted(_LOSERS))
def test_the_losing_overlays_really_did_lose(
    winners: dict[str, str], symbol: str
) -> None:
    """Negative half of the lock: the documented losers must not be winning.

    Without this, a probe that returned garbage module names could still satisfy
    nothing at all, and the lock above would be the only thing standing between
    a reorder and a silently different backtest.
    """
    for loser in _LOSERS[symbol]:
        assert winners[symbol] != loser, (
            f"{symbol} is now served by {loser}, which the runtime probe recorded "
            "as an order-dependent loser"
        )
