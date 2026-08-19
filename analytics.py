"""v63 analytics facade with fresh benchmarks and cache-safe calibration.

The historical analytics implementation lives in ``analytics_core``. This
public boundary keeps the v51 T+1-open alignment contract, v56 benchmark
refresh/evidence provenance and v57 unstable-calibration governance. v63 adds a
fail-closed cache rule: if a ticker backtest cache was built with benchmark
state but the current benchmark frame is completely unavailable, old excess
return samples are not silently reused.
"""

from __future__ import annotations

import sys

import pandas as pd

import analytics_core as _core
from analytics_core import *  # noqa: F403
from backtest_alignment import install_analytics_alignment

install_analytics_alignment(_core)

_LEGACY_APPLY_BACKTEST_PROVENANCE = _core._apply_backtest_provenance
_LEGACY_CALIBRATION_STABILITY_STATS = _core.calibration_stability_stats
_LEGACY_BACKTEST_ONE_TICKER_CACHED = _core._backtest_one_ticker_cached


def _load_benchmark_frames(source: str) -> dict[str, pd.DataFrame]:
    """Refresh each benchmark first; use cache only as a resilient fallback."""
    frames: dict[str, pd.DataFrame] = {}
    for name, ticker in _core.BENCHMARKS.items():
        frame: pd.DataFrame | None = None
        try:
            frame = _core.download_ticker(ticker, source=source)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            _core.logger.warning(
                "刷新基准 %s (%s) 失败，尝试使用现有缓存: %s",
                name,
                ticker,
                exc,
            )

        if frame is None or frame.empty:
            frame = _core._load_cache(ticker, source)
            if frame is not None and not frame.empty:
                _core.logger.warning(
                    "基准 %s (%s) 使用缓存回退，回测时效字段将继续审计其截止日。",
                    name,
                    ticker,
                )

        if frame is not None and not frame.empty:
            frames[name] = frame
        else:
            _core.logger.warning("无法加载基准 %s (%s)", name, ticker)
    return frames


def _backtest_one_ticker_cached(
    ticker: str,
    source: str,
    benchmark_frame: pd.DataFrame | None,
    commission: float,
    stamp_duty: float,
    slippage: float,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None],
    benchmark_signature: str = "",
    *,
    profile: BacktestExecutionProfile | None = None,
    benchmark_name: str = "沪深300",
) -> tuple[list[dict[str, object]], bool]:
    """Never reuse excess-return cache when the benchmark is absent now."""
    if benchmark_frame is not None and not benchmark_frame.empty:
        return _LEGACY_BACKTEST_ONE_TICKER_CACHED(
            ticker,
            source,
            benchmark_frame,
            commission,
            stamp_duty,
            slippage,
            split_dates,
            benchmark_signature,
            profile=profile,
            benchmark_name=benchmark_name,
        )

    frame = _core._load_cache(ticker, source)
    active_profile = profile or _core._resolve_backtest_profile("exact", 1)
    _core.logger.warning(
        "回测基准 %s 当前不可用：%s 不复用历史 benchmark cache，"
        "本轮样本的基准收益将保持缺失并由测试集完整性门槛处理。",
        benchmark_name,
        ticker,
    )
    samples = _core._backtest_one_ticker(
        ticker,
        source,
        None,
        commission,
        stamp_duty,
        slippage,
        split_dates,
        profile=active_profile,
        frame=frame,
    )
    return samples, False


def _apply_backtest_provenance(
    frame: pd.DataFrame,
    summary: BacktestSummary,
    observed: pd.Series,
) -> pd.DataFrame:
    """Clarify that ticker evidence and peer calibration are separate channels."""
    result = _LEGACY_APPLY_BACKTEST_PROVENANCE(frame, summary, observed)
    if "BacktestSkipReason" not in result.columns:
        return result

    reason = result["BacktestSkipReason"].fillna("").astype(str)
    ambiguous = reason.eq("历史样本不足，不参与排名")
    if ambiguous.any():
        result.loc[ambiguous, "BacktestSkipReason"] = (
            "本票历史样本不足，不使用本票回测校准；同类全局校准可独立参与综合分"
        )
    return result


def calibration_stability_stats(
    rows: list[dict[str, object]] | None,
    *,
    minimum_folds: int = 3,
) -> dict[str, object]:
    """Govern peer-calibration confidence using observed walk-forward stability."""
    governed = dict(
        _LEGACY_CALIBRATION_STABILITY_STATS(
            rows,
            minimum_folds=minimum_folds,
        )
    )
    status = str(governed.get("status", "") or "").strip().upper()
    try:
        raw_multiplier = float(governed.get("confidence_multiplier", 1.0) or 0.0)
    except (TypeError, ValueError):
        raw_multiplier = 0.0
    try:
        stable_ratio = float(governed.get("stable_fold_ratio", raw_multiplier) or 0.0)
    except (TypeError, ValueError):
        stable_ratio = raw_multiplier
    raw_multiplier = max(0.0, min(1.0, raw_multiplier))
    stable_ratio = max(0.0, min(1.0, stable_ratio))

    governed["raw_confidence_multiplier"] = round(raw_multiplier, 4)
    if status == "UNSTABLE":
        governed["confidence_multiplier"] = round(
            raw_multiplier * stable_ratio,
            4,
        )
        governed["confidence_governance"] = "unstable-stable-ratio-shrink-v1"
    else:
        governed["confidence_multiplier"] = round(raw_multiplier, 4)
        governed["confidence_governance"] = "legacy-v1"
    return governed


_core._load_benchmark_frames = _load_benchmark_frames
_core._backtest_one_ticker_cached = _backtest_one_ticker_cached
_core._apply_backtest_provenance = _apply_backtest_provenance
_core.calibration_stability_stats = calibration_stability_stats

sys.modules[__name__] = _core
