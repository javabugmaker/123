"""Behaviour gates for ``daily_pipeline_core`` internals that no test reaches.

Everything here runs against the **un-assembled** core, via
``daily_pipeline_core_probe.py``.  That is deliberate: ``_quality_gate_errors``,
``_final_output_errors``, ``_csv_profile``, ``_write_manifest``,
``run_daily_pipeline`` are all rebound by overlays, so importing the facade
would make these tests exercise three stacked wrappers instead of the
implementation they claim to pin.

Findings locked here (none of them fixed -- see REFACTOR_PLAN §9.12):

* ``QualityApplicable`` defaults **True** in ``_csv_profile`` but **False** in
  ``_decision_snapshot``.  Latent only: production frames always carry the
  column, so the default never fires today.
* ``_rollback_transaction`` catches ``FileNotFoundError`` alone, so a
  ``PermissionError``/``OSError`` during restore escapes the failure handler.
* ``_cache_health``'s ``cold_start`` expression is redundant but equivalent to
  its reduced form -- locked as a behaviour statement, not a style one.
"""

from __future__ import annotations

from typing import Any

import pytest
from _daily_pipeline_probe_runner import run_probe


@pytest.fixture(scope="module")
def probe() -> dict[str, Any]:
    return run_probe()


# ---------------------------------------------------------------------------------------
# QualityApplicable default divergence
# ---------------------------------------------------------------------------------------
def test_quality_applicable_defaults_diverge_when_the_column_is_absent(
    probe: dict[str, Any],
) -> None:
    """Locked as-is: the two call sites disagree about the missing-column default.

    ``_csv_profile`` (daily_pipeline_core.py:200-202) reads
    ``row.get("QualityApplicable", True)`` while ``_decision_snapshot``
    (:567) reads ``row.get("QualityApplicable", False)``.  On a frame that
    lacks the column entirely, every valid stock counts as quality-applicable
    for the pass-rate denominator but as not-applicable for the blocker count.

    Reverse validation: the second assertion is the control.  Without it the
    first could pass simply because ``_truthy`` mishandles the value, rather
    than because the defaults differ.
    """
    split = probe["quality_default_split"]
    assert split["bare_profile_applicable_stocks"] == split["bare_profile_valid_stocks"], (
        "_csv_profile no longer defaults QualityApplicable to True: "
        f"{split['bare_profile_applicable_stocks']} of "
        f"{split['bare_profile_valid_stocks']}"
    )
    assert set(split["bare_snapshot_applicable"].values()) == {False}, (
        "_decision_snapshot no longer defaults QualityApplicable to False: "
        f"{split['bare_snapshot_applicable']}"
    )


def test_the_two_call_sites_agree_once_the_column_exists(probe: dict[str, Any]) -> None:
    """Control for the divergence above: with the column present they concur."""
    split = probe["quality_default_split"]
    assert split["flagged_profile_applicable_stocks"] == 3
    assert set(split["flagged_snapshot_applicable"].values()) == {True}, (
        f"{split['flagged_snapshot_applicable']}"
    )


# ---------------------------------------------------------------------------------------
# _cache_health cold_start
# ---------------------------------------------------------------------------------------
def test_cold_start_matches_its_reduced_form(probe: dict[str, Any]) -> None:
    """``bool(A and A != B) or not A`` reduces to ``A != B`` for a non-empty B.

    Locked as a behaviour statement: if the expression is ever simplified, this
    test is the thing that proves the simplification was free.  It is also the
    assertion that catches someone "simplifying" it into something that changes
    the empty-version case.
    """
    cold = probe["cold_start"]
    assert cold["pipeline_version_nonempty"], (
        "PIPELINE_VERSION is empty; the equivalence below no longer holds"
    )
    assert cold["observed"] == cold["reduced_form"], (
        f"cold_start diverged from its reduced form: "
        f"{cold['observed']} != {cold['reduced_form']}"
    )
    assert cold["observed"] == {
        "empty": True,
        "stale": True,
        "same": False,
        "missing": True,
    }, cold["observed"]


# ---------------------------------------------------------------------------------------
# _read_json hardening (contrast with the scanner_core checkpoint gap)
# ---------------------------------------------------------------------------------------
def test_read_json_returns_empty_for_non_object_top_level(probe: dict[str, Any]) -> None:
    observed = probe["read_json"]
    assert observed == {"list": {}, "null": {}, "string": {}}, (
        "_read_json stopped defending against a non-dict top level: "
        f"{observed}. This is the exact gap load_checkpoint had."
    )


# ---------------------------------------------------------------------------------------
# _rollback_transaction exception coverage
# ---------------------------------------------------------------------------------------
def test_rollback_swallows_file_not_found_but_not_other_os_errors(
    probe: dict[str, Any],
) -> None:
    """The handler is one exception type wide.

    Consequences: a ``PermissionError`` (a canonical CSV held open by another
    process on Windows) or any other ``OSError`` during restore escapes
    ``run_daily_pipeline``'s ``except Exception`` handler.  The run then dies
    with a traceback instead of returning 2, and PublicationStatus.json is
    never marked ``failed`` -- though the ``finally`` block still removes the
    staging directory and the transaction journal survives for the next run's
    recovery pass.

    Not fixed: widening the tuple would turn a loud crash into a quiet return
    while leaving canonical files unrestored.  That is a judgement call for the
    owner, so the current behaviour is pinned instead.

    Reverse validation: ``file_not_found`` must stay ``swallowed``.  If that
    case ever reported an escape, the patch is no longer reaching the call and
    this test would be vacuously green on the other two.
    """
    escape = probe["rollback_escape"]
    assert escape == {
        "file_not_found": "swallowed",
        "permission": "PermissionError",
        "os_error": "OSError",
    }, f"_rollback_transaction exception coverage changed: {escape}"


# ---------------------------------------------------------------------------------------
# _quality_gate_errors
# ---------------------------------------------------------------------------------------
def test_quality_gate_accepts_a_healthy_universe(probe: dict[str, Any]) -> None:
    errors = probe["quality_gate_errors"]["healthy"]
    assert errors == [], f"a healthy universe was rejected: {errors}"


def test_quality_gate_reports_every_floor_violation(probe: dict[str, Any]) -> None:
    errors = probe["quality_gate_errors"]["empty"]
    assert len(errors) == 6, f"expected 6 floor violations, got {len(errors)}: {errors}"
    for fragment in ("有效标的仅", "股票仅", "ETF仅", "有效股票仅", "有效ETF仅", "最新交易日覆盖率"):
        assert any(fragment in error for error in errors), (
            f"missing violation class {fragment!r} in {errors}"
        )


def test_quality_gate_is_disabled_by_the_flag(probe: dict[str, Any]) -> None:
    assert probe["quality_gate_errors"]["empty_gates_off"] == [], (
        "quality_gates=False no longer short-circuits the gate"
    )


def test_quality_gate_detects_a_relative_universe_drop(probe: dict[str, Any]) -> None:
    errors = probe["quality_gate_errors"]["relative_drop"]
    assert any("总标的数量从上一轮" in error for error in errors), (
        f"relative universe drop was not reported: {errors}"
    )


# ---------------------------------------------------------------------------------------
# _final_output_errors
# ---------------------------------------------------------------------------------------
def test_final_output_gate_accepts_a_consistent_split(probe: dict[str, Any]) -> None:
    errors = probe["final_output_errors"]["consistent"]
    assert errors == [], f"a consistent split was rejected: {errors}"


def test_final_output_gate_rejects_empty_files_even_with_gates_off(
    probe: dict[str, Any],
) -> None:
    """An empty Top-N file is a hard error, not a gate error.

    ``quality_gates=False`` suppresses the ratio and RunId checks but not the
    "missing or empty" check at daily_pipeline_core.py:338-340.  Both columns
    of the probe confirm the same three errors, which is the point: disabling
    quality gates does not let an empty result set publish.
    """
    with_gates = probe["final_output_errors"]["missing_file"]
    without_gates = probe["final_output_errors"]["gates_off"]
    assert len(with_gates) == 3, with_gates
    assert without_gates == with_gates, (
        "quality_gates=False now suppresses the empty-file check: "
        f"{without_gates} != {with_gates}"
    )


def test_final_output_gate_rejects_stale_freshness(probe: dict[str, Any]) -> None:
    errors = probe["final_output_errors"]["stale_freshness"]
    assert len(errors) == 3, errors
    assert all("最新交易日覆盖率" in error for error in errors), errors


def test_final_output_gate_rejects_run_id_mismatch(probe: dict[str, Any]) -> None:
    errors = probe["final_output_errors"]["run_id_mismatch"]
    assert len(errors) == 3, errors
    assert all("与 AllResults 不一致" in error for error in errors), errors
