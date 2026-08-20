"""v79 scoring acceleration with exact formula preservation.

The scanner/backtest repeatedly asks the scoring layer for the same normalized
columns and endpoint statistics while evaluating one DataFrame.  Cache those
normalized Series per worker thread, use NumPy for the component reductions,
and memoize endpoint-only helpers.  The public score formulas, thresholds and
weights are unchanged; unusual/missing-data paths preserve the stable semantics.
"""

from __future__ import annotations

import threading
import weakref
from typing import Any

import numpy as np
import pandas as pd

import score_core as _score
import volatility_state as _vol

_LEGACY_SERIES = _score._series
_LEGACY_LATEST = _score._latest
_LEGACY_ROLLING_MEAN = _score._rolling_mean
_LEGACY_SAFE_RETURN = _score._safe_return
_LEGACY_SCORE_DIMENSIONS_AVAILABLE = _score._score_dimensions_available
_LEGACY_SCORE_TREND = _score.score_trend
_LEGACY_SCORE_VOLUME = _score.score_volume
_LEGACY_SCORE_ACCUMULATION = _score.score_accumulation
_LEGACY_SCORE_STRUCTURE = _score.score_structure
_LEGACY_CLASSIFY_STYLE = _score.classify_style
_LEGACY_ENTRY_POINT = _score.entry_point
_LEGACY_VOLATILITY_STATE = _vol.evaluate_volatility_contraction

_TLS = threading.local()
_INSTALLED = False


class _FrameState:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame_ref = weakref.ref(frame)
        self.series: dict[str, pd.Series] = {}
        self.arrays: dict[str, np.ndarray] = {}
        self.entry: dict[tuple[object, ...], dict[str, Any]] = {}
        self.style: dict[bool, str] = {}
        self.volatility_state: _vol.VolatilityContractionState | None = None


def _state(frame: pd.DataFrame) -> _FrameState:
    current = getattr(_TLS, "frame_state", None)
    if not isinstance(current, _FrameState) or current.frame_ref() is not frame:
        current = _FrameState(frame)
        _TLS.frame_state = current
    return current


def clear_thread_score_cache() -> None:
    if hasattr(_TLS, "frame_state"):
        delattr(_TLS, "frame_state")


def _series(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(index=df.index, dtype=float)
    state = _state(df)
    cached = state.series.get(column)
    if cached is not None:
        return cached
    values = pd.to_numeric(df[column], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    state.series[column] = values
    return values


def _array(df: pd.DataFrame, column: str) -> np.ndarray:
    state = _state(df)
    cached = state.arrays.get(column)
    if cached is not None:
        return cached
    values = _series(df, column).to_numpy(dtype=np.float64, copy=False)
    state.arrays[column] = values
    return values


def _finite_values(df: pd.DataFrame, column: str) -> np.ndarray:
    values = _array(df, column)
    return values[np.isfinite(values)]


def _latest(df: pd.DataFrame, column: str) -> float:
    values = _array(df, column)
    if values.size == 0:
        return np.nan
    last = float(values[-1])
    if np.isfinite(last):
        return last
    finite = np.flatnonzero(np.isfinite(values))
    return float(values[int(finite[-1])]) if finite.size else np.nan


def _rolling_mean(df: pd.DataFrame, column: str, window: int) -> float:
    values = _finite_values(df, column)
    if len(values) < int(window):
        return np.nan
    return float(np.mean(values[-int(window) :]))


def _safe_return(values: pd.Series, periods: int) -> float:
    numeric = pd.to_numeric(values, errors="coerce").to_numpy(dtype=np.float64)
    clean = numeric[np.isfinite(numeric)]
    if len(clean) <= int(periods):
        return np.nan
    start = float(clean[-int(periods) - 1])
    end = float(clean[-1])
    return (end / start - 1.0) * 100.0 if start > 0 else np.nan


def _has_finite_values_fast(
    df: pd.DataFrame,
    columns: tuple[str, ...],
    minimum: int = 1,
) -> bool:
    if not all(column in df.columns for column in columns) or df.empty:
        return False
    arrays = [_array(df, column) for column in columns]
    finite = np.isfinite(arrays[0])
    for values in arrays[1:]:
        finite = finite & np.isfinite(values)
    return bool(np.count_nonzero(finite) >= int(minimum) and bool(finite[-1]))


def _score_dimensions_available(
    df: pd.DataFrame,
) -> tuple[bool, bool, bool, bool, bool]:
    trend_available = len(df) >= 252 and _has_finite_values_fast(
        df, ("Close", "MA200"), minimum=60
    )
    volume_available = len(df) >= 120 and (
        _has_finite_values_fast(
            df,
            ("VolMA20", "VolMA120"),
            minimum=_score.VOLUME_ACCUM_MIN_DAYS,
        )
        or _has_finite_values_fast(df, ("VolZScore",), minimum=10)
    )
    accumulation_available = len(df) >= 60 and (
        _has_finite_values_fast(df, ("OBV",), minimum=40)
        or _has_finite_values_fast(
            df,
            ("AD", "AD_Slope"),
            minimum=_score.AD_SLOPE_LOOKBACK,
        )
        or _has_finite_values_fast(df, ("CMF",), minimum=20)
        or _has_finite_values_fast(df, ("MFI",), minimum=1)
    )
    volatility_available = len(df) >= _score.BB_WIDTH_COMPRESSION_LOOKBACK and (
        _has_finite_values_fast(df, ("ATR14", "ATR50"))
        or _has_finite_values_fast(
            df,
            ("BB_Width",),
            minimum=_score.BB_WIDTH_COMPRESSION_LOOKBACK,
        )
        or _has_finite_values_fast(df, ("HV20", "HV60"))
    )
    structure_available = len(df) >= 252 and _has_finite_values_fast(
        df, ("Close", "High", "Low")
    )
    return (
        trend_available,
        volume_available,
        accumulation_available,
        volatility_available,
        structure_available,
    )


def score_trend(df: pd.DataFrame) -> float:
    if len(df) < 252 or "Close" not in df.columns or "MA200" not in df.columns:
        return 0.0
    close = _array(df, "Close")
    ma200 = _array(df, "MA200")
    valid = np.isfinite(close) & np.isfinite(ma200)
    if np.count_nonzero(valid) < 60:
        return 0.0
    close_valid = close[valid]
    ma_valid = ma200[valid]
    price_now = float(close_valid[-1])
    ma200_now = float(ma_valid[-1])
    if price_now <= 0 or ma200_now <= 0:
        return 0.0

    score = 0.0
    ma_recent = ma_valid[-60:]
    slope_pct = float(ma_recent[-1] / ma_recent[0] - 1.0)
    if slope_pct < 0:
        score += _score._clamp(abs(slope_pct) / 0.12) * 5.0

    below_pct = (ma200_now - price_now) / ma200_now
    if below_pct > 0:
        score += _score._clamp(below_pct / 0.30) * 6.0
        score -= _score._clamp(max(below_pct - 0.45, 0.0) / 0.30) * 3.0

    below = close_valid < ma_valid
    above_positions = np.flatnonzero(~below)
    days_below = (
        len(close_valid) - int(above_positions[-1]) - 1
        if above_positions.size
        else len(close_valid)
    )
    score += _score._clamp(days_below / 250.0) * 3.0

    lookback = close_valid[-min(504, len(close_valid)) :]
    peak = float(np.max(lookback))
    drawdown = (price_now - peak) / peak if peak > 0 else 0.0
    depth = abs(drawdown)
    if 0.15 <= depth <= 0.50:
        score += _score._clamp(1.0 - abs(depth - 0.32) / 0.25) * 3.0

    recovery = close_valid[-20:]
    if len(recovery) >= 10:
        recent_slope = float(recovery[-1] / recovery[0] - 1.0)
        if recent_slope > 0:
            score += _score._clamp(recent_slope / 0.12) * 3.0
    return _score._clamp(score, 0.0, 20.0)


def score_volume(df: pd.DataFrame) -> float:
    if len(df) < 120:
        return 0.0
    score = 0.0
    if "VolMA20" in df.columns and "VolMA120" in df.columns:
        short = _array(df, "VolMA20")
        long = _array(df, "VolMA120")
        valid = np.isfinite(short) & np.isfinite(long) & (long != 0.0)
        ratio = short[valid] / long[valid]
        if len(ratio) >= _score.VOLUME_ACCUM_MIN_DAYS:
            qualifying = ratio >= float(_score.VOLUME_ACCUM_RATIO)
            if qualifying.size and qualifying[-1]:
                failures = np.flatnonzero(~qualifying)
                consecutive = (
                    len(qualifying)
                    if failures.size == 0
                    else len(qualifying) - int(failures[-1]) - 1
                )
            else:
                consecutive = 0
            if consecutive >= _score.VOLUME_ACCUM_MIN_DAYS:
                score += 4.0 + _score._clamp(
                    (consecutive - _score.VOLUME_ACCUM_MIN_DAYS) / 80.0
                ) * 6.0
            ratio_now = float(ratio[-1])
            score += _score._clamp(
                (ratio_now - _score.VOLUME_ACCUM_RATIO) / 0.8
            ) * 3.0
            if len(ratio) >= 20:
                ratio_change = float(ratio[-1] - ratio[-20])
                score += _score._clamp(ratio_change / 0.5) * 4.0
    if "VolZScore" in df.columns:
        z = _finite_values(df, "VolZScore")
        z_recent = z[-30:]
        if len(z_recent) >= 10:
            score += float(np.mean(z_recent > 0.0)) * 3.0
            score += _score._clamp(float(z_recent[-1]) / 2.0) * 2.0
    return _score._clamp(score, 0.0, 25.0)


def score_accumulation(df: pd.DataFrame) -> float:
    if len(df) < 60:
        return 0.0
    score = 0.0

    if "OBV" in df.columns:
        close = _array(df, "Close")[-60:]
        obv = _array(df, "OBV")[-60:]
        valid = np.isfinite(close) & np.isfinite(obv)
        close_recent = close[valid]
        obv_recent = obv[valid]
        if len(close_recent) >= 40:
            split = len(close_recent) // 2
            first_price_low = float(np.min(close_recent[:split]))
            second_price_low = float(np.min(close_recent[split:]))
            first_obv_low = float(np.min(obv_recent[:split]))
            second_obv_low = float(np.min(obv_recent[split:]))
            price_now = float(close_recent[-1])
            obv_now = float(obv_recent[-1])
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
        ad = _finite_values(df, "AD")
        ad_slope = df["AD_Slope"].iloc[-1]
        if len(ad) >= _score.AD_SLOPE_LOOKBACK and pd.notna(ad_slope):
            try:
                slope_value = float(ad_slope)
            except (TypeError, ValueError):
                return _LEGACY_SCORE_ACCUMULATION(df)
            ad_scale = max(
                float(np.median(np.abs(ad[-_score.AD_SLOPE_LOOKBACK :]))),
                1.0,
            )
            slope_score = _score._clamp(slope_value / (ad_scale * 0.03))
            score += slope_score * 5.0
            if float(ad[-1]) >= float(np.max(ad[-min(120, len(ad)) :])) * 0.95:
                score += 1.0

    if "CMF" in df.columns:
        cmf = _finite_values(df, "CMF")
        if len(cmf) >= 20:
            cmf_now = float(cmf[-1])
            cmf_change = cmf_now - float(cmf[-20])
            score += _score._clamp(cmf_now / 0.15) * 4.0
            score += _score._clamp(cmf_change / 0.10) * 2.0

    if "MFI" in df.columns:
        mfi = _array(df, "MFI")
        mfi_now = float(mfi[-1]) if len(mfi) else np.nan
        if np.isfinite(mfi_now):
            score += (
                3.0
                if 40 <= mfi_now <= 70
                else 1.5
                if 30 <= mfi_now <= 80
                else 0.0
            )
    return _score._clamp(score, 0.0, 25.0)


def _nan_max(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    return float(np.max(finite)) if finite.size else np.nan


def _nan_min(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    return float(np.min(finite)) if finite.size else np.nan


def _nan_mean(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    return float(np.mean(finite)) if finite.size else np.nan


def score_structure(df: pd.DataFrame) -> float:
    if len(df) < 252 or not all(
        column in df.columns for column in ("Close", "High", "Low")
    ):
        return 0.0
    score = 0.0
    if "Low52W" in df.columns and "DistToLow52W" in df.columns:
        dist_low = df["DistToLow52W"].iloc[-1]
        if _score._is_finite(dist_low) and 0 <= float(dist_low) <= 20:
            value = float(dist_low)
            if value < 8:
                score += value / 8 * 5
            elif value <= 12:
                score += 5
            else:
                score += (20 - value) / 8 * 5

    if len(df) >= _score.CONSOLIDATION_DAYS:
        window = int(_score.CONSOLIDATION_DAYS)
        high = _nan_max(_array(df, "High")[-window:])
        low = _nan_min(_array(df, "Low")[-window:])
        avg_price = _nan_mean(_array(df, "Close")[-window:])
        if np.isfinite(avg_price) and avg_price > 0:
            range_pct = (high - low) / avg_price * 100
            if np.isfinite(range_pct) and range_pct <= _score.CONSOLIDATION_MAX_RANGE_PCT:
                tightness = _score._clamp(
                    1 - range_pct / _score.CONSOLIDATION_MAX_RANGE_PCT,
                    0,
                    1,
                )
                score += (0.2 + tightness * 0.8) * 5

    if "RegSlope" in df.columns:
        reg_slope = df["RegSlope"].iloc[-1]
        if _score._is_finite(reg_slope):
            score += _score._clamp(1 - abs(float(reg_slope)) / 0.05, 0, 1) * 2
            if "RegR2" in df.columns:
                r2 = df["RegR2"].iloc[-1]
                if pd.notna(r2) and np.isfinite(float(r2)):
                    score += _score._clamp(float(r2), 0, 1) * 1

    if "Above_HVN" in df.columns and "DistToHVN_Pct" in df.columns:
        above_hvn = df["Above_HVN"].iloc[-1]
        dist_hvn = df["DistToHVN_Pct"].iloc[-1]
        if (
            bool(above_hvn)
            and _score._is_finite(dist_hvn)
            and 0 < float(dist_hvn) < 10
        ):
            score += _score._clamp(1 - float(dist_hvn) / 10, 0, 1) * 2
    return min(score, 15.0)


def classify_style(df: pd.DataFrame, is_etf: bool = False) -> str:
    state = _state(df)
    key = bool(is_etf)
    if key not in state.style:
        state.style[key] = str(_LEGACY_CLASSIFY_STYLE(df, is_etf=is_etf))
    return state.style[key]


def _value_key(value: Any) -> object:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ("raw", repr(value))
    if np.isnan(number):
        return ("nan",)
    if np.isposinf(number):
        return ("inf", 1)
    if np.isneginf(number):
        return ("inf", -1)
    return ("float", number)


def entry_point(
    df: pd.DataFrame,
    breakout: float | None = None,
    volume_score: float | None = None,
    value_trap_risk_value: float | None = None,
    price_decimals: int | None = None,
) -> dict[str, Any]:
    state = _state(df)
    # volume_score is intentionally excluded: the stable entry implementation
    # keeps it in the signature for compatibility but does not consume it.
    key = (
        _value_key(breakout),
        _value_key(value_trap_risk_value),
        price_decimals,
    )
    cached = state.entry.get(key)
    if cached is None:
        cached = dict(
            _LEGACY_ENTRY_POINT(
                df,
                breakout=breakout,
                volume_score=volume_score,
                value_trap_risk_value=value_trap_risk_value,
                price_decimals=price_decimals,
            )
        )
        state.entry[key] = cached
    return dict(cached)


def _latest_ratio(frame: pd.DataFrame, numerator: str, denominator: str) -> float:
    if numerator not in frame.columns or denominator not in frame.columns:
        return np.nan
    num = _array(frame, numerator)
    den = _array(frame, denominator)
    valid = np.isfinite(num) & np.isfinite(den)
    positions = np.flatnonzero(valid)
    if not positions.size:
        return np.nan
    index = int(positions[-1])
    denominator_value = float(den[index])
    return float(num[index] / denominator_value) if denominator_value > 0 else np.nan


def evaluate_volatility_contraction(
    df: pd.DataFrame | None,
) -> _vol.VolatilityContractionState:
    if df is None or df.empty:
        return _vol.VolatilityContractionState()
    state = _state(df)
    if state.volatility_state is not None:
        return state.volatility_state

    atr_ratio = _latest_ratio(df, "ATR14", "ATR50")
    hv_ratio = _latest_ratio(df, "HV20", "HV60")
    atr_contracting = bool(
        np.isfinite(atr_ratio) and atr_ratio <= float(_vol.ATR_CONTRACTION_RATIO)
    )
    hv_contracting = bool(
        np.isfinite(hv_ratio) and hv_ratio <= float(_vol.HV_CONTRACTION_RATIO)
    )

    bb_ratio = np.nan
    bb_percentile = np.nan
    bb_contracting = False
    if "BB_Width" in df.columns:
        bb = _finite_values(df, "BB_Width")
        lookback = max(20, int(_vol.BB_WIDTH_COMPRESSION_LOOKBACK))
        if len(bb) >= lookback:
            recent = bb[-lookback:]
            exclude_recent = min(
                max(1, int(_vol.BB_CONTRACTION_EXCLUDE_RECENT)),
                max(1, len(recent) // 3),
            )
            baseline_window = recent[:-exclude_recent]
            baseline = (
                float(np.median(baseline_window))
                if baseline_window.size
                else np.nan
            )
            current = float(recent[-1])
            if np.isfinite(baseline) and baseline > 0 and np.isfinite(current):
                bb_ratio = float(current / baseline)
                bb_percentile = float(np.mean(recent <= current))
                bb_contracting = bool(
                    bb_ratio <= float(_vol.BB_CONTRACTION_RATIO)
                    and bb_percentile <= float(_vol.BB_CONTRACTION_MAX_PERCENTILE)
                )

    available = int(np.isfinite(atr_ratio)) + int(np.isfinite(bb_ratio)) + int(
        np.isfinite(hv_ratio)
    )
    result = _vol.VolatilityContractionState(
        atr_ratio=atr_ratio,
        bb_ratio=bb_ratio,
        bb_percentile=bb_percentile,
        hv_ratio=hv_ratio,
        atr_contracting=atr_contracting,
        bb_contracting=bb_contracting,
        hv_contracting=hv_contracting,
        available_components=available,
    )
    state.volatility_state = result
    return result


def install() -> None:
    """Install/re-install v79 after older facades without changing semantics."""
    global _INSTALLED
    _score._series = _series
    _score._latest = _latest
    _score._rolling_mean = _rolling_mean
    _score._safe_return = _safe_return
    _score._score_dimensions_available = _score_dimensions_available
    _score.score_trend = score_trend
    _score.score_volume = score_volume
    _score.score_accumulation = score_accumulation
    _score.score_structure = score_structure
    _score.classify_style = classify_style
    _score.entry_point = entry_point
    _vol.evaluate_volatility_contraction = evaluate_volatility_contraction
    _INSTALLED = True


install()
