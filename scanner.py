"""
scanner.py — Orchestration engine for the Institutional Accumulation Scanner.

Responsibilities:
1. Load the ticker universe (stocks + ETFs).
2. Download / update cached OHLCV data in parallel.
3. Compute all indicators for each ticker.
4. Run screening filters.
5. Score passing tickers with the accumulation scoring system.
6. Rank and return results.

Supports checkpointing: if the scan is interrupted, resume from the last
saved checkpoint instead of restarting.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from tqdm import tqdm

from analytics import enrich_results
from config import (
    CACHE_DIR,
    CHECKPOINT_INTERVAL,
    DOWNLOAD_THREADS,
    ENABLE_CHECKPOINT,
    LOG_DIR,
    MIN_MARKET_CAP,
    OUTPUT_DIR,
    SCAN_THREADS,
    SCORING_VERSION,
)
from downloader import (
    TickerInfo,
    _load_cache,
    build_ticker_universe,
    download_batch,
    download_ticker,
    get_market_cap,
    normalize_data_source,
)
from filters import run_all_filters
from indicators import compute_all_indicators
from score import ScoreBreakdown, classify_style, score_ticker

logger = logging.getLogger("institution_scanner.scanner")
logger.setLevel(logging.DEBUG)

_fh = logging.FileHandler(LOG_DIR / "scanner.log", mode="a")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_fh)


_SCAN_RECOVERABLE_ERRORS = (OSError, ValueError, TypeError, KeyError, IndexError)


# ======================================================================
# Scan result container
# ======================================================================


@dataclass
class ScanResult:
    """Full result for one scanned ticker."""

    ticker: str
    name: str = ""
    sector: str = ""
    industry: str = ""
    is_etf: bool = False
    asset_type: str = "stock"
    close: float = 0.0
    score: ScoreBreakdown = field(default_factory=ScoreBreakdown)
    score_missing_indicators: int = 0
    score_coverage: float = 1.0
    score_confidence: float = 1.0
    obv: float = np.nan
    cmf: float = np.nan
    ad: float = np.nan
    atr14: float = np.nan
    rsi14: float = np.nan
    dist_to_low_52w: float = np.nan
    wyckoff_phase: str = "Unknown"
    volume_accum_days: int = 0
    passed_filters: bool = False
    filter_details: dict[str, bool | int] = field(default_factory=dict)
    error: str = ""
    style: str = "均衡"
    market_regime: str = "未知"
    market_regime_reason: str = ""
    industry_relative_strength: float = np.nan
    stage: str = "未知"
    data_source: str = ""
    data_asof: str = ""
    data_age_days: int = -1
    data_trading_age_days: int = -1
    data_coverage: float = 0.0
    backtest_score: float = np.nan
    backtest_samples: int = 0
    backtest_win_rate_20d: float = np.nan
    backtest_win_rate_60d: float = np.nan
    backtest_average_return_20d: float = np.nan
    backtest_average_return_60d: float = np.nan
    backtest_objective_value: float = np.nan
    composite_score: float = np.nan
    universe_type: str = "current_survivor_pool"
    survivorship_bias_warning: bool = True


# ======================================================================
# Checkpointing
# ======================================================================

_CHECKPOINT_PATH = OUTPUT_DIR / "_checkpoint.json"


def _normalize_ticker(ticker: str) -> str:
    return str(ticker).strip().upper()


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "是"}
    return bool(value)


def _parse_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return int(parsed) if np.isfinite(parsed) else default


def _parse_float(value: Any, default: float = np.nan) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if np.isfinite(parsed) else default


def save_checkpoint(processed: set[str], data_source: str = "") -> None:
    """Save the set of already-processed tickers."""
    if not ENABLE_CHECKPOINT:
        return
    try:
        data = {
            "active": True,
            "processed": sorted(_normalize_ticker(ticker) for ticker in processed),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data_source": normalize_data_source(data_source) if data_source else "",
            "scoring_version": SCORING_VERSION,
        }
        _CHECKPOINT_PATH.write_text(json.dumps(data), encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Failed to save checkpoint: %s", exc)


def load_checkpoint(data_source: str = "") -> set[str]:
    """Load previously-processed tickers when checkpoint metadata matches."""
    if not _CHECKPOINT_PATH.exists():
        return set()
    try:
        data = json.loads(_CHECKPOINT_PATH.read_text(encoding="utf-8"))
        if not data.get("active"):
            return set()
        if data.get("scoring_version") != SCORING_VERSION:
            return set()
        expected_source = normalize_data_source(data_source) if data_source else ""
        if expected_source and data.get("data_source") != expected_source:
            return set()
        return {_normalize_ticker(ticker) for ticker in data.get("processed", [])}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return set()


def _load_previous_tickers() -> set[str]:
    prev_parquet = OUTPUT_DIR / "AllResults.parquet"
    if not prev_parquet.exists():
        return set()
    try:
        prev_df = pd.read_parquet(prev_parquet, columns=["Ticker"])
        return {_normalize_ticker(ticker) for ticker in prev_df["Ticker"].dropna()}
    except (OSError, ImportError, KeyError, ValueError):
        return set()


def clear_checkpoint() -> None:
    """Remove the checkpoint file."""
    if _CHECKPOINT_PATH.exists():
        _CHECKPOINT_PATH.unlink(missing_ok=True)


# ======================================================================
# Single-ticker scan
# ======================================================================


def scan_single_from_df(
    ticker_info: TickerInfo,
    df: pd.DataFrame | None,
    indicators_computed: bool = False,
) -> ScanResult:
    """
    Run indicators → filters → score on an already-downloaded DataFrame.

    Returns a ScanResult regardless of whether the ticker passes or fails.
    """
    ticker = _normalize_ticker(ticker_info.ticker)
    ticker_info.ticker = ticker

    try:
        if df is None or df.empty or len(df) < 20:
            return ScanResult(
                ticker=ticker,
                name=ticker_info.name,
                sector=ticker_info.sector,
                industry=ticker_info.industry,
                is_etf=ticker_info.is_etf,
                asset_type=ticker_info.asset_type,
                error="Insufficient data",
            )

        # Pre-filter: skip tickers below the minimum market cap
        market_cap = ticker_info.market_cap
        if market_cap is None and not ticker_info.is_etf:
            try:
                market_cap = get_market_cap(ticker)
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                return ScanResult(
                    ticker=ticker,
                    name=ticker_info.name,
                    sector=ticker_info.sector,
                    industry=ticker_info.industry,
                    is_etf=ticker_info.is_etf,
                    asset_type=ticker_info.asset_type,
                    error=f"市值获取失败: {exc}",
                )
        if (
            not ticker_info.is_etf
            and market_cap is not None
            and market_cap < MIN_MARKET_CAP
        ):
            return ScanResult(
                ticker=ticker,
                name=ticker_info.name,
                sector=ticker_info.sector,
                industry=ticker_info.industry,
                is_etf=ticker_info.is_etf,
                error=f"市值 {market_cap:,.0f} 元低于最低要求 {MIN_MARKET_CAP:,.0f} 元",
            )

        if not indicators_computed:
            df = compute_all_indicators(df.copy())
        close = float(df["Close"].iloc[-1])

        # ---- 3. Filters ----
        filter_results = run_all_filters(
            df,
            market_cap=market_cap,
            require_market_cap=not ticker_info.is_etf,
        )
        passed = filter_results.all_passed()
        filter_map = {
            "min_price": filter_results.min_price.passed,
            "min_volume": filter_results.min_volume.passed,
            "min_market_cap": filter_results.min_market_cap.passed,
            "sufficient_history": filter_results.sufficient_history.passed,
            "signal_count": filter_results.signal_count(),
            "filter_count": filter_results.passed_count(),
            "bear_market": filter_results.bear_market.passed,
            "consolidation": filter_results.consolidation.passed,
            "volume_accumulation": filter_results.volume_accumulation.passed,
            "obv_divergence": filter_results.obv_divergence.passed,
            "cmf_positive": filter_results.cmf_positive.passed,
            "cmf_improving": bool(
                filter_results.cmf_positive.details.get("cmf_improving", False)
            ),
            "ad_slope": filter_results.ad_slope.passed,
            "volatility_contraction": filter_results.volatility_contraction.passed,
        }

        # ---- 4. Score ----
        sb = score_ticker(df, is_etf=ticker_info.is_etf)
        style = classify_style(df, is_etf=ticker_info.is_etf)

        # ---- 5. Extract snapshot values ----
        obv_val = df["OBV"].iloc[-1] if "OBV" in df.columns else np.nan
        cmf_val = df["CMF"].iloc[-1] if "CMF" in df.columns else np.nan
        ad_val = df["AD"].iloc[-1] if "AD" in df.columns else np.nan
        atr14_val = df["ATR14"].iloc[-1] if "ATR14" in df.columns else np.nan
        rsi14_val = df["RSI14"].iloc[-1] if "RSI14" in df.columns else np.nan
        dist_low = (
            df["DistToLow52W"].iloc[-1] if "DistToLow52W" in df.columns else np.nan
        )
        phase = (
            df["WyckoffPhase"].iloc[-1] if "WyckoffPhase" in df.columns else "Unknown"
        )
        vol_accum_days = int(
            filter_results.volume_accumulation.details.get("consecutive_days", 0)
        )

        return ScanResult(
            ticker=ticker,
            name=ticker_info.name,
            sector=ticker_info.sector,
            industry=ticker_info.industry,
            is_etf=ticker_info.is_etf,
            asset_type=ticker_info.asset_type,
            close=close,
            score=sb,
            score_missing_indicators=sb.missing_indicators,
            score_coverage=sb.indicator_coverage,
            score_confidence=sb.confidence,
            obv=obv_val,
            cmf=cmf_val,
            ad=ad_val,
            atr14=atr14_val,
            rsi14=rsi14_val,
            dist_to_low_52w=dist_low,
            wyckoff_phase=phase,
            volume_accum_days=vol_accum_days,
            passed_filters=passed,
            filter_details=filter_map,
            style=style,
        )

    except _SCAN_RECOVERABLE_ERRORS as exc:
        logger.debug("Error scanning %s: %s", ticker, exc)
        return ScanResult(
            ticker=ticker,
            name=ticker_info.name,
            is_etf=ticker_info.is_etf,
            error=str(exc),
        )


def scan_single(
    ticker_info: TickerInfo,
    force_download: bool = False,
    data_source: str = "eastmoney",
) -> ScanResult:
    """
    Full scan pipeline for one ticker:
      download → indicators → filters → score.

    Prefer scan_single_from_df() when data is already downloaded.
    """
    ticker = _normalize_ticker(ticker_info.ticker)
    ticker_info.ticker = ticker
    try:
        df = download_ticker(ticker, force=force_download, source=data_source)
        return scan_single_from_df(ticker_info, df)
    except _SCAN_RECOVERABLE_ERRORS as exc:
        logger.debug("Error scanning %s: %s", ticker, exc)
        return ScanResult(
            ticker=ticker,
            name=ticker_info.name,
            is_etf=ticker_info.is_etf,
            error=str(exc),
        )


# ======================================================================
# Full scan orchestration
# ======================================================================


@dataclass
class ScanReport:
    """Aggregated results from a full scan run."""

    results: list[ScanResult] = field(default_factory=list)
    total_tickers: int = 0
    successful: int = 0
    failed: int = 0
    passed_filters: int = 0
    elapsed_seconds: float = 0.0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


def run_scan(
    stock_universe: list[TickerInfo] | None = None,
    etf_universe: list[TickerInfo] | None = None,
    force_download: bool = False,
    resume: bool = True,
    data_source: str = "eastmoney",
    cache_first: bool = False,
) -> ScanReport:
    """
    Two-phase parallel scan across the entire ticker universe.

    Phase 1 — Download: parallel (ThreadPoolExecutor) data fetch via
    downloader.download_batch().  Tickers already cached are skipped
    unless force_download=True.  Bar the first ever run this phase is
    fast (most tickers only need a few incremental days).

    Phase 2 — Analyse: parallel indicator computation + scoring via
    ThreadPoolExecutor on the cached DataFrames.  This is CPU-bound
    and scales well with more workers.

    Args:
        stock_universe: Optional pre-built list of stocks.
        etf_universe: Optional pre-built list of ETFs.
        force_download: If True, re-download all data.
        resume: If True, load checkpoint and skip already-analysed tickers.

    Returns:
        ScanReport with ranked results.
    """

    start_time = time.time()
    data_source = normalize_data_source(data_source)
    if force_download:
        resume = False

    # ---- Build universe ----
    if stock_universe is None and etf_universe is None:
        stock_universe, etf_universe = build_ticker_universe(
            include_stocks=True,
            include_etfs=True,
        )

    all_tickers: list[TickerInfo] = []
    if stock_universe:
        all_tickers.extend(stock_universe)
    if etf_universe:
        all_tickers.extend(etf_universe)

    # Deduplicate by ticker
    seen: set[str] = set()
    unique: list[TickerInfo] = []
    for ti in all_tickers:
        ti.ticker = _normalize_ticker(ti.ticker)
        tup = ti.ticker
        if tup not in seen:
            seen.add(tup)
            unique.append(ti)
    all_tickers = unique

    # ---- Phase 1: Parallel download ----
    logger.info(
        "Phase 1/2: downloading data for %d tickers (%d threads)...",
        len(all_tickers),
        DOWNLOAD_THREADS,
    )

    # On the first ever run, everything is a full download.
    # On subsequent runs, download_batch() still calls download_ticker()
    # which does incremental fetch for already-cached symbols.
    universe_symbols = {_normalize_ticker(ti.ticker) for ti in all_tickers}
    processed_set = load_checkpoint(data_source) if resume else set()
    processed_set.intersection_update(universe_symbols)
    if processed_set:
        logger.info("Resuming interrupted scan: %d tickers already analysed.", len(processed_set))
    downloaded = download_batch(
        all_tickers,
        desc="Downloading",
        force=force_download,
        source=data_source,
        cache_first=cache_first and not force_download,
        skip_tickers=processed_set if resume else None,
    )

    # ---- Phase 2: Parallel analyse ----
    processed_set = load_checkpoint(data_source) if resume else set()
    previous_tickers = _load_previous_tickers() if resume else set()
    processed_set.intersection_update(previous_tickers)
    processed_set.intersection_update(universe_symbols)

    downloaded_frames = {
        _normalize_ticker(ticker): frame for ticker, frame in downloaded.items()
    }
    downloaded_symbols = set(downloaded_frames)
    analyse_queue: list[TickerInfo] = []
    skipped_no_cache = 0
    for ti in all_tickers:
        ticker = _normalize_ticker(ti.ticker)
        if ticker in processed_set:
            continue
        if ticker in downloaded_symbols and downloaded_frames.get(ticker) is not None:
            analyse_queue.append(ti)
        else:
            skipped_no_cache += 1

    logger.info(
        "Phase 2/2: analysing %d tickers (%d threads) — %d already processed, %d without valid cache. Universe=%d, downloaded=%d.",
        len(analyse_queue),
        SCAN_THREADS,
        len(processed_set),
        skipped_no_cache,
        len(all_tickers),
        len(downloaded_symbols),
    )

    results: list[ScanResult] = []
    analysed_frames: dict[str, pd.DataFrame] = {}
    analysed_this_run: set[str] = set()
    successful: int = 0
    failed: int = 0
    passed: int = 0

    prev_parquet = OUTPUT_DIR / "AllResults.parquet"
    prev_results: dict[str, ScanResult] = {}
    universe_symbols = {_normalize_ticker(ti.ticker) for ti in all_tickers}
    previous_report_source = ""
    if prev_parquet.exists():
        try:
            metadata = pd.read_parquet(prev_parquet, columns=["DataSource"])
            if not metadata.empty:
                previous_report_source = str(
                    metadata["DataSource"].dropna().iloc[0] or ""
                )
        except (OSError, ImportError, KeyError, ValueError):
            previous_report_source = ""

    if resume and prev_parquet.exists() and previous_report_source in ("", data_source):
        try:
            prev_df = pd.read_parquet(prev_parquet)
            for _, row in prev_df.iterrows():
                ticker = _normalize_ticker(str(row.get("Ticker", "") or ""))
                sr = ScanResult(
                    ticker=ticker,
                    name=str(row.get("Name", "") or ""),
                    sector=str(row.get("Sector", "") or ""),
                    industry=str(row.get("Industry", "") or ""),
                    is_etf=_parse_bool(row.get("IsETF", False)),
                    close=_parse_float(row.get("Close", 0), 0.0),
                    score=ScoreBreakdown(
                        total=_parse_float(row.get("Score", 0), 0.0),
                        trend=_parse_float(row.get("TrendScore", 0), 0.0),
                        volume=_parse_float(row.get("VolumeScore", 0), 0.0),
                        accumulation=_parse_float(row.get("AccumulationScore", 0), 0.0),
                        volatility=_parse_float(row.get("CompressionScore", 0), 0.0),
                        structure=_parse_float(row.get("StructureScore", 0), 0.0),
                        missing_indicators=_parse_int(
                            row.get("ScoreMissingIndicators", 0), 0
                        ),
                        indicator_coverage=_parse_float(
                            row.get("ScoreCoverage", 1.0), 1.0
                        ),
                        confidence=_parse_float(
                            row.get("ScoreConfidence", row.get("ScoreCoverage", 1.0)),
                            1.0,
                        ),
                        contributions={
                            "trend": _parse_float(
                                row.get(
                                    "ScoreContributionTrend", row.get("TrendScore", 0)
                                ),
                                0.0,
                            ),
                            "volume": _parse_float(
                                row.get(
                                    "ScoreContributionVolume", row.get("VolumeScore", 0)
                                ),
                                0.0,
                            ),
                            "accumulation": _parse_float(
                                row.get(
                                    "ScoreContributionAccumulation",
                                    row.get("AccumulationScore", 0),
                                ),
                                0.0,
                            ),
                            "compression": _parse_float(
                                row.get(
                                    "ScoreContributionCompression",
                                    row.get("CompressionScore", 0),
                                ),
                                0.0,
                            ),
                            "structure": _parse_float(
                                row.get(
                                    "ScoreContributionStructure",
                                    row.get("StructureScore", 0),
                                ),
                                0.0,
                            ),
                        },
                    ),
                    score_missing_indicators=_parse_int(
                        row.get("ScoreMissingIndicators", 0), 0
                    ),
                    score_coverage=_parse_float(row.get("ScoreCoverage", 1.0), 1.0),
                    score_confidence=_parse_float(
                        row.get("ScoreConfidence", row.get("ScoreCoverage", 1.0)), 1.0
                    ),
                    backtest_score=_parse_float(row.get("BacktestScore", np.nan)),
                    backtest_samples=_parse_int(row.get("BacktestSamples", 0), 0),
                    backtest_win_rate_20d=_parse_float(
                        row.get("BacktestWinRate20D", np.nan)
                    ),
                    backtest_win_rate_60d=_parse_float(
                        row.get("BacktestWinRate60D", np.nan)
                    ),
                    backtest_average_return_20d=_parse_float(
                        row.get("BacktestAverageReturn20D", np.nan)
                    ),
                    backtest_average_return_60d=_parse_float(
                        row.get("BacktestAverageReturn60D", np.nan)
                    ),
                    backtest_objective_value=_parse_float(
                        row.get("BacktestObjectiveValue", np.nan)
                    ),
                    composite_score=_parse_float(row.get("CompositeScore", np.nan)),
                    universe_type=str(
                        row.get("UniverseType", "current_survivor_pool")
                        or "current_survivor_pool"
                    ),
                    survivorship_bias_warning=_parse_bool(
                        row.get("SurvivorshipBiasWarning", True), True
                    ),
                    obv=_parse_float(row.get("OBV", np.nan)),
                    cmf=_parse_float(row.get("CMF", np.nan)),
                    ad=_parse_float(row.get("AD", np.nan)),
                    atr14=_parse_float(row.get("ATR14", np.nan)),
                    rsi14=_parse_float(row.get("RSI14", np.nan)),
                    dist_to_low_52w=_parse_float(row.get("DistToLow52W", np.nan)),
                    wyckoff_phase=str(row.get("WyckoffPhase", "Unknown") or "Unknown"),
                    volume_accum_days=_parse_int(row.get("VolAccumDays", 0), 0),
                    passed_filters=_parse_bool(row.get("PassedFilters", False)),
                    style=str(row.get("Style", "均衡")),
                    filter_details={
                        "obv_divergence": _parse_bool(row.get("OBV_Div", False)),
                        "cmf_positive": _parse_bool(row.get("CMF_Pos", False)),
                        "cmf_improving": _parse_bool(row.get("CMF_Improving", False)),
                        "ad_slope": _parse_bool(row.get("AD_SlopePos", False)),
                        "bear_market": _parse_bool(row.get("BearMarket", False)),
                        "consolidation": _parse_bool(row.get("Consolidation", False)),
                        "volume_accumulation": _parse_bool(row.get("VolAccum", False)),
                        "volatility_contraction": _parse_bool(
                            row.get("VolContract", False)
                        ),
                        "signal_count": _parse_int(row.get("SignalCount", 0), 0),
                        "filter_count": _parse_int(row.get("FilterCount", 0), 0),
                    },
                    error=str(row.get("Error", "") or ""),
                    market_regime=str(row.get("MarketRegime", "未知") or "未知"),
                    industry_relative_strength=_parse_float(
                        row.get("IndustryRelativeStrength", np.nan)
                    ),
                    stage=str(row.get("Stage", "未知") or "未知"),
                    data_source=str(row.get("DataSource", "") or ""),
                    data_asof=str(row.get("DataAsOf", "") or ""),
                    data_age_days=_parse_int(row.get("DataAgeDays", -1), -1),
                    data_trading_age_days=_parse_int(
                        row.get("DataTradingAgeDays", -1), -1
                    ),
                    data_coverage=_parse_float(row.get("DataCoverage", 0.0), 0.0),
                )
                if sr.ticker in universe_symbols and sr.ticker in processed_set:
                    prev_results[sr.ticker] = sr
        except (OSError, ImportError, KeyError, TypeError, ValueError, IndexError) as exc:
            logger.debug("Could not load previous scan results: %s", exc)

    with ThreadPoolExecutor(max_workers=SCAN_THREADS) as executor:
        max_pending = max(SCAN_THREADS * 4, SCAN_THREADS)
        ticker_iter = iter(analyse_queue)
        futures: dict[Any, TickerInfo] = {}

        def submit_next() -> bool:
            try:
                ti = next(ticker_iter)
            except StopIteration:
                return False
            ticker = _normalize_ticker(ti.ticker)
            futures[
                executor.submit(
                    _analyse_one_ticker_from_df,
                    ti,
                    downloaded_frames[ticker],
                )
            ] = ti
            return True

        for _ in range(min(max_pending, len(analyse_queue))):
            submit_next()

        completed = 0
        with tqdm(
            total=len(analyse_queue),
            desc="Analysing",
            unit="ticker",
            disable=not sys.stderr.isatty(),
        ) as progress:
            while futures:
                future = next(as_completed(futures))
                ti = futures.pop(future)
                completed += 1
                try:
                    result, frame = future.result(timeout=120)
                except _SCAN_RECOVERABLE_ERRORS as exc:
                    logger.warning("Analysis error for %s: %s", ti.ticker, exc)
                    result, frame = ScanResult(ticker=ti.ticker, error=str(exc)), None

                results.append(result)
                if frame is not None:
                    analysed_frames[result.ticker] = frame

                if result.error:
                    failed += 1
                    logger.warning("Analysis failed for %s: %s", ti.ticker, result.error)
                else:
                    successful += 1
                    if result.passed_filters:
                        passed += 1

                if not result.error:
                    processed_set.add(ti.ticker)
                analysed_this_run.add(ti.ticker)
                progress.update(1)

                if len(analysed_this_run) % 100 == 0 or len(analysed_this_run) == len(
                    analyse_queue
                ):
                    logger.info(
                        "ANALYSE progress: %d/%d (%d successful, %d failed).",
                        completed,
                        len(analyse_queue),
                        successful,
                        failed,
                    )

                if ENABLE_CHECKPOINT and len(analysed_this_run) % CHECKPOINT_INTERVAL == 0:
                    save_checkpoint(processed_set, data_source)

                submit_next()
    # Merge previous results for tickers we didn't re-analyse
    for ticker, sr in prev_results.items():
        if ticker not in analysed_this_run:
            results.append(sr)
            successful += 1
            if sr.passed_filters:
                passed += 1

    clear_checkpoint()

    logger.info("Enriching %d scan results...", len(results))
    try:
        enrich_results(results, data_source, frames=analysed_frames)
    except _SCAN_RECOVERABLE_ERRORS:
        logger.exception("Failed to enrich scan results; continuing with base results")
    logger.info("Enrichment complete: %d scan results.", len(results))

    # Sort by score descending
    results.sort(key=lambda r: r.score.total, reverse=True)

    elapsed = time.time() - start_time

    report = ScanReport(
        results=results,
        total_tickers=len(all_tickers),
        successful=successful,
        failed=failed,
        passed_filters=passed,
        elapsed_seconds=elapsed,
    )

    logger.info(
        "Scan complete: %d successful, %d failed, %d passed filters, %.1f seconds.",
        successful,
        failed,
        passed,
        elapsed,
    )

    return report


def _cache_path_for(ticker: str, source: str) -> Path:
    safe = _normalize_ticker(ticker).replace("/", "_").replace("\\", "_")
    return CACHE_DIR / f"{safe}__{normalize_data_source(source)}.parquet"


def _analyse_one_ticker_from_df(
    ticker_info: TickerInfo,
    df: pd.DataFrame | None,
) -> tuple[ScanResult, pd.DataFrame | None]:
    if df is None:
        return scan_single_from_df(ticker_info, df), None
    enriched = compute_all_indicators(df.copy())
    result = scan_single_from_df(ticker_info, enriched, indicators_computed=True)
    return result, enriched if not result.error else None


def _analyse_one_ticker(
    ticker_info: TickerInfo, data_source: str = "eastmoney"
) -> ScanResult:
    """Load cached CSV and run the analysis pipeline.  Sits inside a ThreadPool."""
    ticker = ticker_info.ticker
    try:
        df = _load_cache(ticker, data_source)
        if df is None or df.empty or len(df) < 20:
            return ScanResult(
                ticker=ticker,
                name=ticker_info.name,
                sector=ticker_info.sector,
                industry=ticker_info.industry,
                is_etf=ticker_info.is_etf,
                error="No cached data",
            )
        return _analyse_one_ticker_from_df(ticker_info, df)[0]
    except _SCAN_RECOVERABLE_ERRORS as exc:
        return ScanResult(ticker=ticker, name=ticker_info.name, error=str(exc))


# ======================================================================
# Parallel indicator computation (alternative fast path)
# ======================================================================


def run_parallel_indicator_scan(
    tickers: list[TickerInfo],
    max_workers: int = SCAN_THREADS,
    data_source: str = "eastmoney",
) -> list[ScanResult]:
    """
    Compute indicators and scores in parallel using ThreadPoolExecutor.

    Data must already be cached. This is the fast path for re-scans.
    """
    results: list[ScanResult] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_analyse_one_ticker, ti, data_source): ti for ti in tickers
        }
        for future in tqdm(
            as_completed(futures),
            total=len(futures),
            desc="Parallel scan",
            unit="ticker",
            disable=not sys.stderr.isatty(),
        ):
            try:
                result = future.result(timeout=60)
                results.append(result)
            except _SCAN_RECOVERABLE_ERRORS as exc:
                ti = futures[future]
                logger.warning("Scan timeout/error for %s: %s", ti.ticker, exc)
                results.append(
                    ScanResult(
                        ticker=ti.ticker,
                        error=str(exc),
                    )
                )

    results.sort(key=lambda r: r.score.total, reverse=True)
    return results
