from __future__ import annotations

import json
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from config import (
    BACKTEST_FULL_WEIGHT_SAMPLES,
    BACKTEST_LOW_CONFIDENCE_MAX_SAMPLES,
    BACKTEST_MIN_SAMPLES_FOR_RANKING,
    BACKTEST_NEUTRAL_SCORE,
    BACKTEST_NORMAL_WEIGHT,
    BACKTEST_OUTCOME_HORIZON_DAYS,
    BACKTEST_SIGNAL_COOLDOWN_DAYS,
    ENABLE_VOLUME_PROFILE,
    INSTITUTIONAL_TIER_TRAP_LABEL,
    INSTITUTIONAL_TIER_WAIT_LABEL,
    OUTPUT_DIR,
    QUALITY_MULTIPLIER_FAIL,
    QUALITY_MULTIPLIER_PASS,
    QUALITY_MULTIPLIER_UNKNOWN,
    SCAN_THREADS,
)
from downloader import (
    _fetch_eastmoney_realtime_price,
    _fetch_eastmoney_realtime_prices,
    _is_a_share_market_closed,
    _load_cache,
    download_ticker,
    is_etf_ticker,
)
from indicators import compute_all_indicators, compute_volume_profile
from score import entry_point, score_ticker
from signal_lifecycle import finalize_signal_ranking

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
    split_dates: dict[str, str | None] = field(default_factory=dict)
    all_samples: int = 0
    commission: float = 0.0003
    stamp_duty: float = 0.0005
    slippage: float = 0.001
    cost_parameters: dict[str, float] = field(default_factory=dict)
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
        return float(score * 0.7 + quality * 0.3)
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
    result.close = float(enriched["Close"].iloc[-1])
    last_business_day = (pd.Timestamp(today) - pd.offsets.BDay(1)).date()
    if (
        _is_a_share_market_closed()
        and latest_date is not None
        and latest_date < today
        and latest_date >= last_business_day
        and not is_etf_ticker(result.ticker)
    ):
        if realtime_prices is None:
            try:
                realtime_close = _fetch_eastmoney_realtime_price(result.ticker)
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                realtime_close = None
        else:
            realtime_close = realtime_prices.get(result.ticker)
        realtime_value = _finite_float(realtime_close)
        if np.isfinite(realtime_value):
            result.close = realtime_value
            reported_date = today

    if reported_date is None:
        data_age = -1
        trading_age = -1
    else:
        data_age = max(0, (today - reported_date).days)
        trading_age = max(0, len(pd.bdate_range(reported_date, today)) - 1)
    result.market_regime = regime
    result.market_regime_reason = regime_reason
    result.market_regime_fast = regime_fast
    result.market_regime_slow = regime_slow
    result.market_regime_confidence = regime_confidence
    result.data_source = source
    result.data_asof = reported_date.strftime("%Y-%m-%d") if reported_date else ""
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
    realtime_prices: dict[str, float] | None = None
    if _is_a_share_market_closed():
        try:
            realtime_prices = _fetch_eastmoney_realtime_prices(
                [
                    result.ticker
                    for result in results
                    if not is_etf_ticker(result.ticker)
                ]
            )
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            logger.debug("批量实时行情获取失败：%s", exc)
            realtime_prices = {}

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
                if np.isfinite(relative):
                    industry = result.industry or result.sector or "未分类"
                    industry_returns.setdefault(industry, {})[result.ticker] = relative
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
        industry = result.industry or result.sector or "未分类"
        total_return, count = industry_totals.get(industry, (0.0, 0))
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
            result.sector_confirmation_factor = round(
                float(
                    np.clip(
                        0.2 + _bounded_score(peer, -20.0, 20.0) * 0.8,
                        0.2,
                        1.0,
                    )
                ),
                4,
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
        technical_score = (
            base_score
            * (0.7 + 0.3 * sector_factor)
            * (0.8 + 0.2 * breakout_factor)
        )
        quality_adjusted = _quality_adjusted_score(
            technical_score,
            result.quality_score,
            result.quality_data_available,
            result.is_etf,
        )
        quality_multiplier = _finite_float(
            getattr(result, "quality_multiplier", 1.0), 1.0
        )
        result.institutional_score = round(
            quality_adjusted * np.clip(quality_multiplier, 0.0, 1.0), 4
        )


def refresh_research_outcomes(
    source: str,
    history_path: Path | None = None,
) -> pd.DataFrame:
    from signal_lifecycle import HISTORY_FILE, HISTORY_COLUMNS

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


def _signal_points(
    enriched: pd.DataFrame,
    cooldown: int = BACKTEST_SIGNAL_COOLDOWN_DAYS,
    is_etf: bool = False,
) -> list[int]:
    """Find historical entries with the exact live score/entry engine."""
    if len(enriched) < 252:
        return []
    cooldown = max(1, int(cooldown))
    close = pd.to_numeric(enriched["Close"], errors="coerce")
    high = pd.to_numeric(enriched["High"], errors="coerce")
    low = pd.to_numeric(enriched["Low"], errors="coerce")
    ma20 = pd.to_numeric(
        enriched.get("MA20", pd.Series(np.nan, index=enriched.index)),
        errors="coerce",
    )
    ma50 = pd.to_numeric(
        enriched.get("MA50", pd.Series(np.nan, index=enriched.index)),
        errors="coerce",
    )
    atr = pd.to_numeric(
        enriched.get("ATR14", pd.Series(np.nan, index=enriched.index)),
        errors="coerce",
    )
    support = low.rolling(20, min_periods=20).min()
    resistance = high.shift(1).rolling(20, min_periods=20).max()
    effective_atr = atr.where(atr.gt(0), close * 0.03)
    near_support = close.le(support + effective_atr * 1.5)
    five_day_up = close.ge(close.shift(5))
    trend_candidate = close.gt(ma20) & (
        ma20.ge(ma50) | five_day_up | near_support
    )
    broad_candidate = (
        trend_candidate | near_support | close.gt(resistance)
    ).fillna(False)
    candidates = np.flatnonzero(broad_candidate.to_numpy(dtype=bool))

    last_signal = -cooldown
    points: list[int] = []
    for index in candidates:
        if index < 251:
            continue
        if index >= len(enriched) - BACKTEST_OUTCOME_HORIZON_DAYS:
            continue
        if index - last_signal < cooldown:
            continue
        historical = enriched.iloc[: index + 1].copy()
        historical_score = score_ticker(historical, is_etf=is_etf)
        historical_entry = entry_point(
            historical,
            breakout=historical_score.breakout_score,
            volume_score=historical_score.volume,
            value_trap_risk_value=historical_score.value_trap_risk,
        )
        signal = str(historical_entry.get("signal", "AVOID")).upper()
        if signal not in _BACKTEST_ACTIONABLE_SIGNALS:
            continue
        points.append(int(index))
        last_signal = int(index)
    return points


def _backtest_one_ticker(
    ticker: str,
    source: str,
    benchmark_frame: pd.DataFrame | None = None,
    commission: float = 0.0003,
    stamp_duty: float = 0.0005,
    slippage: float = 0.001,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None] = (None, None),
) -> list[dict[str, Any]]:
    frame = _load_cache(ticker, source)
    if frame is None or len(frame) < 300:
        return []
    enriched = compute_all_indicators(frame.copy())
    is_etf = is_etf_ticker(str(ticker))
    signal_points = _signal_points(enriched, is_etf=is_etf)
    if not signal_points:
        return []

    opens = (
        enriched["Open"].to_numpy(dtype=float)
        if "Open" in enriched
        else np.full(len(enriched), np.nan)
    )
    lows = (
        enriched["Low"].to_numpy(dtype=float)
        if "Low" in enriched
        else np.full(len(enriched), np.nan)
    )
    closes = enriched["Close"].to_numpy(dtype=float)
    highs = (
        enriched["High"].to_numpy(dtype=float)
        if "High" in enriched
        else closes.copy()
    )
    outcome_horizon = max(60, int(BACKTEST_OUTCOME_HORIZON_DAYS))
    valid_points: list[int] = []
    for index in signal_points:
        entry_index = index + 1
        if entry_index >= len(enriched):
            continue
        if not np.isfinite(opens[entry_index]) or opens[entry_index] <= 0:
            continue
        if (
            entry_index + outcome_horizon >= len(enriched)
            or not np.isfinite(closes[entry_index + 20])
            or not np.isfinite(closes[entry_index + outcome_horizon])
        ):
            continue
        if np.any(
            ~np.isfinite(highs[entry_index : entry_index + outcome_horizon + 1])
        ) or np.any(highs[entry_index : entry_index + outcome_horizon + 1] <= 0):
            continue
        if np.any(
            ~np.isfinite(lows[entry_index : entry_index + outcome_horizon + 1])
        ) or np.any(lows[entry_index : entry_index + outcome_horizon + 1] <= 0):
            continue
        valid_points.append(index)
    if not valid_points:
        return []

    history_lengths = sorted({index + 1 for index in valid_points})
    score_cache: dict[int, float] = {}
    signal_cache: dict[int, str] = {}
    for length in history_lengths:
        historical = enriched.iloc[:length].copy()
        if ENABLE_VOLUME_PROFILE:
            try:
                compute_volume_profile(historical)
            except (ArithmeticError, TypeError, ValueError):
                logger.debug("Historical volume profile failed for %s.", ticker)
        historical_score = score_ticker(historical, is_etf=is_etf)
        final_score = _finite_float(
            getattr(historical_score, "final_score", np.nan)
        )
        score_cache[length] = (
            final_score
            if np.isfinite(final_score)
            else _finite_float(getattr(historical_score, "total", np.nan), 0.0)
        )
        historical_entry = entry_point(
            historical,
            breakout=historical_score.breakout_score,
            volume_score=historical_score.volume,
            value_trap_risk_value=historical_score.value_trap_risk,
        )
        signal_cache[length] = str(
            historical_entry.get("signal", "AVOID")
        ).upper()

    benchmark_close = None
    if benchmark_frame is not None and not benchmark_frame.empty:
        benchmark_close = benchmark_frame["Close"].astype(float).sort_index()
    validation_end, test_start = split_dates
    samples: list[dict[str, Any]] = []
    previous_sample_index: int | None = None
    for index in valid_points:
        signal_date = pd.Timestamp(enriched.index[index])
        entry_index = index + 1
        entry_date = pd.Timestamp(enriched.index[entry_index])
        entry_price = opens[entry_index]
        future20 = closes[entry_index + 20]
        future60 = closes[entry_index + outcome_horizon]
        benchmark_returns: dict[int, float] = {20: np.nan, 60: np.nan}
        if benchmark_close is not None:
            start_date = benchmark_close.index.asof(entry_date)
            for period in (20, 60):
                future_date = pd.Timestamp(enriched.index[entry_index + period])
                end_date = benchmark_close.index.asof(future_date)
                if (
                    pd.notna(start_date)
                    and pd.notna(end_date)
                    and end_date == future_date
                    and benchmark_close.loc[start_date] > 0
                ):
                    benchmark_returns[period] = (
                        benchmark_close.loc[end_date] / benchmark_close.loc[start_date]
                        - 1
                    ) * 100
        cost_percent = (
            commission * 2 + slippage * 2 + (0.0 if is_etf else stamp_duty)
        ) * 100
        prices20 = np.concatenate(
            ([entry_price], closes[entry_index : entry_index + 21])
        )
        prices60 = np.concatenate(
            ([entry_price], closes[entry_index : entry_index + outcome_horizon + 1])
        )
        lows20 = np.concatenate(
            ([entry_price], lows[entry_index : entry_index + 21])
        )
        lows60 = np.concatenate(
            ([entry_price], lows[entry_index : entry_index + outcome_horizon + 1])
        )
        drawdown20 = float(
            ((lows20 / np.maximum.accumulate(prices20) - 1).min()) * 100
        )
        drawdown60 = float(
            ((lows60 / np.maximum.accumulate(prices60) - 1).min()) * 100
        )
        if test_start is not None and entry_date >= test_start:
            split = "test"
        elif validation_end is not None and entry_date >= validation_end:
            split = "validation"
        else:
            split = "train"
        spacing = (
            outcome_horizon
            if previous_sample_index is None
            else max(1, index - previous_sample_index)
        )
        sample_weight = min(1.0, spacing / float(outcome_horizon))
        samples.append(
            {
                "ticker": ticker,
                "entry_signal": signal_cache.get(index + 1, "AVOID"),
                "signal_date": signal_date.strftime("%Y-%m-%d"),
                "entry_date": entry_date.strftime("%Y-%m-%d"),
                "entry_price": float(entry_price),
                "return20": (future20 / entry_price - 1) * 100,
                "return60": (future60 / entry_price - 1) * 100,
                "benchmark_return20": benchmark_returns[20],
                "benchmark_return60": benchmark_returns[60],
                "net_return20": (future20 / entry_price - 1) * 100 - cost_percent,
                "net_return60": (future60 / entry_price - 1) * 100 - cost_percent,
                "drawdown20": drawdown20,
                "drawdown60": drawdown60,
                "score": score_cache[index + 1],
                "split": split,
                "sample_weight": round(sample_weight, 4),
            }
        )
        previous_sample_index = index
    return samples


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
        win20 = float((group["return20"] > 0).mean())
        win60 = float((group["return60"] > 0).mean())
        avg20 = _robust_mean(group["return20"])
        avg60 = _robust_mean(group["return60"])
        median20 = float(pd.to_numeric(group["return20"], errors="coerce").median())
        median60 = float(pd.to_numeric(group["return60"], errors="coerce").median())
        max_drawdown20 = float(
            pd.to_numeric(group["drawdown20"], errors="coerce").min()
        )
        max_drawdown60 = float(
            pd.to_numeric(group["drawdown60"], errors="coerce").min()
        )
        std20 = float(
            pd.to_numeric(group["return20"], errors="coerce").std(ddof=0)
        )
        gross_profit = pd.to_numeric(
            group.loc[group["return20"] > 0, "return20"], errors="coerce"
        ).sum()
        gross_loss = pd.to_numeric(
            group.loc[group["return20"] < 0, "return20"], errors="coerce"
        ).abs().sum()
        profit_factor = (
            float(gross_profit / gross_loss)
            if gross_loss > 0
            else np.inf
            if gross_profit > 0
            else np.nan
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
        negative_returns60 = group.loc[group["return60"] < 0, "return60"]
        downside60 = (
            _robust_mean(negative_returns60)
            if not negative_returns60.empty
            else 0.0
        )
        raw_score = (
            win20 * 0.20
            + win60 * 0.20
            + _bounded_score(avg20, -15.0, 15.0) * 0.15
            + _bounded_score(avg60, -25.0, 35.0) * 0.25
            + _bounded_score(downside60, -25.0, 0.0) * 0.20
        ) * 100.0
        effective_samples = float(group["sample_weight"].sum())
        reliability, effective_weight, confidence_tier = _backtest_evidence(
            len(group), effective_samples, std20
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
        raw_objective_value = _robust_mean(objective_frame[target_map[objective]])
        objective_value = (
            raw_objective_value * reliability
            if np.isfinite(raw_objective_value)
            else np.nan
        )
        failure_signal_factor = 1.0
        if (
            len(group) >= BACKTEST_MIN_SAMPLES_FOR_RANKING
            and avg20 < 0
            and avg60 < 0
        ):
            loss20 = 1.0 - _bounded_score(avg20, -30.0, 0.0)
            loss60 = 1.0 - _bounded_score(avg60, -50.0, 0.0)
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
                "average_return_20d": round(avg20, 4),
                "average_return_60d": round(avg60, 4),
                "median_return_20d": round(median20, 4),
                "median_return_60d": round(median60, 4),
                "max_drawdown_20d": round(max_drawdown20, 4),
                "max_drawdown_60d": round(max_drawdown60, 4),
                "profit_factor": (
                    round(profit_factor, 4)
                    if np.isfinite(profit_factor)
                    else np.nan
                ),
                "signal_span_days": signal_span_days,
                "return_std_20d": (
                    round(std20, 4) if np.isfinite(std20) else np.nan
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


def apply_backtest_ranking(summary: BacktestSummary, top_n: int = 50) -> None:
    path = OUTPUT_DIR / "AllResults.csv"
    if not path.exists() or not summary.by_ticker:
        return
    frame = pd.read_csv(path, encoding="utf-8-sig")
    metric_columns = {
        "samples": "BacktestSamples",
        "effective_samples": "BacktestEffectiveSamples",
        "win_rate_20d": "BacktestWinRate20D",
        "win_rate_60d": "BacktestWinRate60D",
        "average_return_20d": "BacktestAverageReturn20D",
        "average_return_60d": "BacktestAverageReturn60D",
        "median_return_20d": "BacktestMedianReturn20D",
        "median_return_60d": "BacktestMedianReturn60D",
        "max_drawdown_20d": "BacktestMaxDrawdown20D",
        "max_drawdown_60d": "BacktestMaxDrawdown60D",
        "profit_factor": "BacktestProfitFactor",
        "signal_span_days": "BacktestSignalSpanDays",
        "return_std_20d": "BacktestReturnStd20D",
        "objective_value": "BacktestObjectiveValue",
        "backtest_score": "BacktestScore",
        "backtest_reliability": "BacktestReliability",
        "backtest_effective_weight": "BacktestEffectiveWeight",
        "backtest_confidence_tier": "BacktestConfidenceTier",
        "backtest_adjusted_score": "BacktestAdjustedScore",
        "failure_signal_factor": "FailureSignalFactor",
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
        "InstitutionalTier",
        "InstitutionalScore",
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
    metrics = (
        pd.DataFrame(summary.by_ticker)
        .rename(
            columns={
                "ticker": "Ticker",
                "entry_signal": "EntrySignal",
                **metric_columns,
            }
        )
        .reindex(columns=["Ticker", "EntrySignal", *metric_columns.values()])
    )
    frame["EntrySignal"] = (
        frame.get("EntrySignal", pd.Series("AVOID", index=frame.index))
        .fillna("AVOID")
        .astype(str)
        .str.upper()
    )
    metrics["EntrySignal"] = (
        metrics["EntrySignal"].fillna("UNKNOWN").astype(str).str.upper()
    )
    frame = frame.merge(
        metrics,
        on=["Ticker", "EntrySignal"],
        how="left",
        validate="one_to_one",
    )
    for column in (
        "BacktestSamples",
        "BacktestEffectiveSamples",
        "BacktestScore",
        *metric_columns.values(),
    ):
        if column not in frame:
            frame[column] = np.nan

    observed = pd.to_numeric(frame["BacktestSamples"], errors="coerce").fillna(0.0)
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
    std20 = pd.to_numeric(frame["BacktestReturnStd20D"], errors="coerce")
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
    profit_factor = pd.to_numeric(frame["BacktestProfitFactor"], errors="coerce")
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
    frame["BacktestAdjustedScore"] = (
        BACKTEST_NEUTRAL_SCORE
        + (backtest_component - BACKTEST_NEUTRAL_SCORE) * reliability
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
    institutional_component = (
        frame["FailureAdjustedScore"]
        * sector_multiplier
        * recency_multiplier
        * (0.8 + 0.2 * breakout_factor)
    )
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
    holding_periods = pd.to_numeric(
        frame.get("InstitutionHoldingPeriods", pd.Series(np.nan, index=frame.index)),
        errors="coerce",
    )
    holding_status = (
        frame.get("InstitutionHoldingStatus", pd.Series("", index=frame.index))
        .fillna("")
        .astype(str)
        .str.upper()
    )
    holding_unknown = holding_status.eq("UNKNOWN") | (
        holding_status.eq("") & holding_periods.lt(2)
    )
    known_factor_fail = (
        (
            pd.to_numeric(
                frame.get("ROE", pd.Series(np.nan, index=frame.index)),
                errors="coerce",
            ).notna()
            & ~frame.get("QualityROE", pd.Series(True, index=frame.index))
            .astype(str)
            .str.lower()
            .isin({"true", "1", "yes", "y", "是"})
        )
        | (
            pd.to_numeric(
                frame.get(
                    "IndustryGrossMarginPercentile",
                    pd.Series(np.nan, index=frame.index),
                ),
                errors="coerce",
            ).notna()
            & ~frame.get("QualityGrossMargin", pd.Series(True, index=frame.index))
            .astype(str)
            .str.lower()
            .isin({"true", "1", "yes", "y", "是"})
        )
        | holding_status.eq("FAIL")
    )
    frame["QualityMultiplier"] = np.select(
        [known_factor_fail, holding_unknown],
        [QUALITY_MULTIPLIER_FAIL, QUALITY_MULTIPLIER_UNKNOWN],
        default=QUALITY_MULTIPLIER_PASS,
    )
    frame["InstitutionalScore"] = pd.Series(
        np.where(
            quality_eligible,
            institutional_component * 0.7 + quality_score * 0.3,
            institutional_component,
        ),
        index=frame.index,
    ).mul(frame["QualityMultiplier"], axis=0).round(4)

    frame = finalize_signal_ranking(frame)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    from report import refresh_candidate_exports

    refresh_candidate_exports(frame, top_n_csv=top_n, output_dir=OUTPUT_DIR)
    frame.to_parquet(OUTPUT_DIR / "AllResults.parquet", index=False)


def _spearman(frame: pd.DataFrame, target: str) -> float:
    data = frame[["score", target]].dropna()
    if (
        len(data) < 2
        or data["score"].nunique() < 2
        or data[target].nunique() < 2
    ):
        return 0.0
    try:
        from scipy.stats import spearmanr

        value = spearmanr(data["score"], data[target]).statistic
    except (ImportError, AttributeError):
        value = data["score"].rank().corr(data[target].rank())
    return float(value) if np.isfinite(value) else 0.0


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
        rows.append(
            {
                "bucket": int(bucket) + 1,
                "samples": len(group),
                "average_return20": round(float(group["return20"].mean()), 4),
                "average_return60": round(float(group["return60"].mean()), 4),
                "average_benchmark_return20": round(
                    float(group["benchmark_return20"].mean()), 4
                ),
                "average_benchmark_return60": round(
                    float(group["benchmark_return60"].mean()), 4
                ),
                "average_excess_return20": round(
                    float(
                        (group["return20"] - group["benchmark_return20"]).mean()
                    ),
                    4,
                ),
                "average_excess_return60": round(
                    float(
                        (group["return60"] - group["benchmark_return60"]).mean()
                    ),
                    4,
                ),
                "average_net_return20": round(
                    float(group["net_return20"].mean()), 4
                ),
                "average_net_return60": round(
                    float(group["net_return60"].mean()), 4
                ),
            }
        )
    return rows


def run_historical_backtest(
    tickers: list[str],
    source: str = "eastmoney",
    objective: str = "net_excess_return_20d",
    benchmark: str = "沪深300",
    commission: float = 0.0003,
    stamp_duty: float = 0.0005,
    slippage: float = 0.001,
    test_ratio: float = 0.2,
    validation_ratio: float = 0.2,
    workers: int | None = None,
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
                    "commission": commission,
                    "stamp_duty": stamp_duty,
                    "slippage": slippage,
                },
                test_ratio=test_ratio,
                validation_ratio=validation_ratio,
                error=f"无法加载基准数据：{benchmark}，无法建立回测时间切分",
            )
            summary.insufficient_test_data = True
            return summary

    samples: list[dict[str, Any]] = []
    total = len(tickers)
    completed = 0
    worker_count = min(workers or SCAN_THREADS, max(1, total))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(
                _backtest_one_ticker,
                ticker,
                source,
                benchmark_frame,
                commission,
                stamp_duty,
                slippage,
                (validation_end, test_start),
            ): ticker
            for ticker in tickers
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                samples.extend(future.result())
            except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
                logger.warning("Backtest failed for %s: %s", ticker, exc)
            completed += 1
            if completed == total or completed % 250 == 0:
                logger.info(
                    "Backtesting progress: %d/%d tickers, %d samples.",
                    completed,
                    total,
                    len(samples),
                )

    split_dates = {
        "global_start": global_start.strftime("%Y-%m-%d")
        if global_start is not None
        else None,
        "validation_end": validation_end.strftime("%Y-%m-%d")
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
        objective=objective,
        benchmark=benchmark,
        commission=commission,
        stamp_duty=stamp_duty,
        slippage=slippage,
        cost_parameters={
            "commission": commission,
            "stamp_duty": stamp_duty,
            "slippage": slippage,
        },
        test_ratio=test_ratio,
        validation_ratio=validation_ratio,
        split_dates=split_dates,
    )
    if not samples:
        summary.insufficient_test_data = True
        summary.error = "未生成有效回测样本"
    else:
        all_frame = pd.DataFrame(samples)
        summary.all_samples = len(all_frame)
        test_frame = all_frame[all_frame["split"] == "test"]
        if len(test_frame) < 2:
            summary.insufficient_test_data = True
            summary.error = f"测试集有效样本不足：{len(test_frame)}，至少需要2个样本"
            test_frame = all_frame.iloc[0:0]
        sample_frame = test_frame.replace([np.inf, -np.inf], np.nan)
        summary.samples = len(sample_frame)
        summary.win_rate_20d = float((sample_frame["return20"] > 0).mean())
        summary.win_rate_60d = float((sample_frame["return60"] > 0).mean())
        summary.average_return_20d = float(sample_frame["return20"].mean())
        summary.average_return_60d = float(sample_frame["return60"].mean())
        summary.median_return_20d = float(sample_frame["return20"].median())
        summary.median_return_60d = float(sample_frame["return60"].median())
        summary.average_benchmark_return_20d = float(
            sample_frame["benchmark_return20"].mean()
        )
        summary.average_benchmark_return_60d = float(
            sample_frame["benchmark_return60"].mean()
        )
        summary.average_net_return_20d = float(sample_frame["net_return20"].mean())
        summary.average_net_return_60d = float(sample_frame["net_return60"].mean())
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
        summary.average_net_excess_return_20d = float(
            sample_frame["net_excess20"].mean()
        )
        summary.average_net_excess_return_60d = float(
            sample_frame["net_excess60"].mean()
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
        summary.by_score_bucket = _bucket_rows(sample_frame)
        if summary.by_score_bucket:
            summary.monotonicity_high_low_20d = (
                summary.by_score_bucket[-1]["average_return20"]
                - summary.by_score_bucket[0]["average_return20"]
            )
            summary.monotonicity_high_low_60d = (
                summary.by_score_bucket[-1]["average_return60"]
                - summary.by_score_bucket[0]["average_return60"]
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
        objective_values = pd.to_numeric(objective_series, errors="coerce").dropna()
        summary.objective_value = (
            float(objective_values.mean()) if not objective_values.empty else 0.0
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
            for split in ("train", "validation", "test")
        }
        summary.rolling_oos_stats = {
            split: {"samples": len(all_frame[all_frame["split"] == split])}
            for split in ("train", "validation", "test")
        }
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
