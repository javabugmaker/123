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
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from tqdm import tqdm

from analytics import enrich_results
from classification import etf_theme_key, etf_tracking_key, model_classification, theme_cluster
from config import (
    CHECKPOINT_INTERVAL,
    ENABLE_CHECKPOINT,
    INDICATOR_CACHE_ENABLED,
    LOG_DIR,
    OUTPUT_DIR,
    SCAN_THREADS,
    SCORING_VERSION,
    TICKFLOW_MAX_WORKERS,
    setup_logging,
)
from downloader import (
    TickerInfo,
    _cache_path,
    _load_cache,
    build_ticker_universe,
    download_batch,
    download_ticker,
    get_market_cap,
    normalize_data_source,
)
from filters import run_all_filters
from fundamental_quality import get_quality
from indicators import compute_all_indicators, true_range, wilder_average
from performance_cache import load_or_compute_indicators
from score import (
    ScoreBreakdown,
    classify_style,
    entry_point,
    score_ticker,
    smart_money_stage,
    tradable_price_decimals,
)

logger = setup_logging(
    "institution_scanner.scanner",
    level=logging.DEBUG,
    log_to_file=True,
    log_dir=LOG_DIR,
)

_SCAN_RECOVERABLE_ERRORS = (OSError, ValueError, TypeError, KeyError, IndexError)

ScanProgressCallback = Callable[[str, int, int, str], None]


class ScanCancelled(RuntimeError):
    """Raised when an in-process caller requests cooperative cancellation."""


def _emit_progress(
    callback: ScanProgressCallback | None,
    stage: str,
    current: int,
    total: int,
    message: str,
) -> None:
    if callback is None:
        return
    try:
        callback(stage, int(current), int(total), str(message))
    except Exception:
        logger.debug("Scan progress callback failed.", exc_info=True)


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ScanCancelled("扫描已取消")


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
    atr50: float = np.nan
    atr_expansion_source: str = ""
    rsi14: float = np.nan
    dist_to_low_52w: float = np.nan
    dist_to_ma20: float = np.nan
    dist_to_ma50: float = np.nan
    recent_return_20d: float = np.nan
    atr_expansion: float = np.nan
    wyckoff_phase: str = "Unknown"
    volume_accum_days: int = 0
    base_score: float = np.nan
    breakout_score: float = np.nan
    smart_money_stage: str = "NONE"
    entry_score: float = np.nan
    entry_signal: str = "AVOID"
    raw_entry_signal: str = "AVOID"
    entry_zone: str = ""
    entry_zone_distance_pct: float = np.nan
    entry_zone_distance_atr: float = np.nan
    pullback_quality_score: float = np.nan
    breakout_buy_price: float = np.nan
    breakout_volume_ratio: float = np.nan
    breakout_volume_confirmed: bool = False
    breakout_flow_confirmed: bool = False
    price_breakout: bool = False
    stop_loss: float = np.nan
    projected_target: float = np.nan
    stop_distance_pct: float = np.nan
    reward_risk_ratio: float = np.nan
    value_trap_risk: float = np.nan
    risk_warning: str = ""
    operation_advice: str = ""
    trigger_score: float = np.nan
    final_score: float = np.nan
    passed_filters: bool = False
    universe_eligible: bool = False
    signal_confirmed: bool = False
    failed_filter_count: int = 0
    failed_filter_names: str = ""
    filter_details: dict[str, bool | int] = field(default_factory=dict)
    error: str = ""
    style: str = "均衡"
    market_regime: str = "未知"
    market_regime_reason: str = ""
    market_regime_fast: str = "未知"
    market_regime_slow: str = "未知"
    market_regime_confidence: float = 0.0
    industry_relative_strength: float = np.nan
    industry_momentum_60d: float = np.nan
    sector_confirmation_factor: float = 1.0
    failure_signal_factor: float = 1.0
    failure_adjusted_score: float = np.nan
    signal_recency_factor: float = 1.0
    signal_recency_days: int = -1
    breakout_quality_factor: float = 1.0
    pre_backtest_institutional_score: float = np.nan
    institutional_score: float = np.nan
    quality_roe: float = np.nan
    quality_gross_margin: float = np.nan
    quality_institution_holding_trend: Any = None
    quality_institution_holding_periods: float = np.nan
    quality_net_profit_y1: float = np.nan
    quality_net_profit_y2: float = np.nan
    quality_net_profit_y3: float = np.nan
    quality_industry_gross_margin_percentile: float = np.nan
    quality_roe_factor: bool = False
    quality_gross_margin_factor: bool = False
    quality_institution_holding_factor: bool = False
    quality_net_profit_factor: bool = False
    quality_score: float = np.nan
    quality_gate: bool = True
    quality_reason: str = "基本面数据缺失（中性）"
    quality_data_available: bool = False
    quality_applicable: bool = True
    quality_institution_holding_status: str = "UNKNOWN"
    quality_data_completeness: float = 0.0
    quality_hard_data_complete: bool = False
    quality_gate_reason: str = "基本面数据缺失（中性）"
    quality_multiplier: float = 0.95
    quality_profile: str = "GENERAL"
    quality_profit_trend_status: str = "UNKNOWN"
    cyclical_quality_override: bool = False
    stage: str = "未知"
    data_source: str = ""
    data_asof: str = ""
    data_age_days: int = -1
    data_trading_age_days: int = -1
    data_coverage: float = 0.0
    backtest_score: float = np.nan
    backtest_reliability: float = np.nan
    backtest_samples: int = 0
    backtest_effective_samples: float = 0.0
    backtest_win_rate_20d: float = np.nan
    backtest_win_rate_60d: float = np.nan
    backtest_average_return_20d: float = np.nan
    backtest_average_return_60d: float = np.nan
    backtest_objective_value: float = np.nan
    backtest_effective_weight: float = 0.0
    backtest_confidence_tier: str = "样本不足"
    backtest_adjusted_score: float = np.nan
    backtest_median_return_20d: float = np.nan
    backtest_median_return_60d: float = np.nan
    backtest_max_drawdown_20d: float = np.nan
    backtest_max_drawdown_60d: float = np.nan
    backtest_profit_factor: float = np.nan
    backtest_signal_span_days: int = 0
    backtest_return_std_20d: float = np.nan
    backtest_mode: str = ""
    backtest_cache_hit: bool = False
    backtest_last_evaluated_date: str = ""
    backtest_engine: str = ""
    backtest_status: str = ""
    global_calibration_score: float = np.nan
    global_calibration_confidence: float = 0.0
    global_calibration_level: str = "none"
    composite_score: float = np.nan
    chase_risk_score: float = 0.0
    chase_risk_level: str = "低"
    chase_risk_reason: str = ""
    hard_risk_flag: bool = False
    hard_risk_penalty: float = 1.0
    hard_risk_reason: str = ""
    ranking_penalty_reason: str = ""
    ranking_eligibility: str = "观察"
    ranking_score: float = np.nan
    overall_rank: int = 0
    ranking_reason: str = ""
    decision_state: str = "OBSERVE"
    decision_reason: str = ""
    trade_readiness: str = "观察"
    research_tier: str = ""
    model_classification: str = ""
    etf_tracking_key: str = ""
    theme_cluster: str = ""
    technical_institutional_score: float = np.nan
    asset_percentile: float = np.nan
    cross_asset_score: float = np.nan
    institutional_percentile: float = np.nan
    institutional_rank: int = 0
    institutional_tier_reason: str = ""
    signal_adjustment_reason: str = ""
    opportunity_stage: str = "未知"
    universe_type: str = "current_survivor_pool"
    survivorship_bias_warning: bool = True


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


def _quality_hard_data_complete_from_row(row: pd.Series) -> bool:
    """Read v43 completeness or derive it from legacy fundamental columns."""
    if "QualityHardDataComplete" in row.index and pd.notna(
        row.get("QualityHardDataComplete")
    ):
        return _parse_bool(row.get("QualityHardDataComplete"), False)
    if _parse_bool(row.get("IsETF", False)):
        return True
    profile = str(row.get("QualityProfile", "GENERAL") or "GENERAL").upper()
    roe_available = np.isfinite(_parse_float(row.get("ROE", np.nan)))
    profit_available = all(
        np.isfinite(_parse_float(row.get(column, np.nan)))
        for column in ("NetProfitY1", "NetProfitY2", "NetProfitY3")
    )
    margin_available = np.isfinite(
        _parse_float(row.get("IndustryGrossMarginPercentile", np.nan))
    )
    margin_required = profile not in {"FINANCIAL", "DEFENSIVE", "ETF"}
    return bool(
        roe_available
        and profit_available
        and (not margin_required or margin_available)
    )


def _latest_atr_from_ohlc(df: pd.DataFrame, period: int) -> float:
    if df is None or len(df) < period or not {"High", "Low", "Close"}.issubset(df.columns):
        return np.nan
    value = wilder_average(true_range(df), period).iloc[-1]
    return _parse_float(value, np.nan)


def _checkpoint_trade_date(now: datetime | None = None) -> str:
    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if current.tzinfo is None:
        current = current.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    else:
        current = current.astimezone(ZoneInfo("Asia/Shanghai"))
    return current.date().isoformat()


def save_checkpoint(processed: set[str], data_source: str = "") -> None:
    if not ENABLE_CHECKPOINT:
        return
    try:
        data = {
            "active": True,
            "processed": sorted(_normalize_ticker(ticker) for ticker in processed),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trade_date": _checkpoint_trade_date(),
            "data_source": normalize_data_source(data_source) if data_source else "",
            "scoring_version": SCORING_VERSION,
        }
        _CHECKPOINT_PATH.write_text(json.dumps(data), encoding="utf-8")
    except (OSError, TypeError, ValueError) as exc:
        logger.warning("Failed to save checkpoint: %s", exc)


def load_checkpoint(data_source: str = "") -> set[str]:
    if not _CHECKPOINT_PATH.exists():
        return set()
    try:
        data = json.loads(_CHECKPOINT_PATH.read_text(encoding="utf-8"))
        if not data.get("active"):
            return set()
        if data.get("trade_date") != _checkpoint_trade_date():
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
    try:
        if _CHECKPOINT_PATH.exists():
            _CHECKPOINT_PATH.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Failed to remove checkpoint: %s", exc)


def scan_single_from_df(
    ticker_info: TickerInfo,
    df: pd.DataFrame | None,
    indicators_computed: bool = False,
) -> ScanResult:
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

        if not indicators_computed:
            df = compute_all_indicators(df.copy())
        close = _parse_float(df["Close"].iloc[-1], np.nan)
        if not np.isfinite(close):
            return ScanResult(
                ticker=ticker,
                name=ticker_info.name,
                sector=ticker_info.sector,
                industry=ticker_info.industry,
                is_etf=ticker_info.is_etf,
                asset_type=ticker_info.asset_type,
                error="最新收盘价无效",
            )

        market_cap = ticker_info.market_cap
        if market_cap is None and not ticker_info.is_etf:
            shares = _parse_float(ticker_info.total_shares, np.nan)
            if np.isfinite(shares) and shares > 0:
                market_cap = float(shares * close)
            else:
                try:
                    market_cap = get_market_cap(ticker)
                except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                    logger.warning(
                        "Market-cap metadata unavailable for %s; treating as unknown: %s",
                        ticker,
                        exc,
                    )
                    market_cap = None

        # Market-cap evidence participates in the ordinary filter gate.  A low
        # or missing value must never become ScanResult.error, because errors
        # are excluded from every candidate export and can silently collapse
        # the entire stock research pool when provider metadata changes scale.
        filter_results = run_all_filters(
            df,
            market_cap=market_cap,
            require_market_cap=not ticker_info.is_etf,
        )
        filter_map = {
            "min_price": filter_results.min_price.passed,
            "min_volume": filter_results.min_volume.passed,
            "min_market_cap": filter_results.min_market_cap.passed,
            "market_cap": (
                float(market_cap)
                if market_cap is not None and np.isfinite(float(market_cap))
                else None
            ),
            "market_cap_available": bool(
                market_cap is not None and np.isfinite(float(market_cap))
            ),
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

        base_filter_states = {
            "min_price": True if ticker_info.is_etf else filter_results.min_price.passed,
            "min_volume": filter_results.min_volume.passed,
            "min_market_cap": True if ticker_info.is_etf else filter_results.min_market_cap.passed,
            "sufficient_history": filter_results.sufficient_history.passed,
        }
        accumulation_states = {
            "volume_accumulation": filter_results.volume_accumulation.passed,
            "obv_divergence": filter_results.obv_divergence.passed,
            "cmf_positive": filter_results.cmf_positive.passed,
            "ad_slope": filter_results.ad_slope.passed,
        }
        structure_states = {
            "consolidation": filter_results.consolidation.passed,
            "volatility_contraction": filter_results.volatility_contraction.passed,
        }
        universe_eligible = all(base_filter_states.values())
        signal_confirmed = bool(
            sum(bool(value) for value in accumulation_states.values()) >= 2
            and any(bool(value) for value in structure_states.values())
        )
        passed = bool(universe_eligible and signal_confirmed)
        filter_map.update(base_filter_states)
        failed_filter_names = [
            name
            for name, state in {**base_filter_states, **accumulation_states, **structure_states}.items()
            if not state
        ]

        sb = score_ticker(df, is_etf=ticker_info.is_etf)
        style = classify_style(df, is_etf=ticker_info.is_etf)

        obv_val = df["OBV"].iloc[-1] if "OBV" in df.columns else np.nan
        cmf_val = df["CMF"].iloc[-1] if "CMF" in df.columns else np.nan
        ad_val = df["AD"].iloc[-1] if "AD" in df.columns else np.nan
        atr14_val = df["ATR14"].iloc[-1] if "ATR14" in df.columns else np.nan
        rsi14_val = df["RSI14"].iloc[-1] if "RSI14" in df.columns else np.nan
        dist_low = (
            df["DistToLow52W"].iloc[-1]
            if "DistToLow52W" in df.columns
            else np.nan
        )
        ma20_value = (
            _parse_float(df["MA20"].iloc[-1], np.nan)
            if "MA20" in df.columns
            else np.nan
        )
        ma50_value = (
            _parse_float(df["MA50"].iloc[-1], np.nan)
            if "MA50" in df.columns
            else np.nan
        )
        dist_ma20 = (
            ((close / ma20_value) - 1.0) * 100.0
            if np.isfinite(ma20_value) and ma20_value > 0
            else np.nan
        )
        dist_ma50 = (
            ((close / ma50_value) - 1.0) * 100.0
            if np.isfinite(ma50_value) and ma50_value > 0
            else np.nan
        )
        recent_return_20d = (
            (close / _parse_float(df["Close"].iloc[-21], np.nan) - 1.0) * 100.0
            if len(df) >= 21 and _parse_float(df["Close"].iloc[-21], np.nan) > 0
            else np.nan
        )
        atr_expansion_source = "indicator"
        if not np.isfinite(atr14_val):
            atr14_val = _latest_atr_from_ohlc(df, 14)
            atr_expansion_source = "ohlc_fallback"
        atr50_value = (
            _parse_float(df["ATR50"].iloc[-1], np.nan)
            if "ATR50" in df.columns
            else np.nan
        )
        if not np.isfinite(atr50_value):
            atr50_value = _latest_atr_from_ohlc(df, 50)
            atr_expansion_source = "ohlc_fallback"
        atr_expansion = (
            atr14_val / atr50_value
            if np.isfinite(atr14_val)
            and np.isfinite(atr50_value)
            and atr50_value > 0
            else np.nan
        )
        phase = (
            df["WyckoffPhase"].iloc[-1]
            if "WyckoffPhase" in df.columns
            else "Unknown"
        )
        vol_accum_days = int(
            filter_results.volume_accumulation.details.get("consecutive_days", 0)
        )
        quality = get_quality(ticker, is_etf=ticker_info.is_etf)
        resolved_industry = str(
            ticker_info.industry or getattr(quality, "industry", "") or ""
        ).strip()
        if ticker_info.is_etf:
            resolved_sector = str(ticker_info.sector or "").strip() or etf_theme_key(
                name=ticker_info.name,
                industry=resolved_industry,
                sector=ticker_info.sector,
                ticker=ticker,
            )
        else:
            # TickFlow Free metadata does not consistently expose a separate sector.
            resolved_sector = str(ticker_info.sector or resolved_industry or "").strip()
        resolved_classification = model_classification(
            is_etf=ticker_info.is_etf,
            name=ticker_info.name,
            industry=resolved_industry,
            sector="" if ticker_info.is_etf else resolved_sector,
            ticker=ticker,
        )
        resolved_tracking_key = (
            etf_tracking_key(
                name=ticker_info.name,
                industry=resolved_industry,
                sector="",
                ticker=ticker,
            )
            if ticker_info.is_etf
            else ""
        )
        resolved_theme_cluster = theme_cluster(
            is_etf=ticker_info.is_etf,
            name=ticker_info.name,
            industry=resolved_industry,
            sector=resolved_sector,
            classification=resolved_classification,
            ticker=ticker,
        )
        breakout = _parse_float(getattr(sb, "breakout_score", np.nan), 0.0)
        trap = _parse_float(getattr(sb, "value_trap_risk", np.nan), 0.0)
        entry = entry_point(
            df,
            breakout,
            volume_score=_parse_float(getattr(sb, "volume", np.nan)),
            value_trap_risk_value=trap,
            price_decimals=tradable_price_decimals(ticker_info.is_etf),
        )
        smart_stage = smart_money_stage(df, breakout, trap)
        price_decimals = tradable_price_decimals(ticker_info.is_etf)
        entry_zone = (
            f"{entry['low']:.{price_decimals}f}-{entry['high']:.{price_decimals}f}"
            if np.isfinite(entry["low"]) and np.isfinite(entry["high"])
            else ""
        )
        risk_warning = "价值陷阱风险偏高" if trap >= 60 else ""
        operation_advice = {
            "BUY_NOW": "回调至买入区间可分批介入",
            "BREAKOUT_CONFIRM": "放量突破确认后跟随，控制仓位",
            "PRICE_BREAKOUT": "价格已突破，等待成交量确认，暂不追高",
            "WAIT_VOLUME_CONFIRM": "等待成交量确认，暂不追高",
            "WAIT_PULLBACK": "等待回踩买入区间，不追高",
            "HOLD_WAIT": "保持观察，等待趋势和量能确认",
            "AVOID": "回避，等待结构改善",
        }.get(entry["signal"], "保持观察")

        return ScanResult(
            ticker=ticker,
            name=ticker_info.name,
            sector=resolved_sector,
            industry=resolved_industry,
            is_etf=ticker_info.is_etf,
            asset_type=ticker_info.asset_type,
            close=close,
            score=sb,
            score_missing_indicators=_parse_int(
                getattr(sb, "missing_indicators", 0), 0
            ),
            score_coverage=_parse_float(
                getattr(sb, "indicator_coverage", np.nan), 0.0
            ),
            score_confidence=_parse_float(
                getattr(sb, "confidence", np.nan), 0.0
            ),
            obv=obv_val,
            cmf=cmf_val,
            ad=ad_val,
            atr14=atr14_val,
            atr50=atr50_value,
            atr_expansion_source=atr_expansion_source if np.isfinite(atr_expansion) else "unavailable",
            rsi14=rsi14_val,
            dist_to_low_52w=dist_low,
            dist_to_ma20=dist_ma20,
            dist_to_ma50=dist_ma50,
            recent_return_20d=recent_return_20d,
            atr_expansion=atr_expansion,
            wyckoff_phase=phase,
            volume_accum_days=vol_accum_days,
            base_score=_parse_float(getattr(sb, "base_score", np.nan)),
            breakout_score=breakout,
            smart_money_stage=smart_stage,
            entry_score=_parse_float(entry["score"], 0.0),
            entry_signal=entry["signal"],
            raw_entry_signal=entry["signal"],
            entry_zone=entry_zone,
            entry_zone_distance_pct=_parse_float(entry.get("zone_distance_pct")),
            entry_zone_distance_atr=_parse_float(entry.get("zone_distance_atr")),
            pullback_quality_score=_parse_float(entry.get("pullback_quality")),
            breakout_buy_price=_parse_float(entry["breakout"]),
            breakout_volume_ratio=_parse_float(entry.get("volume_ratio")),
            breakout_volume_confirmed=_parse_bool(
                entry.get("volume_confirmed")
            ),
            breakout_flow_confirmed=_parse_bool(entry.get("flow_confirmed")),
            price_breakout=_parse_bool(entry.get("price_breakout")),
            stop_loss=_parse_float(entry["stop"]),
            projected_target=_parse_float(entry.get("projected_target")),
            stop_distance_pct=_parse_float(entry.get("stop_distance_pct")),
            reward_risk_ratio=_parse_float(entry.get("reward_risk_ratio")),
            value_trap_risk=trap,
            risk_warning=risk_warning,
            operation_advice=operation_advice,
            trigger_score=_parse_float(getattr(sb, "trigger_score", np.nan)),
            final_score=_parse_float(getattr(sb, "final_score", np.nan)),
            passed_filters=passed,
            universe_eligible=universe_eligible,
            signal_confirmed=signal_confirmed,
            failed_filter_count=len(failed_filter_names),
            failed_filter_names=",".join(failed_filter_names),
            filter_details=filter_map,
            style=style,
            quality_roe=quality.roe,
            quality_gross_margin=quality.gross_margin,
            quality_institution_holding_trend=quality.institution_holding_trend,
            quality_institution_holding_periods=quality.institution_holding_periods,
            quality_net_profit_y1=quality.net_profit_y1,
            quality_net_profit_y2=quality.net_profit_y2,
            quality_net_profit_y3=quality.net_profit_y3,
            quality_industry_gross_margin_percentile=quality.industry_gross_margin_percentile,
            quality_roe_factor=quality.roe_factor,
            quality_gross_margin_factor=quality.gross_margin_factor,
            quality_institution_holding_factor=quality.institution_holding_factor,
            quality_net_profit_factor=quality.net_profit_factor,
            quality_score=quality.quality_score,
            quality_gate=quality.quality_gate,
            quality_reason=quality.quality_reason,
            quality_data_available=quality.data_available,
            quality_applicable=getattr(quality, "applicable", not ticker_info.is_etf),
            quality_institution_holding_status=quality.institution_holding_status,
            quality_data_completeness=quality.quality_data_completeness,
            quality_hard_data_complete=getattr(
                quality, "quality_hard_data_complete", False
            ),
            quality_gate_reason=quality.quality_gate_reason,
            quality_multiplier=quality.quality_multiplier,
            quality_profile=getattr(quality, "quality_profile", "GENERAL"),
            quality_profit_trend_status=getattr(quality, "profit_trend_status", "UNKNOWN"),
            cyclical_quality_override=bool(getattr(quality, "cyclical_quality_override", False)),
            model_classification=resolved_classification,
            etf_tracking_key=resolved_tracking_key,
            theme_cluster=resolved_theme_cluster,
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
    data_source: str = "tickflow",
) -> ScanResult:
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


@dataclass
class ScanReport:
    results: list[ScanResult] = field(default_factory=list)
    total_tickers: int = 0
    successful: int = 0
    failed: int = 0
    passed_filters: int = 0
    elapsed_seconds: float = 0.0
    download_seconds: float = 0.0
    analysis_seconds: float = 0.0
    enrichment_seconds: float = 0.0
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


def run_scan(
    stock_universe: list[TickerInfo] | None = None,
    etf_universe: list[TickerInfo] | None = None,
    force_download: bool = False,
    resume: bool = True,
    data_source: str = "tickflow",
    cache_first: bool = False,
    progress_callback: ScanProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> ScanReport:
    start_time = time.perf_counter()
    _raise_if_cancelled(cancel_event)
    _emit_progress(progress_callback, "prepare", 0, 0, "准备扫描")
    data_source = normalize_data_source(data_source)
    if force_download:
        resume = False

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

    seen: set[str] = set()
    unique: list[TickerInfo] = []
    for ti in all_tickers:
        ti.ticker = _normalize_ticker(ti.ticker)
        if ti.ticker not in seen:
            seen.add(ti.ticker)
            unique.append(ti)
    all_tickers = unique

    logger.info(
        "Phase 1/2: preparing TickFlow data for %d tickers (batch workers=%d)...",
        len(all_tickers),
        TICKFLOW_MAX_WORKERS,
    )
    _raise_if_cancelled(cancel_event)
    _emit_progress(
        progress_callback, "download", 0, len(all_tickers),
        f"准备 TickFlow 行情：{len(all_tickers)} 个标的",
    )
    universe_symbols = {_normalize_ticker(ti.ticker) for ti in all_tickers}
    processed_set = load_checkpoint(data_source) if resume else set()
    processed_set.intersection_update(universe_symbols)
    previous_tickers = _load_previous_tickers() if resume and processed_set else set()
    skip_processed = processed_set.intersection(previous_tickers)
    if skip_processed:
        logger.info(
            "Resuming interrupted scan: %d tickers already analysed.",
            len(skip_processed),
        )
    download_started = time.perf_counter()
    def on_download_progress(
        completed: int, total: int, available: int, unavailable: int
    ) -> None:
        _emit_progress(
            progress_callback,
            "download",
            completed,
            total,
            f"TickFlow 行情 {completed}/{total} · 可用 {available} · 无数据/失败 {unavailable}",
        )

    downloaded = download_batch(
        all_tickers,
        desc="Downloading",
        force=force_download,
        source=data_source,
        cache_first=cache_first and not force_download,
        skip_tickers=set(skip_processed) if resume else None,
        progress_callback=on_download_progress,
    )
    download_elapsed = time.perf_counter() - download_started
    logger.info("Download phase complete in %.1f seconds.", download_elapsed)
    _raise_if_cancelled(cancel_event)
    _emit_progress(
        progress_callback, "download", len(all_tickers), len(all_tickers),
        f"行情准备完成，用时 {download_elapsed:.1f}s",
    )

    processed_set = skip_processed
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
    successful = 0
    failed = 0
    passed = 0

    prev_parquet = OUTPUT_DIR / "AllResults.parquet"
    prev_results: dict[str, ScanResult] = {}
    universe_symbols = {_normalize_ticker(ti.ticker) for ti in all_tickers}
    previous_report_source = ""
    if skip_processed and prev_parquet.exists():
        try:
            metadata = pd.read_parquet(prev_parquet, columns=["DataSource"])
            if not metadata.empty:
                previous_report_source = str(
                    metadata["DataSource"].dropna().iloc[0]
                ).strip()
        except (OSError, ImportError, KeyError, ValueError, IndexError):
            previous_report_source = ""
        if previous_report_source and previous_report_source != data_source:
            logger.warning(
                "Ignoring resume rows from source %s while current source is %s.",
                previous_report_source,
                data_source,
            )
            skip_processed = set()
            processed_set = set()
        else:
            try:
                prev_df = pd.read_parquet(prev_parquet)
                for _, row in prev_df.iterrows():
                    ticker = _normalize_ticker(str(row.get("Ticker", "")))
                    if not ticker:
                        continue
                    sr = ScanResult(
                        ticker=ticker,
                        name=str(row.get("Name", "") or ""),
                        sector=str(row.get("Sector", "") or ""),
                        industry=str(row.get("Industry", "") or ""),
                        is_etf=_parse_bool(row.get("IsETF", False)),
                        asset_type=str(row.get("AssetType", "stock") or "stock"),
                        close=_parse_float(row.get("Close", np.nan), 0.0),
                        score=ScoreBreakdown(
                            total=_parse_float(row.get("Score", np.nan), 0.0),
                            trend=_parse_float(row.get("TrendScore", np.nan), 0.0),
                            volume=_parse_float(row.get("VolumeScore", np.nan), 0.0),
                            accumulation=_parse_float(
                                row.get("AccumulationScore", np.nan), 0.0
                            ),
                            volatility=_parse_float(
                                row.get("CompressionScore", np.nan), 0.0
                            ),
                            structure=_parse_float(
                                row.get("StructureScore", np.nan), 0.0
                            ),
                            missing_indicators=_parse_int(
                                row.get("ScoreMissingIndicators", 0), 0
                            ),
                            indicator_coverage=_parse_float(
                                row.get("ScoreCoverage", 1.0), 1.0
                            ),
                            confidence=_parse_float(
                                row.get("ScoreConfidence", 1.0), 1.0
                            ),
                            execution_score=_parse_float(
                                row.get("ExecutionScore", np.nan), 0.0
                            ),
                        ),
                        score_missing_indicators=_parse_int(
                            row.get("ScoreMissingIndicators", 0), 0
                        ),
                        score_coverage=_parse_float(
                            row.get("ScoreCoverage", 1.0), 1.0
                        ),
                        score_confidence=_parse_float(
                            row.get("ScoreConfidence", 1.0), 1.0
                        ),
                        backtest_score=_parse_float(
                            row.get("BacktestScore", np.nan)
                        ),
                        backtest_reliability=_parse_float(
                            row.get("BacktestReliability", np.nan)
                        ),
                        backtest_samples=_parse_int(
                            row.get("BacktestSamples", 0), 0
                        ),
                        backtest_effective_samples=_parse_float(
                            row.get("BacktestEffectiveSamples", 0.0), 0.0
                        ),
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
                        backtest_effective_weight=_parse_float(
                            row.get("BacktestEffectiveWeight", 0.0), 0.0
                        ),
                        backtest_confidence_tier=str(
                            row.get("BacktestConfidenceTier", "样本不足")
                            or "样本不足"
                        ),
                        backtest_adjusted_score=_parse_float(
                            row.get("BacktestAdjustedScore", np.nan)
                        ),
                        backtest_median_return_20d=_parse_float(
                            row.get("BacktestMedianReturn20D", np.nan)
                        ),
                        backtest_median_return_60d=_parse_float(
                            row.get("BacktestMedianReturn60D", np.nan)
                        ),
                        backtest_max_drawdown_20d=_parse_float(
                            row.get("BacktestMaxDrawdown20D", np.nan)
                        ),
                        backtest_max_drawdown_60d=_parse_float(
                            row.get("BacktestMaxDrawdown60D", np.nan)
                        ),
                        backtest_profit_factor=_parse_float(
                            row.get("BacktestProfitFactor", np.nan)
                        ),
                        backtest_signal_span_days=_parse_int(
                            row.get("BacktestSignalSpanDays", 0), 0
                        ),
                        backtest_return_std_20d=_parse_float(
                            row.get("BacktestReturnStd20D", np.nan)
                        ),
                        backtest_mode=str(row.get("BacktestMode", "") or ""),
                        backtest_cache_hit=_parse_bool(row.get("BacktestCacheHit", False)),
                        backtest_last_evaluated_date=str(row.get("BacktestLastEvaluatedDate", "") or ""),
                        backtest_engine=str(row.get("BacktestEngine", "") or ""),
                        backtest_status=str(row.get("BacktestStatus", "") or ""),
                        global_calibration_score=_parse_float(row.get("GlobalCalibrationScore", np.nan)),
                        global_calibration_confidence=_parse_float(row.get("GlobalCalibrationConfidence", 0.0), 0.0),
                        global_calibration_level=str(row.get("GlobalCalibrationLevel", "none") or "none"),
                        composite_score=_parse_float(
                            row.get("CompositeScore", np.nan)
                        ),
                        failure_signal_factor=_parse_float(
                            row.get("FailureSignalFactor", 1.0), 1.0
                        ),
                        failure_adjusted_score=_parse_float(
                            row.get("FailureAdjustedScore", np.nan)
                        ),
                        signal_recency_factor=_parse_float(
                            row.get("SignalRecencyFactor", 1.0), 1.0
                        ),
                        signal_recency_days=_parse_int(
                            row.get("SignalRecencyDays", -1), -1
                        ),
                        breakout_quality_factor=_parse_float(
                            row.get("BreakoutQualityFactor", 1.0), 1.0
                        ),
                        pre_backtest_institutional_score=_parse_float(
                            row.get("PreBacktestInstitutionalScore", np.nan)
                        ),
                        institutional_score=_parse_float(
                            row.get("InstitutionalScore", np.nan)
                        ),
                        base_score=_parse_float(row.get("BaseScore", np.nan)),
                        trigger_score=_parse_float(row.get("TriggerScore", np.nan)),
                        final_score=_parse_float(row.get("FinalScore", np.nan)),
                        breakout_score=_parse_float(
                            row.get("BreakoutScore", np.nan)
                        ),
                        smart_money_stage=str(
                            row.get("SmartMoneyStage", "NONE") or "NONE"
                        ),
                        entry_score=_parse_float(row.get("EntryScore", np.nan)),
                        entry_signal=str(row.get("EntrySignal", "AVOID") or "AVOID"),
                        raw_entry_signal=str(row.get("RawEntrySignal", row.get("EntrySignal", "AVOID")) or "AVOID"),
                        entry_zone=str(row.get("EntryZone", "") or ""),
                        entry_zone_distance_pct=_parse_float(row.get("EntryZoneDistancePct", np.nan)),
                        entry_zone_distance_atr=_parse_float(row.get("EntryZoneDistanceATR", np.nan)),
                        pullback_quality_score=_parse_float(row.get("PullbackQualityScore", np.nan)),
                        breakout_buy_price=_parse_float(
                            row.get("BreakoutBuyPrice", np.nan)
                        ),
                        breakout_volume_ratio=_parse_float(
                            row.get("BreakoutVolumeRatio", np.nan)
                        ),
                        breakout_volume_confirmed=_parse_bool(
                            row.get("BreakoutVolumeConfirmed", False)
                        ),
                        breakout_flow_confirmed=_parse_bool(
                            row.get("BreakoutFlowConfirmed", False)
                        ),
                        price_breakout=_parse_bool(row.get("PriceBreakout", False)),
                        stop_loss=_parse_float(row.get("StopLoss", np.nan)),
                        projected_target=_parse_float(
                            row.get("ProjectedTarget", np.nan)
                        ),
                        stop_distance_pct=_parse_float(
                            row.get("StopDistancePct", np.nan)
                        ),
                        reward_risk_ratio=_parse_float(
                            row.get("RewardRiskRatio", np.nan)
                        ),
                        value_trap_risk=_parse_float(
                            row.get("ValueTrapRisk", np.nan)
                        ),
                        risk_warning=str(row.get("RiskWarning", "") or ""),
                        operation_advice=str(row.get("OperationAdvice", "") or ""),
                        quality_roe=_parse_float(row.get("ROE", np.nan)),
                        quality_gross_margin=_parse_float(
                            row.get("GrossMargin", np.nan)
                        ),
                        quality_institution_holding_trend=row.get(
                            "InstitutionHoldingTrend"
                        ),
                        quality_institution_holding_periods=_parse_float(
                            row.get("InstitutionHoldingPeriods", np.nan)
                        ),
                        quality_net_profit_y1=_parse_float(
                            row.get("NetProfitY1", np.nan)
                        ),
                        quality_net_profit_y2=_parse_float(
                            row.get("NetProfitY2", np.nan)
                        ),
                        quality_net_profit_y3=_parse_float(
                            row.get("NetProfitY3", np.nan)
                        ),
                        quality_industry_gross_margin_percentile=_parse_float(
                            row.get("IndustryGrossMarginPercentile", np.nan)
                        ),
                        quality_roe_factor=_parse_bool(
                            row.get("QualityROE", False)
                        ),
                        quality_gross_margin_factor=_parse_bool(
                            row.get("QualityGrossMargin", False)
                        ),
                        quality_institution_holding_factor=_parse_bool(
                            row.get("QualityInstitutionHolding", False)
                        ),
                        quality_net_profit_factor=_parse_bool(
                            row.get("QualityNetProfit", False)
                        ),
                        quality_score=_parse_float(
                            row.get("QualityScore", np.nan)
                        ),
                        quality_gate=_parse_bool(
                            row.get("QualityGate", True), True
                        ),
                        quality_reason=str(
                            row.get("QualityReason", "基本面数据缺失（中性）")
                            or "基本面数据缺失（中性）"
                        ),
                        quality_data_available=_parse_bool(
                            row.get("QualityDataAvailable", False)
                        ),
                        quality_applicable=_parse_bool(
                            row.get("QualityApplicable", not _parse_bool(row.get("IsETF", False))),
                            not _parse_bool(row.get("IsETF", False)),
                        ),
                        quality_institution_holding_status=str(
                            row.get("InstitutionHoldingStatus", "UNKNOWN")
                            or "UNKNOWN"
                        ),
                        quality_data_completeness=_parse_float(
                            row.get("QualityDataCompleteness", 0.0), 0.0
                        ),
                        quality_hard_data_complete=_parse_bool(
                            _quality_hard_data_complete_from_row(row)
                        ),
                        quality_gate_reason=str(
                            row.get(
                                "QualityGateReason", "基本面数据缺失（中性）"
                            )
                            or "基本面数据缺失（中性）"
                        ),
                        quality_multiplier=_parse_float(
                            row.get("QualityMultiplier", 0.95), 0.95
                        ),
                        quality_profile=str(
                            row.get("QualityProfile", "GENERAL") or "GENERAL"
                        ),
                        quality_profit_trend_status=str(
                            row.get("ProfitTrendStatus", "UNKNOWN") or "UNKNOWN"
                        ),
                        cyclical_quality_override=_parse_bool(
                            row.get("CyclicalQualityOverride", False)
                        ),
                        etf_tracking_key=str(row.get("ETFTrackingKey", "") or ""),
                        theme_cluster=str(row.get("ThemeCluster", "") or ""),
                        technical_institutional_score=_parse_float(row.get("TechnicalInstitutionalScore", np.nan)),
                        asset_percentile=_parse_float(row.get("AssetPercentile", np.nan)),
                        cross_asset_score=_parse_float(row.get("CrossAssetScore", np.nan)),
                        sector_confirmation_factor=_parse_float(
                            row.get("SectorConfirmationFactor", 1.0), 1.0
                        ),
                        industry_momentum_60d=_parse_float(
                            row.get("IndustryMomentum60D", np.nan)
                        ),
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
                        atr50=_parse_float(row.get("ATR50", np.nan)),
                        atr_expansion_source=str(row.get("ATRExpansionSource", "") or ""),
                        rsi14=_parse_float(row.get("RSI14", np.nan)),
                        dist_to_low_52w=_parse_float(
                            row.get("DistToLow52W", np.nan)
                        ),
                        dist_to_ma20=_parse_float(row.get("DistToMA20", np.nan)),
                        dist_to_ma50=_parse_float(row.get("DistToMA50", np.nan)),
                        recent_return_20d=_parse_float(
                            row.get("RecentReturn20D", np.nan)
                        ),
                        atr_expansion=_parse_float(
                            row.get("ATRExpansion", np.nan)
                        ),
                        wyckoff_phase=str(
                            row.get("WyckoffPhase", "Unknown") or "Unknown"
                        ),
                        volume_accum_days=_parse_int(row.get("VolAccumDays", 0), 0),
                        passed_filters=_parse_bool(
                            row.get("PassedFilters", False)
                        ),
                        universe_eligible=_parse_bool(row.get("UniverseEligible", False)),
                        signal_confirmed=_parse_bool(row.get("SignalConfirmed", False)),
                        failed_filter_count=_parse_int(row.get("FailedFilterCount", 0), 0),
                        failed_filter_names=str(row.get("FailedFilterNames", "") or ""),
                        style=str(row.get("Style", "均衡")),
                        filter_details={
                            "min_price": _parse_bool(
                                row.get("MinPricePassed", False)
                            ),
                            "min_volume": _parse_bool(
                                row.get("MinVolumePassed", False)
                            ),
                            "min_market_cap": _parse_bool(
                                row.get("MarketCapPassed", False)
                            ),
                            "market_cap": _parse_float(
                                row.get("MarketCap", np.nan)
                            ),
                            "market_cap_available": _parse_bool(
                                row.get("MarketCapDataAvailable", False)
                            ),
                            "sufficient_history": _parse_bool(
                                row.get("SufficientHistoryPassed", False)
                            ),
                            "obv_divergence": _parse_bool(
                                row.get("OBV_Div", False)
                            ),
                            "cmf_positive": _parse_bool(row.get("CMF_Pos", False)),
                            "cmf_improving": _parse_bool(
                                row.get("CMF_Improving", False)
                            ),
                            "ad_slope": _parse_bool(
                                row.get("AD_SlopePos", False)
                            ),
                            "bear_market": _parse_bool(
                                row.get("BearMarket", False)
                            ),
                            "consolidation": _parse_bool(
                                row.get("Consolidation", False)
                            ),
                            "volume_accumulation": _parse_bool(
                                row.get("VolAccum", False)
                            ),
                            "volatility_contraction": _parse_bool(
                                row.get("VolContract", False)
                            ),
                            "signal_count": _parse_int(
                                row.get("SignalCount", 0), 0
                            ),
                            "filter_count": _parse_int(
                                row.get("FilterCount", 0), 0
                            ),
                        },
                        error=str(row.get("Error", "") or ""),
                        market_regime=str(
                            row.get("MarketRegime", "未知") or "未知"
                        ),
                        market_regime_fast=str(
                            row.get("MarketRegimeFast", "未知") or "未知"
                        ),
                        market_regime_slow=str(
                            row.get("MarketRegimeSlow", "未知") or "未知"
                        ),
                        market_regime_confidence=_parse_float(
                            row.get("MarketRegimeConfidence", 0.0), 0.0
                        ),
                        market_regime_reason=str(
                            row.get("MarketRegimeReason", "") or ""
                        ),
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
                        data_coverage=_parse_float(
                            row.get("DataCoverage", 0.0), 0.0
                        ),
                        chase_risk_score=_parse_float(
                            row.get("ChaseRiskScore", 0.0), 0.0
                        ),
                        chase_risk_level=str(
                            row.get("ChaseRiskLevel", "低") or "低"
                        ),
                        chase_risk_reason=str(
                            row.get("ChaseRiskReason", "") or ""
                        ),
                        hard_risk_flag=_parse_bool(
                            row.get("HardRiskFlag", False)
                        ),
                        hard_risk_penalty=_parse_float(
                            row.get("HardRiskPenalty", 1.0), 1.0
                        ),
                        hard_risk_reason=str(
                            row.get("HardRiskReason", "") or ""
                        ),
                        ranking_penalty_reason=str(
                            row.get("RankingPenaltyReason", "") or ""
                        ),
                        ranking_eligibility=str(
                            row.get("RankingEligibility", "观察") or "观察"
                        ),
                        ranking_score=_parse_float(
                            row.get("RankingScore", np.nan)
                        ),
                        overall_rank=_parse_int(row.get("OverallRank", 0), 0),
                        ranking_reason=str(
                            row.get("RankingReason", "") or ""
                        ),
                        decision_state=str(row.get("DecisionState", "OBSERVE") or "OBSERVE"),
                        decision_reason=str(row.get("DecisionReason", "") or ""),
                        trade_readiness=str(row.get("TradeReadiness", row.get("RankingEligibility", "观察")) or "观察"),
                        research_tier=str(row.get("ResearchTier", "") or ""),
                        model_classification=str(row.get("ModelClassification", row.get("Industry", row.get("Sector", ""))) or ""),
                        institutional_percentile=_parse_float(
                            row.get("InstitutionalPercentile", np.nan)
                        ),
                        institutional_rank=_parse_int(
                            row.get("InstitutionalRank", 0), 0
                        ),
                        institutional_tier_reason=str(
                            row.get("InstitutionalTierReason", "") or ""
                        ),
                        signal_adjustment_reason=str(
                            row.get("SignalAdjustmentReason", "") or ""
                        ),
                        opportunity_stage=str(
                            row.get("OpportunityStage", "未知") or "未知"
                        ),
                    )
                    if sr.ticker in universe_symbols and sr.ticker in processed_set:
                        prev_results[sr.ticker] = sr
            except (
                OSError,
                ImportError,
                KeyError,
                TypeError,
                ValueError,
                IndexError,
            ) as exc:
                logger.debug("Could not load previous scan results: %s", exc)

    _emit_progress(
        progress_callback,
        "analyse",
        0,
        len(analyse_queue),
        f"开始指标分析：{len(analyse_queue)} 个标的",
    )
    analysis_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=SCAN_THREADS) as executor:
        max_pending = max(SCAN_THREADS * 4, SCAN_THREADS)
        ticker_iter = iter(analyse_queue)
        futures: dict[Any, TickerInfo] = {}

        def submit_next() -> bool:
            if cancel_event is not None and cancel_event.is_set():
                return False
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
                    data_source,
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
                _raise_if_cancelled(cancel_event)
                future = next(as_completed(futures))
                ti = futures.pop(future)
                completed += 1
                try:
                    result, frame = future.result()
                except Exception as exc:
                    logger.exception("Analysis error for %s", ti.ticker)
                    result, frame = (
                        ScanResult(ticker=ti.ticker, error=str(exc)),
                        None,
                    )

                results.append(result)
                if frame is not None:
                    analysed_frames[result.ticker] = frame

                if result.error:
                    failed += 1
                    logger.warning(
                        "Analysis failed for %s: %s", ti.ticker, result.error
                    )
                else:
                    successful += 1
                    if result.passed_filters:
                        passed += 1

                if not result.error:
                    processed_set.add(ti.ticker)
                analysed_this_run.add(ti.ticker)
                progress.update(1)

                if len(analysed_this_run) % 100 == 0 or len(
                    analysed_this_run
                ) == len(analyse_queue):
                    logger.info(
                        "ANALYSE progress: %d/%d (%d successful, %d failed).",
                        completed,
                        len(analyse_queue),
                        successful,
                        failed,
                    )
                if completed == len(analyse_queue) or completed % 25 == 0:
                    _emit_progress(
                        progress_callback, "analyse", completed, len(analyse_queue),
                        f"指标分析 {completed}/{len(analyse_queue)} · 成功 {successful} · 失败 {failed}",
                    )

                if (
                    ENABLE_CHECKPOINT
                    and len(analysed_this_run) % CHECKPOINT_INTERVAL == 0
                ):
                    save_checkpoint(processed_set, data_source)

                submit_next()
    analysis_elapsed = time.perf_counter() - analysis_started
    logger.info("Analysis phase complete in %.1f seconds.", analysis_elapsed)

    for ticker, sr in prev_results.items():
        if ticker not in analysed_this_run:
            results.append(sr)
            successful += 1
            if sr.passed_filters:
                passed += 1

    clear_checkpoint()

    _raise_if_cancelled(cancel_event)
    logger.info("Enriching %d scan results...", len(results))
    _emit_progress(progress_callback, "enrich", 0, len(results), "正在增强评分与排序")
    enrichment_started = time.perf_counter()
    try:
        enrich_results(results, data_source, frames=analysed_frames)
    except _SCAN_RECOVERABLE_ERRORS:
        logger.exception("Failed to enrich scan results; continuing with base results")
    enrichment_elapsed = time.perf_counter() - enrichment_started
    logger.info(
        "Enrichment complete: %d scan results in %.1f seconds.",
        len(results),
        enrichment_elapsed,
    )
    _raise_if_cancelled(cancel_event)
    _emit_progress(
        progress_callback, "enrich", len(results), len(results),
        f"评分增强完成，用时 {enrichment_elapsed:.1f}s",
    )

    # RankingScore is the single final execution rank. InstitutionalScore and
    # FinalScore remain fallbacks for partially enriched/legacy rows only.
    results.sort(
        key=lambda result: (
            _parse_float(result.ranking_score, np.nan)
            if np.isfinite(_parse_float(result.ranking_score, np.nan))
            else _parse_float(result.institutional_score, np.nan)
            if np.isfinite(_parse_float(result.institutional_score, np.nan))
            else _parse_float(result.final_score, result.score.total)
        ),
        reverse=True,
    )

    elapsed = time.perf_counter() - start_time
    report = ScanReport(
        results=results,
        total_tickers=len(all_tickers),
        successful=successful,
        failed=failed,
        passed_filters=passed,
        elapsed_seconds=elapsed,
        download_seconds=download_elapsed,
        analysis_seconds=analysis_elapsed,
        enrichment_seconds=enrichment_elapsed,
    )
    logger.info(
        "Scan complete: %d successful, %d failed, %d passed filters, %.1f seconds.",
        successful,
        failed,
        passed,
        elapsed,
    )
    _emit_progress(
        progress_callback, "complete", len(all_tickers), len(all_tickers),
        f"扫描完成：成功 {successful} · 失败 {failed} · 用时 {elapsed:.1f}s",
    )
    return report


def _cache_path_for(ticker: str, source: str) -> Path:
    """Use downloader's canonical, schema-versioned cache path everywhere."""
    return _cache_path(_normalize_ticker(ticker), normalize_data_source(source))


def _analyse_one_ticker_from_df(
    ticker_info: TickerInfo,
    df: pd.DataFrame | None,
    data_source: str = "tickflow",
) -> tuple[ScanResult, pd.DataFrame | None]:
    if df is None:
        return scan_single_from_df(ticker_info, df), None
    raw_path = _cache_path(_normalize_ticker(ticker_info.ticker), data_source)
    enriched, _indicator_cache_hit = load_or_compute_indicators(
        ticker_info.ticker,
        df,
        compute_all_indicators,
        source_path=raw_path if raw_path.exists() else None,
        enabled=INDICATOR_CACHE_ENABLED,
    )
    result = scan_single_from_df(ticker_info, enriched, indicators_computed=True)
    if result.error:
        return result, None
    enrichment_columns = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "MA20",
        "MA50",
        "ATR14",
        "ATR50",
        "RSI14",
    ]
    available_columns = [
        column for column in enrichment_columns if column in enriched
    ]
    return result, enriched.loc[:, available_columns].copy()


def _analyse_one_ticker(
    ticker_info: TickerInfo, data_source: str = "tickflow"
) -> ScanResult:
    df = _load_cache(ticker_info.ticker, data_source)
    if df is None:
        cache_path = _cache_path_for(ticker_info.ticker, data_source)
        return ScanResult(
            ticker=ticker_info.ticker,
            name=ticker_info.name,
            is_etf=ticker_info.is_etf,
            error=f"No cached data: {cache_path}",
        )
    return _analyse_one_ticker_from_df(ticker_info, df, data_source)[0]


def run_parallel_indicator_scan(
    ticker_infos: list[TickerInfo],
    data_source: str = "tickflow",
    max_workers: int = SCAN_THREADS,
) -> list[ScanResult]:
    data_source = normalize_data_source(data_source)
    results: list[ScanResult] = []
    worker_count = max(1, int(max_workers))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_ticker = {
            executor.submit(_analyse_one_ticker, ti, data_source): ti
            for ti in ticker_infos
        }
        with tqdm(
            total=len(future_to_ticker),
            desc="Parallel scan",
            unit="ticker",
            disable=not sys.stderr.isatty(),
        ) as progress:
            for future in as_completed(future_to_ticker):
                ti = future_to_ticker[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    logger.exception("Analysis error for %s", ti.ticker)
                    results.append(
                        ScanResult(ticker=ti.ticker, name=ti.name, error=str(exc))
                    )
                progress.update(1)
    results.sort(key=lambda result: result.score.total, reverse=True)
    return results
