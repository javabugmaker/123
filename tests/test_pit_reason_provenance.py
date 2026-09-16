"""Why ``heldout_unverified_reason_counts`` is empty in every production run.

The observable gap
------------------
``output/BacktestSummary.json`` reports ``heldout_unverified_test_samples``
(51,142 on the 2026-09-15 run) next to ``heldout_unverified_reason_counts``
which is ``{}``.  Fifty thousand rows are called unverified and the summary
cannot say why, so there is no way to tell *degraded* rows (kept at a 0.25
evidence weight) from *dropped* ones.

The mechanism
-------------
Reason counts are captured by a transient hook: ``point_in_time_backtest``
wraps ``core._verified_point_in_time_frame`` so that ``_split_counts`` runs
while the verified frame is built (``point_in_time_backtest.py:396-399``).

``backtest_production_activation_v93`` does not wrap that function, it
**replaces** it for the duration of one run
(``backtest_production_activation_v93:252-265``) and
``_production_point_in_time_frame`` never calls the implementation it
replaced.  Whichever installed last wins the name, so whenever v93 is active
the PIT hook never runs and ``_PIT_SPLIT_COUNTS`` stays empty.

``pit_counts.normalize_runtime_counts`` then repairs the *counts* from durable
core provenance (``rolling_oos``) -- which is why raw and unverified still look
populated -- but it has nothing to rebuild *reasons* from, so the field stays
``{}``.  ``pit_counts.py`` already admits this in its module docstring
("acceleration/wrapper composition can make that transient hook unavailable").

This file freezes that chain of custody so the gap cannot be mistaken for
"there were no reasons".  Fixing it means touching a production overlay; the
gate only records the as-built behaviour.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from institution_scanner.pit_counts import normalize_runtime_counts
from institution_scanner.point_in_time_backtest import (
    _split_counts,
    apply_summary_pit_scope,
)

ROOT = Path(__file__).resolve().parents[1]
V93 = ROOT / "backtest_production_activation_v93.py"


def _tree() -> ast.Module:
    return ast.parse(V93.read_text(encoding="utf-8"))


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
    }


def _previous_hook_names(tree: ast.Module) -> set[str]:
    """Every name that remembers an implementation v93 displaced.

    Both matter: the module-level ``_ORIGINAL_VERIFIED_POINT_IN_TIME_FRAME``
    captured at import time, and the run-local capture taken right before the
    swap.  Delegating through either one would revive the PIT hook.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        value = node.value
        if not isinstance(value, ast.Attribute):
            continue
        if value.attr != "_verified_point_in_time_frame":
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                found.add(target.id)
    if not found:
        raise AssertionError("v93 no longer captures the previous verified-frame hook")
    return found


def test_split_counts_does_report_reasons_when_the_column_exists() -> None:
    """Non-vacuity guard: the counter itself works.

    The emptiness in production is a wiring problem, not a broken counter.  If
    this ever fails, the diagnosis in this file's docstring is wrong.
    """
    raw = pd.DataFrame(
        {
            "split": ["test", "test", "test", "train"],
            "universe_snapshot_status": [
                "UNAVAILABLE",
                "UNAVAILABLE",
                "UNAVAILABLE",
                "ELIGIBLE",
            ],
            "universe_snapshot_reason": [
                "no_point_in_time_snapshot",
                "no_point_in_time_snapshot",
                "snapshot_starts_after_signal",
                "eligible",
            ],
        }
    )
    verified = raw.loc[raw["universe_snapshot_status"].eq("ELIGIBLE")]
    counts = _split_counts(raw, verified)

    reasons = counts["test"]["unverified_reasons"]
    assert reasons == {
        "no_point_in_time_snapshot": 2,
        "snapshot_starts_after_signal": 1,
    }, f"the counter stopped attributing reasons: {reasons}"
    assert sum(reasons.values()) == counts["test"]["unverified"]


def test_the_repair_layer_restores_counts_but_cannot_restore_reasons() -> None:
    """The half that explains why the field looks alive but is empty.

    Raw counts survive because they are durable core provenance.  Reasons are
    only ever observed by the transient hook, so with no hook there is nothing
    to rebuild them from -- the key is dropped rather than filled with ``{}``.
    """
    summary = SimpleNamespace(rolling_oos={"test": 51142}, rolling_oos_stats={})
    normalized = normalize_runtime_counts(summary, {})

    assert normalized["test"]["raw"] == 51142
    assert normalized["test"]["unverified"] == 51142
    assert "unverified_reasons" not in normalized["test"], (
        "the repair layer now fabricates reason counts; verified provenance "
        "must never be inferred"
    )

    scoped = apply_summary_pit_scope(SimpleNamespace(), normalized)
    assert scoped.heldout_unverified_reason_counts == {}
    assert scoped.heldout_unverified_test_samples == 51142


def test_the_production_replacement_never_delegates_to_the_hook_it_replaced() -> None:
    """The bypass itself.

    If ``_production_point_in_time_frame`` ever called the function it
    displaced, the PIT hook would run again and this assertion would fail --
    which is exactly the fix one would want, so failing here is the signal to
    re-record the boundary, not a regression.
    """
    tree = _tree()
    functions = _functions(tree)
    assert "_production_point_in_time_frame" in functions, (
        "v93 no longer installs a production PIT frame builder"
    )
    displaced = _previous_hook_names(tree)
    body = functions["_production_point_in_time_frame"]
    loaded = {
        node.id
        for node in ast.walk(body)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    revived = sorted(loaded & displaced)
    assert not revived, (
        f"the replacement frame builder now delegates through {revived}; the "
        "PIT reason hook is no longer bypassed and the rest of this file needs "
        "re-recording"
    )


def test_the_replacement_is_scoped_to_one_run_and_always_restored() -> None:
    """It is a call-scoped swap, not a permanent rebind.

    That is why no static probe -- assembly manifest included -- can see it.
    """
    tree = _tree()
    functions = _functions(tree)
    run = functions["run_historical_backtest"]
    displaced = _previous_hook_names(tree)

    swapped = False
    restored = False
    for node in ast.walk(run):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t for t in node.targets if isinstance(t, ast.Attribute)]
        if not any(t.attr == "_verified_point_in_time_frame" for t in targets):
            continue
        if isinstance(node.value, ast.Name):
            if node.value.id == "_production_point_in_time_frame":
                swapped = True
            elif node.value.id in displaced:
                restored = True
    assert swapped, "v93 no longer swaps the verified-frame hook during a run"
    assert restored, "v93 no longer restores a captured hook after a run"

    finalbodies = [
        statement
        for node in ast.walk(run)
        if isinstance(node, ast.Try)
        for statement in node.finalbody
    ]
    assert any(
        isinstance(statement, ast.Assign)
        and isinstance(statement.value, ast.Name)
        and statement.value.id in displaced
        for statement in finalbodies
    ), "the restore is no longer in a finally block; a raised backtest leaks the swap"
