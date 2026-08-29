"""Truthful, compact logging for PIT-scoped historical backtests."""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .point_in_time_backtest import PIT_HELDOUT_METRIC_SCOPE


def _value(summary: Any, name: str, default: Any = None) -> Any:
    if isinstance(summary, Mapping):
        return summary.get(name, default)
    return getattr(summary, name, default)


def _integer(summary: Any, name: str, default: int = 0) -> int:
    try:
        return max(0, int(float(_value(summary, name, default))))
    except (TypeError, ValueError):
        return default


def _number(summary: Any, name: str, default: float = 0.0) -> float:
    try:
        result = float(_value(summary, name, default))
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in {0, 1}:
        return bool(value)
    return None


def _top_reason(summary: Any) -> tuple[str, int] | None:
    raw = _value(summary, "heldout_unverified_reason_counts", {})
    if not isinstance(raw, Mapping):
        return None
    reasons: list[tuple[str, int]] = []
    for reason, value in raw.items():
        try:
            count = max(0, int(float(value)))
        except (TypeError, ValueError):
            continue
        if count > 0:
            reasons.append((str(reason), count))
    return min(reasons, key=lambda item: (-item[1], item[0])) if reasons else None


def backtest_log_lines(summary: Any, requested_mode: str) -> tuple[str, str]:
    """Build completion and benchmark lines without presenting masked data as missing."""
    mode = str(_value(summary, "mode", requested_mode) or requested_mode).upper()
    samples = _integer(summary, "samples")
    all_samples = _integer(summary, "all_samples", samples)
    raw_test = _integer(summary, "heldout_raw_test_samples", samples)
    verified_test = _integer(summary, "heldout_verified_test_samples", samples)
    scope = str(_value(summary, "heldout_metric_scope", "") or "")
    metric_available = _optional_bool(_value(summary, "heldout_metric_available"))

    if scope == PIT_HELDOUT_METRIC_SCOPE and metric_available is False:
        completion = (
            f"Backtest complete: mode={mode}, PIT verified test samples="
            f"{verified_test}/{raw_test}, {all_samples} all samples; held-out "
            "performance metrics unavailable during PIT warm-up."
        )
    else:
        completion = (
            f"Backtest complete: mode={mode}, {samples} test samples, "
            f"{all_samples} all samples, 20d win rate "
            f"{_number(summary, 'win_rate_20d') * 100:.1f}%, average return "
            f"{_number(summary, 'average_return_20d'):.2f}%, 60d average return "
            f"{_number(summary, 'average_return_60d'):.2f}%."
        )

    dates = _value(summary, "split_dates", {})
    if scope != PIT_HELDOUT_METRIC_SCOPE:
        benchmark = (
            f"Backtest dates: {dates}; benchmark valid count "
            f"{_integer(summary, 'benchmark_valid_count')}, coverage "
            f"{_number(summary, 'benchmark_coverage') * 100:.1f}%."
        )
        return completion, benchmark

    verified_20d = _integer(summary, "benchmark_valid_count_20d")
    verified_60d = _integer(summary, "benchmark_valid_count_60d")
    raw_20d = _integer(summary, "benchmark_raw_valid_count_20d")
    raw_60d = _integer(summary, "benchmark_raw_valid_count_60d")
    denominator = max(raw_test, 1)
    benchmark = (
        f"Backtest dates: {dates}; benchmark scope=PIT_VERIFIED, "
        f"PIT valid 20d={verified_20d}/{raw_test} "
        f"({verified_20d / denominator * 100:.1f}%), "
        f"60d={verified_60d}/{raw_test} ({verified_60d / denominator * 100:.1f}%); "
        f"raw aligned 20d={raw_20d}/{raw_test} ({raw_20d / denominator * 100:.1f}%), "
        f"60d={raw_60d}/{raw_test} ({raw_60d / denominator * 100:.1f}%)."
    )
    top_reason = _top_reason(summary)
    if top_reason is not None:
        benchmark = (
            f"{benchmark[:-1]}; top unverified reason={top_reason[0]} "
            f"({top_reason[1]})."
        )
    return completion, benchmark
