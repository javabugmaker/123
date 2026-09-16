"""Which parts of ``scanner_core`` production actually runs.

Why this file exists
--------------------
``scanner_core.py`` is 1,784 lines with 21 top-level functions and no direct
behavioural tests -- the largest coverage gap in the repo.  Before writing any
behavioural test against it, the score_core lesson has to be applied first:
that module turned out to be a *name book* (0 of 10 core functions still
defined there), so tests written against its source would have tested nothing.

``scanner_core`` is not that bad, but it is not sound either.  Roughly half of
it is dead in production:

* **Rebound away** -- ``save_checkpoint``, ``load_checkpoint``,
  ``clear_checkpoint``, ``_checkpoint_trade_date`` and ``CheckpointState`` all
  resolve to ``scanner_resume_v59`` on every entry point.
* **Rebound to a wrapper** -- ``run_scan`` (828 lines, 46% of the file) resolves
  to ``scan_resume_boundary``'s closure, which calls ``scanner_resume_v59``.
  ``v59`` keeps ``scanner_core.run_scan`` only as ``_LEGACY_RUN_SCAN`` and calls
  it from one branch, guarded by ``not isinstance(checkpoint, CheckpointState)``
  -- a compatibility path for "older tests/integrations", not production.
* **Alive, and the convergence point of both production paths** --
  ``scan_single_from_df`` (381 lines).  The daily path reaches it through
  ``v59.run_scan -> _analyse_one_ticker_from_df``, and the indicator path
  through ``run_parallel_indicator_scan -> _analyse_one_ticker``.
* **Alive but thin** -- ``_emit_progress`` (called by v59),
  ``run_parallel_indicator_scan`` (called by ``main_core:188``).
* **Dead by unreachability** -- ``scan_single``, ``_quality_hard_data_complete_from_row``,
  ``_load_previous_tickers``: their only call sites are inside ``run_scan``,
  which is itself not on a production path.

So a behavioural test aimed at "scanner_core" in general would mostly be
testing code nothing calls.  This file pins the split; the behavioural work
should target ``scan_single_from_df`` and the ``ScanResult`` it builds.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNER_CORE = ROOT / "scanner_core.py"
V59 = ROOT / "scanner_resume_v59.py"
MANIFESTS = sorted((ROOT / "tests" / "fixtures").glob("assembly_manifest_*.json"))

#: Symbols the resume overlay takes over.  Sourced from the assembly manifests,
#: not from reading the source -- the manifests record what production resolved
#: to, which is the only thing that matters here.
RESUME_OWNED = (
    "save_checkpoint",
    "load_checkpoint",
    "clear_checkpoint",
    "_checkpoint_trade_date",
    "CheckpointState",
)

#: The one substantial function still doing the work, and the reason a
#: behavioural test of scanner_core is worth writing at all.
CONVERGENCE_FUNCTION = "scan_single_from_df"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _function_nodes(path: Path) -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in _tree(path).body
        if isinstance(node, ast.FunctionDef)
    }


def _manifest_final() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for path in MANIFESTS:
        payload = json.loads(path.read_text(encoding="utf-8"))
        final = payload.get("final")
        if isinstance(final, dict):
            out[path.stem.replace("assembly_manifest_", "")] = final
    return out


def test_the_resume_family_is_owned_by_the_overlay_everywhere() -> None:
    """The checkpoint family never resolves back to ``scanner_core``.

    Read from every captured entry manifest, so a new entry point that forgets
    to install the resume overlay shows up here rather than silently running the
    legacy checkpoint code.
    """
    finals = _manifest_final()
    assert finals, "no assembly manifests with a final state were found"
    checked = 0
    for entry, final in finals.items():
        for symbol in RESUME_OWNED:
            key = f"scanner_core.{symbol}"
            if key not in final:
                continue
            resolved = str(final[key])
            assert "scanner_core" not in resolved, (
                f"[{entry}] {key} resolves back to {resolved}; the resume "
                "overlay no longer owns it"
            )
            checked += 1
    assert checked, (
        "no manifest recorded any of the resume-owned symbols; the fixture "
        "shape changed and this gate is now vacuous"
    )


def test_run_scan_is_a_wrapper_and_the_legacy_body_has_one_guarded_caller() -> None:
    """``run_scan``'s 828 lines are reachable from exactly one place.

    ``scanner_resume_v59`` captures it as ``_LEGACY_RUN_SCAN`` and calls it only
    when a caller passes a checkpoint that is not a ``CheckpointState`` -- the
    documented compatibility path for older tests.  Production never does.

    If a second real call site appears, this stops being true and the 828 lines
    stop being effectively dead.
    """
    source = V59.read_text(encoding="utf-8")
    tree = _tree(V59)
    assert "_LEGACY_RUN_SCAN = _core.run_scan" in source, (
        "v59 no longer captures scanner_core.run_scan as the legacy fallback"
    )
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_LEGACY_RUN_SCAN"
    ]
    assert len(calls) == 1, (
        f"_LEGACY_RUN_SCAN is called {len(calls)} times; the legacy run_scan is "
        "no longer confined to a single compatibility branch"
    )


def test_scan_single_from_df_is_the_convergence_of_both_production_paths() -> None:
    """The function a behavioural test should actually target.

    Two independent production paths reach it, so a test written here is a test
    of something production runs -- unlike most of the rest of this module.
    """
    core = _function_nodes(SCANNER_CORE)
    v59_source = V59.read_text(encoding="utf-8")

    assert "_core._analyse_one_ticker_from_df" in v59_source, (
        "the resume path no longer delegates to scanner_core's per-ticker "
        "analysis; scan_single_from_df may no longer be on it"
    )
    from_df = core["_analyse_one_ticker_from_df"]
    assert CONVERGENCE_FUNCTION in {
        node.func.id
        for node in ast.walk(from_df)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }, "_analyse_one_ticker_from_df no longer calls scan_single_from_df"

    parallel = core["run_parallel_indicator_scan"]
    submitted = {
        node.args[0].id
        for node in ast.walk(parallel)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "submit"
        and node.args
        and isinstance(node.args[0], ast.Name)
    }
    assert "_analyse_one_ticker" in submitted, (
        "the parallel indicator path no longer submits _analyse_one_ticker"
    )


def test_the_unreachable_helpers_are_only_called_from_run_scan() -> None:
    """Dead by unreachability rather than by rebinding.

    These are not rebound by any overlay -- they are simply only ever called
    from ``run_scan``, which production does not run.  Listing them keeps them
    out of the behavioural shortlist and, more usefully, makes it visible if
    someone wires one into a live path.
    """
    tree = _tree(SCANNER_CORE)
    functions = _function_nodes(SCANNER_CORE)
    run_scan = functions["run_scan"]
    inside = {
        getattr(node, "lineno", None)
        for node in ast.walk(run_scan)
        if getattr(node, "lineno", None) is not None
    }

    for symbol in ("scan_single", "_quality_hard_data_complete_from_row", "_load_previous_tickers"):
        callers = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == symbol
        ]
        outside = [line for line in callers if line not in inside]
        assert not outside, (
            f"{symbol} is now called outside run_scan (lines {outside}); it has "
            "entered a live path and this classification is wrong"
        )
