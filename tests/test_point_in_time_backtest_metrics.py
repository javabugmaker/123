from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from institution_scanner.point_in_time_backtest import (
    PIT_HELDOUT_METRIC_SCOPE,
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
            "drawdown20": -3.0,
        },
        {
            "ticker": "B.ST",
            "split": "test",
            "universe_snapshot_status": "UNAVAILABLE",
            "sample_weight": 1.0,
            "score": 99.0,
            "return20": 80.0,
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


def test_vectorized_scrub_matches_sample_semantics() -> None:
    frame = pd.DataFrame(
        {
            "ticker": ["A.ST", "B.ST"],
            "universe_snapshot_status": ["ELIGIBLE", "UNAVAILABLE"],
            "sample_weight": [0.5, 0.8],
            "score": [55.0, 95.0],
            "return60": [4.0, 90.0],
        }
    )

    result = scrub_frame_for_pit_metrics(frame)

    assert bool(result.loc[0, "pit_metric_eligible"]) is True
    assert bool(result.loc[1, "pit_metric_eligible"]) is False
    assert result.loc[0, "sample_weight"] == 0.5
    assert result.loc[1, "sample_weight"] == 0.0
    assert np.isnan(result.loc[1, "score"])
    assert np.isnan(result.loc[1, "return60"])


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
    assert summary.insufficient_test_data is False
    assert (
        summary.rolling_oos_stats["test"][
            "point_in_time_verified_samples"
        ]
        == 6
    )


def test_summary_fails_closed_when_verified_test_is_too_small() -> None:
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
    assert summary.insufficient_test_data is True
    assert summary.heldout_point_in_time_status == "INSUFFICIENT_VERIFIED_TEST"
    assert "PIT测试集有效样本不足" in summary.error
