from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config import OUTPUT_DIR, SCAN_THREADS
from downloader import _load_cache, download_ticker, is_etf_ticker
from indicators import compute_all_indicators
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
    objective: str = "return_20d"
    target_definition: str = "入场日开盘价至持有20个交易日后的收盘价"
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
    if len(series) <= periods:
        return np.nan
    start = float(series.iloc[-periods - 1])
    end = float(series.iloc[-1])
    return (end / start - 1.0) * 100 if start > 0 else np.nan


def _bounded_score(value: float, low: float, high: float) -> float:
    if not np.isfinite(value) or high <= low:
        return 0.5
    return float(np.clip((value - low) / (high - low), 0.0, 1.0))


def _load_benchmark_frames(source: str) -> dict[str, pd.DataFrame]:
    frames: dict[str, pd.DataFrame] = {}
    for name, ticker in BENCHMARKS.items():
        frame = _load_cache(ticker, source)
        if frame is None or frame.empty:
            try:
                frame = download_ticker(ticker, source=source)
            except Exception as exc:
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
        states.append(bool(close >= ma60 and close >= ma200))
        value = _safe_return(enriched["Close"], 60)
        if np.isfinite(value):
            returns.append(value)
    if not states:
        return "未知", "基准数据不足"
    average_return = float(np.mean(returns)) if returns else 0.0
    if sum(states) >= max(2, len(states) - 1) and average_return > 3:
        return "风险偏好", f"基准60日平均收益 {average_return:.1f}%"
    if sum(states) == 0 and average_return < -3:
        return "风险规避", f"基准60日平均收益 {average_return:.1f}%"
    return "震荡", f"基准60日平均收益 {average_return:.1f}%"


def _stage_label(df: pd.DataFrame, phase: str) -> str:
    if len(df) < 60:
        return "数据不足"
    close = float(df["Close"].iloc[-1])
    ma20 = float(df["MA20"].iloc[-1]) if "MA20" in df and pd.notna(df["MA20"].iloc[-1]) else np.nan
    ma50 = float(df["MA50"].iloc[-1]) if "MA50" in df and pd.notna(df["MA50"].iloc[-1]) else np.nan
    rsi = float(df["RSI14"].iloc[-1]) if "RSI14" in df and pd.notna(df["RSI14"].iloc[-1]) else np.nan
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
    data_age = max(0, (datetime.now().date() - enriched.index[-1].date()).days)
    result.market_regime = regime
    result.market_regime_reason = regime_reason
    result.data_source = source
    result.data_asof = enriched.index[-1].strftime("%Y-%m-%d")
    result.data_age_days = data_age
    result.data_coverage = round(float(enriched["Close"].notna().mean()), 4)
    result.stage = _stage_label(enriched, result.wyckoff_phase)
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
            except Exception as exc:
                completed += 1
                logger.warning("Enrichment failed for %s: %s", source_result.ticker, exc)
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
    peer_average = {key: float(np.mean(values)) for key, values in industry_returns.items() if values}
    for result in results:
        frame = cached_frames.get(result.ticker)
        if frame is None:
            continue
        value = _safe_return(frame["Close"], 60)
        industry = result.industry or result.sector or "未分类"
        peer = peer_average.get(industry, 0.0)
        result.industry_relative_strength = round(value - peer, 2) if np.isfinite(value) else np.nan


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
    opens = enriched["Open"].to_numpy(dtype=float) if "Open" in enriched else np.full(len(enriched), np.nan)
    lows = enriched["Low"].to_numpy(dtype=float) if "Low" in enriched else np.full(len(enriched), np.nan)
    closes = enriched["Close"].to_numpy(dtype=float)
    highs = enriched["High"].to_numpy(dtype=float) if "High" in enriched else closes.copy()
    valid_points = []
    for index in signal_points:
        entry_index = index + 1
        if entry_index >= len(enriched):
            continue
        if not np.isfinite(opens[entry_index]) or opens[entry_index] <= 0:
            continue
        if entry_index + 60 >= len(enriched) or not np.isfinite(closes[entry_index + 20]) or not np.isfinite(closes[entry_index + 60]):
            continue
        if np.any(~np.isfinite(highs[entry_index:entry_index + 61])) or np.any(highs[entry_index:entry_index + 61] <= 0):
            continue
        if np.any(~np.isfinite(lows[entry_index:entry_index + 61])) or np.any(lows[entry_index:entry_index + 61] <= 0):
            continue
        valid_points.append(index)
    if not valid_points:
        return []
    history_lengths = sorted({index + 1 for index in valid_points})
    is_etf = is_etf_ticker(str(ticker))
    score_cache: dict[int, float] = {}
    for length in history_lengths:
        historical = compute_all_indicators(frame.iloc[:length].copy())
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
                if pd.notna(start_date) and pd.notna(end_date) and benchmark_close.loc[start_date] > 0:
                    benchmark_returns[period] = (benchmark_close.loc[end_date] / benchmark_close.loc[start_date] - 1) * 100
        cost_percent = (commission * 2 + slippage * 2 + (0.0 if is_etf else stamp_duty)) * 100
        prices20 = np.concatenate(([entry_price], closes[entry_index:entry_index + 21]))
        prices60 = np.concatenate(([entry_price], closes[entry_index:entry_index + 61]))
        lows20 = np.concatenate(([entry_price], lows[entry_index:entry_index + 21]))
        lows60 = np.concatenate(([entry_price], lows[entry_index:entry_index + 61]))
        drawdown20 = float(((lows20 / np.maximum.accumulate(prices20) - 1).min()) * 100)
        drawdown60 = float(((lows60 / np.maximum.accumulate(prices60) - 1).min()) * 100)
        if test_start is not None and entry_date >= test_start:
            split = "test"
        elif validation_end is not None and entry_date >= validation_end:
            split = "validation"
        else:
            split = "train"
        samples.append({
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
        })
    return samples


def _ticker_backtest_rows(sample_frame: pd.DataFrame, objective: str = "return_20d") -> list[dict[str, Any]]:
    target_map = {
        "return_20d": "return20",
        "return_60d": "return60",
        "excess_return_20d": "excess20",
        "excess_return_60d": "excess60",
        "max_drawdown": "drawdown60",
        "risk_adjusted": "risk_adjusted",
    }
    if objective not in target_map:
        raise ValueError(f"unsupported objective: {objective}")
    rows: list[dict[str, Any]] = []
    for ticker, group in sample_frame.groupby("ticker", sort=False):
        win20 = float((group["return20"] > 0).mean())
        win60 = float((group["return60"] > 0).mean())
        avg20 = float(group["return20"].mean())
        avg60 = float(group["return60"].mean())
        downside60 = float(group.loc[group["return60"] < 0, "return60"].mean()) if (group["return60"] < 0).any() else 0.0
        win_score = win20 * 0.20 + win60 * 0.20
        return_score = (_bounded_score(avg20, -15.0, 15.0) * 0.15 + _bounded_score(avg60, -25.0, 35.0) * 0.25)
        downside_score = _bounded_score(downside60, -25.0, 0.0) * 0.20
        raw_score = (win_score + return_score + downside_score) * 100.0
        sample_confidence = min(1.0, len(group) / 10.0)
        backtest_score = 50.0 + (raw_score - 50.0) * sample_confidence
        objective_series = pd.to_numeric(group[target_map[objective]], errors="coerce").dropna()
        objective_value = float(objective_series.mean()) if not objective_series.empty else 0.0
        rows.append({
            "ticker": str(ticker),
            "samples": int(len(group)),
            "win_rate_20d": round(win20, 4),
            "win_rate_60d": round(win60, 4),
            "average_return_20d": round(avg20, 4),
            "average_return_60d": round(avg60, 4),
            "objective_value": round(objective_value, 4),
            "backtest_score": round(float(backtest_score), 4),
        })
    return sorted(rows, key=lambda row: (row["objective_value"], row["samples"]), reverse=True)


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
    }
    legacy_columns = {
        "backtest_score", "composite_score", "samples", "win_rate_20d", "win_rate_60d",
        "average_return_20d", "average_return_60d", "BacktestScore", "CompositeScore",
        "BacktestSamples", "BacktestWinRate20D", "BacktestWinRate60D",
        "BacktestAverageReturn20D", "BacktestAverageReturn60D", "BacktestObjectiveValue",
    }
    frame = frame.drop(columns=[column for column in frame.columns if column in legacy_columns], errors="ignore")
    metrics = pd.DataFrame(summary.by_ticker).rename(columns={"ticker": "Ticker", **metric_columns})
    frame = frame.merge(metrics, on="Ticker", how="left", validate="one_to_one")
    for column in ("BacktestSamples", "BacktestScore", "BacktestObjectiveValue"):
        if column not in frame:
            frame[column] = np.nan
    observed = frame["BacktestSamples"].fillna(0).astype(float)
    frame["BacktestScore"] = frame["BacktestScore"].fillna(50.0)
    frame["BacktestObjectiveValue"] = pd.to_numeric(frame["BacktestObjectiveValue"], errors="coerce")
    objective_values = frame["BacktestObjectiveValue"].where(np.isfinite(frame["BacktestObjectiveValue"]))
    if summary.objective == "max_drawdown":
        objective_rank = objective_values.rank(pct=True, ascending=True).fillna(0.5) * 100.0
    else:
        objective_rank = objective_values.rank(pct=True).fillna(0.5) * 100.0
    sample_factor = np.clip(observed / 10.0, 0.0, 1.0)
    backtest_component = frame["BacktestScore"] * 0.5 + objective_rank * 0.5
    frame["CompositeScore"] = frame["Score"] * 0.75 + (backtest_component * 0.25 * sample_factor + 50.0 * 0.25 * (1.0 - sample_factor))
    frame = frame.sort_values(
        ["PassedFilters", "CompositeScore", "Score", "SignalCount"],
        ascending=[False, False, False, False],
        kind="mergesort",
    ).reset_index(drop=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    frame.head(top_n).to_csv(OUTPUT_DIR / f"Top{top_n}.csv", index=False, encoding="utf-8-sig")
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


def _bucket_rows(sample_frame: pd.DataFrame) -> list[dict[str, Any]]:
    if sample_frame["score"].nunique() < 2:
        return []
    frame = sample_frame.copy()
    frame["bucket"] = pd.qcut(frame["score"], q=5, labels=False, duplicates="drop")
    rows = []
    for bucket, group in frame.groupby("bucket", dropna=True):
        rows.append({
            "bucket": int(bucket) + 1,
            "samples": int(len(group)),
            "average_return20": round(float(group["return20"].mean()), 4),
            "average_return60": round(float(group["return60"].mean()), 4),
            "average_benchmark_return20": round(float(group["benchmark_return20"].mean()), 4),
            "average_benchmark_return60": round(float(group["benchmark_return60"].mean()), 4),
            "average_excess_return20": round(float((group["return20"] - group["benchmark_return20"]).mean()), 4),
            "average_excess_return60": round(float((group["return60"] - group["benchmark_return60"]).mean()), 4),
            "average_net_return20": round(float(group["net_return20"].mean()), 4),
            "average_net_return60": round(float(group["net_return60"].mean()), 4),
        })
    return rows


def run_historical_backtest(
    tickers: list[str],
    source: str = "eastmoney",
    objective: str = "return_20d",
    benchmark: str = "沪深300",
    commission: float = 0.0003,
    stamp_duty: float = 0.0005,
    slippage: float = 0.001,
    test_ratio: float = 0.2,
    validation_ratio: float = 0.2,
) -> BacktestSummary:
    if objective not in {"return_20d", "return_60d", "excess_return_20d", "excess_return_60d", "max_drawdown", "risk_adjusted"}:
        raise ValueError(f"unsupported objective: {objective}")
    if benchmark not in BENCHMARKS:
        raise ValueError(f"unsupported benchmark: {benchmark}")
    test_ratio = float(np.clip(test_ratio, 0.0, 0.9))
    validation_ratio = float(np.clip(validation_ratio, 0.0, 0.9 - test_ratio))
    benchmark_frame = _load_benchmark_frames(source).get(benchmark)
    available_dates: list[pd.Timestamp] = []
    for ticker in dict.fromkeys(tickers):
        frame = _load_cache(ticker, source)
        if frame is not None and not frame.empty:
            dates = pd.DatetimeIndex(frame.index).dropna().sort_values()
            if len(dates):
                available_dates.extend([pd.Timestamp(dates[0]), pd.Timestamp(dates[-1])])
    global_start = min(available_dates) if available_dates else None
    global_end = max(available_dates) if available_dates else None
    if BACKTEST_VALIDATION_END or BACKTEST_TEST_START:
        validation_end = pd.Timestamp(BACKTEST_VALIDATION_END) if BACKTEST_VALIDATION_END else None
        test_start = pd.Timestamp(BACKTEST_TEST_START) if BACKTEST_TEST_START else None
    elif global_start is not None and global_end is not None:
        span = global_end - global_start
        validation_end = global_start + span * (1.0 - test_ratio - validation_ratio) if validation_ratio else None
        test_start = global_start + span * (1.0 - test_ratio) if test_ratio else None
    else:
        validation_end = test_start = None
    samples: list[dict[str, Any]] = []
    total = len(tickers)
    completed = 0
    workers = min(12, max(1, total))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_backtest_one_ticker, ticker, source, benchmark_frame, commission, stamp_duty, slippage, (validation_end, test_start)): ticker for ticker in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                samples.extend(future.result())
            except Exception as exc:
                logger.warning("Backtest failed for %s: %s", ticker, exc)
            completed += 1
            if completed == total or completed % 250 == 0:
                logger.info("Backtesting progress: %d/%d tickers, %d samples.", completed, total, len(samples))
    split_dates = {
        "global_start": global_start.strftime("%Y-%m-%d") if global_start is not None else None,
        "validation_end": validation_end.strftime("%Y-%m-%d") if validation_end is not None else None,
        "test_start": test_start.strftime("%Y-%m-%d") if test_start is not None else None,
        "global_end": global_end.strftime("%Y-%m-%d") if global_end is not None else None,
    }
    summary = BacktestSummary(
        ticker_count=len(dict.fromkeys(tickers)), objective=objective, benchmark=benchmark,
        commission=commission, stamp_duty=stamp_duty, slippage=slippage,
        cost_parameters={"commission": commission, "stamp_duty": stamp_duty, "slippage": slippage},
        test_ratio=test_ratio, validation_ratio=validation_ratio, split_dates=split_dates,
    )
    if samples:
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
        summary.average_benchmark_return_20d = float(sample_frame["benchmark_return20"].mean())
        summary.average_benchmark_return_60d = float(sample_frame["benchmark_return60"].mean())
        summary.average_net_return_20d = float(sample_frame["net_return20"].mean())
        summary.average_net_return_60d = float(sample_frame["net_return60"].mean())
        summary.maximum_drawdown_20d = float(sample_frame["drawdown20"].min())
        summary.maximum_drawdown_60d = float(sample_frame["drawdown60"].min())
        sample_frame["excess20"] = sample_frame["return20"] - sample_frame["benchmark_return20"]
        sample_frame["excess60"] = sample_frame["return60"] - sample_frame["benchmark_return60"]
        sample_frame["risk_adjusted"] = sample_frame["net_return20"] / sample_frame["drawdown20"].abs().replace(0, np.nan)
        summary.rank_ic_20d = _spearman(sample_frame, "return20")
        summary.rank_ic_60d = _spearman(sample_frame, "return60")
        summary.by_ticker = _ticker_backtest_rows(sample_frame, objective)
        summary.by_score_bucket = _bucket_rows(sample_frame)
        if summary.by_score_bucket:
            summary.monotonicity_high_low_20d = summary.by_score_bucket[-1]["average_return20"] - summary.by_score_bucket[0]["average_return20"]
            summary.monotonicity_high_low_60d = summary.by_score_bucket[-1]["average_return60"] - summary.by_score_bucket[0]["average_return60"]
        target_definitions = {
            "return_20d": "入场日开盘价至第20个交易日后收盘价的平均收益率，越高越好",
            "return_60d": "入场日开盘价至第60个交易日后收盘价的平均收益率，越高越好",
            "excess_return_20d": "相对基准的20个交易日超额收益率，越高越好",
            "excess_return_60d": "相对基准的60个交易日超额收益率，越高越好",
            "max_drawdown": "持有60个交易日内相对运行峰值的最大回撤，越接近0越好",
            "risk_adjusted": "20个交易日净收益率除以绝对最大回撤，越高越好",
        }
        summary.target_definition = target_definitions[objective]
        objective_series = {
            "return_20d": sample_frame["return20"],
            "return_60d": sample_frame["return60"],
            "excess_return_20d": sample_frame["excess20"],
            "excess_return_60d": sample_frame["excess60"],
            "max_drawdown": sample_frame["drawdown60"],
            "risk_adjusted": sample_frame["risk_adjusted"],
        }[objective]
        objective_values = pd.to_numeric(objective_series, errors="coerce").dropna()
        summary.objective_value = float(objective_values.mean()) if not objective_values.empty else 0.0
        summary.benchmark_valid_count_20d = int(sample_frame["benchmark_return20"].notna().sum())
        summary.benchmark_valid_count_60d = int(sample_frame["benchmark_return60"].notna().sum())
        summary.benchmark_coverage_20d = float(summary.benchmark_valid_count_20d / len(sample_frame)) if len(sample_frame) else 0.0
        summary.benchmark_coverage_60d = float(summary.benchmark_valid_count_60d / len(sample_frame)) if len(sample_frame) else 0.0
        summary.benchmark_valid_count = summary.benchmark_valid_count_20d
        summary.benchmark_coverage = summary.benchmark_coverage_20d
        summary.rank_ic = {"20d": summary.rank_ic_20d, "60d": summary.rank_ic_60d}
        summary.monotonicity_high_low = {"20d": summary.monotonicity_high_low_20d, "60d": summary.monotonicity_high_low_60d}
        summary.rolling_oos = {split: int((all_frame["split"] == split).sum()) for split in ("train", "validation", "test")}
        summary.rolling_oos_stats = {
            split: {"samples": int(len(all_frame[all_frame["split"] == split]))}
            for split in ("train", "validation", "test")
        }
    (OUTPUT_DIR / "BacktestSummary.json").write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
