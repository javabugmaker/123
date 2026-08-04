from __future__ import annotations

import json
import logging
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from config import ENABLE_VOLUME_PROFILE, OUTPUT_DIR, SCAN_THREADS
from downloader import (
    _fetch_eastmoney_realtime_price,
    _is_a_share_market_closed,
    _load_cache,
    download_ticker,
    is_etf_ticker,
)
from indicators import compute_all_indicators, compute_volume_profile
from score import score_ticker

logger = logging.getLogger("institution_scanner.analytics")

BENCHMARKS = {
    "沪深300": "000300.SH",
    "中证500": "000905.SH",
    "创业板指": "399006.SZ",
}
BACKTEST_VALIDATION_END: str | None = None
BACKTEST_TEST_START: str | None = None


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
    clean = pd.to_numeric(series, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    ).dropna()
    if len(clean) <= periods:
        return np.nan
    start = float(clean.iloc[-periods - 1])
    end = float(clean.iloc[-1])
    return (end / start - 1.0) * 100 if start > 0 else np.nan


def _bounded_score(value: float, low: float, high: float) -> float:
    if not np.isfinite(value) or high <= low:
        return 0.5
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))


def _robust_mean(values: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    ).dropna()
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
        ma200 = float(enriched["MA200"].iloc[-1]) if "MA200" in enriched else np.nan
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


def _breakout_quality_factor(frame: pd.DataFrame) -> float:
    if len(frame) < 21 or not {"Close", "High", "Low", "Volume"}.issubset(frame.columns):
        return 1.0
    recent = frame.iloc[-1]
    close = float(recent["Close"])
    high = float(recent["High"])
    low = float(recent["Low"])
    volume = float(recent["Volume"])
    prior_high = float(frame["High"].iloc[-21:-1].max())
    volume_average = float(frame["Volume"].iloc[-21:-1].mean())
    if not all(
        np.isfinite(value) for value in (close, high, low, volume, prior_high, volume_average)
    ) or high <= low or volume_average <= 0:
        return 1.0
    platform_breakout = float(close >= prior_high)
    volume_confirmation = float(np.clip(volume / volume_average / 1.5, 0.0, 1.0))
    close_position = float(np.clip((close - low) / (high - low), 0.0, 1.0))
    return round(float(np.clip(
        platform_breakout * 0.45 + volume_confirmation * 0.35 + close_position * 0.20,
        0.0,
        1.0,
    )), 4)


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
    frames: dict[str, pd.DataFrame] | None = None,
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
    latest_date = indexed_date.date() if not pd.isna(indexed_date) else today
    reported_date = latest_date
    result.close = float(enriched["Close"].iloc[-1])
    last_business_day = (pd.Timestamp(today) - pd.offsets.BDay(1)).date()
    if (
        _is_a_share_market_closed()
        and latest_date < today
        and latest_date >= last_business_day
        and not is_etf_ticker(result.ticker)
    ):
        try:
            realtime_close = _fetch_eastmoney_realtime_price(result.ticker)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            realtime_close = None
        try:
            realtime_value = float(realtime_close)
        except (TypeError, ValueError):
            realtime_value = np.nan
        if np.isfinite(realtime_value):
            result.close = realtime_value
            reported_date = today
    data_age = max(0, (today - reported_date).days)
    trading_age = max(0, len(pd.bdate_range(reported_date, today)) - 1)
    result.market_regime = regime
    result.market_regime_reason = regime_reason
    result.data_source = source
    result.data_asof = reported_date.strftime("%Y-%m-%d")
    result.data_age_days = data_age
    result.data_trading_age_days = trading_age
    result.data_coverage = round(float(enriched["Close"].notna().mean()), 4)
    result.stage = _stage_label(enriched, result.wyckoff_phase)
    result.breakout_quality_factor = _breakout_quality_factor(enriched)
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
    regime, regime_reason = _benchmark_regime(benchmark_frames)
    industry_returns: dict[str, list[float]] = {}
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
                frames,
            ): result
            for result in results
        }
        for future in as_completed(futures):
            source_result = futures[future]
            try:
                result, enriched, relative = future.result()
            except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
                completed += 1
                logger.warning(
                    "Enrichment failed for %s: %s", source_result.ticker, exc
                )
                if completed == total or completed % 100 == 0:
                    logger.info("Enrichment progress: %d/%d results.", completed, total)
                continue
            completed += 1
            if enriched is not None:
                cached_frames[result.ticker] = enriched
                if np.isfinite(relative):
                    industry = result.industry or result.sector or "未分类"
                    industry_returns.setdefault(industry, []).append(relative)
            if completed == total or completed % 100 == 0:
                logger.info("Enrichment progress: %d/%d results.", completed, total)
    peer_average = {
        key: float(np.mean(values))
        for key, values in industry_returns.items()
        if values
    }
    for result in results:
        frame = cached_frames.get(result.ticker)
        if frame is None:
            continue
        value = _safe_return(frame["Close"], 60)
        industry = result.industry or result.sector or "未分类"
        peer = peer_average.get(industry, 0.0)
        result.industry_relative_strength = (
            round(value - peer, 2) if np.isfinite(value) else np.nan
        )
        result.industry_momentum_60d = (
            round(peer, 2) if np.isfinite(peer) else np.nan
        )
        if np.isfinite(peer):
            result.sector_confirmation_factor = round(
                float(np.clip(0.2 + _bounded_score(peer, -20.0, 20.0) * 0.8, 0.2, 1.0)),
                4,
            )
        else:
            result.sector_confirmation_factor = 1.0
    for result in results:
        base_score = result.failure_adjusted_score
        if not np.isfinite(base_score):
            base_score = result.final_score
        if not np.isfinite(base_score):
            base_score = result.score.total
        sector_multiplier = 0.7 + 0.3 * result.sector_confirmation_factor
        breakout_multiplier = 0.8 + 0.2 * result.breakout_quality_factor
        result.institutional_score = round(
            float(base_score * sector_multiplier * breakout_multiplier), 4
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
            entry_date = pd.to_datetime(history.at[position, "TradeDate"], errors="coerce")
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
            "D级陷阱池": 3,
        }
        tiers = sorted(
            history["InstitutionalTier"].dropna().unique(),
            key=lambda value: (tier_order.get(str(value), len(tier_order)), str(value)),
        )
        for tier in tiers:
            group = history.loc[history["InstitutionalTier"].eq(tier)]
            row: dict[str, Any] = {"InstitutionalTier": tier, "Samples": len(group)}
            for horizon in (20, 60):
                returns = pd.to_numeric(group.get(f"Return{horizon}D"), errors="coerce")
                drawdowns = pd.to_numeric(
                    group.get(f"MaxDrawdown{horizon}D"), errors="coerce"
                )
                valid = returns.dropna()
                row[f"WinRate{horizon}D"] = float((valid > 0).mean()) if not valid.empty else np.nan
                row[f"AverageReturn{horizon}D"] = float(valid.mean()) if not valid.empty else np.nan
                row[f"MedianReturn{horizon}D"] = float(valid.median()) if not valid.empty else np.nan
                row[f"MaxDrawdown{horizon}D"] = float(drawdowns.min()) if not drawdowns.dropna().empty else np.nan
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
            data = history[[factor, target]].apply(pd.to_numeric, errors="coerce").dropna()
            row[f"Samples{horizon}D"] = len(data)
            row[f"IC{horizon}D"] = (
                float(data[factor].rank().corr(data[target].rank()))
                if len(data) >= 2 and data[factor].nunique() >= 2 and data[target].nunique() >= 2
                else np.nan
            )
        factor_rows.append(row)
    pd.DataFrame(factor_rows, columns=factor_columns).to_csv(
        ic_path, index=False, encoding="utf-8-sig"
    )
    return tier_path, ic_path


def _signal_points(enriched: pd.DataFrame, cooldown: int = 60) -> list[int]:
    if len(enriched) < 252:
        return []
    volume = enriched.get("VolMA20", pd.Series(index=enriched.index, dtype=float))
    baseline = enriched.get("VolMA120", pd.Series(index=enriched.index, dtype=float))
    cmf = enriched.get("CMF", pd.Series(index=enriched.index, dtype=float))
    close = enriched["Close"]
    ma50 = enriched.get("MA50", pd.Series(index=enriched.index, dtype=float))
    condition = (volume >= baseline * 1.1) & (cmf > 0) & (close <= ma50 * 1.05)
    candidates = np.flatnonzero(condition.to_numpy(dtype=bool))
    last_signal = -cooldown
    points: list[int] = []
    for index in candidates:
        if index >= len(enriched) - 60 or index - last_signal < cooldown:
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
    signal_points = _signal_points(enriched)
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
        enriched["High"].to_numpy(dtype=float) if "High" in enriched else closes.copy()
    )
    valid_points = []
    for index in signal_points:
        entry_index = index + 1
        if entry_index >= len(enriched):
            continue
        if not np.isfinite(opens[entry_index]) or opens[entry_index] <= 0:
            continue
        if (
            entry_index + 60 >= len(enriched)
            or not np.isfinite(closes[entry_index + 20])
            or not np.isfinite(closes[entry_index + 60])
        ):
            continue
        if np.any(~np.isfinite(highs[entry_index : entry_index + 61])) or np.any(
            highs[entry_index : entry_index + 61] <= 0
        ):
            continue
        if np.any(~np.isfinite(lows[entry_index : entry_index + 61])) or np.any(
            lows[entry_index : entry_index + 61] <= 0
        ):
            continue
        valid_points.append(index)
    if not valid_points:
        return []
    history_lengths = sorted({index + 1 for index in valid_points})
    is_etf = is_etf_ticker(str(ticker))
    score_cache: dict[int, float] = {}
    for length in history_lengths:
        historical = enriched.iloc[:length].copy()
        if ENABLE_VOLUME_PROFILE:
            try:
                compute_volume_profile(historical)
            except (ArithmeticError, TypeError, ValueError):
                logger.debug("Historical volume profile failed for %s.", ticker)
        score_cache[length] = float(score_ticker(historical, is_etf=is_etf).total)
    benchmark_close = None
    if benchmark_frame is not None and not benchmark_frame.empty:
        benchmark_close = benchmark_frame["Close"].astype(float).sort_index()
    validation_end, test_start = split_dates
    samples: list[dict[str, Any]] = []
    for index in valid_points:
        signal_date = pd.Timestamp(enriched.index[index])
        entry_index = index + 1
        entry_date = pd.Timestamp(enriched.index[entry_index])
        entry_price = opens[entry_index]
        future20 = closes[entry_index + 20]
        future60 = closes[entry_index + 60]
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
            ([entry_price], closes[entry_index : entry_index + 61])
        )
        lows20 = np.concatenate(([entry_price], lows[entry_index : entry_index + 21]))
        lows60 = np.concatenate(([entry_price], lows[entry_index : entry_index + 61]))
        drawdown20 = float(((lows20 / np.maximum.accumulate(prices20) - 1).min()) * 100)
        drawdown60 = float(((lows60 / np.maximum.accumulate(prices60) - 1).min()) * 100)
        if test_start is not None and entry_date >= test_start:
            split = "test"
        elif validation_end is not None and entry_date >= validation_end:
            split = "validation"
        else:
            split = "train"
        samples.append(
            {
                "ticker": ticker,
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
            }
        )
    return samples


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
    for column in ("benchmark_return20", "benchmark_return60"):
        if column not in sample_frame:
            sample_frame[column] = np.nan
    sample_frame["excess20"] = sample_frame["return20"] - sample_frame["benchmark_return20"]
    sample_frame["excess60"] = sample_frame["return60"] - sample_frame["benchmark_return60"]
    sample_frame["net_excess20"] = sample_frame["net_return20"] - sample_frame["benchmark_return20"]
    sample_frame["net_excess60"] = (
        sample_frame["net_return60"] - sample_frame["benchmark_return60"]
        if "net_return60" in sample_frame
        else np.nan
    )
    sample_frame["risk_adjusted"] = sample_frame["net_return20"] / sample_frame["drawdown20"].abs().replace(0, np.nan)
    rows: list[dict[str, Any]] = []
    for ticker, group in sample_frame.groupby("ticker", sort=False):
        win20 = float((group["return20"] > 0).mean())
        win60 = float((group["return60"] > 0).mean())
        avg20 = _robust_mean(group["return20"])
        avg60 = _robust_mean(group["return60"])
        negative_returns60 = group.loc[group["return60"] < 0, "return60"]
        downside60 = (
            _robust_mean(negative_returns60)
            if not negative_returns60.empty
            else 0.0
        )
        win_score = win20 * 0.20 + win60 * 0.20
        return_score = (
            _bounded_score(avg20, -15.0, 15.0) * 0.15
            + _bounded_score(avg60, -25.0, 35.0) * 0.25
        )
        downside_score = _bounded_score(downside60, -25.0, 0.0) * 0.20
        raw_score = (win_score + return_score + downside_score) * 100.0
        sample_confidence = min(1.0, len(group) / 10.0)
        backtest_score = 50.0 + (raw_score - 50.0) * sample_confidence
        objective_series = pd.to_numeric(
            group[target_map[objective]], errors="coerce"
        ).dropna()
        raw_objective_value = _robust_mean(objective_series)
        objective_value = (
            raw_objective_value * min(1.0, len(objective_series) / 10.0)
            if np.isfinite(raw_objective_value)
            else np.nan
        )
        failure_signal_factor = 1.0
        if avg20 < 0 and avg60 < 0:
            loss20 = 1.0 - _bounded_score(avg20, -30.0, 0.0)
            loss60 = 1.0 - _bounded_score(avg60, -50.0, 0.0)
            failure_strength = loss20 * 0.3 + loss60 * 0.7
            sample_confidence = min(1.0, len(group) / 10.0)
            failure_signal_factor = 1.0 - failure_strength * sample_confidence * 0.7
        rows.append(
            {
                "ticker": str(ticker),
                "samples": len(group),
                "win_rate_20d": round(win20, 4),
                "win_rate_60d": round(win60, 4),
                "average_return_20d": round(avg20, 4),
                "average_return_60d": round(avg60, 4),
                "objective_value": round(objective_value, 4),
                "raw_objective_value": round(raw_objective_value, 4),
                "backtest_score": round(float(backtest_score), 4),
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
        "win_rate_20d": "BacktestWinRate20D",
        "win_rate_60d": "BacktestWinRate60D",
        "average_return_20d": "BacktestAverageReturn20D",
        "average_return_60d": "BacktestAverageReturn60D",
        "objective_value": "BacktestObjectiveValue",
        "backtest_score": "BacktestScore",
        "failure_signal_factor": "FailureSignalFactor",
    }
    legacy_columns = {
        "backtest_score",
        "composite_score",
        "samples",
        "win_rate_20d",
        "win_rate_60d",
        "average_return_20d",
        "average_return_60d",
        "BacktestScore",
        "CompositeScore",
        "BacktestSamples",
        "BacktestWinRate20D",
        "BacktestWinRate60D",
        "BacktestAverageReturn20D",
        "BacktestAverageReturn60D",
        "BacktestObjectiveValue",
        "FailureSignalFactor",
        "FailureAdjustedScore",
        "SignalRecencyDays",
        "SignalRecencyFactor",
        "BreakoutQualityFactor",
        "InstitutionalTier",
        "InstitutionalScore",
    }
    frame = frame.drop(
        columns=[column for column in frame.columns if column in legacy_columns],
        errors="ignore",
    )
    metrics = pd.DataFrame(summary.by_ticker).rename(
        columns={"ticker": "Ticker", **metric_columns}
    )
    frame = frame.merge(metrics, on="Ticker", how="left", validate="one_to_one")
    for column in (
        "BacktestSamples",
        "BacktestScore",
        "BacktestObjectiveValue",
        "FailureSignalFactor",
    ):
        if column not in frame:
            frame[column] = np.nan
    observed = pd.to_numeric(frame["BacktestSamples"], errors="coerce").fillna(0.0)
    frame["BacktestScore"] = pd.to_numeric(
        frame["BacktestScore"], errors="coerce"
    )
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
    sample_factor = np.clip(observed / 10.0, 0.0, 1.0)
    backtest_score = frame["BacktestScore"].where(
        np.isfinite(frame["BacktestScore"])
    )
    backtest_component = backtest_score * 0.5 + objective_rank * 0.5
    blended_backtest = (
        backtest_component * sample_factor + 50.0 * (1.0 - sample_factor)
    ).where(observed.gt(0))
    raw_score = pd.to_numeric(frame["Score"], errors="coerce").fillna(0.0)
    composite_score = raw_score * 0.75 + blended_backtest * 0.25
    frame["CompositeScore"] = composite_score.where(
        blended_backtest.notna(), raw_score
    )
    frame["FailureSignalFactor"] = frame["FailureSignalFactor"].fillna(1.0)
    failure_multiplier = 0.7 + 0.3 * frame["FailureSignalFactor"]
    frame["FailureAdjustedScore"] = (
        frame["CompositeScore"] * failure_multiplier
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
    breakout_multiplier = 0.8 + 0.2 * breakout_factor
    institutional_component = (
        frame["FailureAdjustedScore"]
        * sector_multiplier
        * recency_multiplier
        * breakout_multiplier
    )
    quality_score = pd.to_numeric(
        frame.get("QualityScore", pd.Series(np.nan, index=frame.index)), errors="coerce"
    )
    quality_available = frame.get(
        "QualityDataAvailable", pd.Series(False, index=frame.index)
    ).astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y", "是"})
    quality_gate = frame.get(
        "QualityGate", pd.Series(True, index=frame.index)
    ).astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y", "是"})
    is_etf = frame.get("IsETF", pd.Series(False, index=frame.index)).astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y", "是"})
    quality_failed = quality_available & ~quality_gate & ~is_etf
    frame["InstitutionalScore"] = pd.Series(
        np.where(
            quality_available & np.isfinite(quality_score),
            institutional_component * 0.7 + quality_score * 0.3,
            institutional_component,
        ),
        index=frame.index,
    ).round(4)
    volume_score = pd.to_numeric(
        frame.get("VolumeScore", pd.Series(0.0, index=frame.index)), errors="coerce"
    ).fillna(0.0)
    volume_confirmed = frame.get(
        "VolAccum", pd.Series(False, index=frame.index)
    ).astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y", "是"}) | volume_score.ge(15.0)
    frame["InstitutionalTier"] = "D级陷阱池"
    frame.loc[frame["InstitutionalScore"].ge(65.0), "InstitutionalTier"] = "C级价值观察"
    frame.loc[frame["InstitutionalScore"].between(75.0, 85.0, inclusive="left"), "InstitutionalTier"] = "B级观察"
    quality_tier_map = {
        "A级机构启动": "B级观察",
        "B级观察": "C级价值观察",
        "C级价值观察": "C级价值观察",
    }
    frame.loc[quality_failed, "InstitutionalTier"] = frame.loc[
        quality_failed, "InstitutionalTier"
    ].map(quality_tier_map).fillna("D级陷阱池")
    frame.loc[
        frame["InstitutionalScore"].gt(85.0)
        & frame["SignalRecencyDays"].le(20)
        & volume_confirmed
        & ~quality_failed,
        "InstitutionalTier",
    ] = "A级机构启动"
    frame = frame.sort_values(
        ["PassedFilters", "InstitutionalScore", "FailureAdjustedScore", "Score", "SignalCount"],
        ascending=[False, False, False, False, False],
        kind="mergesort",
    ).reset_index(drop=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    frame.head(top_n).to_csv(
        OUTPUT_DIR / f"Top{top_n}.csv", index=False, encoding="utf-8-sig"
    )
    frame.head(200).to_parquet(OUTPUT_DIR / "Top200.parquet", index=False)
    frame.to_parquet(OUTPUT_DIR / "AllResults.parquet", index=False)


def _spearman(frame: pd.DataFrame, target: str) -> float:
    data = frame[["score", target]].dropna()
    if len(data) < 2 or data["score"].nunique() < 2 or data[target].nunique() < 2:
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
    frame["bucket"] = pd.qcut(frame["score"], q=5, labels=False, duplicates="drop")
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
                    float((group["return20"] - group["benchmark_return20"]).mean()), 4
                ),
                "average_excess_return60": round(
                    float((group["return60"] - group["benchmark_return60"]).mean()), 4
                ),
                "average_net_return20": round(float(group["net_return20"].mean()), 4),
                "average_net_return60": round(float(group["net_return60"].mean()), 4),
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
            pd.Timestamp(BACKTEST_VALIDATION_END) if BACKTEST_VALIDATION_END else None
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
        sample_frame["excess20"] = sample_frame["return20"] - sample_frame["benchmark_return20"]
        sample_frame["excess60"] = sample_frame["return60"] - sample_frame["benchmark_return60"]
        sample_frame["net_excess20"] = sample_frame["net_return20"] - sample_frame["benchmark_return20"]
        sample_frame["net_excess60"] = sample_frame["net_return60"] - sample_frame["benchmark_return60"]
        summary.average_net_excess_return_20d = float(sample_frame["net_excess20"].mean())
        summary.average_net_excess_return_60d = float(sample_frame["net_excess60"].mean())
        summary.median_net_excess_return_20d = float(sample_frame["net_excess20"].median())
        summary.median_net_excess_return_60d = float(sample_frame["net_excess60"].median())
        summary.maximum_drawdown_20d = float(sample_frame["drawdown20"].min())
        summary.maximum_drawdown_60d = float(sample_frame["drawdown60"].min())
        sample_frame["risk_adjusted"] = sample_frame["net_return20"] / sample_frame["drawdown20"].abs().replace(0, np.nan)
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
