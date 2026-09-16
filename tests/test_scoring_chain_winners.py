"""Load-order lock: which implementation actually computes the score.

Context (see REFACTOR_PLAN §10.2)
---------------------------------
A runtime probe over the production entry points showed that **none** of the ten
core scoring functions on ``score_core`` is still served by ``score_core``:

| symbol                          | production winner             |
|---------------------------------|-------------------------------|
| ``_score_dimensions_available`` | ``score_acceleration_v79``    |
| ``score_trend``                 | ``score_acceleration_v79``    |
| ``classify_style``              | ``score_acceleration_v79``    |
| ``score_volume``                | ``score_scale_migration_v95`` |
| ``score_accumulation``          | ``score_scale_migration_v95`` |
| ``score_structure``             | ``score_scale_migration_v95`` |
| ``value_trap_risk``             | ``score_endpoint_acceleration_v79`` |
| ``breakout_score``              | ``score_endpoint_acceleration_v79`` |
| ``execution_quality_score``     | ``score_endpoint_acceleration_v79`` |
| ``entry_point``                 | ``score_cache_guard_v80``      |

A static scan (§10.2) counted **72** symbols on ``score_core`` that some overlay
rebinds.  So ``score_core.py`` -- 1196 lines, 43.8 KB, the file a reader would
naturally open to learn how scoring works -- is in production a *name registry*,
not an implementation.  Editing ``score_core.score_volume`` changes a function
that is never called.

Three of the ten are genuinely contested (more than one overlay installs them);
the rest have a single installer, but the positive assertion still earns its keep
because it catches an overlay *stopping* being installed -- which would silently
fall back to ``score_core`` and re-run the un-accelerated original.

Like ``test_backtest_overlay_winners.py`` this runs in a **child process**:
importing the production entry points installs overlays process-wide and would
pollute the rest of the suite.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: ``(module, attribute)`` pairs resolved in the child process.
CONTESTED = (
    ("score_core", "_score_dimensions_available"),
    ("score_core", "score_trend"),
    ("score_core", "score_volume"),
    ("score_core", "score_accumulation"),
    ("score_core", "score_structure"),
    ("score_core", "classify_style"),
    ("score_core", "entry_point"),
    ("score_core", "value_trap_risk"),
    ("score_core", "breakout_score"),
    ("score_core", "execution_quality_score"),
)

_EXPECTED_WINNERS = {
    "score_core._score_dimensions_available": "score_acceleration_v79",
    "score_core.score_trend": "score_acceleration_v79",
    "score_core.score_volume": "score_scale_migration_v95",
    "score_core.score_accumulation": "score_scale_migration_v95",
    "score_core.score_structure": "score_scale_migration_v95",
    "score_core.classify_style": "score_acceleration_v79",
    "score_core.entry_point": "score_cache_guard_v80",
    "score_core.value_trap_risk": "score_endpoint_acceleration_v79",
    "score_core.breakout_score": "score_endpoint_acceleration_v79",
    "score_core.execution_quality_score": "score_endpoint_acceleration_v79",
}

#: Overlays that *also* install the name but lose the race, from the static scan
#: in §10.2.  Asserting they lost is what makes the positive lock non-vacuous: a
#: probe that returned nonsense module names would otherwise satisfy nothing.
_LOSERS = {
    "score_core._score_dimensions_available": ("score_acceleration_v77",),
    "score_core.score_volume": (
        "score_acceleration_v77",
        "score_acceleration_v79",
    ),
    "score_core.score_accumulation": ("score_acceleration_v79",),
    "score_core.score_structure": ("score_acceleration_v79",),
    "score_core.entry_point": ("score_acceleration_v79",),
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
        "the scoring probe did not run; a winner lock that cannot see the "
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
def test_scoring_symbol_resolves_to_the_expected_implementation(
    winners: dict[str, str], symbol: str
) -> None:
    expected = _EXPECTED_WINNERS[symbol]
    assert winners[symbol] == expected, (
        f"{symbol} now resolves to {winners[symbol]}, expected {expected}. "
        "Either the overlay load order changed on purpose (update this lock and "
        "say so in the commit) or an overlay stopped being installed -- in which "
        "case production just fell back to score_core's un-accelerated original."
    )


@pytest.mark.parametrize("symbol", sorted(_LOSERS))
def test_the_losing_overlays_really_did_lose(
    winners: dict[str, str], symbol: str
) -> None:
    """Negative half of the lock: the documented losers must not be winning."""
    for loser in _LOSERS[symbol]:
        assert winners[symbol] != loser, (
            f"{symbol} is now served by {loser}, which the static scan recorded "
            "as an order-dependent loser"
        )


def test_score_core_is_not_the_implementation_in_production() -> None:
    """Pins §10.2's headline, so the finding cannot quietly stop being true.

    This is *not* a value judgement -- sinking the accelerations back into
    ``score_core`` would be a good change.  It is a tripwire: if that ever
    happens it is deliberate, and this lock plus the §10.2 table must be updated
    in the same commit rather than drifting.
    """
    winners_map = _probe()
    served_by_source = sorted(
        symbol for symbol, module in winners_map.items() if module == "score_core"
    )
    assert not served_by_source, (
        "these are now served by score_core itself; the §10.2 winner table is "
        "stale and the accelerations are no longer installed: "
        + ", ".join(served_by_source)
    )
