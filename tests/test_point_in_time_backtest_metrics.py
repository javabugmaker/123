from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from institution_scanner.point_in_time_backtest import (
    PIT_HELDOUT_METRIC_SCOPE,
    _split_counts,
    apply_summary_pit_scope,
    scrub_frame_for_pit_metrics,
    scrub_samples_for_pit_metrics,
)


def test_unavailable_samples_remain_auditable_but_cannot_move_metrics() -> None:
    samples = [
        {
            "ticker": "A.ST",
            "split": "test",
            "universe_snapshot_status": "ELIGIBLE",
            "sample_weight": 1.0,
            "score": 60.0,
            "return20": 5.0,
            "benchmark_return20": 2.0,
            "benchmark_return60": 4.0,
            "drawdown20": -3.0,
        },
        {
            "ticker": "B.ST",
            "split": "test",
            "universe_snapshot_status": "UNAVAILABLE",
            "sample_weight": 1.0,
            "score": 99.0,
            "return20": 80.0,
            "benchmark_return20": 3.0,
            "benchmark_return60": np.nan,
            "drawdown20": -40.0,
        },
    ]

    result = scrub_samples_for_pit_metrics(samples)

    assert len(result) == 2
    assert result[0]["pit_metric_eligible"] is True
    assert result[0]["sample_weight"] == 1.0
    assert result[0]["return20"] == 5.0
    assert result[1]["pit_metric_eligible"] is False
    assert result[1]["sample_weight"] == 0.0
    assert np.isnan(result[1]["score"])
    assert np.isnan(result[1]["return20"])
    assert np.isnan(result[1]["drawdown20"])
    assert result[1]["ticker"] == "B.ST"
    assert result[1]["split"] == "test"
    assert result[1]["pit_raw_benchmark_available_20d"] is True
    assert result[1]["pit_raw_benchmark_available_60d"] is False
    assert np.isnan(result[1]["benchmark_return20"])


def test_repeated_scrub_preserves_raw_benchmark_availability_without_return() -> None:
    samples = [
        {
            "split": "test",
            "universe_snapshot_status": "UNAVAILABLE",
            "benchmark_return20": 2.5,
            "benchmark_return60": 8.0,
        }
    ]

    first = scrub_samples_for_pit_metrics(samples)
    second = scrub_samples_for_pit_metrics(first)

    assert second[0]["pit_raw_benchmark_available_20d"] is True
    assert second[0]["pit_raw_benchmark_available_60d"] is True
    assert np.isnan(second[0]["benchmark_return20"])
    assert np.isnan(second[0]["benchmark_return60"])


def test_vectorized_scrub_matches_sample_semantics() -> None:
    frame = pd.DataFrame(
        {
            "ticker": ["A.ST", "B.ST"],
            "universe_snapshot_status": ["ELIGIBLE", "UNAVAILABLE"],
            "sample_weight": [0.5, 0.8],
            "score": [55.0, 95.0],
            "return60": [4.0, 90.0],
            "benchmark_return20": [2.0, 3.0],
            "benchmark_return60": [4.0, np.nan],
        }
    )

    result = scrub_frame_for_pit_metrics(frame)

    assert bool(result.loc[0, "pit_metric_eligible"]) is True
    assert bool(result.loc[1, "pit_metric_eligible"]) is False
    assert result.loc[0, "sample_weight"] == 0.5
    assert result.loc[1, "sample_weight"] == 0.0
    assert np.isnan(result.loc[1, "score"])
    assert np.isnan(result.loc[1, "return60"])
    assert bool(result.loc[1, "pit_raw_benchmark_available_20d"]) is True
    assert bool(result.loc[1, "pit_raw_benchmark_available_60d"]) is False


def test_split_audit_distinguishes_raw_alignment_from_pit_masking() -> None:
    raw = scrub_frame_for_pit_metrics(
        pd.DataFrame(
            {
                "split": ["test", "test", "validation"],
                "universe_snapshot_status": [
                    "UNAVAILABLE",
                    "UNAVAILABLE",
                    "ELIGIBLE",
                ],
                "universe_snapshot_reason": [
                    "snapshot_starts_after_signal",
                    "snapshot_starts_after_signal",
                    "eligible",
                ],
                "benchmark_return20": [1.0, 2.0, 3.0],
                "benchmark_return60": [4.0, np.nan, 6.0],
            }
        )
    )
    verified = raw.loc[raw["universe_snapshot_status"].eq("ELIGIBLE")]

    counts = _split_counts(raw, verified)

    assert counts["test"]["raw"] == 2
    assert counts["test"]["verified"] == 0
    assert counts["test"]["raw_benchmark_valid_20d"] == 2
    assert counts["test"]["raw_benchmark_valid_60d"] == 1
    assert counts["test"]["unverified_reasons"] == {
        "snapshot_starts_after_signal": 2
    }


def test_summary_sample_count_matches_verified_heldout_scope() -> None:
    summary = SimpleNamespace(
        samples=9,
        insufficient_test_data=False,
        error=None,
        rolling_oos_stats={
            "train": {"samples": 20},
            "validation": {"samples": 8},
            "test": {"samples": 9},
        },
    )
    counts = {
        "train": {"raw": 20, "verified": 18, "unverified": 2},
        "validation": {"raw": 8, "verified": 7, "unverified": 1},
        "test": {"raw": 9, "verified": 6, "unverified": 3},
    }

    apply_summary_pit_scope(summary, counts)

    assert summary.samples == 6
    assert summary.heldout_raw_test_samples == 9
    assert summary.heldout_verified_test_samples == 6
    assert summary.heldout_unverified_test_samples == 3
    assert summary.heldout_point_in_time_status == "VERIFIED_SUBSET"
    assert summary.heldout_metric_scope == PIT_HELDOUT_METRIC_SCOPE
    assert summary.heldout_metric_available is True
    assert summary.heldout_calibration_enabled is True
    assert summary.insufficient_test_data is False
    assert summary.error is None
    assert (
        summary.rolling_oos_stats["test"][
            "point_in_time_verified_samples"
        ]
        == 6
    )


def test_pit_warmup_disables_calibration_without_failing_pipeline() -> None:
    summary = SimpleNamespace(
        samples=1200,
        insufficient_test_data=False,
        error=None,
        rolling_oos_stats={},
    )
    counts = {
        "train": {"raw": 800, "verified": 0, "unverified": 800},
        "validation": {"raw": 300, "verified": 0, "unverified": 300},
        "test": {
            "raw": 100,
            "verified": 0,
            "unverified": 100,
            "raw_benchmark_valid_20d": 98,
            "raw_benchmark_valid_60d": 91,
            "unverified_reasons": {"snapshot_starts_after_signal": 100},
        },
    }

    apply_summary_pit_scope(summary, counts)

    assert summary.samples == 0
    assert summary.heldout_point_in_time_status == "PIT_WARMUP"
    assert summary.heldout_metric_available is False
    assert summary.heldout_calibration_enabled is False
    assert summary.heldout_pit_shortage_pipeline_fatal is False
    assert "calibration disabled" in summary.heldout_metric_warning
    assert "snapshot_starts_after_signal (100)" in summary.heldout_metric_warning
    assert summary.benchmark_metric_scope == PIT_HELDOUT_METRIC_SCOPE
    assert summary.benchmark_raw_valid_count_20d == 98
    assert summary.benchmark_raw_valid_count_60d == 91
    assert summary.benchmark_raw_coverage_20d == 0.98
    assert summary.benchmark_raw_coverage_60d == 0.91
    assert summary.heldout_unverified_reason_counts == {
        "snapshot_starts_after_signal": 100
    }
    assert summary.insufficient_test_data is False
    assert summary.error is None


def test_one_verified_test_sample_is_nonfatal_but_not_metric_ready() -> None:
    summary = SimpleNamespace(
        samples=12,
        insufficient_test_data=False,
        error=None,
        rolling_oos_stats={},
    )
    counts = {
        "train": {"raw": 20, "verified": 15, "unverified": 5},
        "validation": {"raw": 8, "verified": 4, "unverified": 4},
        "test": {"raw": 12, "verified": 1, "unverified": 11},
    }

    apply_summary_pit_scope(summary, counts)

    assert summary.samples == 1
    assert summary.heldout_point_in_time_status == "INSUFFICIENT_VERIFIED_TEST"
    assert summary.heldout_metric_available is False
    assert summary.heldout_calibration_enabled is False
    assert summary.insufficient_test_data is False
    assert summary.error is None


def test_real_core_test_failure_is_preserved() -> None:
    summary = SimpleNamespace(
        samples=1,
        insufficient_test_data=True,
        error="测试集有效样本不足：1，至少需要2个样本",
        rolling_oos_stats={},
    )
    counts = {
        "train": {"raw": 0, "verified": 0, "unverified": 0},
        "validation": {"raw": 0, "verified": 0, "unverified": 0},
        "test": {"raw": 1, "verified": 0, "unverified": 1},
    }

    apply_summary_pit_scope(summary, counts)

    assert summary.samples == 0
    assert summary.insufficient_test_data is True
    assert summary.error == "测试集有效样本不足：1，至少需要2个样本"
