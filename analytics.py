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
from downloader import _load_cache, download_ticker
from indicators import compute_all_indicators
from score import score_ticker

logger = logging.getLogger("institution_scanner.analytics")

BENCHMARKS = {
    "沪深300": "000300.SH",
    "中证500": "000905.SH",
    "创业板指": "399006.SZ",
}


@dataclass
class BacktestSummary:
    samples: int = 0
    ticker_count: int = 0
    win_rate_20d: float = 0.0
    win_rate_60d: float = 0.0
    average_return_20d: float = 0.0
    average_return_60d: float = 0.0
    median_return_20d: float = 0.0
    median_return_60d: float = 0.0
    by_score_bucket: list[dict[str, Any]] = field(default_factory=list)
    by_ticker: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "ticker_count": self.ticker_count,
            "win_rate_20d": round(self.win_rate_20d, 4),
            "win_rate_60d": round(self.win_rate_60d, 4),
            "average_return_20d": round(self.average_return_20d, 4),
            "average_return_60d": round(self.average_return_60d, 4),
            "median_return_20d": round(self.median_return_20d, 4),
            "median_return_60d": round(self.median_return_60d, 4),
            "by_score_bucket": self.by_score_bucket,
            "by_ticker": self.by_ticker,
        }


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


def _is_etf_ticker(ticker: str) -> bool:
    code = ticker.upper().split(".", 1)[0]
    return code.startswith(("15", "16", "51", "56", "58"))


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


def _enrich_one_result(result: Any, source: str, regime: str, regime_reason: str) -> tuple[Any, pd.DataFrame | None, float]:
    frame = _load_cache(result.ticker, source)
    if frame is None or frame.empty:
        return result, None, 0.0
    enriched = compute_all_indicators(frame.copy())
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


def enrich_results(results: list[Any], source: str) -> None:
    frames = _load_benchmark_frames(source)
    regime, regime_reason = _benchmark_regime(frames)
    industry_returns: dict[str, list[float]] = {}
    cached_frames: dict[str, pd.DataFrame] = {}
    total = len(results)
    completed = 0
    workers = min(max(1, SCAN_THREADS), max(1, total))
    logger.info("Enrichment started: %d results, %d threads.", total, workers)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_enrich_one_result, result, source, regime, regime_reason): result
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


def _signal_points(enriched: pd.DataFrame, cooldown: int = 20) -> list[int]:
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


def _backtest_one_ticker(ticker: str, source: str) -> list[dict[str, Any]]:
    frame = _load_cache(ticker, source)
    if frame is None or len(frame) < 300:
        return []
    enriched = compute_all_indicators(frame.copy())
    signal_points = _signal_points(enriched)
    if not signal_points:
        return []
    closes = enriched["Close"].to_numpy(dtype=float)
    valid_points = [index for index in signal_points if np.isfinite(closes[index]) and closes[index] > 0 and np.isfinite(closes[index + 20]) and np.isfinite(closes[index + 60])]
    if not valid_points:
        return []
    history_lengths = sorted({index + 1 for index in valid_points})
    is_etf = _is_etf_ticker(str(ticker))
    score_cache = {
        length: float(score_ticker(enriched.iloc[:length], is_etf=is_etf).total)
        for length in history_lengths
    }
    samples: list[dict[str, Any]] = []
    for index in valid_points:
        close = closes[index]
        future20 = closes[index + 20]
        future60 = closes[index + 60]
        samples.append({
            "ticker": ticker,
            "signal_date": enriched.index[index].strftime("%Y-%m-%d"),
            "return20": (future20 / close - 1) * 100,
            "return60": (future60 / close - 1) * 100,
            "score": score_cache[index + 1],
        })
    return samples


def _ticker_backtest_rows(sample_frame: pd.DataFrame) -> list[dict[str, Any]]:
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
        rows.append({
            "ticker": str(ticker),
            "samples": int(len(group)),
            "win_rate_20d": round(win20, 4),
            "win_rate_60d": round(win60, 4),
            "average_return_20d": round(avg20, 4),
            "average_return_60d": round(avg60, 4),
            "backtest_score": round(float(backtest_score), 4),
        })
    return sorted(rows, key=lambda row: (row["backtest_score"], row["samples"]), reverse=True)


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
        "backtest_score": "BacktestScore",
    }
    legacy_columns = {
        "backtest_score", "composite_score", "samples", "win_rate_20d", "win_rate_60d",
        "average_return_20d", "average_return_60d", "BacktestScore", "CompositeScore",
        "BacktestSamples", "BacktestWinRate20D", "BacktestWinRate60D",
        "BacktestAverageReturn20D", "BacktestAverageReturn60D",
    }
    frame = frame.drop(columns=[column for column in frame.columns if column in legacy_columns], errors="ignore")
    metrics = pd.DataFrame(summary.by_ticker).rename(columns={"ticker": "Ticker", **metric_columns})
    frame = frame.merge(metrics, on="Ticker", how="left", validate="one_to_one")
    observed = frame["BacktestSamples"].fillna(0).astype(float)
    frame["BacktestScore"] = frame["BacktestScore"].fillna(50.0)
    sample_factor = np.clip(observed / 10.0, 0.0, 1.0)
    frame["CompositeScore"] = frame["Score"] * 0.75 + (frame["BacktestScore"] * 0.25 * sample_factor + 50.0 * 0.25 * (1.0 - sample_factor))
    frame = frame.sort_values(
        ["PassedFilters", "CompositeScore", "Score", "SignalCount"],
        ascending=[False, False, False, False],
        kind="mergesort",
    ).reset_index(drop=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    frame.head(top_n).to_csv(OUTPUT_DIR / f"Top{top_n}.csv", index=False, encoding="utf-8-sig")
    frame.head(200).to_parquet(OUTPUT_DIR / "Top200.parquet", index=False)
    frame.to_parquet(OUTPUT_DIR / "AllResults.parquet", index=False)


def run_historical_backtest(tickers: list[str], source: str = "eastmoney") -> BacktestSummary:
    samples: list[dict[str, Any]] = []
    total = len(tickers)
    completed = 0
    workers = min(12, max(1, total))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_backtest_one_ticker, ticker, source): ticker for ticker in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                samples.extend(future.result())
            except Exception as exc:
                logger.warning("Backtest failed for %s: %s", ticker, exc)
            completed += 1
            if completed == total or completed % 250 == 0:
                logger.info("Backtesting progress: %d/%d tickers, %d samples.", completed, total, len(samples))
    summary = BacktestSummary(ticker_count=len(set(tickers)))
    if samples:
        sample_frame = pd.DataFrame(samples)
        summary.samples = len(sample_frame)
        summary.win_rate_20d = float((sample_frame["return20"] > 0).mean())
        summary.win_rate_60d = float((sample_frame["return60"] > 0).mean())
        summary.average_return_20d = float(sample_frame["return20"].mean())
        summary.average_return_60d = float(sample_frame["return60"].mean())
        summary.median_return_20d = float(sample_frame["return20"].median())
        summary.median_return_60d = float(sample_frame["return60"].median())
        summary.by_ticker = _ticker_backtest_rows(sample_frame)
        if sample_frame["score"].nunique() > 1:
            sample_frame["bucket"] = pd.qcut(sample_frame["score"], q=min(5, len(sample_frame)), labels=False, duplicates="drop")
            for bucket, group in sample_frame.groupby("bucket", dropna=True):
                summary.by_score_bucket.append({
                    "bucket": int(bucket) + 1,
                    "samples": int(len(group)),
                    "win_rate_20d": round(float((group["return20"] > 0).mean()), 4),
                    "average_return_20d": round(float(group["return20"].mean()), 4),
                })
    (OUTPUT_DIR / "BacktestSummary.json").write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
