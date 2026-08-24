"""Point-in-time scope enforcement for held-out backtest diagnostics.

The historical backtest core already tags every sample with
``universe_snapshot_status``. Production calibration consumes only ELIGIBLE
rows, but legacy top-level held-out statistics were calculated from the full
``test`` partition. That allowed UNAVAILABLE universe observations to remain
visible in diagnostic RankIC, bucket monotonicity and return summaries even
though v106 correctly prevented them from activating peer calibration.

This compatibility layer makes those legacy diagnostics point-in-time clean
without changing the price path, signal generation, 60/15/25 production
weights, ranking thresholds or TradeReady policy:
- UNAVAILABLE samples remain in the raw audit set so coverage loss is visible;
- their numeric outcomes and score are masked and their sample weight is zero;
- therefore held-out estimators, RankIC, drawdown and score buckets use only
  ELIGIBLE point-in-time observations;
- BacktestSummary exposes raw/verified/unverified test counts explicitly and
  its canonical ``samples`` count matches the verified held-out metric scope;
- insufficient PIT coverage disables calibration but does not fail DAILY.

The last rule matters for prospective snapshot archives. A newly-created PIT
archive cannot immediately have mature 60-trading-day outcomes, so zero
verified held-out samples is a normal warm-up state rather than a pipeline
failure. Genuine raw-test failures detected by analytics_core remain fatal and
are never cleared here.
"""
from __future__ import annotations

import json
import os
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

POINT_IN_TIME_BACKTEST_VERSION = (
    "2026-08-24-v106.2-pit-warmup-nonfatal-v1"
)
PIT_HELDOUT_METRIC_SCOPE = "POINT_IN_TIME_ELIGIBLE_SAMPLES_ONLY"

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
_PIT_SPLIT_COUNTS: ContextVar[dict[str, dict[str, int]]] = ContextVar(
    "pit_split_counts",
    default={},
)


def _status_is_eligible(value: Any) -> bool:
    return str(value or "").strip().upper() == "ELIGIBLE"


def scrub_samples_for_pit_metrics(
    samples: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep raw sample rows while removing unverified metric influence."""
    result: list[dict[str, Any]] = []
    for source in samples:
        item = dict(source)
        eligible = _status_is_eligible(
            item.get("universe_snapshot_status")
        )
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
    columns = [
        column
        for column in _MASKED_METRIC_COLUMNS
        if column in result.columns
    ]
    if columns:
        result.loc[~eligible, columns] = np.nan
    return result


def _split_counts(
    raw: pd.DataFrame,
    verified: pd.DataFrame,
) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    raw_split = (
        raw.get("split", pd.Series("", index=raw.index))
        .fillna("")
        .astype(str)
        .str.lower()
    )
    verified_split = (
        verified.get(
            "split",
            pd.Series("", index=verified.index),
        )
        .fillna("")
        .astype(str)
        .str.lower()
    )
    for split in _MODEL_SPLITS:
        raw_count = int(raw_split.eq(split).sum())
        verified_count = int(verified_split.eq(split).sum())
        counts[split] = {
            "raw": raw_count,
            "verified": verified_count,
            "unverified": max(0, raw_count - verified_count),
        }
    return counts


def apply_summary_pit_scope(
    summary: Any,
    counts: dict[str, dict[str, int]],
) -> Any:
    """Align held-out metrics with PIT scope without creating a false fatal.

    ``analytics_core`` owns command-fatal test-data validation. This function
    must never turn a healthy raw backtest into exit code 2 merely because a
    prospective PIT archive has not accumulated mature outcomes yet.
    """
    test = counts.get("test", {})
    raw_test = int(test.get("raw", 0) or 0)
    verified_test = int(test.get("verified", 0) or 0)
    unverified_test = int(test.get("unverified", 0) or 0)

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
    summary.heldout_point_in_time_status = pit_status
    summary.heldout_metric_available = metric_available
    summary.heldout_calibration_enabled = metric_available
    summary.heldout_pit_shortage_pipeline_fatal = False

    if metric_available:
        summary.heldout_metric_warning = ""
    else:
        summary.heldout_metric_warning = (
            "PIT held-out calibration disabled: "
            f"{verified_test}/{raw_test} test samples are point-in-time verified; "
            "production scoring continues with unverified backtest evidence excluded"
        )

    # ``samples`` is the count associated with the top-level held-out metrics.
    # Raw partition counts remain available in rolling_oos and the explicit
    # heldout_raw_test_samples field above.
    summary.samples = verified_test

    # Do NOT set or clear summary.insufficient_test_data/error here. If the core
    # already found a genuine raw-test failure it remains fatal. A PIT-only
    # shortage is a calibration warm-up state and must not make cmd_backtest
    # return 2.

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
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


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
            ticker_samples = scrub_samples_for_pit_metrics(
                ticker_samples
            )
            if ticker_samples:
                frames.append(
                    pd.DataFrame.from_records(ticker_samples)
                )
            cache_hits += int(cache_hit)
            if cache_hit:
                cache_hit_tickers.append(str(ticker))
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            errors.append((ticker, str(exc)))
    batch = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame()
    )
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
    if _INSTALLED or getattr(
        core,
        "_POINT_IN_TIME_BACKTEST_V1062_INSTALLED",
        False,
    ):
        return

    original_cached = getattr(
        core,
        "_backtest_one_ticker_cached",
        None,
    )
    original_verified = getattr(
        core,
        "_verified_point_in_time_frame",
        None,
    )
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
            counts = _PIT_SPLIT_COUNTS.get()
            apply_summary_pit_scope(summary, counts)
            if not bool(getattr(summary, "heldout_calibration_enabled", False)):
                core.logger.warning(
                    "PIT held-out calibration is in warm-up: verified test samples=%d, "
                    "raw test samples=%d. Backtest calibration remains disabled; "
                    "DAILY continues with production scoring.",
                    int(getattr(summary, "heldout_verified_test_samples", 0) or 0),
                    int(getattr(summary, "heldout_raw_test_samples", 0) or 0),
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
