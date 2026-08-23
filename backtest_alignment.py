"""Backtest execution hardening for benchmark time-basis alignment.

Production immediate signals enter on the next session's stock open. Benchmark
returns therefore start from the benchmark open on the same effective entry
session. v96 additionally supports WAIT_PULLBACK conditional fills; benchmark
alignment consumes each sample's actual fill date, so delayed zone-touch fills
receive the same time basis as immediate entries.

The spawn-safe installer also re-asserts the canonical v95/v96 scoring and
execution stack after the v80 FAST engine is installed.  Parent and workers
therefore share 504-bar scoring, nominal setup scales, smooth TriggerScore and
conditional-fill semantics.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_EXECUTION_MODEL = (
    "asset_fees_liquidity_immediate_open_waitpullback_zone5_benchmark_open_v3"
)


def _normalized_index(frame: pd.DataFrame) -> pd.DatetimeIndex:
    index = pd.DatetimeIndex(pd.to_datetime(frame.index, errors="coerce"))
    if index.tz is not None:
        index = index.tz_localize(None)
    return index.normalize()


def _price_on_date(
    frame: pd.DataFrame,
    date_value: Any,
    column: str,
) -> float:
    if frame is None or frame.empty or column not in frame.columns:
        return np.nan
    target = pd.to_datetime(date_value, errors="coerce")
    if pd.isna(target):
        return np.nan
    target = pd.Timestamp(target)
    if target.tzinfo is not None:
        target = target.tz_localize(None)
    target = target.normalize()
    index = _normalized_index(frame)
    positions = np.flatnonzero(index == target)
    if not len(positions):
        return np.nan
    value = pd.to_numeric(
        pd.Series([frame.iloc[int(positions[-1])][column]]), errors="coerce"
    ).iloc[0]
    return float(value) if np.isfinite(value) and float(value) > 0 else np.nan


def align_benchmark_returns(
    samples: list[dict[str, Any]],
    benchmark_frame: pd.DataFrame | None,
) -> list[dict[str, Any]]:
    """Recompute benchmark legs from effective-entry OPEN to exit CLOSE."""
    if not samples:
        return samples
    aligned: list[dict[str, Any]] = []
    for source in samples:
        item = dict(source)
        entry_open = _price_on_date(benchmark_frame, item.get("entry_date"), "Open")
        item["benchmark_entry_basis"] = "OPEN"
        item["benchmark_entry_price"] = entry_open
        valid_entry = np.isfinite(entry_open) and entry_open > 0
        statuses: list[str] = []
        for horizon in (20, 60):
            exit_close = _price_on_date(
                benchmark_frame,
                item.get(f"exit{horizon}_date"),
                "Close",
            )
            if valid_entry and np.isfinite(exit_close) and exit_close > 0:
                item[f"benchmark_return{horizon}"] = (
                    float(exit_close / entry_open - 1.0) * 100.0
                )
                statuses.append("ALIGNED")
            else:
                item[f"benchmark_return{horizon}"] = np.nan
                statuses.append("MISSING")
        item["benchmark_alignment_status"] = (
            "ALIGNED"
            if all(status == "ALIGNED" for status in statuses)
            else "INCOMPLETE"
        )
        aligned.append(item)
    return aligned


def _benchmark_arg(args: tuple[Any, ...], kwargs: dict[str, Any]) -> pd.DataFrame | None:
    if "benchmark_frame" in kwargs:
        value = kwargs.get("benchmark_frame")
        return value if isinstance(value, pd.DataFrame) else None
    if len(args) >= 3 and isinstance(args[2], pd.DataFrame):
        return args[2]
    return None


def aligned_backtest_worker_initializer(
    source: str,
    benchmark: str,
    commission: float,
    stamp_duty: float,
    slippage: float,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None],
    benchmark_signature: str,
    profile: Any,
) -> None:
    """Spawn-safe ProcessPool initializer that installs current math in workers."""
    import analytics as analytics_module

    install_analytics_alignment(analytics_module)
    benchmark_frame = analytics_module._load_cache(
        analytics_module.BENCHMARKS[benchmark], source
    )
    analytics_module._BACKTEST_WORKER_CONTEXT = {
        "source": source,
        "benchmark": benchmark,
        "benchmark_frame": benchmark_frame,
        "commission": commission,
        "stamp_duty": stamp_duty,
        "slippage": slippage,
        "split_dates": split_dates,
        "benchmark_signature": benchmark_signature,
        "profile": profile,
    }


def _persist_summary(module: Any, summary: Any) -> None:
    path = Path(module.OUTPUT_DIR) / "BacktestSummary.json"
    temporary = path.with_name(".BacktestSummary.v51.tmp")
    try:
        temporary.write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    except (OSError, TypeError, ValueError):
        module.logger.debug("Unable to persist backtest provenance", exc_info=True)
    finally:
        temporary.unlink(missing_ok=True)


def _install_scoring_execution_stack() -> None:
    """Install v80 acceleration followed by canonical v95/v96 semantics."""
    try:
        import backtest_profile_alignment_v95 as profile_v95

        profile_v95.install()
    except ImportError:
        pass

    try:
        import backtest_fastscore_v80 as fastscore_v80
    except ImportError:
        fastscore_v80 = None
    if fastscore_v80 is not None:
        fastscore_v80.install()

    try:
        import scoring_consistency_v94 as scoring_v95
    except ImportError:
        scoring_v95 = None
    if scoring_v95 is not None:
        scoring_v95.install()

    # Conditional fill must wrap the already aligned scalar executor so both
    # direct and cached paths use the actual fill date for benchmark alignment.
    try:
        import conditional_fill_v96 as conditional_v96
    except ImportError:
        conditional_v96 = None
    if conditional_v96 is not None:
        conditional_v96.install()


def install_analytics_alignment(module: Any) -> None:
    """Install benchmark-open alignment exactly once on an analytics module."""
    if bool(getattr(module, "_V51_BENCHMARK_ALIGNMENT_INSTALLED", False)):
        _install_scoring_execution_stack()
        return

    original_one = module._backtest_one_ticker
    original_cached = module._backtest_one_ticker_cached
    original_run = module.run_historical_backtest

    def aligned_one(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        samples = original_one(*args, **kwargs)
        return align_benchmark_returns(samples, _benchmark_arg(args, kwargs))

    def aligned_cached(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], bool]:
        samples, cache_hit = original_cached(*args, **kwargs)
        return align_benchmark_returns(samples, _benchmark_arg(args, kwargs)), cache_hit

    def aligned_run(*args: Any, **kwargs: Any) -> Any:
        summary = original_run(*args, **kwargs)
        try:
            summary.execution_model = _EXECUTION_MODEL
            summary.target_definition = str(summary.target_definition).replace(
                "相对基准",
                "相对同日开盘基准",
            )
        except (AttributeError, TypeError):
            return summary
        _persist_summary(module, summary)
        return summary

    module._backtest_one_ticker = aligned_one
    module._backtest_one_ticker_cached = aligned_cached
    module._init_backtest_worker = aligned_backtest_worker_initializer
    module.run_historical_backtest = aligned_run
    module.BACKTEST_BENCHMARK_ENTRY_BASIS = "OPEN"
    module.BACKTEST_EXECUTION_MODEL = _EXECUTION_MODEL
    module._V51_BENCHMARK_ALIGNMENT_INSTALLED = True

    _install_scoring_execution_stack()
