"""v79 exact endpoint score kernels.

Vectorize the three remaining pandas-heavy endpoint calculations used by full
scanner scoring and EXACT backtests.  Formulas and dropna chronology mirror the
stable score_core implementation; all normalized columns come from the v79
thread-local cache.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import score_acceleration_v79 as _cache
import score_core as _score

_LEGACY_VALUE_TRAP_RISK = _score.value_trap_risk
_LEGACY_BREAKOUT_SCORE = _score.breakout_score
_LEGACY_EXECUTION_QUALITY_SCORE = _score.execution_quality_score
_INSTALLED = False


def _finite(column: pd.Series) -> np.ndarray:
    values = column.to_numpy(dtype=np.float64, copy=False)
    return values[np.isfinite(values)]


def value_trap_risk(df: pd.DataFrame, is_etf: bool = False) -> float:
    close = _score._series(df, "Close")
    volume = _score._series(df, "Volume")
    clean_close = _finite(close)
    if len(clean_close) < 121:
        return 0.0

    risk = 0.0
    price = _score._latest(df, "Close")
    ma20 = _score._latest(df, "MA20")
    ma50 = _score._latest(df, "MA50")
    ret20 = _score._safe_return(close, 20)
    ret60 = _score._safe_return(close, 60)
    ret120 = _score._safe_return(close, 120)

    if _score._is_finite(ret120) and ret120 < 0:
        risk += _score._clamp(abs(ret120) / 45.0) * 15.0

    if _score._is_finite(ma50) and len(df) >= 25 and "MA50" in df:
        ma50_values = _score._series(df, "MA50").to_numpy(
            dtype=np.float64, copy=False
        )
        old_ma50 = float(ma50_values[-25]) if len(ma50_values) >= 25 else np.nan
        if _score._is_finite(old_ma50) and old_ma50 > 0 and ma50 < old_ma50:
            risk += _score._clamp((old_ma50 - ma50) / old_ma50 / 0.12) * 12.0
        if (
            _score._is_finite(price)
            and price < ma50
            and _score._is_finite(ret20)
            and ret20 < 0
        ):
            risk += 8.0

    recent_low = float(np.min(clean_close[-40:]))
    prior_low = (
        float(np.min(clean_close[-80:-40]))
        if len(clean_close) >= 80
        else recent_low
    )
    if prior_low > 0 and recent_low < prior_low * 0.98:
        risk += _score._clamp((prior_low - recent_low) / prior_low / 0.12) * 15.0

    if (
        _score._is_finite(ret20)
        and _score._is_finite(ret60)
        and ret20 < 0
        and ret60 < 0
    ):
        risk += 10.0

    flow_positive = 0
    flow_available = 0
    cmf = _score._latest(df, "CMF")
    ad_slope = _score._latest(df, "AD_Slope")
    obv = _finite(_score._series(df, "OBV"))
    for value in (cmf, ad_slope):
        if _score._is_finite(value):
            flow_available += 1
            flow_positive += int(value > 0)
    if len(obv) >= 20:
        flow_available += 1
        flow_positive += int(float(obv[-1] - obv[-20]) > 0)
    if flow_available:
        if flow_positive == 0:
            risk += 25.0
        elif flow_positive == 1:
            risk += 10.0
        elif flow_positive >= 2:
            risk -= 8.0

    clean_volume = _finite(volume)
    if len(clean_volume) >= 60:
        vol20 = float(np.mean(clean_volume[-20:]))
        vol60 = float(np.mean(clean_volume[-60:-20]))
        if vol60 > 0 and vol20 < vol60 * 0.75:
            if _score._is_finite(ret20) and ret20 < 0:
                risk += 10.0
            elif _score._is_finite(ret20) and ret20 >= 0:
                risk -= 3.0

    recovery_confirmed = (
        _score._is_finite(price)
        and _score._is_finite(ma20)
        and _score._is_finite(ma50)
        and price >= ma20 >= ma50
        and _score._is_finite(ret20)
        and ret20 > 0
    )
    if recovery_confirmed:
        risk -= 15.0
    elif _score._is_finite(ret20) and ret20 > 5.0 and flow_positive >= 2:
        risk -= 8.0

    if is_etf:
        risk *= 0.80
    return _score._clamp(risk, 0.0, 100.0)


def breakout_score(df: pd.DataFrame) -> float:
    close = _score._series(df, "Close").to_numpy(dtype=np.float64, copy=False)
    high = _score._series(df, "High").to_numpy(dtype=np.float64, copy=False)
    volume = _score._series(df, "Volume").to_numpy(dtype=np.float64, copy=False)
    valid = np.isfinite(close) & np.isfinite(high) & np.isfinite(volume)
    if np.count_nonzero(valid) < 60:
        return 0.0
    close_valid = close[valid]
    high_valid = high[valid]
    volume_valid = volume[valid]

    points = 0.0
    price = _score._latest(df, "Close")
    ma20 = _score._latest(df, "MA20")
    ma50 = _score._latest(df, "MA50")
    ma200 = _score._latest(df, "MA200")
    if all(_score._is_finite(value) for value in (price, ma20, ma50)):
        points += 15.0 if price > ma20 > ma50 else 8.0 if price > ma20 else 0.0
    if _score._is_finite(ma200) and price > ma200:
        points += 10.0

    if len(close_valid) >= 21:
        resistance = float(np.max(high_valid[-21:-1]))
        vol20 = float(np.mean(volume_valid[-21:-1]))
        vol_now = float(volume_valid[-1])
        if _score._is_finite(resistance) and price > resistance:
            points += 25.0
            if vol20 > 0 and vol_now >= vol20 * 1.5:
                points += 15.0

    if len(close_valid) >= 10:
        changes = np.diff(close_valid, prepend=np.nan)
        up = changes > 0
        recent_up = volume_valid[-10:][up[-10:]]
        recent_down = volume_valid[-10:][~up[-10:]]
        up_volume = float(np.mean(recent_up)) if recent_up.size else np.nan
        down_volume = float(np.mean(recent_down)) if recent_down.size else np.nan
        if (
            _score._is_finite(up_volume)
            and _score._is_finite(down_volume)
            and down_volume > 0
            and up_volume > down_volume * 1.15
        ):
            points += 15.0

    if len(close_valid) >= 20:
        recent_range = (
            float(np.max(close_valid[-5:]) - np.min(close_valid[-5:]))
            / max(price, 1e-9)
        )
        prior_range = (
            float(np.max(close_valid[-20:-5]) - np.min(close_valid[-20:-5]))
            / max(price, 1e-9)
        )
        if prior_range > 0 and recent_range < prior_range * 0.75:
            points += 10.0

    if (
        _score._is_finite(ma20)
        and len(close_valid) >= 10
        and close_valid[-1] > close_valid[-10]
        and ma20 > _score._rolling_mean(df, "MA20", 10)
    ):
        points += 10.0
    return _score._clamp(points, 0.0, 100.0)


def execution_quality_score(
    df: pd.DataFrame,
    entry: dict[str, Any] | None = None,
) -> float:
    if df is None or df.empty:
        return 0.0
    price = _score._latest(df, "Close")
    atr = _score._latest(df, "ATR14")
    rsi = _score._latest(df, "RSI14")
    ma20 = _score._latest(df, "MA20")
    high = _finite(_score._series(df, "High"))
    low = _finite(_score._series(df, "Low"))
    if not _score._is_finite(price) or price <= 0:
        return 0.0

    effective_atr = atr if _score._is_finite(atr) and atr > 0 else price * 0.03
    support = (
        float(np.min(low[-20:]))
        if len(low) >= 20
        else price - effective_atr
    )
    resistance = (
        float(np.max(high[-21:-1]))
        if len(high) >= 21
        else price + effective_atr * 2.0
    )
    stop = float(entry.get("stop", np.nan)) if entry else np.nan
    if not _score._is_finite(stop):
        stop = max(support - effective_atr, 0.0)

    score = 0.0
    price_breakout = bool(entry and entry.get("price_breakout", False))
    execution_support = resistance if price_breakout else support
    distance_support_atr = max(0.0, price - execution_support) / max(
        effective_atr, 1e-9
    )
    score += (1.0 - _score._clamp(distance_support_atr / 3.0)) * 35.0

    if _score._is_finite(ma20):
        ma_distance_atr = abs(price - ma20) / max(effective_atr, 1e-9)
        score += (1.0 - _score._clamp(ma_distance_atr / 2.5)) * 20.0

    risk_distance = (price - stop) / price if price > 0 and stop >= 0 else np.nan
    if _score._is_finite(risk_distance):
        if 0.02 <= risk_distance <= 0.08:
            score += 20.0
        elif 0.01 <= risk_distance <= 0.12:
            score += 10.0

    projected_target = (
        float(entry.get("projected_target", np.nan)) if entry else np.nan
    )
    if not _score._is_finite(projected_target):
        projected_target = (
            price + effective_atr * 2.5 if price_breakout else resistance
        )
    reward = max(0.0, projected_target - price)
    risk_amount = max(price - stop, effective_atr * 0.25)
    reward_risk = reward / risk_amount if risk_amount > 0 else 0.0
    score += _score._clamp(reward_risk / 2.5) * 15.0

    if _score._is_finite(rsi):
        if 40.0 <= rsi <= 68.0:
            score += 10.0
        elif 30.0 <= rsi <= 75.0:
            score += 5.0
    return _score._clamp(score, 0.0, 100.0)


def install() -> None:
    global _INSTALLED
    _cache.install()
    _score.value_trap_risk = value_trap_risk
    _score.breakout_score = breakout_score
    _score.execution_quality_score = execution_quality_score
    _INSTALLED = True


install()
