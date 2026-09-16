"""Behavioural coverage for ``model_audit`` — the last zero-coverage module.

Why this module, and why now
----------------------------
A reachability sweep (see ``test_model_audit_provenance.py``) established that
all 27 top-level definitions in ``model_audit`` are reachable from
``run_audit``, so unlike ``scanner_core`` there is no dead half to avoid here.
That makes it the safest remaining target: every assertion below lands on code
that production actually runs. ``model_audit`` is imported by ``analytics``,
``main`` and ``scan_service``, all three through ``run_audit`` alone.

What is asserted, and what is not
---------------------------------
No numeric audit output is pinned. The module's whole purpose is to measure how
much each exported ranking factor reshapes the cross-section, and those numbers
move whenever a factor's distribution moves — freezing them would produce a
test that goes red on legitimate data changes.

What is asserted instead is the *contract*: the full-universe guards that
abort the run, the identity of the reconstruction formula (a coherent frame
must reconstruct to zero error), the eleven scenario names and their ordering,
the degenerate-input handling in ``_spearman`` / ``_safe_divide`` /
``_top_index``, and the four artefacts ``run_audit`` must write.

The frame
---------
A *coherent* synthetic frame: ``RankingScore`` is built by hand as the product
of exactly the factors ``build_scenarios`` multiplies back together. That is
what makes the reconstruction assertion meaningful — it is not self-fulfilling,
because the expected product is computed in the test from literal constants,
not by calling the module. If the module ever drops or adds a leg, the error
becomes non-zero and this file goes red.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import model_audit
from model_audit import (
    _recency_multiplier,
    _safe_divide,
    _scenario_metrics,
    _spearman,
    _top_index,
    _validate_full_universe,
    build_scenarios,
    run_audit,
    scenario_report,
    threshold_report,
)

# Literal factor values used to build the coherent frame. Kept as module
# constants so the reconstruction assertion reads as a formula, not a magic
# number.
_ENTRY_BUY_NOW = 1.0  # config.ENTRY_SIGNAL_MULTIPLIERS["BUY_NOW"]
_ENTRY_AVOID = 0.50  # config.ENTRY_SIGNAL_MULTIPLIERS["AVOID"]
_HARD = 0.90
_CHASE = 0.80
_DATA = 0.95
_RECENCY_RAW = 0.50  # below the 0.7 floor, so it exercises the clip
_RECENCY = 0.8 + 0.2 * 0.7  # 0.94 after _recency_multiplier
_READINESS = 0.85

SCENARIO_NAMES = [
    "baseline",
    "no_readiness",
    "no_decision",
    "no_readiness_or_decision",
    "no_entry",
    "no_hard_risk",
    "no_chase",
    "no_data_confidence",
    "no_recency",
    "neutralize_quality_decision_overlap",
    "neutralize_filter_lifecycle_decision_overlap",
]


def _coherent_frame(rows: int = 24, seed: int = 20260916) -> pd.DataFrame:
    """A frame whose RankingScore equals the product of the exported factors."""
    rng = np.random.default_rng(seed)
    is_etf = np.array([index % 5 == 0 for index in range(rows)])
    base = np.round(rng.uniform(20.0, 90.0, rows), 4)
    entry = np.where(is_etf, _ENTRY_AVOID, _ENTRY_BUY_NOW)
    ranking = base * entry * _HARD * _CHASE * _DATA * _RECENCY * _READINESS
    return pd.DataFrame(
        {
            "Ticker": [f"{600000 + index}" for index in range(rows)],
            "IsETF": is_etf,
            "EntrySignal": np.where(is_etf, "AVOID", "BUY_NOW"),
            "RankingScore": ranking,
            "CrossAssetScore": base,
            "HardRiskPenalty": _HARD,
            "ChaseRiskFactor": _CHASE,
            "DataConfidenceFactor": _DATA,
            "SignalRecencyFactor": _RECENCY_RAW,
            "ReadinessPenaltyFactor": _READINESS,
            "RankingUniverseSize": rows,
            "RankingScope": "FULL_UNIVERSE",
        }
    )


# --------------------------------------------------------------------------
# _validate_full_universe — the guards that abort a run
# --------------------------------------------------------------------------


def test_empty_frame_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        _validate_full_universe(pd.DataFrame())


def test_universe_size_mismatch_is_rejected() -> None:
    frame = pd.DataFrame({"Ticker": ["a", "b"], "RankingUniverseSize": [5, 5]})
    with pytest.raises(ValueError, match="ranking scope violation"):
        _validate_full_universe(frame)


def test_mixed_universe_sizes_are_rejected() -> None:
    frame = pd.DataFrame({"Ticker": ["a", "b"], "RankingUniverseSize": [5, 7]})
    with pytest.raises(ValueError, match="mixed RankingUniverseSize"):
        _validate_full_universe(frame)


def test_non_full_universe_scope_is_rejected() -> None:
    frame = pd.DataFrame({"Ticker": ["a", "b"], "RankingScope": ["TOP50", "TOP50"]})
    with pytest.raises(ValueError, match="unsupported ranking scopes"):
        _validate_full_universe(frame)


def test_missing_universe_size_column_warns_without_raising() -> None:
    frame = pd.DataFrame({"Ticker": ["a", "b"], "RankingScore": [1.0, 2.0]})
    warnings = _validate_full_universe(frame)
    assert len(warnings) == 1
    assert "RankingUniverseSize" in warnings[0]


def test_compliant_frame_produces_no_warnings() -> None:
    assert _validate_full_universe(_coherent_frame()) == []


# --------------------------------------------------------------------------
# build_scenarios
# --------------------------------------------------------------------------


def test_scenario_names_and_order_are_stable() -> None:
    scenarios, _ = build_scenarios(_coherent_frame())
    assert [item.name for item in scenarios] == SCENARIO_NAMES


def test_every_scenario_carries_a_description() -> None:
    scenarios, _ = build_scenarios(_coherent_frame())
    assert all(item.description.strip() for item in scenarios)


def test_baseline_score_is_the_exported_ranking_score() -> None:
    frame = _coherent_frame()
    scenarios, _ = build_scenarios(frame)
    baseline = next(item for item in scenarios if item.name == "baseline")
    pd.testing.assert_series_equal(
        baseline.score,
        frame["RankingScore"].astype(float),
        check_names=False,
    )


def test_build_scenarios_is_deterministic() -> None:
    frame = _coherent_frame()
    first_scenarios, first_diag = build_scenarios(frame)
    second_scenarios, second_diag = build_scenarios(frame)
    pd.testing.assert_frame_equal(first_diag, second_diag)
    for left, right in zip(first_scenarios, second_scenarios, strict=True):
        pd.testing.assert_series_equal(left.score, right.score)


def test_reconstruction_reproduces_a_coherent_frame() -> None:
    """The core contract: the exported factors must multiply back to the score."""
    _, diagnostics = build_scenarios(_coherent_frame())
    assert diagnostics["ReconstructionAbsError"].max() < 1e-6


def test_no_readiness_divides_out_the_readiness_leg() -> None:
    frame = _coherent_frame()
    scenarios, _ = build_scenarios(frame)
    baseline = next(item for item in scenarios if item.name == "baseline")
    no_readiness = next(item for item in scenarios if item.name == "no_readiness")
    expected = baseline.score / _READINESS
    pd.testing.assert_series_equal(no_readiness.score, expected, check_names=False)


def test_no_entry_divides_out_the_entry_multiplier() -> None:
    frame = _coherent_frame()
    scenarios, diagnostics = build_scenarios(frame)
    baseline = next(item for item in scenarios if item.name == "baseline")
    no_entry = next(item for item in scenarios if item.name == "no_entry")
    expected = baseline.score / diagnostics["EntryFactor"]
    pd.testing.assert_series_equal(no_entry.score, expected, check_names=False)


def test_diagnostics_columns_are_complete() -> None:
    _, diagnostics = build_scenarios(_coherent_frame())
    expected = [
        "Ticker",
        "AssetGroup",
        "RankingScore",
        "CrossAssetScore",
        "EntryFactor",
        "HardRiskFactor",
        "ChaseRiskFactor",
        "DataConfidenceFactor",
        "RecencyMultiplier",
        "ReadinessPenaltyFactor",
        "InferredDecisionFactor",
        "DecisionInferenceAbsError",
        "RankingDecisionStateAtScore",
        "RankingTierReconciliationFactor",
        "RankingTierReconciliationState",
        "ExportedDecisionState",
        "QualityActionBlock",
        "FilterFailure",
        "LifecycleFailure",
        "ReconstructedRankingScore",
        "ReconstructionAbsError",
        "ModelAuditIntegrityVersion",
    ]
    assert list(diagnostics.columns) == expected


def test_integrity_version_is_stamped_on_every_row() -> None:
    _, diagnostics = build_scenarios(_coherent_frame())
    assert set(diagnostics["ModelAuditIntegrityVersion"].unique()) == {
        model_audit.AUDIT_VERSION
    }


def test_etf_rows_are_grouped_separately_from_stocks() -> None:
    _, diagnostics = build_scenarios(_coherent_frame())
    groups = diagnostics["AssetGroup"].tolist()
    assert groups.count("ETF") == 5  # every fifth row of a 24-row frame
    assert groups.count("STOCK") == 19


# --------------------------------------------------------------------------
# small helpers: recency, division, correlation, top-n
# --------------------------------------------------------------------------


def test_recency_multiplier_clips_the_raw_factor_at_seventy_percent() -> None:
    frame = pd.DataFrame({"SignalRecencyFactor": [0.0, 0.5, 1.0, 2.0]})
    result = _recency_multiplier(frame)
    assert result.iloc[0] == pytest.approx(0.94)  # 0.0 clipped up to 0.7
    assert result.iloc[1] == pytest.approx(0.94)  # 0.5 clipped up to 0.7
    assert result.iloc[2] == pytest.approx(1.0)
    assert result.iloc[3] == pytest.approx(1.0)  # 2.0 clipped down to 1.0


def test_safe_divide_clips_zero_factors_instead_of_producing_infinity() -> None:
    result = _safe_divide(pd.Series([1.0, 2.0]), pd.Series([0.0, 2.0]))
    assert np.isfinite(result).all()
    assert result.iloc[0] == pytest.approx(1e9, rel=1e-6)
    assert result.iloc[1] == pytest.approx(1.0)


def test_spearman_of_identical_series_is_one() -> None:
    values = pd.Series([3.0, 1.0, 2.0, 5.0, 4.0])
    assert _spearman(values, values.copy()) == pytest.approx(1.0)


def test_spearman_of_reversed_series_is_minus_one() -> None:
    left = pd.Series([1.0, 2.0, 3.0, 4.0])
    right = pd.Series([4.0, 3.0, 2.0, 1.0])
    assert _spearman(left, right) == pytest.approx(-1.0)


def test_spearman_is_nan_when_either_side_is_constant() -> None:
    assert np.isnan(_spearman(pd.Series([1.0, 1.0, 1.0]), pd.Series([1.0, 2.0, 3.0])))
    assert np.isnan(_spearman(pd.Series([1.0, 2.0, 3.0]), pd.Series([5.0, 5.0, 5.0])))


def test_spearman_is_nan_with_fewer_than_two_usable_rows() -> None:
    assert np.isnan(_spearman(pd.Series([1.0]), pd.Series([2.0])))
    assert np.isnan(_spearman(pd.Series([], dtype=float), pd.Series([], dtype=float)))


def test_spearman_drops_infinite_pairs_before_ranking() -> None:
    left = pd.Series([1.0, 2.0, 3.0, 4.0])
    right = pd.Series([1.0, np.inf, 3.0, 4.0])
    assert _spearman(left, right) == pytest.approx(1.0)


def test_top_index_returns_every_row_when_n_exceeds_length() -> None:
    result = _top_index(pd.Series([1.0, 5.0, 3.0]), 10)
    assert len(result) == 3


def test_top_index_drops_nan_and_infinite_entries() -> None:
    result = _top_index(pd.Series([1.0, np.nan, 5.0, np.inf, 3.0]), 10)
    assert len(result) == 3
    assert 1 not in result  # the NaN row keeps its index and is excluded


# --------------------------------------------------------------------------
# _scenario_metrics / scenario_report
# --------------------------------------------------------------------------


def test_scenario_metrics_rows_follow_the_mask() -> None:
    baseline = pd.Series([5.0, 4.0, 3.0, 2.0])
    mask = pd.Series([True, True, False, False])
    assert _scenario_metrics(baseline, baseline.copy(), mask)["rows"] == 2


def test_scenario_metrics_of_identical_series_is_the_identity() -> None:
    baseline = pd.Series(np.linspace(1.0, 60.0, 60))
    metrics = _scenario_metrics(baseline, baseline.copy(), pd.Series([True] * 60))
    assert metrics["spearman"] == pytest.approx(1.0)
    assert metrics["top50_overlap"] == pytest.approx(1.0)
    assert metrics["top50_out"] == 0
    assert metrics["top50_in"] == 0
    assert metrics["mean_score_shift"] == pytest.approx(0.0)


def test_scenario_metrics_survives_an_all_false_mask() -> None:
    baseline = pd.Series([5.0, 4.0, 3.0])
    metrics = _scenario_metrics(baseline, baseline.copy(), pd.Series([False] * 3))
    assert metrics["rows"] == 0
    assert np.isnan(metrics["spearman"])


def test_scenario_report_covers_every_non_baseline_scenario_in_three_scopes() -> None:
    frame = _coherent_frame()
    scenarios, _ = build_scenarios(frame)
    metrics, _ = scenario_report(frame, scenarios)
    assert len(metrics) == (len(SCENARIO_NAMES) - 1) * 3
    assert set(metrics["Scope"]) == {"ALL", "STOCK", "ETF"}
    assert set(metrics["Scenario"]) == set(SCENARIO_NAMES) - {"baseline"}


def test_scenario_report_movers_exclude_the_baseline() -> None:
    frame = _coherent_frame()
    scenarios, _ = build_scenarios(frame)
    _, movers = scenario_report(frame, scenarios)
    assert set(movers["Scenario"]) == set(SCENARIO_NAMES) - {"baseline"}


# --------------------------------------------------------------------------
# threshold_report
# --------------------------------------------------------------------------


def test_threshold_report_exposes_every_configured_threshold() -> None:
    rows = threshold_report(_coherent_frame())
    assert len(rows) == 17
    assert {
        "Parameter",
        "Threshold",
        "Window",
        "NearCount",
        "ValidCount",
        "NearRatio",
        "Q25",
        "Median",
        "Q75",
        "PerturbationGrid",
        "Semantics",
    } <= set(rows.columns)


def test_threshold_near_ratio_matches_the_counts() -> None:
    rows = threshold_report(_coherent_frame())
    for _, row in rows.iterrows():
        expected = row["NearCount"] / max(1, row["ValidCount"])
        assert row["NearRatio"] == pytest.approx(expected)


# --------------------------------------------------------------------------
# run_audit — end to end
# --------------------------------------------------------------------------


def test_run_audit_writes_all_four_artifacts(tmp_path: Path) -> None:
    source = tmp_path / "AllResults.csv"
    _coherent_frame().to_csv(source, index=False, encoding="utf-8-sig")
    out_dir = tmp_path / "audit"

    run_audit(source, out_dir)

    for name in (
        "ranking_audit.json",
        "ranking_scenarios.csv",
        "ranking_top_movers.csv",
        "threshold_exposure.csv",
    ):
        assert (out_dir / name).is_file(), name


def test_run_audit_payload_counts_stocks_and_etfs(tmp_path: Path) -> None:
    source = tmp_path / "AllResults.csv"
    _coherent_frame(rows=20).to_csv(source, index=False, encoding="utf-8-sig")

    payload = run_audit(source, tmp_path / "audit")

    assert payload["rows"] == 20
    assert payload["stocks"] == 16  # every fifth row is an ETF
    assert payload["etfs"] == 4
    assert payload["audit_version"] == model_audit.AUDIT_VERSION
    assert payload["warnings"] == []


def test_run_audit_reconstruction_is_exact_for_a_coherent_frame(tmp_path: Path) -> None:
    source = tmp_path / "AllResults.csv"
    _coherent_frame().to_csv(source, index=False, encoding="utf-8-sig")

    payload = run_audit(source, tmp_path / "audit")

    assert payload["reconstruction"]["max_abs_error"] < 1e-6
    assert payload["reconstruction"]["median_abs_error"] < 1e-6


def test_run_audit_rejects_an_empty_input_csv(tmp_path: Path) -> None:
    source = tmp_path / "AllResults.csv"
    pd.DataFrame({"Ticker": []}).to_csv(source, index=False, encoding="utf-8-sig")

    with pytest.raises(ValueError, match="empty"):
        run_audit(source, tmp_path / "audit")


def test_run_audit_payload_is_json_serialisable_without_nan(tmp_path: Path) -> None:
    """``allow_nan=False`` means any stray NaN would abort the write."""
    source = tmp_path / "AllResults.csv"
    _coherent_frame(rows=12).to_csv(source, index=False, encoding="utf-8-sig")
    out_dir = tmp_path / "audit"

    run_audit(source, out_dir)

    loaded = json.loads((out_dir / "ranking_audit.json").read_text(encoding="utf-8"))
    assert loaded["rows"] == 12
    assert loaded["notes"], "the audit must always publish its caveats"
