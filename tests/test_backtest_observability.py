from __future__ import annotations

from types import SimpleNamespace

from institution_scanner.backtest_observability import backtest_log_lines
from institution_scanner.point_in_time_backtest import PIT_HELDOUT_METRIC_SCOPE


def test_pit_warmup_log_reports_raw_alignment_without_nan_metrics() -> None:
    summary = SimpleNamespace(
        mode="fast",
        samples=0,
        all_samples=163474,
        heldout_raw_test_samples=50950,
        heldout_verified_test_samples=0,
        heldout_metric_scope=PIT_HELDOUT_METRIC_SCOPE,
        heldout_metric_available=False,
        heldout_unverified_reason_counts={
            "snapshot_starts_after_signal": 50950
        },
        benchmark_valid_count_20d=0,
        benchmark_valid_count_60d=0,
        benchmark_raw_valid_count_20d=50950,
        benchmark_raw_valid_count_60d=50700,
        split_dates={"test_start": "2024-06-17"},
    )

    completion, benchmark = backtest_log_lines(summary, "auto")

    assert "PIT verified test samples=0/50950" in completion
    assert "metrics unavailable during PIT warm-up" in completion
    assert "nan" not in completion.lower()
    assert "raw aligned 20d=50950/50950 (100.0%)" in benchmark
    assert "PIT valid 20d=0/50950 (0.0%)" in benchmark
    assert "top unverified reason=snapshot_starts_after_signal (50950)" in benchmark


def test_legacy_log_keeps_existing_metric_semantics() -> None:
    summary = SimpleNamespace(
        mode="exact",
        samples=12,
        all_samples=30,
        win_rate_20d=0.5,
        average_return_20d=1.25,
        average_return_60d=3.5,
        benchmark_valid_count=11,
        benchmark_coverage=11 / 12,
        split_dates={"test_start": "2025-01-01"},
    )

    completion, benchmark = backtest_log_lines(summary, "auto")

    assert "12 test samples" in completion
    assert "20d win rate 50.0%" in completion
    assert "benchmark valid count 11, coverage 91.7%" in benchmark
