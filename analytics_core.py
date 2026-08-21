from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from calibration_bridge import bridge_global_calibration
from classification import etf_tracking_key, model_classification, theme_cluster
from config import (
    BACKTEST_ASSUMED_TRADE_NOTIONAL,
    BACKTEST_AUTO_EXACT_MAX_TICKERS,
    BACKTEST_AUTO_EXACT_REFINEMENT,
    BACKTEST_CACHE_ENABLED,
    BACKTEST_CHUNK_SIZE,
    BACKTEST_ETF_COMMISSION_RATE,
    BACKTEST_EXACT_REFINEMENT_CANDIDATES,
    BACKTEST_FAST_CANDIDATE_GAP_DAYS,
    BACKTEST_FAST_CHUNK_SIZE,
    BACKTEST_FAST_COOLDOWN_DAYS,
    BACKTEST_FAST_SCORE_WINDOW_BARS,
    BACKTEST_FRESHNESS_DELAYED_TRADING_DAYS,
    BACKTEST_FRESHNESS_STALE_TRADING_DAYS,
    BACKTEST_FULL_WEIGHT_SAMPLES,
    BACKTEST_INCREMENTAL_TAIL_BARS,
    BACKTEST_LOW_CONFIDENCE_MAX_SAMPLES,
    BACKTEST_MAX_EXIT_DELAY_DAYS,
    BACKTEST_MAX_PROCESSES,
    BACKTEST_MIN_SAMPLES_FOR_RANKING,
    BACKTEST_NEUTRAL_SCORE,
    BACKTEST_NORMAL_WEIGHT,
    BACKTEST_OUTCOME_HORIZON_DAYS,
    BACKTEST_PROCESS_MIN_TICKERS,
    BACKTEST_PROGRESS_INTERVAL,
    BACKTEST_SCORE_WINDOW_BARS,
    BACKTEST_SIGNAL_COOLDOWN_DAYS,
    BACKTEST_STOCK_COMMISSION_RATE,
    ENABLE_VOLUME_PROFILE,
    GLOBAL_CALIBRATION_MAX_WEIGHT,
    GLOBAL_CALIBRATION_MIN_SAMPLES,
    INDICATOR_CACHE_ENABLED,
    INSTITUTIONAL_TIER_TRAP_LABEL,
    INSTITUTIONAL_TIER_WAIT_LABEL,
    MODEL_QUALITY_WEIGHT,
    OUTPUT_DIR,
    QUALITY_MULTIPLIER_FAIL,
    QUALITY_MULTIPLIER_PASS,
    QUALITY_MULTIPLIER_UNKNOWN,
    SCAN_THREADS,
    SECTOR_CONFIRMATION_INDUSTRY_WEIGHT,
    SECTOR_CONFIRMATION_MIN_FACTOR,
    SECTOR_CONFIRMATION_RELATIVE_WEIGHT,
    TICKFLOW_ADJUST,
)
from downloader import (
    _cache_path,
    _load_cache,
    download_ticker,
    is_etf_ticker,
)
from execution_costs import BrokerFeeSchedule, round_trip_cost_percent
from historical_universe import historical_universe_status, point_in_time_eligibility
from indicators import compute_all_indicators, compute_volume_profile
from model_calibration import (
    build_global_calibration,
    calibrate_component_weights,
    calibration_details_for_frame,
    calibration_stability_stats,
    walk_forward_stats,
)
from performance_cache import (
    backtest_cache_key,
    load_backtest_cache_state,
    load_or_compute_indicators,
    market_cache_state,
    market_prefix_matches,
    save_backtest_cache,
)
from result_contract import candidate_generation_stage
from score import (
    breakout_score,
    entry_point,
    model_weight_signature,
    score_ticker,
    tradable_price_decimals,
    value_trap_risk,
)
from signal_lifecycle import finalize_signal_ranking
from tradeability import is_entry_tradeable, resolve_exit_index
from trading_calendar import is_trading_day, trading_age_days

logger = logging.getLogger("institution_scanner.analytics")

BENCHMARKS = {
    "沪深300": "000300.SH",
    "中证500": "000905.SH",
    "创业板指": "399006.SZ",
}
BACKTEST_VALIDATION_END: str | None = None
BACKTEST_TEST_START: str | None = None
_BACKTEST_ACTIONABLE_SIGNALS = frozenset(
    {"BUY_NOW", "BREAKOUT_CONFIRM", "WAIT_PULLBACK"}
)


@dataclass
class BacktestSummary:
    samples: int = 0
    ticker_count: int = 0
    cache_hits: int = 0
    cache_hit_tickers: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    worker_count: int = 0
    engine: str = "sequential"
    mode: str = "auto"
    objective: str = "net_excess_return_20d"
    target_definition: str = "扣除交易成本后相对基准的20个交易日超额收益率"
    benchmark: str = "沪深300"
    insufficient_test_data: bool = False
    error: str | None = None
    benchmark_valid_count: int = 0
    benchmark_coverage: float = 0.0
    benchmark_valid_count_20d: int = 0
    benchmark_valid_count_60d: int = 0
    benchmark_coverage_20d: float = 0.0
    benchmark_coverage_60d: float = 0.0
    universe_type: str = "current_survivor_pool"
    survivorship_bias_warning: bool = True
    current_pool_selection_warning: str = "回测使用当前股票池，存在幸存者偏差"
    point_in_time_universe: dict[str, Any] = field(default_factory=dict)
    split_dates: dict[str, str | None] = field(default_factory=dict)
    all_samples: int = 0
    purged_samples: int = 0
    commission: float = BACKTEST_STOCK_COMMISSION_RATE
    stamp_duty: float = 0.0005
    slippage: float = 0.001
    cost_parameters: dict[str, float] = field(default_factory=dict)
    execution_model: str = "asset_fees_liquidity_t1_limit_exit_v1"
    etf_commission: float = BACKTEST_ETF_COMMISSION_RATE
    assumed_trade_notional: float = BACKTEST_ASSUMED_TRADE_NOTIONAL
    test_ratio: float = 0.2
    validation_ratio: float = 0.2
    test_fallback: bool = False
    rolling_oos: dict[str, Any] = field(default_factory=dict)
    rolling_oos_stats: dict[str, Any] = field(default_factory=dict)
    objective_value: float = 0.0
    rank_ic: dict[str, float] = field(default_factory=dict)
    monotonicity_high_low: dict[str, float] = field(default_factory=dict)
    win_rate_20d: float = 0.0
    win_rate_60d: float = 0.0
    average_return_20d: float = 0.0
    average_return_60d: float = 0.0
    median_return_20d: float = 0.0
    median_return_60d: float = 0.0
    average_benchmark_return_20d: float = 0.0
    average_benchmark_return_60d: float = 0.0
    average_net_return_20d: float = 0.0
    average_net_return_60d: float = 0.0
    average_net_excess_return_20d: float = 0.0
    average_net_excess_return_60d: float = 0.0
    median_net_excess_return_20d: float = 0.0
    median_net_excess_return_60d: float = 0.0
    maximum_drawdown_20d: float = 0.0
    maximum_drawdown_60d: float = 0.0
    rank_ic_20d: float = 0.0
    rank_ic_60d: float = 0.0
    monotonicity_high_low_20d: float = 0.0
    monotonicity_high_low_60d: float = 0.0
    by_score_bucket: list[dict[str, Any]] = field(default_factory=list)
    by_ticker: list[dict[str, Any]] = field(default_factory=list)
    global_calibration: list[dict[str, Any]] = field(default_factory=list)
    walk_forward: list[dict[str, Any]] = field(default_factory=list)
    calibration_stability: dict[str, Any] = field(default_factory=dict)
    component_calibration: dict[str, Any] = field(default_factory=dict)
    fast_exact_bridge: dict[str, Any] = field(default_factory=dict)
    # v29 run-level provenance / observability.  requested_tickers is populated
    # by the CLI before ranking so manual subset backtests never mark unrelated
    # AllResults rows as if they had been evaluated.
    requested_tickers: list[str] = field(default_factory=list)
    fast_screen_ticker_count: int = 0
    exact_refinement_count: int = 0
    exact_refinement_tickers: list[str] = field(default_factory=list)
    exact_refinement_elapsed_seconds: float = 0.0
    exact_worker_count: int = 0
    total_ticker_evaluations: int = 0
    signal_sample_ticker_count: int = 0
    no_signal_ticker_count: int = 0
    ranking_eligible_ticker_count: int = 0
    cache_hit_rate: float = 0.0
    calibration_lookup_elapsed_seconds: float = 0.0
    ranking_compute_elapsed_seconds: float = 0.0
    persistence_elapsed_seconds: float = 0.0
    postprocess_elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        result = dict(self.__dict__)
        for key, value in result.items():
            if isinstance(value, float):
                result[key] = round(value, 4)
        return result


def _safe_return(series: pd.Series, periods: int) -> float:
    clean = (
        pd.to_numeric(series, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )
    if len(clean) <= periods:
        return np.nan
    start = float(clean.iloc[-periods - 1])
    end = float(clean.iloc[-1])
    return (end / start - 1.0) * 100 if start > 0 else np.nan


def _bounded_score(value: float, low: float, high: float) -> float:
    if not np.isfinite(value) or high <= low:
        return 0.5
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))


def _sector_confirmation_factor(peer_return: float, relative_strength: float) -> float:
    if not np.isfinite(peer_return):
        return 1.0
    industry_component = _bounded_score(peer_return, -20.0, 20.0)
    relative_component = (
        _bounded_score(relative_strength, -15.0, 15.0)
        if np.isfinite(relative_strength)
        else 0.5
    )
    total_weight = max(
        float(SECTOR_CONFIRMATION_INDUSTRY_WEIGHT + SECTOR_CONFIRMATION_RELATIVE_WEIGHT),
        1e-9,
    )
    combined = (
        industry_component * float(SECTOR_CONFIRMATION_INDUSTRY_WEIGHT)
        + relative_component * float(SECTOR_CONFIRMATION_RELATIVE_WEIGHT)
    ) / total_weight
    floor = float(np.clip(SECTOR_CONFIRMATION_MIN_FACTOR, 0.0, 1.0))
    return round(float(np.clip(floor + (1.0 - floor) * combined, floor, 1.0)), 4)


def _finite_float(value: Any, default: float = np.nan) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if np.isfinite(parsed) else default


def _quality_adjusted_score(
    score: float,
    quality_score: Any,
    quality_available: bool,
    is_etf: bool,
) -> float:
    if not np.isfinite(score):
        return np.nan
    quality = _finite_float(quality_score)
    if quality_available and not is_etf and np.isfinite(quality):
        quality_weight = float(np.clip(MODEL_QUALITY_WEIGHT, 0.0, 0.5))
        return float(score * (1.0 - quality_weight) + quality * quality_weight)
    return float(score)


def _robust_mean(values: pd.Series) -> float:
    numeric = (
        pd.to_numeric(values, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )
    if numeric.empty:
        return float("nan")
    if len(numeric) < 5:
        return float(numeric.median())
    lower, upper = numeric.quantile([0.1, 0.9])
    return float(numeric.clip(lower, upper).mean())


def _weighted_arrays(
    values: pd.Series,
    weights: pd.Series,
) -> tuple[np.ndarray, np.ndarray]:
    numeric = pd.to_numeric(values, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    weight = pd.to_numeric(weights, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    valid = numeric.notna() & weight.notna() & weight.gt(0.0)
    return (
        numeric.loc[valid].to_numpy(dtype=np.float64),
        weight.loc[valid].to_numpy(dtype=np.float64),
    )


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    numeric, weight = _weighted_arrays(values, weights)
    total = float(weight.sum())
    return float(np.dot(numeric, weight) / total) if total > 0.0 else float("nan")


def _weighted_rate(values: pd.Series, weights: pd.Series) -> float:
    numeric, weight = _weighted_arrays(values, weights)
    total = float(weight.sum())
    if total <= 0.0:
        return float("nan")
    return float(np.dot((numeric > 0.0).astype(np.float64), weight) / total)


def _weighted_robust_mean(values: pd.Series, weights: pd.Series) -> float:
    """Winsorized weighted mean used for dependent backtest observations."""
    numeric, weight = _weighted_arrays(values, weights)
    total = float(weight.sum())
    if total <= 0.0:
        return float("nan")
    if numeric.size < 5:
        return float(np.dot(numeric, weight) / total)
    order = np.argsort(numeric, kind="mergesort")
    ordered_values = numeric[order]
    ordered_weights = weight[order]
    mid_cdf = (np.cumsum(ordered_weights) - ordered_weights * 0.5) / total
    lower = float(np.interp(0.10, mid_cdf, ordered_values))
    upper = float(np.interp(0.90, mid_cdf, ordered_values))
    clipped = np.clip(numeric, lower, upper)
    return float(np.dot(clipped, weight) / total)


def _weighted_std(values: pd.Series, weights: pd.Series) -> float:
    numeric, weight = _weighted_arrays(values, weights)
    total = float(weight.sum())
    if total <= 0.0:
        return float("nan")
    mean = float(np.dot(numeric, weight) / total)
    variance = float(np.dot((numeric - mean) ** 2, weight) / total)
    return float(np.sqrt(max(variance, 0.0)))


def _weighted_profit_factor(values: pd.Series, weights: pd.Series) -> float:
    numeric, weight = _weighted_arrays(values, weights)
    positive = numeric > 0.0
    negative = numeric < 0.0
    profit = float(np.dot(numeric[positive], weight[positive]))
    loss = float(np.dot(-numeric[negative], weight[negative]))
    if loss > 0.0:
        return float(profit / loss)
    if profit > 0.0:
        return float("inf")
    return float("nan")


def _load_benchmark_frames(source: str) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for name, ticker in BENCHMARKS.items():
        frame = _load_cache(ticker, source)
        if frame is None or frame.empty:
            try:
                frame = download_ticker(ticker, source=source)
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("无法加载基准 %s: %s", name, exc)
                frame = None
        if frame is not None and not frame.empty:
            frames[name] = frame
    return frames


def _benchmark_regime(frames: dict[str, pd.DataFrame]) -> tuple[str, str]:
    states: list[bool] = []
    returns: list[float] = []
    for frame in frames.values():
        enriched = compute_all_indicators(frame.copy())
        if len(enriched) < 60:
            continue
        close = float(enriched["Close"].iloc[-1])
        ma60 = float(enriched["Close"].rolling(60, min_periods=30).mean().iloc[-1])
        ma200 = (
            float(enriched["MA200"].iloc[-1])
            if "MA200" in enriched
            else np.nan
        )
        long_average = ma200 if np.isfinite(ma200) else ma60
        states.append(bool(close >= ma60 and close >= long_average))
        value = _safe_return(enriched["Close"], 60)
        if np.isfinite(value):
            returns.append(value)
    if not states:
        return "未知", "基准数据不足"
    average_return = float(np.mean(returns)) if returns else 0.0
    required_positive = max(1, (len(states) * 2 + 2) // 3)
    if sum(states) >= required_positive and average_return > 3:
        return "风险偏好", f"基准60日平均收益 {average_return:.1f}%"
    if sum(states) == 0 and average_return < -3:
        return "风险规避", f"基准60日平均收益 {average_return:.1f}%"
    return "震荡", f"基准60日平均收益 {average_return:.1f}%"


def _benchmark_regime_components(
    frames: dict[str, pd.DataFrame], slow_regime: str, slow_reason: str
) -> tuple[str, str, str, float, str]:
    fast_states: list[bool] = []
    fast_returns: list[float] = []
    for frame in frames.values():
        enriched = compute_all_indicators(frame.copy())
        if len(enriched) < 12 or "Close" not in enriched:
            continue
        close = _finite_float(enriched["Close"].iloc[-1])
        ma10 = _finite_float(
            enriched["Close"].rolling(10, min_periods=5).mean().iloc[-1]
        )
        ret10 = _safe_return(enriched["Close"], 10)
        if np.isfinite(close) and np.isfinite(ma10):
            fast_states.append(
                bool(close >= ma10 and (not np.isfinite(ret10) or ret10 >= 0))
            )
        if np.isfinite(ret10):
            fast_returns.append(ret10)
    if not fast_states:
        return slow_regime, slow_regime, slow_regime, 0.0, slow_reason
    average_return = float(np.mean(fast_returns)) if fast_returns else 0.0
    positive = sum(fast_states)
    if (
        positive >= max(1, (len(fast_states) * 2 + 2) // 3)
        and average_return > 0.8
    ):
        fast = "风险偏好"
    elif positive == 0 and average_return < -0.8:
        fast = "风险规避"
    else:
        fast = "震荡"
    if fast == slow_regime:
        combined = fast
    elif fast == "风险偏好" and slow_regime == "风险规避":
        combined = "震荡修复"
    elif fast == "风险规避" and slow_regime == "风险偏好":
        combined = "震荡转弱"
    else:
        combined = "震荡"
    confidence = min(
        1.0,
        abs(positive / len(fast_states) - 0.5) * 2.0 * 0.6
        + (0.4 if fast == slow_regime else 0.15),
    )
    reason = f"快线10日均收 {average_return:.1f}%；慢线：{slow_reason}"
    return fast, slow_regime, combined, round(float(confidence), 4), reason


def _breakout_quality_factor(frame: pd.DataFrame) -> float:
    if len(frame) < 21 or not {"Close", "High", "Low", "Volume"}.issubset(
        frame.columns
    ):
        return 1.0
    recent = frame.iloc[-1]
    close = float(recent["Close"])
    high = float(recent["High"])
    low = float(recent["Low"])
    volume = float(recent["Volume"])
    prior_high = float(frame["High"].iloc[-21:-1].max())
    volume_average = float(frame["Volume"].iloc[-21:-1].mean())
    if (
        not all(
            np.isfinite(value)
            for value in (close, high, low, volume, prior_high, volume_average)
        )
        or high <= low
        or volume_average <= 0
    ):
        return 1.0
    platform_breakout = float(close >= prior_high)
    volume_confirmation = float(
        np.clip(volume / volume_average / 1.5, 0.0, 1.0)
    )
    close_position = float(np.clip((close - low) / (high - low), 0.0, 1.0))
    return round(
        float(
            np.clip(
                platform_breakout * 0.45
                + volume_confirmation * 0.35
                + close_position * 0.20,
                0.0,
                1.0,
            )
        ),
        4,
    )


def _stage_label(df: pd.DataFrame, phase: str) -> str:
    if len(df) < 60:
        return "数据不足"
    close = float(df["Close"].iloc[-1])
    ma20 = (
        float(df["MA20"].iloc[-1])
        if "MA20" in df and pd.notna(df["MA20"].iloc[-1])
        else np.nan
    )
    ma50 = (
        float(df["MA50"].iloc[-1])
        if "MA50" in df and pd.notna(df["MA50"].iloc[-1])
        else np.nan
    )
    rsi = (
        float(df["RSI14"].iloc[-1])
        if "RSI14" in df and pd.notna(df["RSI14"].iloc[-1])
        else np.nan
    )
    return20 = _safe_return(df["Close"], 20)
    if not all(np.isfinite(value) for value in (close, ma20, ma50, rsi, return20)):
        return "数据不足"
    if close > ma50 and return20 >= 8 and rsi >= 60:
        return "已经启动"
    if close > ma20 and close > ma50:
        return "趋势确认"
    if phase in {"Accumulation", "Reaccumulation"} and close <= ma50 and rsi < 65:
        return "正在吸筹"
    return "观察"


def _enrich_one_result(
    result: Any,
    source: str,
    regime: str,
    regime_reason: str,
    regime_fast: str = "未知",
    regime_slow: str = "未知",
    regime_confidence: float = 0.0,
    frames: dict[str, pd.DataFrame] | None = None,
    realtime_prices: dict[str, float] | None = None,
) -> tuple[Any, pd.DataFrame | None, float]:
    enriched = frames.get(result.ticker) if frames is not None else None
    if enriched is None:
        frame = _load_cache(result.ticker, source)
        if frame is None or frame.empty:
            return result, None, 0.0
        enriched = compute_all_indicators(frame.copy())
    if enriched.empty:
        return result, None, 0.0
    required_indicators = {"MA20", "MA50", "RSI14"}
    if not required_indicators.issubset(enriched.columns):
        enriched = compute_all_indicators(enriched.copy())
    if enriched.empty or "Close" not in enriched:
        return result, None, 0.0

    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    indexed_date = pd.to_datetime(enriched.index[-1], errors="coerce")
    latest_date = indexed_date.date() if not pd.isna(indexed_date) else None
    reported_date = latest_date
    # TickFlow Free exposes historical daily bars only; the last daily close
    # remains tied to its actual trade_date and is never promoted to today.
    result.close = float(enriched["Close"].iloc[-1])

    if reported_date is None:
        data_age = -1
        trading_age = -1
    else:
        data_age = max(0, (today - reported_date).days)
        trading_age = trading_age_days(reported_date)
    result.market_regime = regime
    result.market_regime_reason = regime_reason
    result.market_regime_fast = regime_fast
    result.market_regime_slow = regime_slow
    result.market_regime_confidence = regime_confidence
    result.data_source = source
    result.data_asof = reported_date.strftime("%Y-%m-%d") if reported_date else ""
    result.price_adjustment_mode = str(
        enriched.attrs.get("price_adjustment_mode", TICKFLOW_ADJUST)
    )
    result.adjustment_base_date = str(
        enriched.attrs.get("adjustment_base_date", result.data_asof)
    )
    result.atr_asof = result.data_asof
    result.corporate_action_rebase_detected = bool(
        enriched.attrs.get("corporate_action_rebase_detected", False)
    )
    result.data_age_days = data_age
    result.data_trading_age_days = trading_age
    result.data_coverage = round(float(enriched["Close"].notna().mean()), 4)
    result.stage = _stage_label(enriched, result.wyckoff_phase)
    result.breakout_quality_factor = _breakout_quality_factor(enriched)
    ma20 = _finite_float(enriched["MA20"].iloc[-1]) if "MA20" in enriched else np.nan
    ma50 = _finite_float(enriched["MA50"].iloc[-1]) if "MA50" in enriched else np.nan
    result.dist_to_ma20 = (
        round((result.close / ma20 - 1.0) * 100.0, 4)
        if np.isfinite(ma20) and ma20 > 0
        else np.nan
    )
    result.dist_to_ma50 = (
        round((result.close / ma50 - 1.0) * 100.0, 4)
        if np.isfinite(ma50) and ma50 > 0
        else np.nan
    )
    result.recent_return_20d = _safe_return(enriched["Close"], 20)
    atr14 = _finite_float(enriched["ATR14"].iloc[-1]) if "ATR14" in enriched else np.nan
    atr50 = _finite_float(enriched["ATR50"].iloc[-1]) if "ATR50" in enriched else np.nan
    if not np.isfinite(atr14):
        atr14 = _finite_float(getattr(result, "atr14", np.nan))
    if not np.isfinite(atr50):
        atr50 = _finite_float(getattr(result, "atr50", np.nan))
    if np.isfinite(atr14):
        result.atr14 = atr14
    if np.isfinite(atr50):
        result.atr50 = atr50
    result.atr_expansion = (
        atr14 / atr50
        if np.isfinite(atr14) and np.isfinite(atr50) and atr50 > 0
        else np.nan
    )
    relative = _safe_return(enriched["Close"], 60)
    result.filter_details["market_regime"] = regime
    result.filter_details["market_regime_reason"] = regime_reason
    return result, enriched, relative


def enrich_results(
    results: list[Any],
    source: str,
    frames: dict[str, pd.DataFrame] | None = None,
) -> None:
    benchmark_frames = _load_benchmark_frames(source)
    slow_regime, slow_reason = _benchmark_regime(benchmark_frames)
    (
        regime_fast,
        regime_slow,
        regime,
        regime_confidence,
        regime_reason,
    ) = _benchmark_regime_components(benchmark_frames, slow_regime, slow_reason)
    # TickFlow Free has no realtime quote service.
    realtime_prices: dict[str, float] | None = None

    industry_returns: dict[str, dict[str, float]] = {}
    cached_frames: dict[str, pd.DataFrame] = {}
    total = len(results)
    completed = 0
    workers = min(max(1, SCAN_THREADS), max(1, total))
    logger.info("Enrichment started: %d results, %d threads.", total, workers)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _enrich_one_result,
                result,
                source,
                regime,
                regime_reason,
                regime_fast,
                regime_slow,
                regime_confidence,
                frames,
                realtime_prices,
            ): result
            for result in results
        }
        for future in as_completed(futures):
            source_result = futures[future]
            try:
                result, enriched, relative = future.result()
            except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
                completed += 1
                logger.warning("Enrichment failed for %s: %s", source_result.ticker, exc)
                continue
            completed += 1
            if enriched is not None:
                cached_frames[result.ticker] = enriched
                classification = model_classification(
                    is_etf=bool(result.is_etf),
                    name=result.name,
                    industry=result.industry,
                    sector=result.sector,
                    ticker=result.ticker,
                )
                result.model_classification = classification
                result.etf_tracking_key = etf_tracking_key(
                    name=result.name, industry=result.industry, sector="", ticker=result.ticker
                ) if result.is_etf else ""
                result.theme_cluster = theme_cluster(
                    is_etf=bool(result.is_etf), name=result.name, industry=result.industry,
                    sector=result.sector, classification=classification, ticker=result.ticker
                )
                if result.is_etf and not str(result.sector or "").strip() and classification:
                    result.sector = classification
                if classification and np.isfinite(relative):
                    industry_returns.setdefault(classification, {})[result.ticker] = relative
            if completed == total or completed % 100 == 0:
                logger.info("Enrichment progress: %d/%d results.", completed, total)

    industry_totals = {
        industry: (float(sum(values.values())), len(values))
        for industry, values in industry_returns.items()
        if values
    }
    for result in results:
        frame = cached_frames.get(result.ticker)
        if frame is None:
            continue
        value = _safe_return(frame["Close"], 60)
        classification = model_classification(
            is_etf=bool(result.is_etf),
            name=result.name,
            industry=result.industry,
            sector=result.sector,
            ticker=result.ticker,
        )
        result.model_classification = classification
        result.etf_tracking_key = etf_tracking_key(
            name=result.name, industry=result.industry, sector="", ticker=result.ticker
        ) if result.is_etf else ""
        result.theme_cluster = theme_cluster(
            is_etf=bool(result.is_etf), name=result.name, industry=result.industry,
            sector=result.sector, classification=classification, ticker=result.ticker
        )
        if result.is_etf and not str(result.sector or "").strip() and classification:
            result.sector = classification
        if not classification:
            result.industry_relative_strength = np.nan
            result.industry_momentum_60d = np.nan
            result.sector_confirmation_factor = 1.0
            continue
        total_return, count = industry_totals.get(classification, (0.0, 0))
        peer = (
            (total_return - value) / (count - 1)
            if np.isfinite(value) and count >= 2
            else np.nan
        )
        result.industry_relative_strength = (
            round(value - peer, 2)
            if np.isfinite(value) and np.isfinite(peer)
            else np.nan
        )
        result.industry_momentum_60d = round(peer, 2) if np.isfinite(peer) else np.nan
        if np.isfinite(peer):
            relative_strength = value - peer if np.isfinite(value) else np.nan
            result.sector_confirmation_factor = _sector_confirmation_factor(
                peer, relative_strength
            )
        else:
            result.sector_confirmation_factor = 1.0

    for result in results:
        base_score = _finite_float(result.failure_adjusted_score)
        if not np.isfinite(base_score):
            base_score = _finite_float(result.final_score)
        if not np.isfinite(base_score):
            base_score = _finite_float(result.score.total, 0.0)
        sector_factor = float(
            np.clip(_finite_float(result.sector_confirmation_factor, 1.0), 0.0, 1.0)
        )
        breakout_factor = float(
            np.clip(_finite_float(result.breakout_quality_factor, 1.0), 0.0, 1.0)
        )
        breakout_state = str(result.entry_signal or "").upper() in {
            "BREAKOUT_CONFIRM", "PRICE_BREAKOUT", "WAIT_VOLUME_CONFIRM"
        }
        effective_breakout_factor = breakout_factor if breakout_state else 1.0
        technical_score = (
            base_score
            * (0.7 + 0.3 * sector_factor)
            * (0.8 + 0.2 * effective_breakout_factor)
        )
        result.technical_institutional_score = round(technical_score, 4)
        quality_adjusted = _quality_adjusted_score(
            technical_score,
            result.quality_score,
            result.quality_data_available,
            result.is_etf,
        )
        # QualityGate/QualityMultiplier are decision gates, not a second score
        # penalty.  The quality contribution is already present in the blend.
        result.institutional_score = round(quality_adjusted, 4)


def refresh_research_outcomes(
    source: str,
    history_path: Path | None = None,
) -> pd.DataFrame:
    from signal_lifecycle import HISTORY_COLUMNS, HISTORY_FILE

    path = history_path or HISTORY_FILE
    if not path.exists():
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    try:
        history = pd.read_csv(path, encoding="utf-8-sig", dtype={"Ticker": str})
    except (OSError, UnicodeError, pd.errors.ParserError):
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    for horizon in (20, 60):
        for column in (f"Return{horizon}D", f"MaxDrawdown{horizon}D"):
            if column not in history:
                history[column] = np.nan
    for ticker, positions in history.groupby("Ticker", sort=False).groups.items():
        frame = _load_cache(str(ticker), source)
        if frame is None or frame.empty or "Close" not in frame:
            continue
        prices = frame["Close"].astype(float).replace([np.inf, -np.inf], np.nan)
        dates = pd.DatetimeIndex(frame.index)
        for position in positions:
            entry_date = pd.to_datetime(
                history.at[position, "TradeDate"], errors="coerce"
            )
            entry_price = pd.to_numeric(history.at[position, "Close"], errors="coerce")
            if pd.isna(entry_date) or not np.isfinite(entry_price) or entry_price <= 0:
                continue
            entry_index = int(dates.searchsorted(entry_date, side="left"))
            if entry_index >= len(prices):
                continue
            for horizon in (20, 60):
                exit_index = entry_index + horizon
                if exit_index >= len(prices) or not np.isfinite(prices.iloc[exit_index]):
                    continue
                holding = prices.iloc[entry_index : exit_index + 1]
                history.at[position, f"Return{horizon}D"] = (
                    float(prices.iloc[exit_index] / entry_price - 1.0) * 100
                )
                history.at[position, f"MaxDrawdown{horizon}D"] = (
                    float(holding.min() / entry_price - 1.0) * 100
                )
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        history.to_csv(temporary_path, index=False, encoding="utf-8-sig")
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return history


def write_research_reports(history: pd.DataFrame) -> tuple[Path, Path]:
    tier_path = OUTPUT_DIR / "TierPerformanceReport.csv"
    ic_path = OUTPUT_DIR / "FactorICReport.csv"
    performance_columns = [
        "InstitutionalTier",
        "Samples",
        "WinRate20D",
        "AverageReturn20D",
        "MedianReturn20D",
        "MaxDrawdown20D",
        "WinRate60D",
        "AverageReturn60D",
        "MedianReturn60D",
        "MaxDrawdown60D",
    ]
    factor_columns = ["Factor", "Samples20D", "IC20D", "Samples60D", "IC60D"]
    tier_rows: list[dict[str, Any]] = []
    if not history.empty and "InstitutionalTier" in history:
        tier_order = {
            "A级机构启动": 0,
            "B级观察": 1,
            "C级价值观察": 2,
            INSTITUTIONAL_TIER_WAIT_LABEL: 3,
            INSTITUTIONAL_TIER_TRAP_LABEL: 4,
        }
        tiers = sorted(
            history["InstitutionalTier"].dropna().unique(),
            key=lambda value: (
                tier_order.get(str(value), len(tier_order)),
                str(value),
            ),
        )
        for tier in tiers:
            group = history.loc[history["InstitutionalTier"].eq(tier)]
            row: dict[str, Any] = {"InstitutionalTier": tier, "Samples": len(group)}
            for horizon in (20, 60):
                returns = pd.to_numeric(
                    group.get(f"Return{horizon}D"), errors="coerce"
                )
                drawdowns = pd.to_numeric(
                    group.get(f"MaxDrawdown{horizon}D"), errors="coerce"
                )
                valid = returns.dropna()
                row[f"WinRate{horizon}D"] = (
                    float((valid > 0).mean()) if not valid.empty else np.nan
                )
                row[f"AverageReturn{horizon}D"] = (
                    float(valid.mean()) if not valid.empty else np.nan
                )
                row[f"MedianReturn{horizon}D"] = (
                    float(valid.median()) if not valid.empty else np.nan
                )
                row[f"MaxDrawdown{horizon}D"] = (
                    float(drawdowns.min()) if not drawdowns.dropna().empty else np.nan
                )
            tier_rows.append(row)
    pd.DataFrame(tier_rows, columns=performance_columns).to_csv(
        tier_path, index=False, encoding="utf-8-sig"
    )

    factors = [
        "InstitutionalScore",
        "QualityScore",
        "Score",
        "OpportunityScore",
        "BreakoutQualityFactor",
        "SignalRecencyFactor",
        "SectorConfirmationFactor",
        "FailureSignalFactor",
        "TrendScore",
        "AccumulationScore",
        "IndustryRelativeStrength",
    ]
    factor_rows: list[dict[str, Any]] = []
    for factor in factors:
        if factor not in history:
            continue
        row = {"Factor": factor}
        for horizon in (20, 60):
            target = f"Return{horizon}D"
            data = history[[factor, target]].apply(
                pd.to_numeric, errors="coerce"
            ).dropna()
            row[f"Samples{horizon}D"] = len(data)
            row[f"IC{horizon}D"] = (
                float(data[factor].rank().corr(data[target].rank()))
                if len(data) >= 2
                and data[factor].nunique() >= 2
                and data[target].nunique() >= 2
                else np.nan
            )
        factor_rows.append(row)
    pd.DataFrame(factor_rows, columns=factor_columns).to_csv(
        ic_path, index=False, encoding="utf-8-sig"
    )
    return tier_path, ic_path


def _legacy_signal_points(
    enriched: pd.DataFrame, cooldown: int
) -> list[int]:
    """Compatibility path for old synthetic tests/caches lacking live-entry columns."""
    volume = enriched.get("VolMA20", pd.Series(index=enriched.index, dtype=float))
    baseline = enriched.get("VolMA120", pd.Series(index=enriched.index, dtype=float))
    cmf = enriched.get("CMF", pd.Series(index=enriched.index, dtype=float))
    close = enriched.get("Close", pd.Series(index=enriched.index, dtype=float))
    ma50 = enriched.get("MA50", pd.Series(index=enriched.index, dtype=float))
    condition = (volume >= baseline * 1.1) & (cmf > 0) & (close <= ma50 * 1.05)
    candidates = np.flatnonzero(condition.fillna(False).to_numpy(dtype=bool))
    last_signal = -cooldown
    points: list[int] = []
    outcome_limit = max(0, len(enriched) - BACKTEST_OUTCOME_HORIZON_DAYS)
    for index in candidates:
        if index >= outcome_limit:
            continue
        if index - last_signal < cooldown:
            continue
        points.append(int(index))
        last_signal = int(index)
    return points


@dataclass(frozen=True)
class BacktestExecutionProfile:
    name: str
    cooldown: int
    score_window: int
    historical_volume_profile: bool
    candidate_gap: int
    fast_prefilter: bool
    chunk_size: int


def _resolve_backtest_profile(mode: str | None, ticker_count: int) -> BacktestExecutionProfile:
    normalized = str(mode or "auto").strip().lower()
    if normalized not in {"auto", "fast", "exact"}:
        raise ValueError(f"unsupported backtest mode: {mode}")
    if normalized == "auto":
        normalized = (
            "exact"
            if int(ticker_count) <= int(BACKTEST_AUTO_EXACT_MAX_TICKERS)
            else "fast"
        )
    if normalized == "exact":
        return BacktestExecutionProfile(
            name="exact",
            cooldown=max(1, int(BACKTEST_SIGNAL_COOLDOWN_DAYS)),
            score_window=max(252, int(BACKTEST_SCORE_WINDOW_BARS)),
            historical_volume_profile=bool(ENABLE_VOLUME_PROFILE),
            candidate_gap=1,
            fast_prefilter=False,
            chunk_size=max(1, int(BACKTEST_CHUNK_SIZE)),
        )
    return BacktestExecutionProfile(
        name="fast",
        cooldown=max(1, int(BACKTEST_FAST_COOLDOWN_DAYS)),
        score_window=max(252, int(BACKTEST_FAST_SCORE_WINDOW_BARS)),
        historical_volume_profile=False,
        candidate_gap=max(1, int(BACKTEST_FAST_CANDIDATE_GAP_DAYS)),
        fast_prefilter=True,
        chunk_size=max(1, int(BACKTEST_FAST_CHUNK_SIZE)),
    )


def _backtest_scoring_window(
    enriched: pd.DataFrame,
    index: int,
    *,
    score_window: int | None = None,
    include_volume_profile: bool | None = None,
) -> pd.DataFrame:
    """Return the bounded, point-in-time frame consumed by score_ticker."""
    end = int(index) + 1
    window = int(score_window or BACKTEST_SCORE_WINDOW_BARS)
    start = max(0, end - max(252, window))
    historical = enriched.iloc[start:end].copy(deep=False)
    vp_columns = [
        "VP_HVN_Center",
        "DistToHVN_Pct",
        "Above_HVN",
        "VP_LVN_Center",
        "DistToLVN_Pct",
    ]
    historical = historical.drop(columns=vp_columns, errors="ignore")
    should_compute_vp = ENABLE_VOLUME_PROFILE if include_volume_profile is None else bool(include_volume_profile)
    if should_compute_vp:
        historical = historical.copy(deep=False)
        try:
            compute_volume_profile(historical)
        except (ArithmeticError, TypeError, ValueError):
            logger.debug("Historical volume profile failed.", exc_info=True)
    return historical


def _candidate_endpoint_matrix(
    enriched: pd.DataFrame,
    *,
    fast_prefilter: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorize cheap endpoint features before any historical score_ticker call."""
    index = enriched.index
    def numeric(name: str) -> pd.Series:
        if name not in enriched.columns:
            return pd.Series(np.nan, index=index, dtype=float)
        return pd.to_numeric(enriched[name], errors="coerce")

    close = numeric("Close")
    high = numeric("High")
    low = numeric("Low")
    ma20 = numeric("MA20")
    ma50 = numeric("MA50")
    atr = numeric("ATR14")
    support = low.rolling(20, min_periods=20).min()
    resistance = high.shift(1).rolling(20, min_periods=20).max()
    effective_atr = atr.where(atr.gt(0), close * 0.03)
    near_support = close.le(support + effective_atr * 1.5)
    five_day_up = close.ge(close.shift(5))
    trend_candidate = close.gt(ma20) & (ma20.ge(ma50) | five_day_up | near_support)
    breakout_flag = close.gt(resistance).fillna(False)
    broad = (trend_candidate | near_support | breakout_flag).fillna(False)

    if fast_prefilter:
        volume = numeric("Volume")
        vol20 = numeric("VolMA20")
        volume_ratio = volume / vol20.replace(0, np.nan)
        cmf = numeric("CMF")
        ad_slope = numeric("AD_Slope")
        flow_ok = cmf.ge(-0.02) | ad_slope.gt(0)
        volume_ok = volume_ratio.ge(0.85)
        evidence_available = volume_ratio.notna() | cmf.notna() | ad_slope.notna()
        support_ready = near_support & (flow_ok | volume_ok)
        trend_ready = close.gt(ma20) & (ma20.ge(ma50) | five_day_up) & (flow_ok | volume_ok)
        filtered = (breakout_flag | support_ready | trend_ready).fillna(False)
        broad = broad.where(~evidence_available, filtered)

    candidates = np.flatnonzero(broad.to_numpy(dtype=bool))
    return candidates, breakout_flag.to_numpy(dtype=bool)


def _signal_evaluations(
    enriched: pd.DataFrame,
    cooldown: int = BACKTEST_SIGNAL_COOLDOWN_DAYS,
    is_etf: bool = False,
    *,
    profile: BacktestExecutionProfile | None = None,
    start_index: int | None = None,
    component_sink: dict[int, tuple[float, float, float]] | None = None,
) -> list[tuple[int, float, str]]:
    """Find actionable historical entries with lazy exact scoring.

    Cheap endpoint logic first determines whether a date can possibly become an
    actionable entry.  Full score_ticker and historical Volume Profile are only
    executed for dates that survive that gate; Exact mode therefore preserves
    the final actionable score while avoiding thousands of wasted full scores.
    """
    live_columns = {"High", "Low", "MA20", "MA50", "ATR14"}
    if len(enriched) < 252:
        if not live_columns.issubset(enriched.columns):
            return [
                (index, np.nan, "UNKNOWN")
                for index in _legacy_signal_points(enriched, max(1, int(cooldown)))
            ]
        return []
    if not live_columns.issubset(enriched.columns):
        return [
            (index, np.nan, "UNKNOWN")
            for index in _legacy_signal_points(enriched, max(1, int(cooldown)))
        ]

    if profile is None:
        profile = BacktestExecutionProfile(
            name="exact",
            cooldown=max(1, int(cooldown)),
            score_window=max(252, int(BACKTEST_SCORE_WINDOW_BARS)),
            historical_volume_profile=bool(ENABLE_VOLUME_PROFILE),
            candidate_gap=1,
            fast_prefilter=False,
            chunk_size=max(1, int(BACKTEST_CHUNK_SIZE)),
        )
    cooldown = max(1, int(profile.cooldown))
    candidates, breakout_flags = _candidate_endpoint_matrix(
        enriched, fast_prefilter=profile.fast_prefilter
    )
    minimum_index = max(251, int(start_index) if start_index is not None else 251)
    last_signal = minimum_index - cooldown
    last_evaluated = minimum_index - max(1, int(profile.candidate_gap))
    evaluations: list[tuple[int, float, str]] = []

    for index in candidates:
        index = int(index)
        if index < minimum_index:
            continue
        if index >= len(enriched) - BACKTEST_OUTCOME_HORIZON_DAYS:
            continue
        if index - last_signal < cooldown:
            continue
        if (
            profile.candidate_gap > 1
            and index - last_evaluated < profile.candidate_gap
            and not breakout_flags[index]
        ):
            continue
        last_evaluated = index

        historical = _backtest_scoring_window(
            enriched,
            index,
            score_window=profile.score_window,
            include_volume_profile=False,
        )
        quick_breakout = breakout_score(historical)
        quick_trap = value_trap_risk(historical)
        quick_entry = entry_point(
            historical,
            breakout=quick_breakout,
            volume_score=None,
            value_trap_risk_value=quick_trap,
            price_decimals=tradable_price_decimals(is_etf),
        )
        quick_signal = str(quick_entry.get("signal", "AVOID")).upper()
        # volume_score can only change the price-breakout branch into
        # BREAKOUT_CONFIRM.  All other non-actionable quick signals are safe to
        # reject without a full historical score.
        if (
            quick_signal not in _BACKTEST_ACTIONABLE_SIGNALS
            and not bool(quick_entry.get("price_breakout", False))
        ):
            continue

        scoring_frame = historical
        if profile.historical_volume_profile:
            scoring_frame = _backtest_scoring_window(
                enriched,
                index,
                score_window=profile.score_window,
                include_volume_profile=True,
            )
        historical_score = score_ticker(scoring_frame, is_etf=is_etf)
        historical_entry = entry_point(
            scoring_frame,
            breakout=_finite_float(getattr(historical_score, "breakout_score", np.nan), np.nan),
            volume_score=_finite_float(getattr(historical_score, "volume", np.nan), np.nan),
            value_trap_risk_value=_finite_float(getattr(historical_score, "value_trap_risk", np.nan), np.nan),
            price_decimals=tradable_price_decimals(is_etf),
        )
        signal = str(historical_entry.get("signal", "AVOID")).upper()
        if signal not in _BACKTEST_ACTIONABLE_SIGNALS:
            continue
        final_score = _finite_float(getattr(historical_score, "final_score", np.nan), np.nan)
        if not np.isfinite(final_score):
            final_score = _finite_float(getattr(historical_score, "total", np.nan), 0.0)
        evaluations.append((index, float(final_score), signal))
        if component_sink is not None:
            component_sink[index] = (
                _finite_float(getattr(historical_score, "base_score", np.nan), 0.0),
                _finite_float(getattr(historical_score, "trigger_score", np.nan), 0.0),
                _finite_float(getattr(historical_score, "execution_score", np.nan),
                    _finite_float(getattr(historical_score, "entry_score", np.nan), 0.0)),
            )
        last_signal = index
    return evaluations


class _SignalPointList(list[int]):
    """List-compatible signal points carrying precomputed real-run evaluations."""

    def __init__(
        self,
        evaluations: list[tuple[int, float, str]],
        components: dict[int, tuple[float, float, float]] | None = None,
    ) -> None:
        super().__init__(index for index, _score, _signal in evaluations)
        self.evaluations = evaluations
        self.components = components or {}


def _signal_points(
    enriched: pd.DataFrame,
    cooldown: int = BACKTEST_SIGNAL_COOLDOWN_DAYS,
    is_etf: bool = False,
) -> list[int]:
    components: dict[int, tuple[float, float, float]] = {}
    evaluations = _signal_evaluations(
        enriched, cooldown=cooldown, is_etf=is_etf, component_sink=components
    )
    return _SignalPointList(evaluations, components)


def _historical_entry_signal(
    historical: pd.DataFrame, historical_score: Any, is_etf: bool = False
) -> str:
    try:
        entry = entry_point(
            historical,
            breakout=_finite_float(
                getattr(historical_score, "breakout_score", np.nan), np.nan
            ),
            volume_score=_finite_float(
                getattr(historical_score, "volume", np.nan), np.nan
            ),
            value_trap_risk_value=_finite_float(
                getattr(historical_score, "value_trap_risk", np.nan), np.nan
            ),
            price_decimals=tradable_price_decimals(is_etf),
        )
    except (ArithmeticError, TypeError, ValueError, KeyError, IndexError):
        return "UNKNOWN"
    return str(entry.get("signal", "UNKNOWN")).upper()


def _backtest_one_ticker(
    ticker: str,
    source: str,
    benchmark_frame: pd.DataFrame | None = None,
    commission: float = BACKTEST_STOCK_COMMISSION_RATE,
    stamp_duty: float = 0.0005,
    slippage: float = 0.001,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None] = (None, None),
    *,
    profile: BacktestExecutionProfile | None = None,
    signal_start_index: int | None = None,
    sample_min_signal_index: int | None = None,
    frame: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    if frame is None:
        frame = _load_cache(ticker, source)
    if frame is None or len(frame) < 300:
        return []
    raw_path = _cache_path(ticker, source)
    enriched, _indicator_cache_hit = load_or_compute_indicators(
        ticker,
        frame,
        compute_all_indicators,
        source_path=raw_path if raw_path.exists() else None,
        enabled=INDICATOR_CACHE_ENABLED,
    )
    is_etf = is_etf_ticker(str(ticker))
    if profile is None and signal_start_index is None:
        signal_points = _signal_points(enriched, is_etf=is_etf)
    else:
        active_profile = profile or _resolve_backtest_profile("exact", 1)
        profile_components: dict[int, tuple[float, float, float]] = {}
        signal_points = _SignalPointList(
            _signal_evaluations(
                enriched,
                is_etf=is_etf,
                profile=active_profile,
                start_index=signal_start_index,
                component_sink=profile_components,
            ),
            profile_components,
        )
    if not signal_points:
        return []

    attached_evaluations = getattr(signal_points, "evaluations", None)
    component_map = dict(getattr(signal_points, "components", {}) or {})
    if attached_evaluations is not None:
        evaluation_map = {
            index: (score, signal)
            for index, score, signal in attached_evaluations
        }
    else:
        evaluation_map: dict[int, tuple[float, str]] = {}
        for index in signal_points:
            historical = _backtest_scoring_window(enriched, int(index))
            historical_score = score_ticker(historical, is_etf=is_etf)
            final_score = _finite_float(
                getattr(historical_score, "final_score", np.nan), np.nan
            )
            if not np.isfinite(final_score):
                final_score = _finite_float(
                    getattr(historical_score, "total", np.nan), 0.0
                )
            evaluation_map[int(index)] = (
                float(final_score),
                _historical_entry_signal(
                    historical, historical_score, is_etf=is_etf
                ),
            )
            component_map[int(index)] = (
                _finite_float(getattr(historical_score, "base_score", np.nan), 0.0),
                _finite_float(getattr(historical_score, "trigger_score", np.nan), 0.0),
                _finite_float(getattr(historical_score, "execution_score", np.nan),
                    _finite_float(getattr(historical_score, "entry_score", np.nan), 0.0)),
            )

    opens = enriched["Open"].to_numpy(dtype=float) if "Open" in enriched else np.full(len(enriched), np.nan)
    lows = enriched["Low"].to_numpy(dtype=float) if "Low" in enriched else np.full(len(enriched), np.nan)
    closes = enriched["Close"].to_numpy(dtype=float)
    highs = enriched["High"].to_numpy(dtype=float) if "High" in enriched else closes.copy()
    volumes = enriched["Volume"].to_numpy(dtype=float) if "Volume" in enriched else np.full(len(enriched), np.nan)
    outcome_horizon = max(60, int(BACKTEST_OUTCOME_HORIZON_DAYS))
    minimum_sample_index = int(sample_min_signal_index) if sample_min_signal_index is not None else 0
    valid_points: list[int] = []
    for index in signal_points:
        if int(index) < minimum_sample_index:
            continue
        entry_index = index + 1
        if entry_index >= len(enriched):
            continue
        if not np.isfinite(opens[entry_index]) or opens[entry_index] <= 0:
            continue
        tradeable, _tradeability_reason = is_entry_tradeable(
            ticker, enriched, entry_index, is_etf=is_etf
        )
        if not tradeable:
            continue
        if (
            entry_index + outcome_horizon >= len(enriched)
            or not np.isfinite(closes[entry_index + 20])
            or not np.isfinite(closes[entry_index + outcome_horizon])
        ):
            continue
        if np.any(~np.isfinite(highs[entry_index : entry_index + outcome_horizon + 1])) or np.any(highs[entry_index : entry_index + outcome_horizon + 1] <= 0):
            continue
        if np.any(~np.isfinite(lows[entry_index : entry_index + outcome_horizon + 1])) or np.any(lows[entry_index : entry_index + outcome_horizon + 1] <= 0):
            continue
        valid_points.append(index)
    if not valid_points:
        return []

    benchmark_close = None
    if benchmark_frame is not None and not benchmark_frame.empty:
        benchmark_close = benchmark_frame["Close"].astype(float).sort_index()

    def historical_regime(at_date: pd.Timestamp) -> str:
        if benchmark_close is None:
            return "UNKNOWN"
        history = benchmark_close.loc[:at_date].dropna()
        if len(history) < 60:
            return "UNKNOWN"
        last = float(history.iloc[-1])
        ma60 = float(history.iloc[-60:].mean())
        ma200 = float(history.iloc[-200:].mean()) if len(history) >= 200 else ma60
        ret60 = (
            (last / float(history.iloc[-61]) - 1.0) * 100.0
            if len(history) >= 61 and float(history.iloc[-61]) > 0
            else 0.0
        )
        if last >= ma60 and last >= ma200 and ret60 > 3.0:
            return "RISK_ON"
        if last < ma60 and last < ma200 and ret60 < -3.0:
            return "RISK_OFF"
        return "NEUTRAL"
    validation_end, test_start = split_dates
    samples: list[dict[str, Any]] = []
    previous_sample_index: int | None = None
    for index in valid_points:
        signal_date = pd.Timestamp(enriched.index[index])
        historical_eligible, historical_reason = point_in_time_eligibility(
            ticker, signal_date
        )
        if historical_eligible is False:
            continue
        entry_index = index + 1
        entry_date = pd.Timestamp(enriched.index[entry_index])
        entry_price = opens[entry_index]
        exit20_index, exit20_delay, exit20_reason = resolve_exit_index(
            ticker,
            enriched,
            entry_index + 20,
            is_etf=is_etf,
            max_delay_days=BACKTEST_MAX_EXIT_DELAY_DAYS,
        )
        exit60_index, exit60_delay, exit60_reason = resolve_exit_index(
            ticker,
            enriched,
            entry_index + outcome_horizon,
            is_etf=is_etf,
            max_delay_days=BACKTEST_MAX_EXIT_DELAY_DAYS,
        )
        if exit20_index is None or exit60_index is None:
            continue
        future20 = closes[exit20_index]
        future60 = closes[exit60_index]
        benchmark_returns: dict[int, float] = {20: np.nan, 60: np.nan}
        if benchmark_close is not None:
            start_date = benchmark_close.index.asof(entry_date)
            for period, resolved_exit in (
                (20, exit20_index),
                (60, exit60_index),
            ):
                future_date = pd.Timestamp(enriched.index[resolved_exit])
                end_date = benchmark_close.index.asof(future_date)
                if (
                    pd.notna(start_date)
                    and pd.notna(end_date)
                    and end_date == future_date
                    and benchmark_close.loc[start_date] > 0
                ):
                    benchmark_returns[period] = (
                        benchmark_close.loc[end_date] / benchmark_close.loc[start_date] - 1
                    ) * 100
        fee_schedule = BrokerFeeSchedule(stock_commission_rate=float(commission))
        cost20_percent = round_trip_cost_percent(
            is_etf=is_etf,
            entry_price=float(entry_price),
            entry_volume=float(volumes[entry_index]),
            exit_price=float(future20),
            exit_volume=float(volumes[exit20_index]),
            base_slippage=float(slippage),
            stamp_duty=float(stamp_duty),
            schedule=fee_schedule,
        )
        cost60_percent = round_trip_cost_percent(
            is_etf=is_etf,
            entry_price=float(entry_price),
            entry_volume=float(volumes[entry_index]),
            exit_price=float(future60),
            exit_volume=float(volumes[exit60_index]),
            base_slippage=float(slippage),
            stamp_duty=float(stamp_duty),
            schedule=fee_schedule,
        )
        prices20 = np.concatenate(([entry_price], closes[entry_index : exit20_index + 1]))
        prices60 = np.concatenate(([entry_price], closes[entry_index : exit60_index + 1]))
        lows20 = np.concatenate(([entry_price], lows[entry_index : exit20_index + 1]))
        lows60 = np.concatenate(([entry_price], lows[entry_index : exit60_index + 1]))
        drawdown20 = float(((lows20 / np.maximum.accumulate(prices20) - 1).min()) * 100)
        drawdown60 = float(((lows60 / np.maximum.accumulate(prices60) - 1).min()) * 100)
        exit60_date = pd.Timestamp(enriched.index[exit60_index])
        split = _purged_split_label(
            entry_date,
            exit60_date,
            (validation_end, test_start),
        )
        spacing = outcome_horizon if previous_sample_index is None else max(1, index - previous_sample_index)
        sample_weight = min(1.0, spacing / float(outcome_horizon))
        historical_score, historical_signal = evaluation_map[index]
        setup_component, trigger_component, execution_component = component_map.get(
            index, (historical_score, 0.0, 0.0)
        )
        samples.append(
            {
                "ticker": ticker,
                "asset_type": "etf" if is_etf else "stock",
                "entry_signal": historical_signal,
                "market_regime": historical_regime(signal_date),
                "universe_snapshot_status": (
                    "ELIGIBLE" if historical_eligible is True else "UNAVAILABLE"
                ),
                "universe_snapshot_reason": str(historical_reason),
                "signal_date": signal_date.strftime("%Y-%m-%d"),
                "entry_date": entry_date.strftime("%Y-%m-%d"),
                "entry_price": float(entry_price),
                "exit20_date": pd.Timestamp(enriched.index[exit20_index]).strftime("%Y-%m-%d"),
                "exit60_date": exit60_date.strftime("%Y-%m-%d"),
                "exit20_delay_days": int(exit20_delay),
                "exit60_delay_days": int(exit60_delay),
                "exit20_delay_reason": str(exit20_reason),
                "exit60_delay_reason": str(exit60_reason),
                "round_trip_cost20_pct": round(float(cost20_percent), 6),
                "round_trip_cost60_pct": round(float(cost60_percent), 6),
                "return20": (future20 / entry_price - 1) * 100,
                "return60": (future60 / entry_price - 1) * 100,
                "benchmark_return20": benchmark_returns[20],
                "benchmark_return60": benchmark_returns[60],
                "net_return20": (future20 / entry_price - 1) * 100 - cost20_percent,
                "net_return60": (future60 / entry_price - 1) * 100 - cost60_percent,
                "drawdown20": drawdown20,
                "drawdown60": drawdown60,
                "score": historical_score,
                "setup_score": float(setup_component),
                "trigger_score": float(trigger_component),
                "execution_score": float(execution_component),
                "split": split,
                "sample_weight": round(sample_weight, 4),
            }
        )
        previous_sample_index = index
    return samples


def _relabel_sample_splits(
    samples: list[dict[str, Any]],
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for sample in samples:
        item = dict(sample)
        item["split"] = _purged_split_label(
            item.get("entry_date"),
            item.get("exit60_date"),
            split_dates,
        )
        result.append(item)
    return result


def _purged_split_label(
    entry_date: Any,
    outcome_date: Any,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None],
) -> str:
    """Label a sample without allowing its 60-day outcome across a boundary."""
    validation_start, test_start = split_dates
    entry = pd.to_datetime(entry_date, errors="coerce")
    outcome = pd.to_datetime(outcome_date, errors="coerce")
    if pd.isna(entry) or pd.isna(outcome) or outcome < entry:
        return "purged"
    if test_start is not None and entry >= test_start:
        return "test"
    if validation_start is not None and entry >= validation_start:
        if test_start is not None and outcome >= test_start:
            return "purged"
        return "validation"
    first_boundary = validation_start if validation_start is not None else test_start
    if first_boundary is not None and outcome >= first_boundary:
        return "purged"
    return "train"


def _reweight_samples(samples: list[dict[str, Any]], frame: pd.DataFrame) -> list[dict[str, Any]]:
    if not samples:
        return samples
    positions = {
        pd.Timestamp(value).strftime("%Y-%m-%d"): index
        for index, value in enumerate(pd.DatetimeIndex(frame.index))
    }
    horizon = max(60, int(BACKTEST_OUTCOME_HORIZON_DAYS))
    ordered = sorted(samples, key=lambda item: str(item.get("signal_date", "")))
    previous: int | None = None
    for item in ordered:
        position = positions.get(str(item.get("signal_date", "")))
        if position is None:
            continue
        spacing = horizon if previous is None else max(1, position - previous)
        item["sample_weight"] = round(min(1.0, spacing / float(horizon)), 4)
        previous = position
    return ordered


def _merge_backtest_samples(
    historical: list[dict[str, Any]],
    tail: list[dict[str, Any]],
    frame: pd.DataFrame,
) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in [*historical, *tail]:
        key = (
            str(item.get("ticker", "")),
            str(item.get("signal_date", "")),
            str(item.get("entry_signal", "")),
        )
        merged[key] = dict(item)
    return _reweight_samples(list(merged.values()), frame)


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
) -> tuple[list[dict[str, Any]], bool]:
    del benchmark_signature  # v5 validates benchmark data by market-state prefix instead.
    frame = _load_cache(ticker, source)
    if frame is None or len(frame) < 300:
        return (
            _backtest_one_ticker(
                ticker,
                source,
                benchmark_frame,
                commission,
                stamp_duty,
                slippage,
                split_dates,
            ),
            False,
        )
    active_profile = profile or _resolve_backtest_profile("exact", 1)
    cache_key = backtest_cache_key(
        {
            "ticker": str(ticker),
            "source": str(source),
            "benchmark": str(benchmark_name),
            "commission": float(commission),
            "etf_commission": float(BACKTEST_ETF_COMMISSION_RATE),
            "stamp_duty": float(stamp_duty),
            "slippage": float(slippage),
            "assumed_trade_notional": float(BACKTEST_ASSUMED_TRADE_NOTIONAL),
            "execution_model": "asset_fees_liquidity_t1_limit_exit_v1",
            "max_exit_delay_days": int(BACKTEST_MAX_EXIT_DELAY_DAYS),
            "cooldown": int(active_profile.cooldown),
            "horizon": int(BACKTEST_OUTCOME_HORIZON_DAYS),
            "score_window": int(active_profile.score_window),
            "mode": active_profile.name,
            "historical_volume_profile": bool(active_profile.historical_volume_profile),
            "candidate_gap": int(active_profile.candidate_gap),
            "fast_prefilter": bool(active_profile.fast_prefilter),
            "model_weight_signature": model_weight_signature(),
        }
    )
    current_market = market_cache_state(frame)
    current_benchmark = market_cache_state(benchmark_frame) if benchmark_frame is not None else {}
    cached_payload = load_backtest_cache_state(ticker, cache_key) if BACKTEST_CACHE_ENABLED else None
    if cached_payload is not None:
        cached_samples = list(cached_payload.get("samples", []))
        cached_state = cached_payload.get("state", {}) if isinstance(cached_payload.get("state", {}), dict) else {}
        cached_market = cached_state.get("market", {})
        cached_benchmark = cached_state.get("benchmark", {})
        market_ok = market_prefix_matches(frame, cached_market)
        benchmark_ok = (
            not cached_benchmark
            or benchmark_frame is None
            or market_prefix_matches(benchmark_frame, cached_benchmark)
        )
        if market_ok and benchmark_ok:
            old_rows = int(cached_market.get("rows", 0) or 0)
            old_last = str(cached_market.get("last", ""))
            same_market = old_rows == len(frame) and old_last == str(current_market.get("last", ""))
            if same_market:
                return _relabel_sample_splits(cached_samples, split_dates), True

            cutoff_index = max(251, len(frame) - max(300, int(BACKTEST_INCREMENTAL_TAIL_BARS)))
            warmup = max(
                251,
                cutoff_index
                - max(
                    int(active_profile.cooldown),
                    int(BACKTEST_OUTCOME_HORIZON_DAYS),
                    int(active_profile.candidate_gap),
                ),
            )
            cutoff_date = pd.Timestamp(frame.index[cutoff_index])
            retained = [
                dict(item)
                for item in cached_samples
                if pd.Timestamp(item.get("signal_date")) < cutoff_date
            ]
            tail_samples = _backtest_one_ticker(
                ticker,
                source,
                benchmark_frame,
                commission,
                stamp_duty,
                slippage,
                split_dates,
                profile=active_profile,
                signal_start_index=warmup,
                sample_min_signal_index=cutoff_index,
                frame=frame,
            )
            samples = _merge_backtest_samples(retained, tail_samples, frame)
            samples = _relabel_sample_splits(samples, split_dates)
            if BACKTEST_CACHE_ENABLED:
                save_backtest_cache(
                    ticker,
                    cache_key,
                    samples,
                    state={"market": current_market, "benchmark": current_benchmark},
                )
            return samples, True

    samples = _backtest_one_ticker(
        ticker,
        source,
        benchmark_frame,
        commission,
        stamp_duty,
        slippage,
        split_dates,
        profile=active_profile,
        frame=frame,
    )
    if BACKTEST_CACHE_ENABLED:
        save_backtest_cache(
            ticker,
            cache_key,
            samples,
            state={"market": current_market, "benchmark": current_benchmark},
        )
    return samples, False

def _backtest_evidence(
    samples: int, effective_samples: float, return_std: float
) -> tuple[float, float, str]:
    count = max(0.0, float(samples))
    effective = min(count, max(0.0, float(effective_samples)))
    if count < BACKTEST_MIN_SAMPLES_FOR_RANKING:
        return 0.0, 0.0, "样本不足"
    if count < BACKTEST_LOW_CONFIDENCE_MAX_SAMPLES:
        support = 0.25 * (
            (count - BACKTEST_MIN_SAMPLES_FOR_RANKING + 1.0)
            / (
                BACKTEST_LOW_CONFIDENCE_MAX_SAMPLES
                - BACKTEST_MIN_SAMPLES_FOR_RANKING
                + 1.0
            )
        )
        tier = "低可信度"
    elif count < BACKTEST_FULL_WEIGHT_SAMPLES:
        support = 0.25 + 0.75 * (
            (count - BACKTEST_LOW_CONFIDENCE_MAX_SAMPLES)
            / (BACKTEST_FULL_WEIGHT_SAMPLES - BACKTEST_LOW_CONFIDENCE_MAX_SAMPLES)
        )
        tier = "中可信度"
    else:
        support = 1.0
        tier = "高可信度"
    independence = np.sqrt(effective / count) if count else 0.0
    dispersion = (
        float(np.clip(1.0 - abs(return_std) / 80.0, 0.55, 1.0))
        if np.isfinite(return_std)
        else 1.0
    )
    reliability = float(np.clip(support * independence * dispersion, 0.0, 1.0))
    return (
        round(reliability, 4),
        round(reliability * BACKTEST_NORMAL_WEIGHT, 4),
        tier,
    )


def _ticker_backtest_rows(
    sample_frame: pd.DataFrame, objective: str = "net_excess_return_20d"
) -> list[dict[str, Any]]:
    target_map = {
        "return_20d": "return20",
        "return_60d": "return60",
        "excess_return_20d": "excess20",
        "excess_return_60d": "excess60",
        "net_excess_return_20d": "net_excess20",
        "net_excess_return_60d": "net_excess60",
        "max_drawdown": "drawdown60",
        "risk_adjusted": "risk_adjusted",
    }
    if objective not in target_map:
        raise ValueError(f"unsupported objective: {objective}")
    sample_frame = sample_frame.copy()
    if "entry_signal" not in sample_frame:
        sample_frame["entry_signal"] = "UNKNOWN"
    sample_frame["entry_signal"] = (
        sample_frame["entry_signal"].fillna("UNKNOWN").astype(str).str.upper()
    )
    sample_weights = sample_frame.get("sample_weight")
    if sample_weights is None:
        sample_weights = pd.Series(1.0, index=sample_frame.index)
    sample_frame["sample_weight"] = (
        pd.to_numeric(sample_weights, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .fillna(1.0)
        .clip(0.0, 1.0)
    )
    for column in ("benchmark_return20", "benchmark_return60"):
        if column not in sample_frame:
            sample_frame[column] = np.nan
    sample_frame["excess20"] = (
        sample_frame["return20"] - sample_frame["benchmark_return20"]
    )
    sample_frame["excess60"] = (
        sample_frame["return60"] - sample_frame["benchmark_return60"]
    )
    sample_frame["net_excess20"] = (
        sample_frame["net_return20"] - sample_frame["benchmark_return20"]
    )
    sample_frame["net_excess60"] = (
        sample_frame["net_return60"] - sample_frame["benchmark_return60"]
        if "net_return60" in sample_frame
        else np.nan
    )
    sample_frame["risk_adjusted"] = sample_frame["net_return20"] / sample_frame[
        "drawdown20"
    ].abs().replace(0, np.nan)

    rows: list[dict[str, Any]] = []
    for (ticker, entry_signal), group in sample_frame.groupby(
        ["ticker", "entry_signal"], sort=False
    ):
        weights = group["sample_weight"]
        win20 = _weighted_rate(group["return20"], weights)
        win60 = _weighted_rate(group["return60"], weights)
        avg20 = _weighted_robust_mean(group["return20"], weights)
        avg60 = _weighted_robust_mean(group["return60"], weights)
        net_win20 = _weighted_rate(group["net_excess20"], weights)
        net_win60 = _weighted_rate(group["net_excess60"], weights)
        net_avg20 = _weighted_robust_mean(group["net_excess20"], weights)
        net_avg60 = _weighted_robust_mean(group["net_excess60"], weights)
        median20 = float(pd.to_numeric(group["return20"], errors="coerce").median())
        median60 = float(pd.to_numeric(group["return60"], errors="coerce").median())
        max_drawdown20 = float(
            pd.to_numeric(group["drawdown20"], errors="coerce").min()
        )
        max_drawdown60 = float(
            pd.to_numeric(group["drawdown60"], errors="coerce").min()
        )
        std20 = _weighted_std(group["return20"], weights)
        target_std20 = _weighted_std(group["net_excess20"], weights)
        profit_factor = _weighted_profit_factor(group["return20"], weights)
        net_excess_profit_factor = _weighted_profit_factor(
            group["net_excess20"], weights
        )
        signal_dates = pd.to_datetime(
            group.get("signal_date", pd.Series(pd.NaT, index=group.index)),
            errors="coerce",
        )
        signal_span_days = (
            int((signal_dates.max() - signal_dates.min()).days)
            if signal_dates.notna().any()
            else 0
        )
        last_mature_signal_date = (
            signal_dates.max().strftime("%Y-%m-%d")
            if signal_dates.notna().any()
            else ""
        )
        negative_returns60 = group.loc[
            group["net_excess60"] < 0, "net_excess60"
        ]
        downside60 = (
            _weighted_robust_mean(
                negative_returns60,
                group.loc[negative_returns60.index, "sample_weight"],
            )
            if not negative_returns60.empty
            else 0.0
        )
        score_win60 = net_win60 if np.isfinite(net_win60) else net_win20
        score_avg60 = net_avg60 if np.isfinite(net_avg60) else net_avg20
        raw_score = (
            net_win20 * 0.20
            + score_win60 * 0.20
            + _bounded_score(net_avg20, -15.0, 15.0) * 0.15
            + _bounded_score(score_avg60, -25.0, 35.0) * 0.25
            + _bounded_score(downside60, -25.0, 0.0) * 0.20
        ) * 100.0
        effective_samples = float(group["sample_weight"].sum())
        reliability, effective_weight, confidence_tier = _backtest_evidence(
            len(group), effective_samples, target_std20
        )
        adjusted_backtest_score = BACKTEST_NEUTRAL_SCORE + (
            raw_score - BACKTEST_NEUTRAL_SCORE
        ) * reliability
        objective_frame = group[[target_map[objective], "sample_weight"]].copy()
        objective_frame[target_map[objective]] = pd.to_numeric(
            objective_frame[target_map[objective]], errors="coerce"
        )
        objective_frame = objective_frame.replace([np.inf, -np.inf], np.nan).dropna(
            subset=[target_map[objective]]
        )
        raw_objective_value = _weighted_robust_mean(
            objective_frame[target_map[objective]],
            objective_frame["sample_weight"],
        )
        objective_value = (
            raw_objective_value * reliability
            if np.isfinite(raw_objective_value)
            else np.nan
        )
        failure_signal_factor = 1.0
        if (
            effective_samples >= BACKTEST_MIN_SAMPLES_FOR_RANKING
            and net_avg20 < 0
            and score_avg60 < 0
        ):
            loss20 = 1.0 - _bounded_score(net_avg20, -30.0, 0.0)
            loss60 = 1.0 - _bounded_score(score_avg60, -50.0, 0.0)
            failure_strength = loss20 * 0.3 + loss60 * 0.7
            failure_signal_factor = 1.0 - failure_strength * reliability * 0.7
        rows.append(
            {
                "ticker": str(ticker),
                "entry_signal": str(entry_signal),
                "samples": len(group),
                "effective_samples": round(effective_samples, 4),
                "win_rate_20d": round(win20, 4),
                "win_rate_60d": round(win60, 4),
                "net_excess_win_rate_20d": round(net_win20, 4),
                "net_excess_win_rate_60d": round(net_win60, 4),
                "average_return_20d": round(avg20, 4),
                "average_return_60d": round(avg60, 4),
                "average_net_excess_return_20d": round(net_avg20, 4),
                "average_net_excess_return_60d": round(net_avg60, 4),
                "median_return_20d": round(median20, 4),
                "median_return_60d": round(median60, 4),
                "max_drawdown_20d": round(max_drawdown20, 4),
                "max_drawdown_60d": round(max_drawdown60, 4),
                "profit_factor": (
                    round(profit_factor, 4)
                    if np.isfinite(profit_factor)
                    else np.nan
                ),
                "net_excess_profit_factor": (
                    round(net_excess_profit_factor, 4)
                    if np.isfinite(net_excess_profit_factor)
                    else np.nan
                ),
                "signal_span_days": signal_span_days,
                "backtest_last_mature_signal_date": last_mature_signal_date,
                "return_std_20d": (
                    round(std20, 4) if np.isfinite(std20) else np.nan
                ),
                "target_std_20d": (
                    round(target_std20, 4)
                    if np.isfinite(target_std20)
                    else np.nan
                ),
                "objective_value": round(objective_value, 4),
                "raw_objective_value": round(raw_objective_value, 4),
                "backtest_score": round(float(raw_score), 4),
                "backtest_reliability": reliability,
                "backtest_effective_weight": effective_weight,
                "backtest_confidence_tier": confidence_tier,
                "backtest_adjusted_score": round(float(adjusted_backtest_score), 4),
                "failure_signal_factor": round(float(failure_signal_factor), 4),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            row["objective_value"]
            if np.isfinite(row["objective_value"])
            else -np.inf,
            row["samples"],
        ),
        reverse=True,
    )


def _expand_legacy_backtest_metrics(
    frame: pd.DataFrame, metrics: pd.DataFrame
) -> pd.DataFrame:
    """Map old ticker-only calibration rows onto the current entry signal."""
    if metrics.empty:
        return metrics
    metrics = metrics.copy()
    metrics["EntrySignal"] = (
        metrics.get("EntrySignal", pd.Series("UNKNOWN", index=metrics.index))
        .fillna("UNKNOWN")
        .astype(str)
        .str.upper()
    )
    legacy = metrics[metrics["EntrySignal"].eq("UNKNOWN")]
    exact = metrics[~metrics["EntrySignal"].eq("UNKNOWN")]
    if not legacy.empty:
        current_signals = frame[["Ticker", "EntrySignal"]].drop_duplicates("Ticker")
        legacy = legacy.drop(columns="EntrySignal").merge(
            current_signals, on="Ticker", how="left", validate="many_to_one"
        )
    return (
        pd.concat([exact, legacy], ignore_index=True)
        .drop_duplicates(["Ticker", "EntrySignal"], keep="first")
    )



def _minimum_fast_samples_for_exact_refinement() -> int:
    """Evidence floor for promoting a FAST candidate into expensive EXACT work.

    FAST intentionally samples signals more sparsely than EXACT.  Scale the
    ranking evidence floor by the cooldown ratio so a candidate that has a
    realistic chance of reaching the normal ten-sample ranking floor is still
    eligible for refinement, while one-off signals are not recomputed exactly.
    """
    fast_cooldown = max(1, int(BACKTEST_FAST_COOLDOWN_DAYS))
    exact_cooldown = max(1, int(BACKTEST_SIGNAL_COOLDOWN_DAYS))
    return max(
        1,
        int(np.ceil(float(BACKTEST_MIN_SAMPLES_FOR_RANKING) * exact_cooldown / fast_cooldown)),
    )


def _select_exact_refinement_pool(
    frame: pd.DataFrame,
    fast_rows: list[dict[str, Any]],
    top_n: int = 50,
) -> pd.DataFrame:
    """Select only evidence-qualified, decision-relevant EXACT candidates."""
    if frame.empty:
        return frame.head(0).copy()

    working = frame.copy()
    working["_CurrentSignal"] = (
        working.get("EntrySignal", pd.Series("UNKNOWN", index=working.index))
        .fillna("UNKNOWN").astype(str).str.upper()
    )
    working["_Eligibility"] = (
        working.get("RankingEligibility", pd.Series("观察", index=working.index))
        .fillna("观察").astype(str).str.strip()
    )
    working["_RefineMetric"] = pd.to_numeric(
        working.get(
            "RankingScore",
            working.get("InstitutionalScore", working.get("FinalScore", pd.Series(np.nan, index=working.index))),
        ),
        errors="coerce",
    ).replace([np.inf, -np.inf], np.nan).fillna(-np.inf)

    by_key: dict[tuple[str, str], int] = {}
    by_ticker: dict[str, int] = {}
    effective_by_key: dict[tuple[str, str], float] = {}
    effective_by_ticker: dict[str, float] = {}
    for row in fast_rows:
        ticker = str(row.get("ticker", "")).strip()
        if not ticker:
            continue
        signal = str(row.get("entry_signal", "UNKNOWN")).strip().upper() or "UNKNOWN"
        try:
            samples = max(0, int(float(row.get("samples", 0) or 0)))
        except (TypeError, ValueError):
            samples = 0
        by_key[(ticker, signal)] = max(samples, by_key.get((ticker, signal), 0))
        by_ticker[ticker] = max(samples, by_ticker.get(ticker, 0))
        try:
            effective_samples = float(row.get("effective_samples", np.nan))
        except (TypeError, ValueError):
            effective_samples = np.nan
        if np.isfinite(effective_samples):
            effective_samples = max(0.0, effective_samples)
            effective_by_key[(ticker, signal)] = max(
                effective_samples,
                effective_by_key.get((ticker, signal), 0.0),
            )
            effective_by_ticker[ticker] = max(
                effective_samples,
                effective_by_ticker.get(ticker, 0.0),
            )

    fast_samples: list[int] = []
    for ticker, signal in zip(
        working.get("Ticker", pd.Series("", index=working.index)).fillna("").astype(str),
        working["_CurrentSignal"],
    ):
        fast_samples.append(by_key.get((ticker, signal), by_ticker.get(ticker, 0)))
    working["_FastSamples"] = fast_samples
    fast_effective_samples: list[float] = []
    for ticker, signal in zip(
        working.get("Ticker", pd.Series("", index=working.index)).fillna("").astype(str),
        working["_CurrentSignal"],
    ):
        fast_effective_samples.append(
            effective_by_key.get(
                (ticker, signal), effective_by_ticker.get(ticker, np.nan)
            )
        )
    working["_FastEffectiveSamples"] = fast_effective_samples

    ranked = (
        working.loc[~working["_Eligibility"].eq("风险过滤")]
        .sort_values("_RefineMetric", ascending=False, kind="mergesort")
        .copy()
    )
    if ranked.empty:
        return ranked
    ranked["_RefineRank"] = np.arange(1, len(ranked) + 1)
    ranked["_PriorityEligibility"] = ranked["_Eligibility"].isin({"推荐", "谨慎候选"})
    minimum_fast_samples = _minimum_fast_samples_for_exact_refinement()
    top_limit = max(1, int(top_n))
    candidate_cap = max(
        1,
        min(int(BACKTEST_EXACT_REFINEMENT_CANDIDATES), top_limit),
    )
    selected = ranked.loc[
        ranked["_FastSamples"].ge(minimum_fast_samples)
        & (
            ranked["_FastEffectiveSamples"].isna()
            | ranked["_FastEffectiveSamples"].ge(BACKTEST_MIN_SAMPLES_FOR_RANKING)
        )
        & (ranked["_PriorityEligibility"] | ranked["_RefineRank"].le(top_limit))
    ].copy()
    return (
        selected.sort_values(
            ["_PriorityEligibility", "_RefineMetric"],
            ascending=[False, False],
            kind="mergesort",
        )
        .head(candidate_cap)
        .copy()
    )


def _apply_backtest_provenance(
    frame: pd.DataFrame,
    summary: BacktestSummary,
    observed: pd.Series,
) -> pd.DataFrame:
    """Separate run-level HYBRID provenance from per-ticker execution state.

    A HYBRID run means the task used FAST screening plus selective EXACT
    refinement.  It does *not* mean every ticker was evaluated in HYBRID mode.
    Manual subset backtests also leave unrelated AllResults rows explicitly
    NOT_EVALUATED instead of fabricating a zero-sample result.
    """
    frame = frame.copy()
    ticker_text = frame.get("Ticker", pd.Series("", index=frame.index)).fillna("").astype(str)
    requested = {
        str(value).strip()
        for value in (getattr(summary, "requested_tickers", []) or [])
        if str(value).strip()
    }
    if not requested:
        requested = {
            str(row.get("ticker", "")).strip()
            for row in (getattr(summary, "by_ticker", []) or [])
            if str(row.get("ticker", "")).strip()
        }
    if not requested and int(getattr(summary, "ticker_count", 0) or 0) >= len(frame):
        requested = set(ticker_text)
    requested_mask = ticker_text.isin(requested)

    run_mode = str(getattr(summary, "mode", "") or "").strip().upper() or "UNKNOWN"
    run_engine = str(getattr(summary, "engine", "") or "").strip()
    screen_mode = "FAST" if run_mode == "HYBRID" else run_mode
    screen_engine = run_engine.split("+exact:", 1)[0] if run_engine else ""

    raw_mode = frame.get("BacktestMode", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip().str.upper()
    inferred_mode = pd.Series(
        np.where(requested_mask, screen_mode, "NONE"), index=frame.index, dtype=object
    )
    ticker_mode = raw_mode.where(raw_mode.ne(""), inferred_mode)
    ticker_mode = ticker_mode.where(requested_mask, "NONE")

    raw_engine = frame.get("BacktestEngine", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip()
    ticker_engine = raw_engine.where(raw_engine.ne(""), screen_engine)
    ticker_engine = ticker_engine.where(requested_mask, "")

    raw_stage = frame.get("BacktestStage", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip().str.upper()
    default_stage = pd.Series(
        np.where(
            ~requested_mask,
            "NOT_EVALUATED",
            np.where(
                ticker_mode.eq("EXACT"),
                "EXACT_REFINEMENT" if run_mode == "HYBRID" else "EXACT",
                "FAST_SCREEN",
            ),
        ),
        index=frame.index,
        dtype=object,
    )
    stage = raw_stage.where(raw_stage.ne(""), default_stage)
    stage = stage.where(requested_mask, "NOT_EVALUATED")

    numeric_observed = pd.to_numeric(observed, errors="coerce").fillna(0.0).clip(lower=0.0)
    frame["BacktestSamples"] = numeric_observed.round().astype(int)
    if "BacktestEffectiveSamples" in frame:
        frame["BacktestEffectiveSamples"] = (
            pd.to_numeric(frame["BacktestEffectiveSamples"], errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
            .clip(lower=0.0)
        )
    effective_evidence = pd.to_numeric(
        frame.get(
            "BacktestEffectiveSamples",
            pd.Series(np.nan, index=frame.index),
        ),
        errors="coerce",
    ).replace([np.inf, -np.inf], np.nan)
    effective_evidence = effective_evidence.where(
        effective_evidence.gt(0.0), numeric_observed
    ).clip(lower=0.0, upper=numeric_observed)
    frame["BacktestRunMode"] = run_mode
    frame["BacktestRunEngine"] = run_engine
    frame["BacktestRequested"] = requested_mask.astype(bool)
    frame["BacktestMode"] = ticker_mode
    frame["BacktestEngine"] = ticker_engine
    frame["BacktestStage"] = stage
    # CandidateGenerationStage is initialized as SCAN by the scanner.  Once a
    # backtest has run it must be replaced from the final per-ticker provenance
    # before decision-integrity validation sees the frame.
    frame["CandidateGenerationStage"] = candidate_generation_stage(stage)
    frame["BacktestStatus"] = np.select(
        [~requested_mask, numeric_observed.gt(0.0)],
        ["SKIPPED", "SAMPLES"],
        default="NO_SIGNAL_SAMPLES",
    )
    frame["BacktestEligibleForRanking"] = (
        requested_mask
        & numeric_observed.ge(BACKTEST_MIN_SAMPLES_FOR_RANKING)
        & effective_evidence.ge(BACKTEST_MIN_SAMPLES_FOR_RANKING)
    )

    minimum_fast = _minimum_fast_samples_for_exact_refinement()
    frame["BacktestSkipReason"] = np.select(
        [
            ~requested_mask,
            requested_mask & ticker_mode.eq("FAST") & numeric_observed.eq(0.0),
            requested_mask & ticker_mode.eq("EXACT") & numeric_observed.eq(0.0),
            requested_mask & ticker_mode.eq("FAST") & numeric_observed.gt(0.0) & numeric_observed.lt(minimum_fast) & pd.Series(run_mode == "HYBRID", index=frame.index),
            requested_mask & ticker_mode.eq("FAST") & numeric_observed.ge(minimum_fast) & pd.Series(run_mode == "HYBRID", index=frame.index),
            requested_mask
            & (
                numeric_observed.lt(BACKTEST_MIN_SAMPLES_FOR_RANKING)
                | effective_evidence.lt(BACKTEST_MIN_SAMPLES_FOR_RANKING)
            ),
        ],
        [
            "不在本次回测范围",
            "FAST无历史信号样本",
            "EXACT无历史信号样本",
            "FAST样本不足，跳过EXACT",
            "未进入EXACT候选池",
            "历史样本不足，不参与排名",
        ],
        default="",
    )
    return frame


def _trading_days_between(start: date, end: date) -> int:
    """Count completed China trading sessions after ``start`` through ``end``."""
    if end <= start:
        return 0
    count = 0
    cursor = start + timedelta(days=1)
    while cursor <= end:
        if is_trading_day(cursor):
            count += 1
        cursor += timedelta(days=1)
    return count


def _apply_backtest_freshness(
    frame: pd.DataFrame, summary: BacktestSummary
) -> pd.DataFrame:
    """Expose benchmark cutoff freshness without changing any model weight."""
    result = frame.copy()
    requested = result.get(
        "BacktestRequested", pd.Series(False, index=result.index)
    ).fillna(False).astype(bool)
    legacy_cutoff = result.get(
        "BacktestLastEvaluatedDate", pd.Series("", index=result.index)
    ).fillna("").astype(str).str.strip()
    explicit_cutoff = result.get(
        "BacktestDataCutoffDate", pd.Series("", index=result.index)
    ).fillna("").astype(str).str.strip()
    cutoff = explicit_cutoff.where(explicit_cutoff.ne(""), legacy_cutoff)
    run_cutoff = str((getattr(summary, "split_dates", {}) or {}).get("global_end") or "")
    cutoff = cutoff.where(cutoff.ne("") | ~requested, run_cutoff)
    result["BacktestDataCutoffDate"] = cutoff
    # Keep the old field as a compatibility alias, but make its exact meaning
    # explicit through the new canonical field and GUI label.
    result["BacktestLastEvaluatedDate"] = cutoff

    data_asof = pd.to_datetime(
        result.get("DataAsOf", pd.Series(pd.NaT, index=result.index)),
        errors="coerce",
    )
    cutoff_dates = pd.to_datetime(cutoff, errors="coerce")
    delayed_limit = max(0, int(BACKTEST_FRESHNESS_DELAYED_TRADING_DAYS))
    stale_limit = max(delayed_limit, int(BACKTEST_FRESHNESS_STALE_TRADING_DAYS))
    statuses: list[str] = []
    reasons: list[str] = []
    gaps: list[float] = []
    for is_requested, asof_value, cutoff_value in zip(
        requested, data_asof, cutoff_dates
    ):
        if not bool(is_requested):
            statuses.append("未请求")
            reasons.append("本标的不在本轮回测范围")
            gaps.append(np.nan)
            continue
        if pd.isna(cutoff_value):
            statuses.append("未知")
            reasons.append("未取得回测基准数据截止日")
            gaps.append(np.nan)
            continue
        if pd.isna(asof_value):
            statuses.append("未知")
            reasons.append("行情数据日期缺失，无法判断回测时效")
            gaps.append(np.nan)
            continue
        cutoff_day = pd.Timestamp(cutoff_value).date()
        asof_day = pd.Timestamp(asof_value).date()
        if cutoff_day > asof_day:
            statuses.append("异常")
            reasons.append(
                f"回测基准数据截止日 {cutoff_day.isoformat()} 晚于行情日期 {asof_day.isoformat()}"
            )
            gaps.append(0.0)
            continue
        gap = _trading_days_between(cutoff_day, asof_day)
        gaps.append(float(gap))
        if gap <= delayed_limit:
            statuses.append("同步")
            reasons.append(
                f"回测基准数据截至 {cutoff_day.isoformat()}，与行情日期相差 {gap} 个交易日"
            )
        elif gap <= stale_limit:
            statuses.append("延迟")
            reasons.append(
                f"回测基准数据比行情日期落后 {gap} 个交易日，仅作历史校准"
            )
        else:
            statuses.append("过期")
            reasons.append(
                f"回测基准数据比行情日期落后 {gap} 个交易日，请刷新基准缓存后重跑"
            )
    result["BacktestFreshnessTradingDays"] = gaps
    result["BacktestFreshnessStatus"] = statuses
    result["BacktestFreshnessReason"] = reasons
    return result

def _decision_quality_multiplier(
    frame: pd.DataFrame,
    *,
    is_etf: pd.Series,
    quality_available: pd.Series,
) -> pd.Series:
    """Reproduce Fundamental Gate multiplier semantics after backtesting."""
    holding_status = (
        frame.get("InstitutionHoldingStatus", pd.Series("", index=frame.index))
        .fillna("")
        .astype(str)
        .str.upper()
    )
    quality_applicable = (
        frame.get("QualityApplicable", pd.Series(~is_etf, index=frame.index))
        .astype(str)
        .str.strip()
        .str.lower()
        .isin({"true", "1", "yes", "y", "是"})
        & ~is_etf
    )
    quality_gate = (
        frame.get("QualityGate", pd.Series(True, index=frame.index))
        .astype(str)
        .str.strip()
        .str.lower()
        .isin({"true", "1", "yes", "y", "是"})
    )
    if "QualityHardDataComplete" in frame:
        hard_data_complete = (
            frame["QualityHardDataComplete"]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"true", "1", "yes", "y", "是"})
        )
    else:
        quality_profile = (
            frame.get("QualityProfile", pd.Series("GENERAL", index=frame.index))
            .fillna("GENERAL")
            .astype(str)
            .str.upper()
        )
        roe_available = pd.to_numeric(
            frame.get("ROE", pd.Series(np.nan, index=frame.index)),
            errors="coerce",
        ).notna()
        profit_available = pd.concat(
            [
                pd.to_numeric(
                    frame.get(column, pd.Series(np.nan, index=frame.index)),
                    errors="coerce",
                ).notna()
                for column in ("NetProfitY1", "NetProfitY2", "NetProfitY3")
            ],
            axis=1,
        ).all(axis=1)
        margin_available = pd.to_numeric(
            frame.get(
                "IndustryGrossMarginPercentile",
                pd.Series(np.nan, index=frame.index),
            ),
            errors="coerce",
        ).notna()
        margin_required = ~quality_profile.isin(
            {"FINANCIAL", "DEFENSIVE", "ETF"}
        )
        hard_data_complete = (
            roe_available
            & profit_available
            & (~margin_required | margin_available)
        )
    hard_gate_fail = quality_applicable & ~quality_gate
    quality_uncertain = quality_applicable & (
        ~quality_available | ~hard_data_complete | holding_status.ne("PASS")
    )
    return pd.Series(
        np.select(
            [~quality_applicable, hard_gate_fail, quality_uncertain],
            [1.0, QUALITY_MULTIPLIER_FAIL, QUALITY_MULTIPLIER_UNKNOWN],
            default=QUALITY_MULTIPLIER_PASS,
        ),
        index=frame.index,
        dtype=float,
    )


def apply_backtest_ranking(summary: BacktestSummary, top_n: int = 50) -> None:
    path = OUTPUT_DIR / "AllResults.csv"
    if not path.exists() or not summary.by_ticker:
        return
    frame = pd.read_csv(path, encoding="utf-8-sig", low_memory=False).copy()
    original_mode = str(summary.mode or "").strip().lower()
    summary.fast_screen_ticker_count = int(getattr(summary, "ticker_count", 0) or 0)
    summary.exact_refinement_count = 0
    summary.exact_refinement_tickers = []
    summary.exact_refinement_elapsed_seconds = 0.0
    summary.exact_worker_count = 0
    if original_mode == "fast" and BACKTEST_AUTO_EXACT_REFINEMENT:
        fast_rows = list(summary.by_ticker or [])
        pool = _select_exact_refinement_pool(frame, fast_rows, top_n=top_n)
        refine_tickers = pool.get("Ticker", pd.Series(dtype=str)).dropna().astype(str).tolist()
        if refine_tickers:
            logger.info(
                "FAST screen complete; exact-refining %d evidence-qualified candidates "
                "(min FAST samples=%d, cap=%d).",
                len(refine_tickers),
                _minimum_fast_samples_for_exact_refinement(),
                min(int(BACKTEST_EXACT_REFINEMENT_CANDIDATES), max(1, int(top_n))),
            )
            exact = run_historical_backtest(
                refine_tickers, source="tickflow", objective=summary.objective,
                benchmark=summary.benchmark, commission=summary.commission,
                stamp_duty=summary.stamp_duty, slippage=summary.slippage,
                test_ratio=summary.test_ratio, validation_ratio=summary.validation_ratio,
                mode="exact",
            )
            exact_rows = list(exact.by_ticker or [])
            summary.exact_refinement_count = len(refine_tickers)
            summary.exact_refinement_tickers = list(refine_tickers)
            summary.exact_refinement_elapsed_seconds = float(getattr(exact, "elapsed_seconds", 0.0) or 0.0)
            summary.exact_worker_count = int(getattr(exact, "worker_count", 0) or 0)
            summary.elapsed_seconds = float(getattr(summary, "elapsed_seconds", 0.0) or 0.0) + summary.exact_refinement_elapsed_seconds
            summary.cache_hits = int(getattr(summary, "cache_hits", 0) or 0) + int(getattr(exact, "cache_hits", 0) or 0)
            summary.cache_hit_tickers = sorted(
                set(getattr(summary, "cache_hit_tickers", []) or [])
                | set(getattr(exact, "cache_hit_tickers", []) or [])
            )
            exact_keys = {(str(row.get("ticker", "")), str(row.get("entry_signal", "")).upper()) for row in exact_rows}
            current_signal = dict(zip(pool["Ticker"].astype(str), pool.get("EntrySignal", pd.Series("UNKNOWN", index=pool.index)).fillna("UNKNOWN").astype(str).str.upper()))
            for ticker in refine_tickers:
                key = (ticker, current_signal.get(ticker, "UNKNOWN"))
                if key not in exact_keys:
                    exact_rows.append({
                        "ticker": ticker, "entry_signal": key[1], "samples": 0,
                        "effective_samples": 0.0, "backtest_score": BACKTEST_NEUTRAL_SCORE,
                        "backtest_mode": "EXACT", "backtest_cache_hit": False,
                        "backtest_last_evaluated_date": exact.split_dates.get("global_end") or "",
                        "backtest_data_cutoff_date": exact.split_dates.get("global_end") or "",
                        "backtest_last_mature_signal_date": "",
                        "backtest_engine": exact.engine, "backtest_stage": "EXACT_REFINEMENT",
                    })
            for row in exact_rows:
                row["backtest_stage"] = "EXACT_REFINEMENT"
            fast_rows = list(summary.by_ticker or [])
            adjusted_global, bridge_metadata = bridge_global_calibration(
                summary.global_calibration,
                fast_rows,
                exact_rows,
                min_samples=BACKTEST_MIN_SAMPLES_FOR_RANKING,
            )
            summary.global_calibration = adjusted_global
            summary.fast_exact_bridge = bridge_metadata
            for row in fast_rows:
                row.setdefault("backtest_stage", "FAST_SCREEN")
            refined_tickers = set(refine_tickers)
            combined = [row for row in fast_rows if str(row.get("ticker", "")) not in refined_tickers]
            combined.extend(exact_rows)
            summary.by_ticker = combined
            summary.mode = "hybrid"
            summary.engine = f"{summary.engine}+exact:{exact.engine}"
            # Full-market peer calibration is retained, but the overlapping
            # FAST/EXACT candidates now estimate a bounded bridge correction so
            # the global prior is closer to the exact execution distribution.

    postprocess_started = time.perf_counter()

    current_institutional_score = pd.to_numeric(
        frame.get("InstitutionalScore", pd.Series(np.nan, index=frame.index)),
        errors="coerce",
    ).replace([np.inf, -np.inf], np.nan)
    stored_institutional_score = pd.to_numeric(
        frame.get(
            "PreBacktestInstitutionalScore",
            pd.Series(np.nan, index=frame.index),
        ),
        errors="coerce",
    ).replace([np.inf, -np.inf], np.nan)
    prior_institutional_score = stored_institutional_score.where(
        stored_institutional_score.notna(), current_institutional_score
    )
    # Preserve the pre-calibration anchor so repeated post-processing is
    # numerically idempotent instead of compounding the prior run's multiplier.
    frame["PreBacktestInstitutionalScore"] = prior_institutional_score.round(4)
    metric_columns = {
        "samples": "BacktestSamples",
        "effective_samples": "BacktestEffectiveSamples",
        "win_rate_20d": "BacktestWinRate20D",
        "win_rate_60d": "BacktestWinRate60D",
        "net_excess_win_rate_20d": "BacktestNetExcessWinRate20D",
        "net_excess_win_rate_60d": "BacktestNetExcessWinRate60D",
        "average_return_20d": "BacktestAverageReturn20D",
        "average_return_60d": "BacktestAverageReturn60D",
        "average_net_excess_return_20d": "BacktestAverageNetExcessReturn20D",
        "average_net_excess_return_60d": "BacktestAverageNetExcessReturn60D",
        "median_return_20d": "BacktestMedianReturn20D",
        "median_return_60d": "BacktestMedianReturn60D",
        "max_drawdown_20d": "BacktestMaxDrawdown20D",
        "max_drawdown_60d": "BacktestMaxDrawdown60D",
        "profit_factor": "BacktestProfitFactor",
        "net_excess_profit_factor": "BacktestNetExcessProfitFactor",
        "signal_span_days": "BacktestSignalSpanDays",
        "return_std_20d": "BacktestReturnStd20D",
        "target_std_20d": "BacktestTargetStd20D",
        "objective_value": "BacktestObjectiveValue",
        "backtest_score": "BacktestScore",
        "backtest_reliability": "BacktestReliability",
        "backtest_effective_weight": "BacktestEffectiveWeight",
        "backtest_confidence_tier": "BacktestConfidenceTier",
        "backtest_adjusted_score": "BacktestAdjustedScore",
        "failure_signal_factor": "FailureSignalFactor",
        "backtest_mode": "BacktestMode",
        "backtest_cache_hit": "BacktestCacheHit",
        "backtest_last_evaluated_date": "BacktestLastEvaluatedDate",
        "backtest_data_cutoff_date": "BacktestDataCutoffDate",
        "backtest_last_mature_signal_date": "BacktestLastMatureSignalDate",
        "backtest_engine": "BacktestEngine",
        "backtest_stage": "BacktestStage",
    }
    legacy_columns = {
        "backtest_score",
        "composite_score",
        "raw_objective_value",
        "raw_objective_value_x",
        "raw_objective_value_y",
        "samples",
        "win_rate_20d",
        "win_rate_60d",
        "average_return_20d",
        "average_return_60d",
        "BacktestScore",
        "CompositeScore",
        "BacktestSamples",
        "BacktestEffectiveSamples",
        "BacktestWinRate20D",
        "BacktestWinRate60D",
        "BacktestAverageReturn20D",
        "BacktestAverageReturn60D",
        "BacktestObjectiveValue",
        "BacktestRawObjectiveValue",
        "BacktestRawObjectiveValue_x",
        "BacktestRawObjectiveValue_y",
        "FailureSignalFactor",
        "FailureAdjustedScore",
        "SignalRecencyDays",
        "SignalRecencyFactor",
        "BacktestReliability",
        "BacktestEffectiveWeight",
        "BacktestConfidenceTier",
        "BacktestAdjustedScore",
        "BacktestMode",
        "BacktestCacheHit",
        "BacktestLastEvaluatedDate",
        "BacktestDataCutoffDate",
        "BacktestLastMatureSignalDate",
        "BacktestFreshnessTradingDays",
        "BacktestFreshnessStatus",
        "BacktestFreshnessReason",
        "BacktestEngine",
        "BacktestStatus",
        "BacktestStage",
        "BacktestRunMode",
        "BacktestRunEngine",
        "BacktestRequested",
        "BacktestEligibleForRanking",
        "BacktestSkipReason",
        "GlobalCalibrationScore",
        "GlobalCalibrationConfidence",
        "GlobalCalibrationLevel",
        "GlobalCalibrationSamples",
        "GlobalCalibrationEffectiveSamples",
        "GlobalCalibrationMeanExcess20D",
        "GlobalCalibrationWinRate20D",
        "GlobalCalibrationStartDate",
        "GlobalCalibrationEndDate",
        "InstitutionalTier",
        "InstitutionalPercentile",
        "InstitutionalRank",
        "InstitutionalTierReason",
        "RankingScore",
        "OverallRank",
        "RankingEligibility",
        "RankingReason",
    }
    legacy_columns.update(metric_columns.values())
    frame = frame.drop(
        columns=[column for column in frame.columns if column in legacy_columns],
        errors="ignore",
    )
    frame["EntrySignal"] = (
        frame.get("EntrySignal", pd.Series("AVOID", index=frame.index))
        .fillna("AVOID")
        .astype(str)
        .str.upper()
    )
    raw_metrics = pd.DataFrame(summary.by_ticker)
    if "entry_signal" not in raw_metrics:
        raw_metrics["entry_signal"] = "UNKNOWN"
    metrics = (
        raw_metrics.rename(
            columns={
                "ticker": "Ticker",
                "entry_signal": "EntrySignal",
                **metric_columns,
            }
        )
        .reindex(columns=["Ticker", "EntrySignal", *metric_columns.values()])
    )
    metrics = _expand_legacy_backtest_metrics(frame, metrics)
    frame = frame.merge(
        metrics,
        on=["Ticker", "EntrySignal"],
        how="left",
        validate="one_to_one",
    ).copy()
    for column in (
        "BacktestSamples",
        "BacktestEffectiveSamples",
        "BacktestScore",
        *metric_columns.values(),
    ):
        if column not in frame:
            frame[column] = np.nan

    observed = pd.to_numeric(frame["BacktestSamples"], errors="coerce").fillna(0.0)
    frame = _apply_backtest_provenance(frame, summary, observed)
    frame = _apply_backtest_freshness(frame, summary)
    observed = pd.to_numeric(frame["BacktestSamples"], errors="coerce").fillna(0.0)
    frame["BacktestCacheHit"] = frame.get(
        "BacktestCacheHit", pd.Series(False, index=frame.index)
    ).eq(True)
    effective_observed = (
        pd.to_numeric(frame["BacktestEffectiveSamples"], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
        .clip(lower=0.0)
    )
    effective_observed = effective_observed.where(
        effective_observed.gt(0.0), observed
    ).clip(upper=observed)
    frame["BacktestScore"] = pd.to_numeric(frame["BacktestScore"], errors="coerce")
    frame["BacktestObjectiveValue"] = pd.to_numeric(
        frame["BacktestObjectiveValue"], errors="coerce"
    )
    objective_values = frame["BacktestObjectiveValue"].where(
        np.isfinite(frame["BacktestObjectiveValue"]) & observed.gt(0)
    )
    if summary.objective == "max_drawdown":
        objective_rank = objective_values.rank(pct=True, ascending=True) * 100.0
    else:
        objective_rank = objective_values.rank(pct=True) * 100.0
    std20 = pd.to_numeric(
        frame.get(
            "BacktestTargetStd20D",
            frame.get(
                "BacktestReturnStd20D",
                pd.Series(np.nan, index=frame.index),
            ),
        ),
        errors="coerce",
    )
    evidence = [
        _backtest_evidence(
            int(samples),
            float(effective),
            float(std) if np.isfinite(std) else np.nan,
        )
        for samples, effective, std in zip(observed, effective_observed, std20)
    ]
    frame["BacktestReliability"] = [item[0] for item in evidence]
    frame["BacktestEffectiveWeight"] = [item[1] for item in evidence]
    frame["BacktestConfidenceTier"] = [item[2] for item in evidence]
    backtest_score = frame["BacktestScore"].where(
        np.isfinite(frame["BacktestScore"]), BACKTEST_NEUTRAL_SCORE
    )
    profit_factor = pd.to_numeric(
        frame.get(
            "BacktestNetExcessProfitFactor",
            frame.get("BacktestProfitFactor", pd.Series(np.nan, index=frame.index)),
        ),
        errors="coerce",
    )
    drawdown = pd.to_numeric(frame["BacktestMaxDrawdown60D"], errors="coerce")
    profit_factor_score = (
        profit_factor.clip(lower=0.0, upper=3.0) / 3.0 * 100.0
    ).fillna(50.0)
    drawdown_score = (
        100.0 - drawdown.abs().clip(lower=0.0, upper=50.0) * 2.0
    ).fillna(50.0)
    objective_score = objective_rank.fillna(BACKTEST_NEUTRAL_SCORE)
    backtest_component = (
        backtest_score * 0.50
        + objective_score * 0.25
        + profit_factor_score * 0.15
        + drawdown_score * 0.10
    )
    reliability = pd.to_numeric(
        frame["BacktestReliability"], errors="coerce"
    ).fillna(0.0)
    calibration_started = time.perf_counter()
    calibration_details = calibration_details_for_frame(
        frame, getattr(summary, "global_calibration", None)
    )
    summary.calibration_lookup_elapsed_seconds = float(
        time.perf_counter() - calibration_started
    )
    peer_score = calibration_details["score"]
    peer_confidence = calibration_details["confidence"]
    stability = dict(getattr(summary, "calibration_stability", {}) or {})
    stability_multiplier = float(
        np.clip(stability.get("confidence_multiplier", 1.0), 0.0, 1.0)
    )
    peer_confidence = peer_confidence * stability_multiplier
    frame["GlobalCalibrationScore"] = peer_score.round(4)
    frame["GlobalCalibrationConfidence"] = peer_confidence.round(4)
    frame["GlobalCalibrationLevel"] = calibration_details["level"].astype(str)
    frame["GlobalCalibrationSamples"] = calibration_details["samples"].astype(int)
    frame["GlobalCalibrationEffectiveSamples"] = calibration_details["effective_samples"].round(4)
    frame["GlobalCalibrationMeanExcess20D"] = calibration_details["mean_net_excess20"].round(4)
    frame["GlobalCalibrationWinRate20D"] = calibration_details["win_rate_net_excess20"].round(4)
    frame["GlobalCalibrationStartDate"] = calibration_details["start_date"].astype(str)
    frame["GlobalCalibrationEndDate"] = calibration_details["end_date"].astype(str)
    frame["GlobalCalibrationStability"] = str(
        stability.get("status", "INSUFFICIENT_FOLDS")
    )
    frame["GlobalCalibrationFoldCount"] = int(stability.get("fold_count", 0) or 0)
    frame["GlobalCalibrationStableFoldRatio"] = float(
        stability.get("stable_fold_ratio", 0.0) or 0.0
    )
    frame["GlobalCalibrationICDrift"] = float(
        stability.get("recent_vs_mean_ic_drift", 0.0) or 0.0
    )
    peer_available = peer_confidence.gt(0.0)
    peer_anchor = peer_score.where(peer_available, BACKTEST_NEUTRAL_SCORE)
    frame["BacktestAdjustedScore"] = (
        peer_anchor + (backtest_component - peer_anchor) * reliability
    ).round(4)
    final_score = pd.to_numeric(
        frame.get("FinalScore", pd.Series(np.nan, index=frame.index)),
        errors="coerce",
    )
    raw_score = final_score.where(
        np.isfinite(final_score), pd.to_numeric(frame["Score"], errors="coerce")
    ).fillna(0.0)
    effective_weight = pd.to_numeric(
        frame["BacktestEffectiveWeight"], errors="coerce"
    ).fillna(0.0)
    if peer_available.any():
        peer_weight = (peer_confidence * float(GLOBAL_CALIBRATION_MAX_WEIGHT)).clip(0.0, GLOBAL_CALIBRATION_MAX_WEIGHT)
        effective_weight = pd.Series(
            np.maximum(effective_weight.to_numpy(dtype=float), peer_weight.to_numpy(dtype=float)),
            index=frame.index,
        )
        frame["BacktestEffectiveWeight"] = effective_weight.round(4)
    frame["CompositeScore"] = (
        raw_score * (1.0 - effective_weight)
        + frame["BacktestAdjustedScore"] * effective_weight
    ).round(4)
    frame["FailureSignalFactor"] = pd.to_numeric(
        frame["FailureSignalFactor"], errors="coerce"
    ).fillna(1.0)
    frame.loc[
        observed.lt(BACKTEST_MIN_SAMPLES_FOR_RANKING), "FailureSignalFactor"
    ] = 1.0
    frame["FailureAdjustedScore"] = (
        frame["CompositeScore"] * (0.7 + 0.3 * frame["FailureSignalFactor"])
    ).round(4)

    if "SectorConfirmationFactor" in frame:
        sector_factor = pd.to_numeric(
            frame["SectorConfirmationFactor"], errors="coerce"
        ).fillna(1.0)
    else:
        sector_factor = pd.Series(1.0, index=frame.index)
    sector_text = frame.get("Sector", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip()
    industry_text = frame.get("Industry", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip()
    classified = sector_text.ne("") | industry_text.ne("")
    sector_factor = sector_factor.where(classified, 1.0).clip(0.0, 1.0)
    frame["SectorConfirmationFactor"] = sector_factor.round(4)
    if "IndustryRelativeStrength" in frame:
        frame.loc[~classified, "IndustryRelativeStrength"] = np.nan
    if "IndustryMomentum60D" in frame:
        frame.loc[~classified, "IndustryMomentum60D"] = np.nan
    sector_multiplier = 0.7 + 0.3 * sector_factor
    signal_start = pd.to_datetime(
        frame.get("SignalStartDate", pd.Series(pd.NaT, index=frame.index)),
        errors="coerce",
    )
    data_asof = pd.to_datetime(
        frame.get("DataAsOf", pd.Series(pd.NaT, index=frame.index)),
        errors="coerce",
    )
    recency_days = (data_asof - signal_start).dt.days
    valid_recency = recency_days.notna() & recency_days.ge(0)
    frame["SignalRecencyDays"] = recency_days.where(valid_recency)
    frame["SignalRecencyFactor"] = np.where(
        valid_recency,
        np.maximum(0.7, 1.0 - recency_days / 100.0),
        1.0,
    )
    recency_multiplier = 0.8 + 0.2 * frame["SignalRecencyFactor"]
    breakout_factor = pd.to_numeric(
        frame.get("BreakoutQualityFactor", pd.Series(1.0, index=frame.index)),
        errors="coerce",
    ).fillna(1.0).clip(0.0, 1.0)
    frame["BreakoutQualityFactor"] = breakout_factor
    breakout_state = frame["EntrySignal"].isin(
        {"BREAKOUT_CONFIRM", "PRICE_BREAKOUT", "WAIT_VOLUME_CONFIRM"}
    )
    effective_breakout_factor = breakout_factor.where(breakout_state, 1.0)
    institutional_component = (
        frame["FailureAdjustedScore"]
        * sector_multiplier
        * recency_multiplier
        * (0.8 + 0.2 * effective_breakout_factor)
    )
    frame["TechnicalInstitutionalScore"] = institutional_component.round(4)
    quality_score = pd.to_numeric(
        frame.get("QualityScore", pd.Series(np.nan, index=frame.index)),
        errors="coerce",
    )
    quality_available = (
        frame.get("QualityDataAvailable", pd.Series(False, index=frame.index))
        .astype(str)
        .str.strip()
        .str.lower()
        .isin({"true", "1", "yes", "y", "是"})
    )
    is_etf = (
        frame.get("IsETF", pd.Series(False, index=frame.index))
        .astype(str)
        .str.strip()
        .str.lower()
        .isin({"true", "1", "yes", "y", "是"})
    )
    quality_eligible = quality_available & np.isfinite(quality_score) & ~is_etf
    frame["QualityMultiplier"] = _decision_quality_multiplier(
        frame,
        is_etf=is_etf,
        quality_available=quality_available,
    )
    quality_weight = float(np.clip(MODEL_QUALITY_WEIGHT, 0.0, 0.5))
    legacy_institutional = pd.Series(
        np.where(
            quality_eligible,
            institutional_component * (1.0 - quality_weight)
            + quality_score * quality_weight,
            institutional_component,
        ),
        index=frame.index,
    )
    raw_reference = raw_score.replace(0.0, np.nan)
    calibration_ratio = (
        pd.to_numeric(frame["FailureAdjustedScore"], errors="coerce") / raw_reference
    ).replace([np.inf, -np.inf], np.nan).fillna(1.0).clip(0.70, 1.30)
    single_quality_score = prior_institutional_score * calibration_ratio * recency_multiplier
    frame["InstitutionalScore"] = single_quality_score.where(
        prior_institutional_score.notna(), legacy_institutional
    ).round(4)

    requested_mask = frame.get(
        "BacktestRequested", pd.Series(False, index=frame.index)
    ).fillna(False).astype(bool)
    requested_count = int(requested_mask.sum())
    sample_count = int(
        frame.loc[
            requested_mask
            & pd.to_numeric(frame["BacktestSamples"], errors="coerce").fillna(0).gt(0),
            "Ticker",
        ].astype(str).nunique()
    )
    ranking_eligible_count = int(
        frame.loc[
            requested_mask
            & frame.get(
                "BacktestEligibleForRanking", pd.Series(False, index=frame.index)
            ).fillna(False).astype(bool),
            "Ticker",
        ].astype(str).nunique()
    )
    summary.signal_sample_ticker_count = sample_count
    summary.no_signal_ticker_count = max(0, requested_count - sample_count)
    summary.ranking_eligible_ticker_count = ranking_eligible_count
    summary.total_ticker_evaluations = int(getattr(summary, "fast_screen_ticker_count", 0) or 0) + int(getattr(summary, "exact_refinement_count", 0) or 0)
    summary.cache_hit_rate = round(
        float(getattr(summary, "cache_hits", 0) or 0)
        / max(1, int(summary.total_ticker_evaluations or requested_count)),
        4,
    )

    frame = finalize_signal_ranking(frame)
    summary.ranking_compute_elapsed_seconds = float(
        time.perf_counter() - postprocess_started
    )
    persistence_started = time.perf_counter()
    from report import _atomic_write_csv, _atomic_write_parquet, refresh_candidate_exports

    _atomic_write_csv(frame, path)
    refresh_candidate_exports(frame, top_n_csv=top_n, output_dir=OUTPUT_DIR)
    _atomic_write_parquet(frame, OUTPUT_DIR / "AllResults.parquet")
    summary.persistence_elapsed_seconds = float(
        time.perf_counter() - persistence_started
    )
    summary.postprocess_elapsed_seconds = float(
        summary.ranking_compute_elapsed_seconds + summary.persistence_elapsed_seconds
    )
    logger.info(
        "Backtest postprocess: calibration=%.2fs, ranking=%.2fs, persistence=%.2fs, total=%.2fs.",
        summary.calibration_lookup_elapsed_seconds,
        summary.ranking_compute_elapsed_seconds,
        summary.persistence_elapsed_seconds,
        summary.postprocess_elapsed_seconds,
    )


def _spearman(frame: pd.DataFrame, target: str) -> float:
    columns = ["score", target]
    if "sample_weight" in frame.columns:
        columns.append("sample_weight")
    data = frame[columns].replace([np.inf, -np.inf], np.nan).dropna(
        subset=["score", target]
    )
    if (
        len(data) < 2
        or data["score"].nunique() < 2
        or data[target].nunique() < 2
    ):
        return 0.0
    left = data["score"].rank(method="average").to_numpy(dtype=np.float64)
    right = data[target].rank(method="average").to_numpy(dtype=np.float64)
    weights = pd.to_numeric(
        data.get("sample_weight", pd.Series(1.0, index=data.index)),
        errors="coerce",
    ).fillna(0.0).clip(lower=0.0).to_numpy(dtype=np.float64)
    total = float(weights.sum())
    if total <= 0.0:
        return 0.0
    left_mean = float(np.dot(left, weights) / total)
    right_mean = float(np.dot(right, weights) / total)
    left_centered = left - left_mean
    right_centered = right - right_mean
    denominator = float(
        np.sqrt(
            np.dot(left_centered**2, weights)
            * np.dot(right_centered**2, weights)
        )
    )
    if denominator <= 0.0:
        return 0.0
    value = float(np.dot(left_centered * right_centered, weights) / denominator)
    return value if np.isfinite(value) else 0.0


def _max_drawdown(values: pd.Series) -> float:
    clean = values.dropna()
    if clean.empty:
        return 0.0
    curve = (1 + clean / 100).cumprod()
    return float(((curve / curve.cummax()) - 1).min() * 100)


def _entry_date_equal_weight_stats(sample_frame: pd.DataFrame) -> dict[str, Any]:
    if sample_frame.empty:
        return {"entry_dates": 0, "samples": 0}
    numeric_columns = [
        "return20",
        "return60",
        "benchmark_return20",
        "benchmark_return60",
        "net_return20",
        "net_return60",
        "drawdown20",
        "drawdown60",
    ]
    daily = sample_frame.groupby("entry_date", sort=True)[numeric_columns].mean()
    daily["excess20"] = daily["return20"] - daily["benchmark_return20"]
    daily["excess60"] = daily["return60"] - daily["benchmark_return60"]
    return {
        "entry_dates": len(daily),
        "samples": len(sample_frame),
        "average_return_20d": float(daily["return20"].mean()),
        "average_return_60d": float(daily["return60"].mean()),
        "average_benchmark_return_20d": float(daily["benchmark_return20"].mean()),
        "average_benchmark_return_60d": float(daily["benchmark_return60"].mean()),
        "average_excess_return_20d": float(daily["excess20"].mean()),
        "average_excess_return_60d": float(daily["excess60"].mean()),
        "average_net_return_20d": float(daily["net_return20"].mean()),
        "average_net_return_60d": float(daily["net_return60"].mean()),
        "maximum_drawdown_20d": float(daily["drawdown20"].min()),
        "maximum_drawdown_60d": float(daily["drawdown60"].min()),
    }


def _bucket_rows(sample_frame: pd.DataFrame) -> list[dict[str, Any]]:
    if sample_frame["score"].nunique() < 2:
        return []
    frame = sample_frame.copy()
    frame["bucket"] = pd.qcut(
        frame["score"], q=5, labels=False, duplicates="drop"
    )
    rows = []
    for bucket, group in frame.groupby("bucket", dropna=True):
        weights = pd.to_numeric(
            group.get("sample_weight", pd.Series(1.0, index=group.index)),
            errors="coerce",
        ).fillna(0.0).clip(lower=0.0)
        excess20 = group["return20"] - group["benchmark_return20"]
        excess60 = group["return60"] - group["benchmark_return60"]
        net_excess20 = group["net_return20"] - group["benchmark_return20"]
        net_excess60 = group["net_return60"] - group["benchmark_return60"]
        rows.append(
            {
                "bucket": int(bucket) + 1,
                "samples": len(group),
                "effective_samples": round(float(weights.sum()), 4),
                "average_return20": round(
                    _weighted_mean(group["return20"], weights), 4
                ),
                "average_return60": round(
                    _weighted_mean(group["return60"], weights), 4
                ),
                "average_benchmark_return20": round(
                    _weighted_mean(group["benchmark_return20"], weights), 4
                ),
                "average_benchmark_return60": round(
                    _weighted_mean(group["benchmark_return60"], weights), 4
                ),
                "average_excess_return20": round(
                    _weighted_mean(excess20, weights),
                    4,
                ),
                "average_excess_return60": round(
                    _weighted_mean(excess60, weights),
                    4,
                ),
                "average_net_return20": round(
                    _weighted_mean(group["net_return20"], weights), 4
                ),
                "average_net_return60": round(
                    _weighted_mean(group["net_return60"], weights), 4
                ),
                "average_net_excess_return20": round(
                    _weighted_mean(net_excess20, weights), 4
                ),
                "average_net_excess_return60": round(
                    _weighted_mean(net_excess60, weights), 4
                ),
            }
        )
    return rows


_BACKTEST_WORKER_CONTEXT: dict[str, Any] = {}


def _format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _adaptive_worker_count(
    total: int,
    requested: int | None,
    profile: BacktestExecutionProfile,
) -> int:
    cpu_limit = max(1, (os.cpu_count() or 2) - 1)
    hard_limit = min(max(1, int(BACKTEST_MAX_PROCESSES)), cpu_limit, max(1, total))
    if requested is not None:
        return min(hard_limit, max(1, int(requested)))
    if total <= 1:
        return 1
    # Small batches now use threads instead of being artificially serialized;
    # larger CPU-heavy batches still switch to isolated worker processes.
    utilization = 0.90 if profile.name == "fast" else 0.80
    target = max(2, round(cpu_limit * utilization))
    return min(hard_limit, target, max(1, total))


def _init_backtest_worker(
    source: str,
    benchmark: str,
    commission: float,
    stamp_duty: float,
    slippage: float,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None],
    benchmark_signature: str,
    profile: BacktestExecutionProfile,
) -> None:
    global _BACKTEST_WORKER_CONTEXT
    benchmark_frame = _load_cache(BENCHMARKS[benchmark], source)
    _BACKTEST_WORKER_CONTEXT = {
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


def _backtest_chunk_worker(
    tickers: list[str],
) -> tuple[pd.DataFrame, int, list[str], list[tuple[str, str]], int]:
    context = _BACKTEST_WORKER_CONTEXT
    frames: list[pd.DataFrame] = []
    cache_hits = 0
    cache_hit_tickers: list[str] = []
    errors: list[tuple[str, str]] = []
    for ticker in tickers:
        try:
            ticker_samples, cache_hit = _backtest_one_ticker_cached(
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
            if ticker_samples:
                frames.append(pd.DataFrame.from_records(ticker_samples))
            cache_hits += int(cache_hit)
            if cache_hit:
                cache_hit_tickers.append(str(ticker))
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            errors.append((ticker, str(exc)))
    batch = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return batch, cache_hits, cache_hit_tickers, errors, len(tickers)


def run_historical_backtest(
    tickers: list[str],
    source: str = "eastmoney",
    objective: str = "net_excess_return_20d",
    benchmark: str = "沪深300",
    commission: float = BACKTEST_STOCK_COMMISSION_RATE,
    stamp_duty: float = 0.0005,
    slippage: float = 0.001,
    test_ratio: float = 0.2,
    validation_ratio: float = 0.2,
    workers: int | None = None,
    mode: str = "auto",
) -> BacktestSummary:
    if objective not in {
        "return_20d",
        "return_60d",
        "excess_return_20d",
        "excess_return_60d",
        "net_excess_return_20d",
        "net_excess_return_60d",
        "max_drawdown",
        "risk_adjusted",
    }:
        raise ValueError(f"unsupported objective: {objective}")
    if benchmark not in BENCHMARKS:
        raise ValueError(f"unsupported benchmark: {benchmark}")
    test_ratio = float(np.clip(test_ratio, 0.0, 0.9))
    validation_ratio = float(np.clip(validation_ratio, 0.0, 0.9 - test_ratio))
    benchmark_frame = _load_benchmark_frames(source).get(benchmark)
    benchmark_dates = pd.DatetimeIndex([])
    if benchmark_frame is not None and not benchmark_frame.empty:
        benchmark_dates = (
            pd.DatetimeIndex(benchmark_frame.index).dropna().sort_values().unique()
        )
    global_start = pd.Timestamp(benchmark_dates[0]) if len(benchmark_dates) else None
    global_end = pd.Timestamp(benchmark_dates[-1]) if len(benchmark_dates) else None
    if BACKTEST_VALIDATION_END or BACKTEST_TEST_START:
        validation_end = (
            pd.Timestamp(BACKTEST_VALIDATION_END)
            if BACKTEST_VALIDATION_END
            else None
        )
        test_start = pd.Timestamp(BACKTEST_TEST_START) if BACKTEST_TEST_START else None
    elif len(benchmark_dates):
        validation_index = int(
            len(benchmark_dates) * (1.0 - test_ratio - validation_ratio)
        )
        test_index = int(len(benchmark_dates) * (1.0 - test_ratio))
        validation_end = (
            pd.Timestamp(benchmark_dates[validation_index])
            if validation_ratio
            else None
        )
        test_start = pd.Timestamp(benchmark_dates[test_index]) if test_ratio else None
    else:
        validation_end = test_start = None
        if benchmark_frame is None or benchmark_frame.empty:
            summary = BacktestSummary(
                ticker_count=len(dict.fromkeys(tickers)),
                objective=objective,
                benchmark=benchmark,
                commission=commission,
                stamp_duty=stamp_duty,
                slippage=slippage,
                cost_parameters={
                    "stock_commission": commission,
                    "etf_commission": BACKTEST_ETF_COMMISSION_RATE,
                    "stamp_duty": stamp_duty,
                    "slippage": slippage,
                    "assumed_trade_notional": BACKTEST_ASSUMED_TRADE_NOTIONAL,
                },
                test_ratio=test_ratio,
                validation_ratio=validation_ratio,
                error=f"无法加载基准数据：{benchmark}，无法建立回测时间切分",
            )
            summary.insufficient_test_data = True
            return summary

    unique_tickers = list(dict.fromkeys(tickers))
    total = len(unique_tickers)
    profile = _resolve_backtest_profile(mode, total)
    sample_batches: list[pd.DataFrame] = []
    sample_count = 0
    worker_count = _adaptive_worker_count(total, workers, profile)
    use_process_pool = bool(
        total >= int(BACKTEST_PROCESS_MIN_TICKERS) and worker_count > 1
    )
    use_thread_pool = bool(
        1 < total < int(BACKTEST_PROCESS_MIN_TICKERS) and worker_count > 1
    )
    engine = "process" if use_process_pool else "thread" if use_thread_pool else "sequential"
    benchmark_signature = "state-v5"
    completed = 0
    cache_hits = 0
    cache_hit_tickers: set[str] = set()
    next_progress = max(1, int(BACKTEST_PROGRESS_INTERVAL))
    backtest_started = time.perf_counter()

    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

    logger.info(
        "Backtest engine: %s, mode=%s, workers=%d, chunk=%d, persistent cache=%s.",
        engine,
        profile.name.upper(),
        worker_count,
        profile.chunk_size,
        "on" if BACKTEST_CACHE_ENABLED else "off",
    )

    def record_progress(
        batch_frame: pd.DataFrame,
        batch_completed: int,
        batch_cache_hits: int,
        batch_cache_hit_tickers: list[str] | None = None,
    ) -> None:
        nonlocal completed, cache_hits, next_progress, sample_count
        if batch_frame is not None and not batch_frame.empty:
            sample_batches.append(batch_frame)
            sample_count += len(batch_frame)
        completed += int(batch_completed)
        cache_hits += int(batch_cache_hits)
        if batch_cache_hit_tickers:
            cache_hit_tickers.update(str(ticker) for ticker in batch_cache_hit_tickers)
        if completed >= next_progress or completed >= total:
            elapsed = max(time.perf_counter() - backtest_started, 1e-9)
            rate = completed / elapsed
            remaining = max(0, total - completed)
            eta = remaining / rate if rate > 0 else 0.0
            percent = completed / max(total, 1) * 100.0
            logger.info(
                "Backtesting progress: %d/%d tickers, %d samples. %.1f%% | mode=%s | cache=%d | elapsed=%s | ETA=%s | rate=%.2f ticker/s",
                completed,
                total,
                sample_count,
                percent,
                profile.name.upper(),
                cache_hits,
                _format_duration(elapsed),
                _format_duration(eta),
                rate,
            )
            interval = max(1, int(BACKTEST_PROGRESS_INTERVAL))
            next_progress = ((completed // interval) + 1) * interval

    if use_process_pool:
        chunk_size = max(1, int(profile.chunk_size))
        chunks = [
            unique_tickers[start : start + chunk_size]
            for start in range(0, total, chunk_size)
        ]
        with ProcessPoolExecutor(
            max_workers=worker_count,
            initializer=_init_backtest_worker,
            initargs=(
                source,
                benchmark,
                commission,
                stamp_duty,
                slippage,
                (validation_end, test_start),
                benchmark_signature,
                profile,
            ),
        ) as executor:
            futures = {
                executor.submit(_backtest_chunk_worker, chunk): chunk for chunk in chunks
            }
            for future in as_completed(futures):
                chunk = futures[future]
                try:
                    batch_frame, batch_hits, batch_hit_tickers, errors, batch_count = future.result()
                except Exception as exc:
                    logger.exception("Backtest worker chunk failed: %s", exc)
                    record_progress(pd.DataFrame(), len(chunk), 0, [])
                    continue
                for ticker, error in errors:
                    logger.warning("Backtest failed for %s: %s", ticker, error)
                record_progress(batch_frame, batch_count, batch_hits, batch_hit_tickers)
    elif use_thread_pool:
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="backtest",
        ) as executor:
            futures = {
                executor.submit(
                    _backtest_one_ticker_cached,
                    ticker,
                    source,
                    benchmark_frame,
                    commission,
                    stamp_duty,
                    slippage,
                    (validation_end, test_start),
                    benchmark_signature,
                    profile=profile,
                    benchmark_name=benchmark,
                ): ticker
                for ticker in unique_tickers
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    ticker_samples, cache_hit = future.result()
                except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
                    logger.warning("Backtest failed for %s: %s", ticker, exc)
                    ticker_samples, cache_hit = [], False
                batch_frame = (
                    pd.DataFrame.from_records(ticker_samples)
                    if ticker_samples
                    else pd.DataFrame()
                )
                record_progress(
                    batch_frame,
                    1,
                    int(cache_hit),
                    [str(ticker)] if cache_hit else [],
                )
    else:
        for ticker in unique_tickers:
            try:
                ticker_samples, cache_hit = _backtest_one_ticker_cached(
                    ticker,
                    source,
                    benchmark_frame,
                    commission,
                    stamp_duty,
                    slippage,
                    (validation_end, test_start),
                    benchmark_signature,
                    profile=profile,
                    benchmark_name=benchmark,
                )
            except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
                logger.warning("Backtest failed for %s: %s", ticker, exc)
                ticker_samples, cache_hit = [], False
            batch_frame = (
                pd.DataFrame.from_records(ticker_samples)
                if ticker_samples
                else pd.DataFrame()
            )
            record_progress(
                batch_frame,
                1,
                int(cache_hit),
                [str(ticker)] if cache_hit else [],
            )

    split_dates = {
        "global_start": global_start.strftime("%Y-%m-%d")
        if global_start is not None
        else None,
        "validation_end": validation_end.strftime("%Y-%m-%d")
        if validation_end is not None
        else None,
        # Compatibility keeps the historical ``validation_end`` field; its
        # actual semantics have always been the validation *start* boundary.
        "validation_start": validation_end.strftime("%Y-%m-%d")
        if validation_end is not None
        else None,
        "test_start": test_start.strftime("%Y-%m-%d")
        if test_start is not None
        else None,
        "global_end": global_end.strftime("%Y-%m-%d")
        if global_end is not None
        else None,
    }
    summary = BacktestSummary(
        ticker_count=len(dict.fromkeys(tickers)),
        mode=profile.name,
        objective=objective,
        benchmark=benchmark,
        commission=commission,
        stamp_duty=stamp_duty,
        slippage=slippage,
        cost_parameters={
            "stock_commission": commission,
            "etf_commission": BACKTEST_ETF_COMMISSION_RATE,
            "stamp_duty": stamp_duty,
            "slippage": slippage,
            "assumed_trade_notional": BACKTEST_ASSUMED_TRADE_NOTIONAL,
        },
        etf_commission=BACKTEST_ETF_COMMISSION_RATE,
        assumed_trade_notional=BACKTEST_ASSUMED_TRADE_NOTIONAL,
        test_ratio=test_ratio,
        validation_ratio=validation_ratio,
        split_dates=split_dates,
    )
    summary.point_in_time_universe = historical_universe_status()
    if bool(summary.point_in_time_universe.get("available", False)):
        summary.universe_type = "point_in_time_snapshots_plus_cache"
        summary.current_pool_selection_warning = (
            "已按可用历史快照过滤ST/非成分状态；快照覆盖区间外仍保留幸存者偏差提示"
        )
    summary.cache_hits = int(cache_hits)
    summary.cache_hit_tickers = sorted(cache_hit_tickers)
    summary.elapsed_seconds = float(time.perf_counter() - backtest_started)
    summary.worker_count = int(worker_count)
    summary.engine = engine
    if not sample_batches:
        summary.insufficient_test_data = True
        summary.error = "未生成有效回测样本"
    else:
        all_frame = pd.concat(sample_batches, ignore_index=True)
        summary.all_samples = len(all_frame)
        summary.purged_samples = int(all_frame["split"].eq("purged").sum())
        calibration_frame = all_frame.loc[all_frame["split"].isin(["train", "validation"])].copy()
        summary.global_calibration = build_global_calibration(
            calibration_frame, min_samples=GLOBAL_CALIBRATION_MIN_SAMPLES
        )
        summary.walk_forward = walk_forward_stats(all_frame)
        summary.calibration_stability = calibration_stability_stats(
            summary.walk_forward
        )
        component_calibration = calibrate_component_weights(all_frame)
        summary.component_calibration = component_calibration.to_dict()
        calibration_path = OUTPUT_DIR / "ScoreCalibration.json"
        try:
            calibration_path.write_text(
                json.dumps(summary.component_calibration, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            logger.warning("无法写入评分权重校准文件 %s", calibration_path)
        test_frame = all_frame[all_frame["split"] == "test"]
        if len(test_frame) < 2:
            summary.insufficient_test_data = True
            summary.error = f"测试集有效样本不足：{len(test_frame)}，至少需要2个样本"
            test_frame = all_frame.iloc[0:0]
        sample_frame = test_frame.replace([np.inf, -np.inf], np.nan)
        summary.samples = len(sample_frame)
        test_weights = pd.to_numeric(
            sample_frame.get(
                "sample_weight", pd.Series(1.0, index=sample_frame.index)
            ),
            errors="coerce",
        ).fillna(0.0).clip(lower=0.0)
        summary.win_rate_20d = _weighted_rate(
            sample_frame["return20"], test_weights
        )
        summary.win_rate_60d = _weighted_rate(
            sample_frame["return60"], test_weights
        )
        summary.average_return_20d = _weighted_mean(
            sample_frame["return20"], test_weights
        )
        summary.average_return_60d = _weighted_mean(
            sample_frame["return60"], test_weights
        )
        summary.median_return_20d = float(sample_frame["return20"].median())
        summary.median_return_60d = float(sample_frame["return60"].median())
        summary.average_benchmark_return_20d = _weighted_mean(
            sample_frame["benchmark_return20"], test_weights
        )
        summary.average_benchmark_return_60d = _weighted_mean(
            sample_frame["benchmark_return60"], test_weights
        )
        summary.average_net_return_20d = _weighted_mean(
            sample_frame["net_return20"], test_weights
        )
        summary.average_net_return_60d = _weighted_mean(
            sample_frame["net_return60"], test_weights
        )
        sample_frame["excess20"] = (
            sample_frame["return20"] - sample_frame["benchmark_return20"]
        )
        sample_frame["excess60"] = (
            sample_frame["return60"] - sample_frame["benchmark_return60"]
        )
        sample_frame["net_excess20"] = (
            sample_frame["net_return20"] - sample_frame["benchmark_return20"]
        )
        sample_frame["net_excess60"] = (
            sample_frame["net_return60"] - sample_frame["benchmark_return60"]
        )
        summary.average_net_excess_return_20d = _weighted_mean(
            sample_frame["net_excess20"], test_weights
        )
        summary.average_net_excess_return_60d = _weighted_mean(
            sample_frame["net_excess60"], test_weights
        )
        summary.median_net_excess_return_20d = float(
            sample_frame["net_excess20"].median()
        )
        summary.median_net_excess_return_60d = float(
            sample_frame["net_excess60"].median()
        )
        summary.maximum_drawdown_20d = float(sample_frame["drawdown20"].min())
        summary.maximum_drawdown_60d = float(sample_frame["drawdown60"].min())
        sample_frame["risk_adjusted"] = sample_frame["net_return20"] / sample_frame[
            "drawdown20"
        ].abs().replace(0, np.nan)
        summary.rank_ic_20d = _spearman(sample_frame, "return20")
        summary.rank_ic_60d = _spearman(sample_frame, "return60")
        summary.by_ticker = _ticker_backtest_rows(sample_frame, objective)
        last_evaluated = split_dates.get("global_end") or ""
        for row in summary.by_ticker:
            row["backtest_mode"] = profile.name.upper()
            row["backtest_cache_hit"] = str(row.get("ticker", "")) in cache_hit_tickers
            row["backtest_last_evaluated_date"] = last_evaluated
            row["backtest_data_cutoff_date"] = last_evaluated
            row["backtest_engine"] = engine
            row["backtest_stage"] = "FAST_SCREEN" if profile.name == "fast" else "EXACT"
        summary.by_score_bucket = _bucket_rows(sample_frame)
        if summary.by_score_bucket:
            summary.monotonicity_high_low_20d = (
                summary.by_score_bucket[-1]["average_net_excess_return20"]
                - summary.by_score_bucket[0]["average_net_excess_return20"]
            )
            summary.monotonicity_high_low_60d = (
                summary.by_score_bucket[-1]["average_net_excess_return60"]
                - summary.by_score_bucket[0]["average_net_excess_return60"]
            )
        target_definitions = {
            "return_20d": "入场日开盘价至第20个交易日后收盘价的平均收益率，越高越好",
            "return_60d": "入场日开盘价至第60个交易日后收盘价的平均收益率，越高越好",
            "excess_return_20d": "相对基准的20个交易日超额收益率，越高越好",
            "excess_return_60d": "相对基准的60个交易日超额收益率，越高越好",
            "net_excess_return_20d": "扣除交易成本后相对基准的20个交易日超额收益率，越高越好",
            "net_excess_return_60d": "扣除交易成本后相对基准的60个交易日超额收益率，越高越好",
            "max_drawdown": "持有60个交易日内相对运行峰值的最大回撤，越接近0越好",
            "risk_adjusted": "20个交易日净收益率除以绝对最大回撤，越高越好",
        }
        summary.target_definition = target_definitions[objective]
        objective_series = {
            "return_20d": sample_frame["return20"],
            "return_60d": sample_frame["return60"],
            "excess_return_20d": sample_frame["excess20"],
            "excess_return_60d": sample_frame["excess60"],
            "net_excess_return_20d": sample_frame["net_excess20"],
            "net_excess_return_60d": sample_frame["net_excess60"],
            "max_drawdown": sample_frame["drawdown60"],
            "risk_adjusted": sample_frame["risk_adjusted"],
        }[objective]
        objective_value = _weighted_mean(objective_series, test_weights)
        summary.objective_value = (
            float(objective_value) if np.isfinite(objective_value) else 0.0
        )
        summary.benchmark_valid_count_20d = int(
            sample_frame["benchmark_return20"].notna().sum()
        )
        summary.benchmark_valid_count_60d = int(
            sample_frame["benchmark_return60"].notna().sum()
        )
        summary.benchmark_coverage_20d = (
            float(summary.benchmark_valid_count_20d / len(sample_frame))
            if len(sample_frame)
            else 0.0
        )
        summary.benchmark_coverage_60d = (
            float(summary.benchmark_valid_count_60d / len(sample_frame))
            if len(sample_frame)
            else 0.0
        )
        summary.benchmark_valid_count = summary.benchmark_valid_count_20d
        summary.benchmark_coverage = summary.benchmark_coverage_20d
        summary.rank_ic = {"20d": summary.rank_ic_20d, "60d": summary.rank_ic_60d}
        summary.monotonicity_high_low = {
            "20d": summary.monotonicity_high_low_20d,
            "60d": summary.monotonicity_high_low_60d,
        }
        summary.rolling_oos = {
            split: int((all_frame["split"] == split).sum())
            for split in ("train", "validation", "test", "purged")
        }
        summary.rolling_oos_stats = {
            split: {"samples": len(all_frame[all_frame["split"] == split])}
            for split in ("train", "validation", "test", "purged")
        }
        summary.rolling_oos_stats["walk_forward"] = summary.walk_forward
        for split in ("validation", "test"):
            summary.rolling_oos_stats[split]["entry_date_equal_weight"] = (
                _entry_date_equal_weight_stats(
                    all_frame[all_frame["split"] == split].replace(
                        [np.inf, -np.inf], np.nan
                    )
                )
            )

    summary_path = OUTPUT_DIR / "BacktestSummary.json"
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=OUTPUT_DIR, delete=False
    ) as file:
        temporary_path = Path(file.name)
        json.dump(summary.to_dict(), file, ensure_ascii=False, indent=2)
    try:
        os.replace(temporary_path, summary_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return summary
