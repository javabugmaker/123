"""Point-in-time scope enforcement for held-out backtest diagnostics.

The historical backtest core already tags every sample with
``universe_snapshot_status``. Production calibration consumes only ELIGIBLE
rows, but legacy top-level held-out statistics were calculated from the full
``test`` partition. That allowed UNAVAILABLE universe observations to remain
visible in diagnostic RankIC, bucket monotonicity and return summaries even
though v106 correctly prevented them from activating peer calibration.

This compatibility layer makes those legacy diagnostics point-in-time clean
without changing the price path, signal generation, production weights,
ranking thresholds or TradeReady policy:
- UNAVAILABLE samples remain in the raw audit set so coverage loss is visible;
- their numeric outcomes and score are masked and their sample weight is zero;
- therefore held-out estimators, RankIC, drawdown and score buckets use only
  ELIGIBLE point-in-time observations;
- BacktestSummary exposes raw/verified/unverified test counts explicitly and
  its canonical ``samples`` count matches the verified held-out metric scope;
- insufficient PIT coverage disables calibration but does not fail DAILY.
"""
from __future__ import annotations

import json
import os
from contextlib import suppress
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

POINT_IN_TIME_BACKTEST_VERSION = (
    "2026-08-29-v106.2-pit-benchmark-provenance-v2"
)
PIT_HELDOUT_METRIC_SCOPE = "POINT_IN_TIME_ELIGIBLE_SAMPLES_ONLY"

_RAW_BENCHMARK_MARKERS = {
    "benchmark_return20": "pit_raw_benchmark_available_20d",
    "benchmark_return60": "pit_raw_benchmark_available_60d",
}

_MASKED_METRIC_COLUMNS = (
    "return20",
    "return60",
    "benchmark_return20",
    "benchmark_return60",
    "net_return20",
    "net_return60",
    "drawdown20",
    "drawdown60",
    "score",
    "setup_score",
    "trigger_score",
    "execution_score",
)
_MODEL_SPLITS = ("train", "validation", "test")

_INSTALLED = False
_ORIGINAL_CACHED: Any = None
_ORIGINAL_VERIFIED: Any = None
_ORIGINAL_RUN: Any = None
_PIT_SPLIT_COUNTS: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar(
    "pit_split_counts",
    default=None,
)


def _status_is_eligible(value: Any) -> bool:
    if value is None or value is pd.NA:
        return False
    return str(value).strip().upper() == "ELIGIBLE"


def _is_finite_number(value: Any) -> bool:
    try:
        return bool(np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _audit_flag(value: Any) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if value is None or value is pd.NA:
        return False
    if isinstance(value, (int, float, np.integer, np.floating)):
        return _is_finite_number(value) and float(value) != 0.0
    return str(value).strip().lower() in {"1", "true", "yes"}


def _add_raw_benchmark_markers(item: dict[str, Any]) -> None:
    """Capture availability only; never retain a masked unverified return."""
    for source, marker in _RAW_BENCHMARK_MARKERS.items():
        if marker in item:
            item[marker] = _audit_flag(item[marker])
        else:
            item[marker] = _is_finite_number(item.get(source))


def _leading_reason(reason_counts: dict[str, int]) -> tuple[str, int] | None:
    if not reason_counts:
        return None
    return min(
        reason_counts.items(),
        key=lambda item: (-item[1], item[0]),
    )


def scrub_samples_for_pit_metrics(
    samples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep raw sample rows while removing unverified metric influence."""
    result: list[dict[str, Any]] = []
    for source in samples:
        item = dict(source)
        _add_raw_benchmark_markers(item)
        eligible = _status_is_eligible(item.get("universe_snapshot_status"))
        item["pit_metric_eligible"] = eligible
        if not eligible:
            item["sample_weight"] = 0.0
            for column in _MASKED_METRIC_COLUMNS:
                if column in item:
                    item[column] = np.nan
        result.append(item)
    return result


def scrub_frame_for_pit_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    """Vectorized equivalent used by process-worker batches."""
    if frame is None or frame.empty:
        return frame
    result = frame.copy()
    for source, marker in _RAW_BENCHMARK_MARKERS.items():
        if marker in result.columns:
            result[marker] = result[marker].map(_audit_flag).astype(bool)
        elif source in result.columns:
            numeric = pd.to_numeric(result[source], errors="coerce")
            result[marker] = numeric.replace([np.inf, -np.inf], np.nan).notna()
        else:
            result[marker] = False
    status = (
        result.get(
            "universe_snapshot_status",
            pd.Series("", index=result.index, dtype=object),
        )
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )
    eligible = status.eq("ELIGIBLE")
    result["pit_metric_eligible"] = eligible
    if "sample_weight" not in result.columns:
        result["sample_weight"] = 1.0
    result.loc[~eligible, "sample_weight"] = 0.0
    columns = [column for column in _MASKED_METRIC_COLUMNS if column in result.columns]
    if columns:
        result.loc[~eligible, columns] = np.nan
    return result


def _split_counts(
    raw: pd.DataFrame,
    verified: pd.DataFrame,
) -> dict[str, dict[str, Any]]:
    counts: dict[str, dict[str, Any]] = {}
    raw_split = (
        raw.get("split", pd.Series("", index=raw.index))
        .fillna("")
        .astype(str)
        .str.lower()
    )
    verified_split = (
        verified.get("split", pd.Series("", index=verified.index))
        .fillna("")
        .astype(str)
        .str.lower()
    )
    for split in _MODEL_SPLITS:
        raw_mask = raw_split.eq(split)
        raw_count = int(raw_mask.sum())
        verified_count = int(verified_split.eq(split).sum())
        reason_counts: dict[str, int] = {}
        if "universe_snapshot_reason" in raw.columns:
            status = raw.get(
                "universe_snapshot_status",
                pd.Series("", index=raw.index, dtype=object),
            )
            unverified_mask = raw_mask & ~status.map(_status_is_eligible)
            reasons = (
                raw.loc[unverified_mask, "universe_snapshot_reason"]
                .fillna("unspecified")
                .astype(str)
                .str.strip()
                .replace("", "unspecified")
                .value_counts()
            )
            reason_counts = {
                str(reason): int(count)
                for reason, count in reasons.items()
            }
        counts[split] = {
            "raw": raw_count,
            "verified": verified_count,
            "unverified": max(0, raw_count - verified_count),
            "raw_benchmark_valid_20d": int(
                raw.loc[raw_mask, "pit_raw_benchmark_available_20d"].sum()
            )
            if "pit_raw_benchmark_available_20d" in raw.columns
            else 0,
            "raw_benchmark_valid_60d": int(
                raw.loc[raw_mask, "pit_raw_benchmark_available_60d"].sum()
            )
            if "pit_raw_benchmark_available_60d" in raw.columns
            else 0,
            "unverified_reasons": reason_counts,
        }
    return counts


def apply_summary_pit_scope(
    summary: Any,
    counts: dict[str, dict[str, Any]],
) -> Any:
    """Align held-out metrics with PIT scope without creating a false fatal."""
    test = counts.get("test", {})
    raw_test = int(test.get("raw", 0) or 0)
    verified_test = int(test.get("verified", 0) or 0)
    unverified_test = int(test.get("unverified", 0) or 0)
    raw_benchmark_20d = min(
        raw_test,
        int(test.get("raw_benchmark_valid_20d", 0) or 0),
    )
    raw_benchmark_60d = min(
        raw_test,
        int(test.get("raw_benchmark_valid_60d", 0) or 0),
    )
    unverified_reasons = {
        str(reason): max(0, int(count or 0))
        for reason, count in dict(test.get("unverified_reasons", {}) or {}).items()
        if int(count or 0) > 0
    }

    metric_available = verified_test >= 2
    if metric_available and unverified_test == 0:
        pit_status = "VERIFIED_ONLY"
    elif metric_available:
        pit_status = "VERIFIED_SUBSET"
    elif raw_test >= 2 and verified_test == 0:
        pit_status = "PIT_WARMUP"
    else:
        pit_status = "INSUFFICIENT_VERIFIED_TEST"

    summary.heldout_metric_scope = PIT_HELDOUT_METRIC_SCOPE
    summary.heldout_raw_test_samples = raw_test
    summary.heldout_verified_test_samples = verified_test
    summary.heldout_unverified_test_samples = unverified_test
    summary.heldout_unverified_reason_counts = unverified_reasons
    summary.heldout_point_in_time_status = pit_status
    summary.heldout_metric_available = metric_available
    summary.heldout_calibration_enabled = metric_available
    summary.heldout_pit_shortage_pipeline_fatal = False
    summary.benchmark_metric_scope = PIT_HELDOUT_METRIC_SCOPE
    summary.benchmark_raw_valid_count_20d = raw_benchmark_20d
    summary.benchmark_raw_valid_count_60d = raw_benchmark_60d
    summary.benchmark_raw_coverage_20d = (
        float(raw_benchmark_20d / raw_test) if raw_test else 0.0
    )
    summary.benchmark_raw_coverage_60d = (
        float(raw_benchmark_60d / raw_test) if raw_test else 0.0
    )

    if metric_available:
        summary.heldout_metric_warning = ""
    else:
        leading_reason = ""
        leading = _leading_reason(unverified_reasons)
        if leading is not None:
            reason, count = leading
            leading_reason = f"; leading exclusion={reason} ({count})"
        summary.heldout_metric_warning = (
            "PIT held-out calibration disabled: "
            f"{verified_test}/{raw_test} test samples are point-in-time verified; "
            "production scoring continues with unverified backtest evidence excluded"
            f"{leading_reason}"
        )

    summary.samples = verified_test

    rolling = getattr(summary, "rolling_oos_stats", None)
    if isinstance(rolling, dict):
        for split, values in counts.items():
            bucket = rolling.setdefault(split, {})
            if not isinstance(bucket, dict):
                bucket = {}
                rolling[split] = bucket
            bucket["point_in_time_verified_samples"] = int(
                values.get("verified", 0) or 0
            )
            bucket["point_in_time_unverified_samples"] = int(
                values.get("unverified", 0) or 0
            )
            bucket["heldout_metric_scope"] = PIT_HELDOUT_METRIC_SCOPE
    return summary


def _rewrite_summary_json(core: Any, summary: Any) -> None:
    serializer = getattr(summary, "to_dict", None)
    if not callable(serializer):
        return
    payload = serializer()
    if not isinstance(payload, dict):
        return
    output_dir = Path(core.OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "BacktestSummary.json"
    temporary = output_dir / ".BacktestSummary.json.v1062.tmp"
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _pit_backtest_chunk_worker(
    tickers: list[str],
) -> tuple[pd.DataFrame, int, list[str], list[tuple[str, str]], int]:
    """Process-safe worker wrapper that scrubs the returned batch."""
    import analytics_core as core

    context = core._BACKTEST_WORKER_CONTEXT
    frames: list[pd.DataFrame] = []
    cache_hits = 0
    cache_hit_tickers: list[str] = []
    errors: list[tuple[str, str]] = []
    for ticker in tickers:
        try:
            ticker_samples, cache_hit = core._backtest_one_ticker_cached(
                ticker,
                context["source"],
                context["benchmark_frame"],
                context["commission"],
                context["stamp_duty"],
                context["slippage"],
                context["split_dates"],
                context["benchmark_signature"],
                profile=context["profile"],
                benchmark_name=context["benchmark"],
            )
            ticker_samples = scrub_samples_for_pit_metrics(ticker_samples)
            if ticker_samples:
                frames.append(pd.DataFrame.from_records(ticker_samples))
            cache_hits += int(cache_hit)
            if cache_hit:
                cache_hit_tickers.append(str(ticker))
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            errors.append((ticker, str(exc)))
    batch = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return (
        scrub_frame_for_pit_metrics(batch),
        cache_hits,
        cache_hit_tickers,
        errors,
        len(tickers),
    )


def install(core: Any) -> None:
    """Install PIT metric scoping before any historical backtest runs."""
    global _INSTALLED, _ORIGINAL_CACHED, _ORIGINAL_VERIFIED, _ORIGINAL_RUN
    if _INSTALLED or getattr(core, "_POINT_IN_TIME_BACKTEST_V1062_INSTALLED", False):
        return

    original_cached = getattr(core, "_backtest_one_ticker_cached", None)
    original_verified = getattr(core, "_verified_point_in_time_frame", None)
    original_run = getattr(core, "run_historical_backtest", None)
    if not all(
        callable(value)
        for value in (original_cached, original_verified, original_run)
    ):
        return

    _ORIGINAL_CACHED = original_cached
    _ORIGINAL_VERIFIED = original_verified
    _ORIGINAL_RUN = original_run

    def pit_cached(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], bool]:
        samples, cache_hit = _ORIGINAL_CACHED(*args, **kwargs)
        return scrub_samples_for_pit_metrics(samples), cache_hit

    def pit_verified(frame: pd.DataFrame) -> pd.DataFrame:
        verified = _ORIGINAL_VERIFIED(frame)
        _PIT_SPLIT_COUNTS.set(_split_counts(frame, verified))
        return verified

    def pit_run(*args: Any, **kwargs: Any) -> Any:
        token = _PIT_SPLIT_COUNTS.set({})
        try:
            summary = _ORIGINAL_RUN(*args, **kwargs)
            counts = _PIT_SPLIT_COUNTS.get() or {}
            apply_summary_pit_scope(summary, counts)
            if not bool(getattr(summary, "heldout_calibration_enabled", False)):
                raw_test = int(
                    getattr(summary, "heldout_raw_test_samples", 0) or 0
                )
                raw_benchmark = int(
                    getattr(summary, "benchmark_raw_valid_count_20d", 0) or 0
                )
                leading = _leading_reason(
                    getattr(summary, "heldout_unverified_reason_counts", {}) or {}
                )
                reason_text = (
                    f"{leading[0]} ({leading[1]})"
                    if leading is not None
                    else "unspecified"
                )
                core.logger.warning(
                    "PIT held-out calibration is in warm-up: verified test samples=%d, "
                    "raw test samples=%d; raw benchmark aligned 20d=%d/%d; "
                    "top unverified reason=%s. Backtest calibration remains disabled; "
                    "DAILY continues with production scoring.",
                    int(getattr(summary, "heldout_verified_test_samples", 0) or 0),
                    raw_test,
                    raw_benchmark,
                    raw_test,
                    reason_text,
                )
            _rewrite_summary_json(core, summary)
            return summary
        finally:
            _PIT_SPLIT_COUNTS.reset(token)

    core._backtest_one_ticker_cached = pit_cached
    core._backtest_chunk_worker = _pit_backtest_chunk_worker
    core._verified_point_in_time_frame = pit_verified
    core.run_historical_backtest = pit_run
    core.POINT_IN_TIME_BACKTEST_VERSION = POINT_IN_TIME_BACKTEST_VERSION
    core.PIT_HELDOUT_METRIC_SCOPE = PIT_HELDOUT_METRIC_SCOPE
    core._POINT_IN_TIME_BACKTEST_V1062_INSTALLED = True
    _INSTALLED = True
