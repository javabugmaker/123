"""Who actually decides the backtest sample set and its weights (S4 recon).

The sample/weight pipeline is assembled out of five overlay layers plus one
surviving native function.  Resolved by *defining file*, not by ``__module__``:

| symbol                                                  | defined in                                |
|---------------------------------------------------------|-------------------------------------------|
| ``analytics_core.run_historical_backtest``               | ``backtest_production_activation_v93.py`` |
| ``analytics_core._backtest_one_ticker``                  | ``backtest_alignment.py``                 |
| ``analytics_core._verified_point_in_time_frame``         | ``point_in_time_backtest.py``             |
| ``analytics_core._relabel_sample_splits``                | ``analytics_core.py``  (only native one)  |
| ``analytics_core._date_balanced_weights``                | ``backtest_math_integrity_v94.py``        |
| ``analytics_core.calibration_details_for_frame``         | ``backtest_math_integrity_v94.py``        |
| ``model_calibration._prepare_samples``                   | ``backtest_math_integrity_v94.py``        |
| ``backtest_sample_acceleration_v80._drawdown_percent``   | ``backtest_sample_guard_v80.py``          |
| ``score_core._model_component_weights``                  | ``score_weight_cache_v79.py``             |

Two traps, both of which produced a wrong reading before being caught
------------------------------------------------------------------
1. **``__module__`` is spoofed here.**  ``backtest_production_activation_v93``
   sets ``run_historical_backtest.__module__`` to the *original's* module
   (v93:272-274), so it claims to come from ``point_in_time_backtest``.  The
   same wrapper also closes over ``_verified_point_in_time_frame``.  Only
   ``__code__.co_filename`` tells the truth, which is what this probe reads.
   ``test_scoring_chain_winners`` gets away with ``__module__`` because none of
   the scoring overlays bothers to fake it.

2. **A static probe cannot see scoped patches.**  v93 does *not* rebind
   ``_verified_point_in_time_frame`` permanently; it swaps in
   ``_production_point_in_time_frame`` for the duration of one
   ``run_historical_backtest`` call and restores the previous value afterwards
   (v93:253-265).  ``conditional_fill_v96`` is installed and uninstalled the
   same way (v93:258-264).  At rest neither is visible -- not to this probe,
   and not to the assembly manifest either.  ``test_the_scoped_layer...`` locks
   that behaviour structurally, because no at-rest probe can.

Changing a layer on purpose is fine; update the table and say so in the commit.
What this file catches is a layer going missing or an order change nobody
announced -- which silently changes which samples a backtest is fitted on.
"""

from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

EXPECTED_WINNERS: dict[str, str] = {
    "analytics_core.run_historical_backtest": "backtest_production_activation_v93.py",
    "analytics_core._backtest_one_ticker": "backtest_alignment.py",
    "analytics_core._verified_point_in_time_frame": "point_in_time_backtest.py",
    "analytics_core._relabel_sample_splits": "analytics_core.py",
    "analytics_core._date_balanced_weights": "backtest_math_integrity_v94.py",
    "analytics_core.calibration_details_for_frame": "backtest_math_integrity_v94.py",
    "model_calibration._prepare_samples": "backtest_math_integrity_v94.py",
    "backtest_sample_acceleration_v80._drawdown_percent": "backtest_sample_guard_v80.py",
    "score_core._model_component_weights": "score_weight_cache_v79.py",
}

_CHILD = '''
import sys, types, json, importlib

# asyncio cannot initialise on some Windows builds (WinError 10106 on
# _overlapped); the import graph is what we measure, not the vendor clients.
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

import main, daily_pipeline, scan_service, scanner  # noqa: F401

out = {}
for symbol in json.loads(sys.argv[1]):
    module_name, attr = symbol.rsplit(".", 1)
    obj = getattr(importlib.import_module(module_name), attr)
    code = getattr(obj, "__code__", None)
    out[symbol] = (
        "" if code is None else code.co_filename.replace("\\\\", "/").rsplit("/", 1)[-1]
    )
print("@@@" + json.dumps(out))
'''


def _probe() -> dict[str, str]:
    with tempfile.TemporaryDirectory() as tmp:
        child = pathlib.Path(tmp) / "probe.py"
        child.write_text(_CHILD, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(child), json.dumps(sorted(EXPECTED_WINNERS))],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={"PYTHONPATH": str(ROOT), "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
        )
    blob = (proc.stdout or "") + (proc.stderr or "")
    for line in blob.splitlines():
        if line.startswith("@@@"):
            return json.loads(line[3:])
    raise AssertionError(
        "the sample-pipeline probe did not run; a winner lock that cannot see "
        "the import graph protects nothing:\n" + blob[-2000:]
    )


@pytest.fixture(scope="module")
def winners() -> dict[str, str]:
    resolved = _probe()
    assert set(resolved) == set(EXPECTED_WINNERS)
    return resolved


@pytest.mark.parametrize("symbol", sorted(EXPECTED_WINNERS))
def test_sample_pipeline_symbol_is_defined_where_the_table_says(
    winners: dict[str, str], symbol: str
) -> None:
    expected = EXPECTED_WINNERS[symbol]
    assert winners[symbol] == expected, (
        f"{symbol} is now defined in {winners[symbol]!r}, expected {expected!r}. "
        "Either a layer was removed/reordered on purpose (update the table in "
        "this module's docstring and say so in the commit), or the backtest is "
        "now fitting on a different sample set than you think."
    )


def test_the_lock_reads_defining_file_not_module_attribute(
    winners: dict[str, str],
) -> None:
    """Tripwire for trap #1: ``__module__`` lies about the v93 wrapper.

    v93 sets ``run_historical_backtest.__module__`` to the original's module.
    If this ever stops being true the table above is still correct but the
    *reason* for reading ``co_filename`` disappears -- and so does the only
    defence against the next overlay that fakes its ``__module__``.
    """
    assert winners["analytics_core.run_historical_backtest"].endswith("v93.py"), (
        "the v93 wrapper no longer wins; if it was inlined into analytics_core, "
        "remove this tripwire in the same commit"
    )


def test_the_scoped_layer_swaps_and_restores_around_every_run() -> None:
    """Trap #2: the during-run swaps are invisible to any at-rest probe.

    v93 replaces ``_verified_point_in_time_frame`` and installs
    ``conditional_fill_v96`` only while one backtest runs, then puts both back.
    Reading the source is the only way to lock it without running a backtest.
    """
    source = (ROOT / "backtest_production_activation_v93.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    def assignments_to(attribute: str) -> list[ast.Assign]:
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == attribute
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "_core"
                    ):
                        found.append(node)
        return found

    swaps = assignments_to("_verified_point_in_time_frame")
    assert len(swaps) == 2, (
        f"expected exactly one swap-in and one restore of "
        f"_verified_point_in_time_frame, found {len(swaps)}"
    )
    values = {
        ast.unparse(node.value) if hasattr(ast, "unparse") else "" for node in swaps
    }
    assert "_production_point_in_time_frame" in values, "the swap-in is gone"
    assert "previous_verified" in values, (
        "the restore is gone -- the production PIT filter would leak into every "
        "later call in the process"
    )

    # The restore must be in a finally, otherwise a raising backtest leaks it.
    restored_in_finally = any(
        isinstance(node, ast.Try)
        and any(any(item is swap for item in node.finalbody) for swap in swaps)
        for node in ast.walk(tree)
    )
    assert restored_in_finally, "the restore is no longer inside a finally block"

    calls = {
        ast.unparse(node.func) if hasattr(ast, "unparse") else ""
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "_conditional_fill.install" in calls, "conditional fill is no longer installed"
    assert "_conditional_fill.uninstall" in calls, (
        "conditional fill is no longer uninstalled -- it would stay installed "
        "for the rest of the process"
    )
