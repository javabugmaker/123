"""
score.py — Institutional Accumulation Scoring Engine.

Scores each ticker on a 0–100 scale across five dimensions:
  - Trend          (20 points):  Bear market characteristics
  - Volume         (25 points):  Sustained above-average volume
  - Accumulation   (25 points):  OBV, A/D, CMF, MFI signals
  - Volatility     (15 points):  ATR & BB compression
  - Structure      (15 points):  Distance from lows, consolidation duration

The scoring is designed so that higher scores mean stronger
institutional accumulation signals.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config import (
    AD_SLOPE_LOOKBACK,
    BB_WIDTH_COMPRESSION_LOOKBACK,
    BREAKOUT_CONFIRM_MIN_VOLUME_RATIO,
    CONSOLIDATION_DAYS,
    CONSOLIDATION_MAX_RANGE_PCT,
    LOG_DIR,
    MODEL_EXECUTION_WEIGHT,
    MODEL_SETUP_WEIGHT,
    MODEL_TRIGGER_WEIGHT,
    OUTPUT_DIR,
    SCORING_WEIGHTS,
    VOLUME_ACCUM_MIN_DAYS,
    VOLUME_ACCUM_RATIO,
    setup_logging,
)

logger = setup_logging(
    "institution_scanner.score",
    level=logging.INFO,
    log_to_file=True,
    log_dir=LOG_DIR,
)


class ScoreContributions(dict[str, float]):
    def __array__(self, dtype: Any = None) -> np.ndarray:
        return np.asarray(float(sum(self.values())), dtype=dtype)


@dataclass
class ScoreBreakdown:
    """Full scoring output for one ticker."""

    total: float = 0.0
    trend: float = 0.0
    volume: float = 0.0
    accumulation: float = 0.0
    volatility: float = 0.0
    structure: float = 0.0
    missing_indicators: int = 0
    indicator_coverage: float = 1.0
    confidence: float = 1.0
    base_score: float = 0.0
    breakout_score: float = 0.0
    entry_score: float = 0.0
    execution_score: float = 0.0
    value_trap_risk: float = 0.0
    trigger_score: float = 0.0
    final_score: float = 0.0
    entry_zone_low: float = 0.0
    entry_zone_high: float = 0.0
    breakout_buy_price: float = 0.0
    stop_loss: float = 0.0
    contributions: dict[str, float] = field(default_factory=ScoreContributions)

    def to_dict(self) -> dict[str, float]:
        return {
            "Score": round(self.total, 2),
            "BaseScore": round(self.base_score, 2),
            "BreakoutScore": round(self.breakout_score, 2),
            "EntryScore": round(self.entry_score, 2),
            "ExecutionScore": round(self.execution_score, 2),
            "ValueTrapRisk": round(self.value_trap_risk, 2),
            "TriggerScore": round(self.trigger_score, 2),
            "TrendScore": round(self.trend, 2),
            "VolumeScore": round(self.volume, 2),
            "AccumulationScore": round(self.accumulation, 2),
            "CompressionScore": round(self.volatility, 2),
            "StructureScore": round(self.structure, 2),
            "ScoreMissingIndicators": self.missing_indicators,
            "ScoreCoverage": round(self.indicator_coverage, 4),
            "ScoreConfidence": round(self.confidence, 4),
            "ScoreContributionTrend": round(
                self.contributions.get("trend", self.trend), 2
            ),
            "ScoreContributionVolume": round(
                self.contributions.get("volume", self.volume), 2
            ),
            "ScoreContributionAccumulation": round(
                self.contributions.get("accumulation", self.accumulation), 2
            ),
            "ScoreContributionCompression": round(
                self.contributions.get("compression", self.volatility), 2
            ),
            "ScoreContributionStructure": round(
                self.contributions.get("structure", self.structure), 2
            ),
        }


_MODEL_WEIGHT_CACHE: tuple[float, float, float] | None = None
_MODEL_WEIGHT_CACHE_STATE: tuple[int, int] | None = None


def _model_weight_file_state(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return int(stat.st_mtime_ns), int(stat.st_size)


def invalidate_model_weight_cache() -> None:
    global _MODEL_WEIGHT_CACHE, _MODEL_WEIGHT_CACHE_STATE
    _MODEL_WEIGHT_CACHE = None
    _MODEL_WEIGHT_CACHE_STATE = None


def _model_component_weights() -> tuple[float, float, float]:
    global _MODEL_WEIGHT_CACHE, _MODEL_WEIGHT_CACHE_STATE
    defaults = (MODEL_SETUP_WEIGHT, MODEL_TRIGGER_WEIGHT, MODEL_EXECUTION_WEIGHT)
    path = OUTPUT_DIR / "ScoreCalibration.json"
    state = _model_weight_file_state(path)
    if _MODEL_WEIGHT_CACHE is not None and state == _MODEL_WEIGHT_CACHE_STATE:
        return _MODEL_WEIGHT_CACHE
    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not bool(payload.get("accepted", False)):
            raise ValueError("calibration not accepted")
        setup = float(payload.get("setup_weight"))
        trigger = float(payload.get("trigger_weight"))
        execution = float(payload.get("execution_weight"))
        if not (0.45 <= setup <= 0.70 and 0.15 <= trigger <= 0.35 and 0.10 <= execution <= 0.25):
            raise ValueError("calibration outside guard rails")
        if abs(setup + trigger + execution - 1.0) > 1e-6:
            raise ValueError("calibration weights must sum to one")
        _MODEL_WEIGHT_CACHE = (setup, trigger, execution)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        _MODEL_WEIGHT_CACHE = defaults
    _MODEL_WEIGHT_CACHE_STATE = _model_weight_file_state(path)
    return _MODEL_WEIGHT_CACHE


def model_weight_signature() -> str:
    setup, trigger, execution = _model_component_weights()
    return f"{setup:.4f}:{trigger:.4f}:{execution:.4f}"


def _is_finite(value: Any) -> bool:
    try:
        return bool(pd.notna(value) and np.isfinite(float(value)))
    except (TypeError, ValueError):
        return False


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    if not _is_finite(value):
        return low
    return max(low, min(high, float(value)))


def _normalize_to_range(
    value: float, min_val: float, max_val: float, invert: bool = False
) -> float:
    if max_val == min_val:
        return 0.5
    norm = (value - min_val) / (max_val - min_val)
    norm = _clamp(norm)
    return 1.0 - norm if invert else norm


def score_trend(df: pd.DataFrame) -> float:
    if len(df) < 252 or "Close" not in df.columns or "MA200" not in df.columns:
        return 0.0
    close = df["Close"].replace([np.inf, -np.inf], np.nan).dropna()
    ma200 = df["MA200"].reindex(close.index).replace([np.inf, -np.inf], np.nan)
    valid = pd.concat({"close": close, "ma200": ma200}, axis=1).dropna()
    if len(valid) < 60:
        return 0.0
    price_now = float(valid["close"].iloc[-1])
    ma200_now = float(valid["ma200"].iloc[-1])
    if price_now <= 0 or ma200_now <= 0:
        return 0.0
    score = 0.0
    ma_recent = valid["ma200"].iloc[-60:]
    slope_pct = float(ma_recent.iloc[-1]) / float(ma_recent.iloc[0]) - 1.0
    if slope_pct < 0:
        score += _clamp(abs(slope_pct) / 0.12) * 5.0
    below_pct = (ma200_now - price_now) / ma200_now
    if below_pct > 0:
        score += _clamp(below_pct / 0.30) * 6.0
        score -= _clamp(max(below_pct - 0.45, 0.0) / 0.30) * 3.0
    below = valid["close"] < valid["ma200"]
    last_above = np.flatnonzero((~below).to_numpy())
    days_below = len(valid) - int(last_above[-1]) - 1 if len(last_above) else len(valid)
    score += _clamp(days_below / 250.0) * 3.0
    lookback = valid["close"].iloc[-min(504, len(valid)) :]
    peak = float(lookback.max())
    drawdown = (price_now - peak) / peak if peak > 0 else 0.0
    depth = abs(drawdown)
    if 0.15 <= depth <= 0.50:
        score += _clamp(1.0 - abs(depth - 0.32) / 0.25) * 3.0
    recovery = valid["close"].iloc[-20:]
    if len(recovery) >= 10:
        recent_slope = float(recovery.iloc[-1] / recovery.iloc[0] - 1.0)
        if recent_slope > 0:
            score += _clamp(recent_slope / 0.12) * 3.0
    return _clamp(score, 0.0, 20.0)


def score_volume(df: pd.DataFrame) -> float:
    if len(df) < 120:
        return 0.0
    score = 0.0
    if "VolMA20" in df.columns and "VolMA120" in df.columns:
        vol_ma20 = df["VolMA20"].replace([np.inf, -np.inf], np.nan)
        vol_ma120 = df["VolMA120"].replace([np.inf, -np.inf], np.nan)
        ratio_series = (vol_ma20 / vol_ma120.replace(0, np.nan)).dropna()
        if len(ratio_series) >= VOLUME_ACCUM_MIN_DAYS:
            consecutive = 0
            for value in ratio_series.iloc[::-1]:
                if value >= VOLUME_ACCUM_RATIO:
                    consecutive += 1
                else:
                    break
            if consecutive >= VOLUME_ACCUM_MIN_DAYS:
                score += 4.0 + _clamp(
                    (consecutive - VOLUME_ACCUM_MIN_DAYS) / 80.0
                ) * 6.0
            ratio_now = float(ratio_series.iloc[-1])
            score += _clamp((ratio_now - VOLUME_ACCUM_RATIO) / 0.8) * 3.0
            if len(ratio_series) >= 20:
                ratio_change = float(ratio_series.iloc[-1] - ratio_series.iloc[-20])
                score += _clamp(ratio_change / 0.5) * 4.0
    if "VolZScore" in df.columns:
        z_recent = (
            df["VolZScore"].replace([np.inf, -np.inf], np.nan).dropna().iloc[-30:]
        )
        if len(z_recent) >= 10:
            z_now = float(z_recent.iloc[-1])
            positive_days = float((z_recent > 0).mean())
            score += positive_days * 3.0
            score += _clamp(z_now / 2.0) * 2.0
    return _clamp(score, 0.0, 25.0)


def score_accumulation(df: pd.DataFrame) -> float:
    if len(df) < 60:
        return 0.0
    score = 0.0
    if "OBV" in df.columns and len(df) >= 60:
        recent = (
            df[["Close", "OBV"]]
            .iloc[-60:]
            .replace([np.inf, -np.inf], np.nan)
            .dropna()
        )
        if len(recent) >= 40:
            split = len(recent) // 2
            first_half = recent.iloc[:split]
            second_half = recent.iloc[split:]
            first_price_low = float(first_half["Close"].min())
            second_price_low = float(second_half["Close"].min())
            first_obv_low = float(first_half["OBV"].min())
            second_obv_low = float(second_half["OBV"].min())
            price_now = float(recent["Close"].iloc[-1])
            obv_now = float(recent["OBV"].iloc[-1])
            near_low = (
                second_price_low > 0
                and (price_now - second_price_low) / second_price_low <= 0.05
            )
            price_retest = second_price_low <= first_price_low * 1.02
            obv_divergence = (
                second_obv_low > first_obv_low and obv_now >= second_obv_low
            )
            if near_low and price_retest and obv_divergence:
                score += 8.0
            elif obv_divergence:
                score += 3.0
    if "AD" in df.columns and "AD_Slope" in df.columns:
        ad = df["AD"].replace([np.inf, -np.inf], np.nan).dropna()
        ad_slope = df["AD_Slope"].iloc[-1]
        if len(ad) >= AD_SLOPE_LOOKBACK and pd.notna(ad_slope):
            ad_scale = max(float(ad.iloc[-AD_SLOPE_LOOKBACK:].abs().median()), 1.0)
            slope_score = _clamp(float(ad_slope) / (ad_scale * 0.03))
            score += slope_score * 5.0
            if float(ad.iloc[-1]) >= float(
                ad.iloc[-min(120, len(ad)) :].max()
            ) * 0.95:
                score += 1.0
    if "CMF" in df.columns:
        cmf = df["CMF"].replace([np.inf, -np.inf], np.nan).dropna()
        if len(cmf) >= 20:
            cmf_now = float(cmf.iloc[-1])
            cmf_change = cmf_now - float(cmf.iloc[-20])
            score += _clamp(cmf_now / 0.15) * 4.0
            score += _clamp(cmf_change / 0.10) * 2.0
    if "MFI" in df.columns:
        mfi_now = df["MFI"].replace([np.inf, -np.inf], np.nan).iloc[-1]
        if pd.notna(mfi_now):
            score += (
                3.0
                if 40 <= mfi_now <= 70
                else 1.5
                if 30 <= mfi_now <= 80
                else 0.0
            )
    return _clamp(score, 0.0, 25.0)


def score_volatility(df: pd.DataFrame) -> float:
    if len(df) < BB_WIDTH_COMPRESSION_LOOKBACK:
        return 0.0
    components: list[float] = []
    if "ATR14" in df.columns and "ATR50" in df.columns:
        atr14 = df["ATR14"].replace([np.inf, -np.inf], np.nan).iloc[-1]
        atr50 = df["ATR50"].replace([np.inf, -np.inf], np.nan).iloc[-1]
        if pd.notna(atr14) and pd.notna(atr50) and atr50 > 0:
            components.append(_clamp((1.0 - float(atr14 / atr50)) / 0.35))
    if "BB_Width" in df.columns:
        bb = df["BB_Width"].replace([np.inf, -np.inf], np.nan).dropna()
        if len(bb) >= BB_WIDTH_COMPRESSION_LOOKBACK:
            current = float(bb.iloc[-1])
            baseline = float(bb.iloc[-BB_WIDTH_COMPRESSION_LOOKBACK:-10].median())
            if baseline > 0:
                components.append(_clamp(1.0 - current / baseline))
    if "HV20" in df.columns and "HV60" in df.columns:
        hv20 = df["HV20"].replace([np.inf, -np.inf], np.nan).iloc[-1]
        hv60 = df["HV60"].replace([np.inf, -np.inf], np.nan).iloc[-1]
        if pd.notna(hv20) and pd.notna(hv60) and hv60 > 0:
            components.append(_clamp((1.0 - float(hv20 / hv60)) / 0.5))
    if not components:
        return 0.0
    coverage = len(components) / 3.0
    return _clamp(float(np.mean(components)) * coverage) * 15.0


def score_structure(df: pd.DataFrame) -> float:
    if len(df) < 252 or not all(
        column in df.columns for column in ("Close", "High", "Low")
    ):
        return 0.0
    score = 0.0
    if "Low52W" in df.columns and "DistToLow52W" in df.columns:
        dist_low = df["DistToLow52W"].iloc[-1]
        if _is_finite(dist_low) and 0 <= dist_low <= 20:
            if dist_low < 8:
                score += dist_low / 8 * 5
            elif dist_low <= 12:
                score += 5
            else:
                score += (20 - dist_low) / 8 * 5
    if len(df) >= CONSOLIDATION_DAYS:
        recent = df.iloc[-CONSOLIDATION_DAYS:]
        high, low = recent["High"].max(), recent["Low"].min()
        avg_price = recent["Close"].mean()
        if avg_price > 0:
            range_pct = (high - low) / avg_price * 100
            if range_pct <= CONSOLIDATION_MAX_RANGE_PCT:
                tightness = _clamp(
                    1 - range_pct / CONSOLIDATION_MAX_RANGE_PCT, 0, 1
                )
                score += (0.2 + tightness * 0.8) * 5
    if "RegSlope" in df.columns:
        reg_slope = df["RegSlope"].iloc[-1]
        if _is_finite(reg_slope):
            abs_slope = abs(reg_slope)
            score += _clamp(1 - abs_slope / 0.05, 0, 1) * 2
            if "RegR2" in df.columns:
                r2 = df["RegR2"].iloc[-1]
                if pd.notna(r2) and np.isfinite(float(r2)):
                    score += _clamp(r2, 0, 1) * 1
    if "Above_HVN" in df.columns and "DistToHVN_Pct" in df.columns:
        above_hvn = df["Above_HVN"].iloc[-1]
        dist_hvn = df["DistToHVN_Pct"].iloc[-1]
        if bool(above_hvn) and _is_finite(dist_hvn) and 0 < dist_hvn < 10:
            score += _clamp(1 - dist_hvn / 10, 0, 1) * 2
    return min(score, 15.0)


def cyclical_turn_factor(
    df: pd.DataFrame,
    industry_return: float | None = None,
    quality_net_profit_y1: float | None = None,
    quality_net_profit_y2: float | None = None,
    quality_net_profit_y3: float | None = None,
) -> dict[str, float | int]:
    close = _series(df, "Close")
    volume = _series(df, "Volume")
    if len(close.dropna()) < 60:
        return {
            "score": 50.0,
            "confidence": 0.0,
            "available": 0,
            "price": 50.0,
            "inventory": 50.0,
            "earnings": 50.0,
            "capex": 50.0,
        }
    components: list[float] = []
    price_turn = 50.0
    ret20 = _safe_return(close, 20)
    ret60 = _safe_return(close, 60)
    ma20 = _latest(df, "MA20")
    ma50 = _latest(df, "MA50")
    if _is_finite(ret20) and _is_finite(ret60):
        price_turn = _clamp(50.0 + (ret20 - ret60 * 0.35) * 2.0, 0.0, 100.0)
        if (
            _is_finite(ma20)
            and _is_finite(ma50)
            and close.iloc[-1] > ma20 > ma50
        ):
            price_turn = min(100.0, price_turn + 12.0)
        components.append(price_turn)
    industry_turn = 50.0
    if industry_return is not None and _is_finite(float(industry_return)):
        industry_turn = _clamp(
            50.0 + float(industry_return) * 1.5, 0.0, 100.0
        )
        components.append(industry_turn)
    inventory = 50.0
    if len(volume.dropna()) >= 60 and len(close.dropna()) >= 60:
        vol20 = volume.iloc[-20:].mean()
        vol60 = volume.iloc[-60:-20].mean()
        recent_range = (close.iloc[-10:].max() - close.iloc[-10:].min()) / max(
            close.iloc[-1], 1e-9
        )
        prior_range = (close.iloc[-50:-10].max() - close.iloc[-50:-10].min()) / max(
            close.iloc[-1], 1e-9
        )
        if vol60 > 0 and prior_range > 0:
            inventory = _clamp(
                50.0
                + (1.0 - vol20 / vol60) * 35.0
                + (1.0 - recent_range / prior_range) * 25.0,
                0.0,
                100.0,
            )
            components.append(inventory)
    earnings = 50.0
    profits = [
        value
        for value in (
            quality_net_profit_y1,
            quality_net_profit_y2,
            quality_net_profit_y3,
        )
        if value is not None and _is_finite(float(value))
    ]
    if len(profits) >= 2:
        earnings = _clamp(
            50.0
            + (profits[0] - profits[1]) / max(abs(profits[1]), 1e-9) * 45.0,
            0.0,
            100.0,
        )
        components.append(earnings)
    capex = 50.0
    score = float(np.mean(components)) if components else 50.0
    return {
        "score": round(_clamp(score, 0.0, 100.0), 2),
        "confidence": round(len(components) / 4.0, 4),
        "available": len(components),
        "price": round(price_turn, 2),
        "industry": round(industry_turn, 2),
        "inventory": round(inventory, 2),
        "earnings": round(earnings, 2),
        "capex": capex,
    }


def _series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(index=df.index, dtype=float)
    return pd.to_numeric(df[column], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )


def _latest(df: pd.DataFrame, column: str) -> float:
    values = _series(df, column).dropna()
    return float(values.iloc[-1]) if not values.empty else np.nan


def _rolling_mean(df: pd.DataFrame, column: str, window: int) -> float:
    values = _series(df, column).dropna()
    return float(values.iloc[-window:].mean()) if len(values) >= window else np.nan


def _safe_return(values: pd.Series, periods: int) -> float:
    clean = (
        pd.to_numeric(values, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )
    if len(clean) <= periods:
        return np.nan
    start = float(clean.iloc[-periods - 1])
    end = float(clean.iloc[-1])
    return (end / start - 1.0) * 100.0 if start > 0 else np.nan


def value_trap_risk(df: pd.DataFrame, is_etf: bool = False) -> float:
    """Estimate deterioration risk without treating a low price as a trap by itself.

    Model v2 rewards genuine bottom recovery and penalises *continued* lower lows,
    failed rebounds and absent money-flow confirmation.  This removes the old
    contradiction where being below MA200 both helped the setup score and heavily
    penalised the same ticker as a value trap.
    """
    close = _series(df, "Close")
    volume = _series(df, "Volume")
    clean_close = close.dropna()
    if len(clean_close) < 121:
        return 0.0

    risk = 0.0
    price = _latest(df, "Close")
    ma20 = _latest(df, "MA20")
    ma50 = _latest(df, "MA50")
    ret20 = _safe_return(close, 20)
    ret60 = _safe_return(close, 60)
    ret120 = _safe_return(close, 120)

    # Persistent deterioration rather than absolute low-price location.
    if _is_finite(ret120) and ret120 < 0:
        risk += _clamp(abs(ret120) / 45.0) * 15.0
    if _is_finite(ma50) and len(df) >= 25 and "MA50" in df:
        old_ma50 = _series(df, "MA50").iloc[-25] if len(_series(df, "MA50")) >= 25 else np.nan
        if _is_finite(old_ma50) and old_ma50 > 0 and ma50 < old_ma50:
            risk += _clamp((old_ma50 - ma50) / old_ma50 / 0.12) * 12.0
        if _is_finite(price) and price < ma50 and _is_finite(ret20) and ret20 < 0:
            risk += 8.0

    recent_low = float(clean_close.iloc[-40:].min())
    prior_low = float(clean_close.iloc[-80:-40].min()) if len(clean_close) >= 80 else recent_low
    if prior_low > 0 and recent_low < prior_low * 0.98:
        risk += _clamp((prior_low - recent_low) / prior_low / 0.12) * 15.0

    if _is_finite(ret20) and _is_finite(ret60) and ret20 < 0 and ret60 < 0:
        risk += 10.0

    # Money-flow evidence is the key distinction between accumulation and a trap.
    flow_positive = 0
    flow_available = 0
    cmf = _latest(df, "CMF")
    ad_slope = _latest(df, "AD_Slope")
    obv = _series(df, "OBV").dropna()
    for value in (cmf, ad_slope):
        if _is_finite(value):
            flow_available += 1
            flow_positive += int(value > 0)
    if len(obv) >= 20:
        flow_available += 1
        flow_positive += int(float(obv.iloc[-1] - obv.iloc[-20]) > 0)
    if flow_available:
        if flow_positive == 0:
            risk += 25.0
        elif flow_positive == 1:
            risk += 10.0
        elif flow_positive >= 2:
            risk -= 8.0

    if len(volume.dropna()) >= 60:
        vol20 = float(volume.dropna().iloc[-20:].mean())
        vol60 = float(volume.dropna().iloc[-60:-20].mean())
        if vol60 > 0 and vol20 < vol60 * 0.75:
            if _is_finite(ret20) and ret20 < 0:
                risk += 10.0
            elif _is_finite(ret20) and ret20 >= 0:
                risk -= 3.0

    recovery_confirmed = (
        _is_finite(price)
        and _is_finite(ma20)
        and _is_finite(ma50)
        and price >= ma20 >= ma50
        and _is_finite(ret20)
        and ret20 > 0
    )
    if recovery_confirmed:
        risk -= 15.0
    elif _is_finite(ret20) and ret20 > 5.0 and flow_positive >= 2:
        risk -= 8.0

    # ETFs do not carry company-specific value-trap risk; retain only technical
    # deterioration with a softer scale while keeping the public field name.
    if is_etf:
        risk *= 0.80
    return _clamp(risk, 0.0, 100.0)


def breakout_score(df: pd.DataFrame) -> float:
    close = _series(df, "Close")
    high = _series(df, "High")
    volume = _series(df, "Volume")
    valid = pd.concat(
        {"close": close, "high": high, "volume": volume}, axis=1
    ).dropna()
    if len(valid) < 60:
        return 0.0
    close, high, volume = valid["close"], valid["high"], valid["volume"]
    points = 0.0
    price = _latest(df, "Close")
    ma20 = _latest(df, "MA20")
    ma50 = _latest(df, "MA50")
    ma200 = _latest(df, "MA200")
    if all(_is_finite(value) for value in (price, ma20, ma50)):
        points += 15.0 if price > ma20 > ma50 else 8.0 if price > ma20 else 0.0
    if _is_finite(ma200) and price > ma200:
        points += 10.0
    if len(close) >= 21 and len(high) >= 21 and len(volume.dropna()) >= 21:
        resistance = high.iloc[-21:-1].max()
        vol20 = volume.iloc[-21:-1].mean()
        vol_now = volume.iloc[-1]
        if _is_finite(resistance) and price > resistance:
            points += 25.0
            if vol20 > 0 and vol_now >= vol20 * 1.5:
                points += 15.0
    if len(close) >= 10 and len(volume.dropna()) >= 10:
        up = close.diff() > 0
        up_volume = volume.where(up).iloc[-10:].mean()
        down_volume = volume.where(~up).iloc[-10:].mean()
        if (
            _is_finite(up_volume)
            and _is_finite(down_volume)
            and down_volume > 0
            and up_volume > down_volume * 1.15
        ):
            points += 15.0
    if len(close) >= 20:
        recent_range = (close.iloc[-5:].max() - close.iloc[-5:].min()) / max(
            price, 1e-9
        )
        prior_range = (close.iloc[-20:-5].max() - close.iloc[-20:-5].min()) / max(
            price, 1e-9
        )
        if prior_range > 0 and recent_range < prior_range * 0.75:
            points += 10.0
    if (
        _is_finite(ma20)
        and len(close) >= 10
        and close.iloc[-1] > close.iloc[-10]
        and ma20 > _rolling_mean(df, "MA20", 10)
    ):
        points += 10.0
    return _clamp(points, 0.0, 100.0)


def smart_money_stage(
    df: pd.DataFrame,
    breakout: float | None = None,
    trap: float | None = None,
) -> str:
    breakout = breakout_score(df) if breakout is None else breakout
    trap = value_trap_risk(df) if trap is None else trap
    close = _series(df, "Close")
    price = _latest(df, "Close")
    ma20, ma50 = _latest(df, "MA20"), _latest(df, "MA50")
    vol20, vol60 = _rolling_mean(df, "Volume", 20), _rolling_mean(df, "Volume", 60)
    if trap >= 70.0:
        return "NONE"
    if (
        len(close.dropna()) >= 20
        and _is_finite(vol20)
        and _is_finite(vol60)
        and vol20 > vol60 * 1.4
        and _is_finite(price)
        and _is_finite(ma20)
        and price < ma20
    ):
        return "DISTRIBUTION"
    if (
        breakout >= 65.0
        and _is_finite(price)
        and _is_finite(ma20)
        and price > ma20
    ):
        return "BREAKOUT"
    if (
        _is_finite(ma20)
        and _is_finite(ma50)
        and ma20 >= ma50
        and breakout >= 35.0
        and _is_finite(vol20)
        and _is_finite(vol60)
        and vol20 >= vol60 * 0.9
    ):
        return "ACCUMULATION"
    return "NONE"


def entry_point(
    df: pd.DataFrame,
    breakout: float | None = None,
    volume_score: float | None = None,
    value_trap_risk_value: float | None = None,
) -> dict[str, Any]:
    breakout = breakout_score(df) if breakout is None else breakout
    close = _series(df, "Close")
    high = _series(df, "High")
    low = _series(df, "Low")
    price, atr = _latest(df, "Close"), _latest(df, "ATR14")
    ma20, ma50 = _latest(df, "MA20"), _latest(df, "MA50")
    rsi = _latest(df, "RSI14")
    resistance = (
        float(high.iloc[-21:-1].max()) if len(high.dropna()) >= 21 else price
    )
    support = float(low.iloc[-20:].min()) if len(low.dropna()) >= 20 else price
    volume_history = pd.to_numeric(
        _series(df, "Volume").iloc[-21:-1], errors="coerce"
    ).replace([np.inf, -np.inf], np.nan)
    vol20 = (
        float(volume_history.mean())
        if len(volume_history) == 20 and volume_history.notna().all()
        else np.nan
    )
    volume_now = _latest(df, "Volume")
    volume_ratio = (
        float(volume_now / vol20)
        if _is_finite(volume_now) and _is_finite(vol20) and vol20 > 0
        else np.nan
    )
    # ``volume_score`` measures long-horizon accumulation and remains in the
    # public signature for compatibility.  It must not substitute for current
    # breakout-event volume confirmation.
    _ = volume_score
    cmf = _latest(df, "CMF")
    ad_slope = _latest(df, "AD_Slope")
    obv = _series(df, "OBV")
    obv_up = bool(len(obv.dropna()) >= 6 and obv.iloc[-1] > obv.iloc[-6])
    flow_confirmed = bool(
        (_is_finite(cmf) and cmf > 0)
        or (_is_finite(ad_slope) and ad_slope > 0)
        or obv_up
    )
    volume_confirmed = bool(
        _is_finite(volume_ratio)
        and volume_ratio >= BREAKOUT_CONFIRM_MIN_VOLUME_RATIO
    )
    price_breakout = bool(
        breakout >= 75.0
        and _is_finite(price)
        and _is_finite(resistance)
        and price > resistance
    )
    trap = (
        float(value_trap_risk_value)
        if _is_finite(value_trap_risk_value)
        else value_trap_risk(df)
    )
    if not _is_finite(price):
        return {
            "score": 0.0,
            "signal": "AVOID",
            "low": np.nan,
            "high": np.nan,
            "breakout": np.nan,
            "stop": np.nan,
            "volume_ratio": np.nan,
            "volume_confirmed": False,
            "flow_confirmed": False,
            "price_breakout": False,
            "zone_distance_pct": np.nan,
            "zone_distance_atr": np.nan,
            "pullback_quality": 0.0,
            "projected_target": np.nan,
            "stop_distance_pct": np.nan,
            "reward_risk_ratio": np.nan,
        }
    atr = atr if _is_finite(atr) and atr > 0 else price * 0.03
    # Define a forward-looking support zone.  Anchoring it around the current
    # close made nearly every WAIT_PULLBACK row already sit inside EntryZone.
    support_anchor = support + atr * 0.55
    if _is_finite(ma20) and ma20 <= price:
        support_anchor = max(support_anchor, float(ma20))
    support_anchor = min(support_anchor, price)
    low_zone = max(support, support_anchor - atr * 0.35)
    high_zone = min(resistance, support_anchor + atr * 0.35)
    if high_zone < low_zone:
        high_zone = low_zone
    if low_zone <= price <= high_zone:
        zone_distance = 0.0
    elif price > high_zone:
        zone_distance = price - high_zone
    else:
        zone_distance = price - low_zone
    zone_distance_pct = zone_distance / price * 100.0 if price > 0 else np.nan
    zone_distance_atr = zone_distance / atr if atr > 0 else np.nan
    if zone_distance == 0.0:
        pullback_quality = 100.0
    elif zone_distance > 0.0:
        pullback_quality = _clamp(100.0 - max(zone_distance_atr, 0.0) * 30.0, 0.0, 100.0)
    else:
        pullback_quality = _clamp(65.0 - abs(zone_distance_atr) * 25.0, 0.0, 65.0)
    score = 0.0
    if _is_finite(ma20) and price >= ma20:
        score += 20.0
    if _is_finite(ma50) and _is_finite(ma20) and ma20 >= ma50:
        score += 20.0
    if support <= price <= support + atr * 1.5:
        score += 20.0
    if breakout >= 65.0:
        score += 25.0
    elif breakout >= 45.0:
        score += 10.0
    if len(close) >= 6 and close.iloc[-1] >= close.iloc[-6]:
        score += 15.0
    score = _clamp(score, 0.0, 100.0)
    if trap >= 70.0:
        signal = "AVOID"
    elif _is_finite(rsi) and rsi >= 78.0:
        signal = "HOLD_WAIT"
    elif price_breakout and volume_confirmed and flow_confirmed:
        signal = "BREAKOUT_CONFIRM"
    elif price_breakout:
        signal = "PRICE_BREAKOUT"
    elif score >= 70.0 and low_zone <= price <= high_zone:
        signal = "BUY_NOW"
    elif score >= 50.0 and price > high_zone:
        signal = "WAIT_PULLBACK"
    elif score >= 50.0 and low_zone <= price <= high_zone:
        signal = "HOLD_WAIT"
    elif score >= 35.0:
        signal = "HOLD_WAIT"
    else:
        signal = "AVOID"
    stop = max((resistance if price_breakout else support) - atr, 0.0)
    projected_target = (
        price + atr * 2.5 if price_breakout else max(resistance, price)
    )
    risk_amount = max(price - stop, 0.0)
    reward_amount = max(projected_target - price, 0.0)
    stop_distance_pct = risk_amount / price * 100.0 if price > 0 else np.nan
    reward_risk_ratio = (
        reward_amount / risk_amount if risk_amount > 0 else np.nan
    )
    return {
        "score": score,
        "signal": signal,
        "low": low_zone,
        "high": high_zone,
        "breakout": resistance,
        "stop": stop,
        "volume_ratio": volume_ratio,
        "volume_confirmed": volume_confirmed,
        "flow_confirmed": flow_confirmed,
        "price_breakout": price_breakout,
        "zone_distance_pct": zone_distance_pct,
        "zone_distance_atr": zone_distance_atr,
        "pullback_quality": pullback_quality,
        "projected_target": projected_target,
        "stop_distance_pct": stop_distance_pct,
        "reward_risk_ratio": reward_risk_ratio,
    }


def value_trap_risk_score(df: pd.DataFrame) -> float:
    return value_trap_risk(df)


def classify_style(df: pd.DataFrame, is_etf: bool = False) -> str:
    if is_etf:
        return "ETF趋势/资金"
    if len(df) < 60 or "Close" not in df.columns:
        return "数据不足"
    close_now = df["Close"].iloc[-1]
    atr_now = df["ATR14"].iloc[-1] if "ATR14" in df.columns else np.nan
    atr_pct = (
        float(atr_now) / float(close_now)
        if _is_finite(close_now)
        and float(close_now) > 0
        and _is_finite(atr_now)
        else np.nan
    )
    roc_now = df["ROC"].iloc[-1] if "ROC" in df.columns else np.nan
    roc = float(roc_now) if _is_finite(roc_now) else 0.0
    volume_ratio = 1.0
    if "VolMA20" in df.columns and "VolMA120" in df.columns:
        vol_ma20 = df["VolMA20"].iloc[-1]
        vol_ma120 = df["VolMA120"].iloc[-1]
        if (
            _is_finite(vol_ma20)
            and _is_finite(vol_ma120)
            and float(vol_ma120) > 0
        ):
            volume_ratio = float(vol_ma20) / float(vol_ma120)
    if _is_finite(atr_pct) and atr_pct >= 0.045:
        return "高波动成长"
    if roc >= 12:
        return "趋势成长"
    if volume_ratio >= 1.25:
        return "资金吸筹"
    if _is_finite(atr_pct) and atr_pct <= 0.025:
        return "低波动防守"
    return "均衡"


def _style_adjustment(
    df: pd.DataFrame, style: str
) -> tuple[float, float, float, float, float]:
    if style == "高波动成长":
        return (1.15, 1.05, 0.90, 0.85, 0.95)
    if style == "趋势成长":
        return (1.25, 1.00, 0.90, 0.85, 0.95)
    if style == "资金吸筹":
        return (0.90, 1.05, 1.25, 1.05, 1.00)
    if style == "低波动防守":
        return (0.90, 0.95, 1.05, 1.25, 1.20)
    if style == "ETF趋势/资金":
        return (1.00, 1.00, 1.10, 1.00, 0.90)
    return (1.00, 1.00, 1.00, 1.00, 1.00)


def _has_finite_values(
    df: pd.DataFrame, columns: tuple[str, ...], minimum: int = 1
) -> bool:
    if not all(column in df.columns for column in columns):
        return False
    values = df[list(columns)].apply(pd.to_numeric, errors="coerce")
    values = values.replace([np.inf, -np.inf], np.nan)
    return len(values.dropna()) >= minimum and not values.iloc[-1].isna().any()


def _score_dimensions_available(
    df: pd.DataFrame,
) -> tuple[bool, bool, bool, bool, bool]:
    trend_available = len(df) >= 252 and _has_finite_values(
        df, ("Close", "MA200"), minimum=60
    )
    volume_available = len(df) >= 120 and (
        _has_finite_values(
            df,
            ("VolMA20", "VolMA120"),
            minimum=VOLUME_ACCUM_MIN_DAYS,
        )
        or _has_finite_values(df, ("VolZScore",), minimum=10)
    )
    accumulation_available = len(df) >= 60 and (
        _has_finite_values(df, ("OBV",), minimum=40)
        or _has_finite_values(
            df,
            ("AD", "AD_Slope"),
            minimum=AD_SLOPE_LOOKBACK,
        )
        or _has_finite_values(df, ("CMF",), minimum=20)
        or _has_finite_values(df, ("MFI",), minimum=1)
    )
    volatility_available = len(df) >= BB_WIDTH_COMPRESSION_LOOKBACK and (
        _has_finite_values(df, ("ATR14", "ATR50"))
        or _has_finite_values(
            df,
            ("BB_Width",),
            minimum=BB_WIDTH_COMPRESSION_LOOKBACK,
        )
        or _has_finite_values(df, ("HV20", "HV60"))
    )
    structure_available = len(df) >= 252 and _has_finite_values(
        df, ("Close", "High", "Low")
    )
    return (
        trend_available,
        volume_available,
        accumulation_available,
        volatility_available,
        structure_available,
    )


def execution_quality_score(
    df: pd.DataFrame, entry: dict[str, Any] | None = None
) -> float:
    """Score execution location only; trend and breakout evidence live elsewhere."""
    if df is None or df.empty:
        return 0.0
    price = _latest(df, "Close")
    atr = _latest(df, "ATR14")
    rsi = _latest(df, "RSI14")
    ma20 = _latest(df, "MA20")
    high = _series(df, "High")
    low = _series(df, "Low")
    if not _is_finite(price) or price <= 0:
        return 0.0
    effective_atr = atr if _is_finite(atr) and atr > 0 else price * 0.03
    support = float(low.dropna().iloc[-20:].min()) if len(low.dropna()) >= 20 else price - effective_atr
    resistance = float(high.dropna().iloc[-21:-1].max()) if len(high.dropna()) >= 21 else price + effective_atr * 2.0
    stop = float(entry.get("stop", np.nan)) if entry else np.nan
    if not _is_finite(stop):
        stop = max(support - effective_atr, 0.0)

    score = 0.0
    price_breakout = bool(entry and entry.get("price_breakout", False))
    execution_support = resistance if price_breakout else support
    distance_support_atr = max(0.0, price - execution_support) / max(effective_atr, 1e-9)
    score += (1.0 - _clamp(distance_support_atr / 3.0)) * 35.0

    if _is_finite(ma20):
        ma_distance_atr = abs(price - ma20) / max(effective_atr, 1e-9)
        score += (1.0 - _clamp(ma_distance_atr / 2.5)) * 20.0

    risk_distance = (price - stop) / price if price > 0 and stop >= 0 else np.nan
    if _is_finite(risk_distance):
        # A 2%-8% stop distance is practical; extremely wide or zero stops are poor execution.
        if 0.02 <= risk_distance <= 0.08:
            score += 20.0
        elif 0.01 <= risk_distance <= 0.12:
            score += 10.0

    projected_target = (
        float(entry.get("projected_target", np.nan)) if entry else np.nan
    )
    if not _is_finite(projected_target):
        projected_target = price + effective_atr * 2.5 if price_breakout else resistance
    reward = max(0.0, projected_target - price)
    risk_amount = max(price - stop, effective_atr * 0.25)
    reward_risk = reward / risk_amount if risk_amount > 0 else 0.0
    score += _clamp(reward_risk / 2.5) * 15.0

    if _is_finite(rsi):
        if 40.0 <= rsi <= 68.0:
            score += 10.0
        elif 30.0 <= rsi <= 75.0:
            score += 5.0
    return _clamp(score, 0.0, 100.0)


def score_ticker(df: pd.DataFrame, is_etf: bool = False) -> ScoreBreakdown:
    """Compute orthogonal setup, trigger and execution components."""
    available = _score_dimensions_available(df)
    missing_indicators = available.count(False)
    indicator_coverage = sum(available) / len(available)

    if missing_indicators >= 4:
        logger.warning(
            "数据不足：%d/5 个维度不可用，覆盖率 %.1f%%，跳过评分",
            missing_indicators,
            indicator_coverage * 100,
        )
        return ScoreBreakdown(
            total=0.0,
            missing_indicators=missing_indicators,
            indicator_coverage=indicator_coverage,
            confidence=0.0,
        )

    raw_scores = (
        score_trend(df) if available[0] else 0.0,
        score_volume(df) if available[1] else 0.0,
        score_accumulation(df) if available[2] else 0.0,
        score_volatility(df) if available[3] else 0.0,
        score_structure(df) if available[4] else 0.0,
    )
    style = classify_style(df, is_etf=is_etf)
    adjustments = _style_adjustment(df, style)
    limits = tuple(
        float(value)
        for value in (
            SCORING_WEIGHTS.trend,
            SCORING_WEIGHTS.volume,
            SCORING_WEIGHTS.accumulation,
            SCORING_WEIGHTS.volatility,
            SCORING_WEIGHTS.structure,
        )
    )
    adjusted_scores = tuple(
        _clamp(score * adjustment, 0.0, limit)
        for score, adjustment, limit in zip(raw_scores, adjustments, limits)
    )
    trend, volume, accumulation, volatility, structure = adjusted_scores
    available_weight = sum(
        limit for is_available, limit in zip(available, limits) if is_available
    )
    total = (
        sum(
            score
            for is_available, score in zip(available, adjusted_scores)
            if is_available
        )
        / available_weight
        * 100.0
        if available_weight
        else 0.0
    )

    trap = value_trap_risk(df, is_etf=is_etf)
    breakout = breakout_score(df)
    entry = entry_point(
        df,
        breakout,
        volume_score=volume,
        value_trap_risk_value=trap,
    )
    execution_raw = execution_quality_score(df, entry)

    setup_coverage = 0.55 + 0.45 * indicator_coverage
    trigger_coverage = 0.75 + 0.25 * indicator_coverage
    execution_coverage = 0.70 + 0.30 * indicator_coverage
    base_score = _clamp(total * setup_coverage, 0.0, 100.0)
    trigger_score = _clamp(breakout * trigger_coverage, 0.0, 100.0)
    execution_score = _clamp(execution_raw * execution_coverage, 0.0, 100.0)

    setup_weight, trigger_weight, execution_weight = _model_component_weights()
    final_score = _clamp(
        base_score * setup_weight
        + trigger_score * trigger_weight
        + execution_score * execution_weight,
        0.0,
        100.0,
    )
    coverage_cap = 40.0 + 60.0 * indicator_coverage
    final_score = min(final_score, coverage_cap)

    contributions = ScoreContributions(
        {
            "trend": trend,
            "volume": volume,
            "accumulation": accumulation,
            "compression": volatility,
            "structure": structure,
        }
    )
    contributions.update(
        {
            "base": base_score,
            "breakout": breakout,
            "entry": entry["score"],
            "execution": execution_score,
            "coverage_cap": coverage_cap,
            "value_trap_risk": trap,
        }
    )

    return ScoreBreakdown(
        total=total,
        trend=trend,
        volume=volume,
        accumulation=accumulation,
        volatility=volatility,
        structure=structure,
        missing_indicators=missing_indicators,
        indicator_coverage=indicator_coverage,
        confidence=indicator_coverage,
        base_score=base_score,
        breakout_score=breakout,
        entry_score=entry["score"],
        execution_score=execution_score,
        value_trap_risk=trap,
        trigger_score=trigger_score,
        final_score=final_score,
        entry_zone_low=entry["low"],
        entry_zone_high=entry["high"],
        breakout_buy_price=entry["breakout"],
        stop_loss=entry["stop"],
        contributions=contributions,
    )
