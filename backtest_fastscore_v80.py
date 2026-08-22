"""v80 whole-ticker vectorised FAST historical scoring.

FAST mode previously vectorised only a quick entry gate. Every surviving date
still sliced a 252-row DataFrame and ran the complete score engine. On a full
A-share universe that recreates tens of thousands of short pandas frames.

v80 evaluates the exact dense-history v51 score/entry formulas once for every
row of a ticker, then the chronological candidate loop performs O(1) array
lookups. Histories with unexpected indicator gaps fail closed to the v78
per-candidate implementation. EXACT mode is untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

import analytics_core as _core
import score_core as _score
import volatility_state as _vol

_INSTALLED = False
_LEGACY_SIGNAL_EVALUATIONS = _core._signal_evaluations
_FAST_VECTOR_START = 320
_REQUIRED_COLUMNS = (
    "Close",
    "High",
    "Low",
    "Volume",
    "MA20",
    "MA50",
    "MA200",
    "VolMA20",
    "VolMA120",
    "VolZScore",
    "OBV",
    "AD",
    "AD_Slope",
    "CMF",
    "MFI",
    "ATR14",
    "ATR50",
    "BB_Width",
    "HV20",
    "HV60",
    "DistToLow52W",
    "RegSlope",
    "RegR2",
    "RSI14",
)


@dataclass(frozen=True)
class FastScoreMatrix:
    base_score: np.ndarray
    trigger_score: np.ndarray
    execution_score: np.ndarray
    final_score: np.ndarray
    breakout_score: np.ndarray
    value_trap_risk: np.ndarray
    entry_signal: np.ndarray


def _series(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )


def _array(frame: pd.DataFrame, column: str) -> np.ndarray:
    return _series(frame, column).to_numpy(dtype=np.float64)


def _rolling_count(mask: np.ndarray, window: int) -> np.ndarray:
    values = mask.astype(np.int32, copy=False)
    prefix = np.empty(len(values) + 1, dtype=np.int64)
    prefix[0] = 0
    np.cumsum(values, out=prefix[1:])
    ends = np.arange(1, len(values) + 1, dtype=np.int64)
    starts = np.maximum(0, ends - int(window))
    return prefix[ends] - prefix[starts]


def _trailing_run(mask: np.ndarray) -> np.ndarray:
    positions = np.arange(len(mask), dtype=np.int64)
    last_failure = np.where(mask, -1, positions)
    np.maximum.accumulate(last_failure, out=last_failure)
    run = positions - last_failure
    run[~mask] = 0
    return run


def _clip01(values: np.ndarray) -> np.ndarray:
    return np.clip(values, 0.0, 1.0)


def _dense_history_supported(frame: pd.DataFrame) -> bool:
    if frame is None or len(frame) <= _FAST_VECTOR_START:
        return False
    if not all(column in frame.columns for column in _REQUIRED_COLUMNS):
        return False
    warmups = {
        "MA200": 199,
        "VolMA120": 119,
        "DistToLow52W": 251,
    }
    for column in _REQUIRED_COLUMNS:
        values = _series(frame, column)
        start = warmups.get(column, 199)
        if len(values) <= start or values.iloc[start:].isna().any():
            return False
    return True


def _rolling_endpoint_percentile(values: np.ndarray, window: int) -> np.ndarray:
    output = np.full(len(values), np.nan, dtype=np.float64)
    if len(values) < window:
        return output
    windows = np.lib.stride_tricks.sliding_window_view(values, int(window))
    current = windows[:, -1]
    valid = np.isfinite(windows).all(axis=1)
    percentages = np.full(len(windows), np.nan, dtype=np.float64)
    if valid.any():
        percentages[valid] = np.mean(
            windows[valid] <= current[valid, None], axis=1
        )
    output[window - 1 :] = percentages
    return output


def _fast_score_matrix(
    frame: pd.DataFrame,
    *,
    is_etf: bool,
) -> FastScoreMatrix | None:
    if not _dense_history_supported(frame):
        return None
    n = len(frame)
    index = frame.index
    close_s = _series(frame, "Close")
    high_s = _series(frame, "High")
    low_s = _series(frame, "Low")
    volume_s = _series(frame, "Volume")
    ma20_s = _series(frame, "MA20")
    ma50_s = _series(frame, "MA50")
    ma200_s = _series(frame, "MA200")
    vol20_s = _series(frame, "VolMA20")
    vol120_s = _series(frame, "VolMA120")
    z_s = _series(frame, "VolZScore")
    obv_s = _series(frame, "OBV")
    ad_s = _series(frame, "AD")
    ad_slope_s = _series(frame, "AD_Slope")
    cmf_s = _series(frame, "CMF")
    mfi_s = _series(frame, "MFI")
    atr14_s = _series(frame, "ATR14")
    atr50_s = _series(frame, "ATR50")
    bb_s = _series(frame, "BB_Width")
    hv20_s = _series(frame, "HV20")
    hv60_s = _series(frame, "HV60")
    dist_low_s = _series(frame, "DistToLow52W")
    reg_slope_s = _series(frame, "RegSlope")
    reg_r2_s = _series(frame, "RegR2")
    rsi_s = _series(frame, "RSI14")

    close = close_s.to_numpy(dtype=np.float64)
    high = high_s.to_numpy(dtype=np.float64)
    low = low_s.to_numpy(dtype=np.float64)
    volume = volume_s.to_numpy(dtype=np.float64)
    ma20 = ma20_s.to_numpy(dtype=np.float64)
    ma50 = ma50_s.to_numpy(dtype=np.float64)
    ma200 = ma200_s.to_numpy(dtype=np.float64)
    vol20 = vol20_s.to_numpy(dtype=np.float64)
    vol120 = vol120_s.to_numpy(dtype=np.float64)
    z = z_s.to_numpy(dtype=np.float64)
    obv = obv_s.to_numpy(dtype=np.float64)
    ad = ad_s.to_numpy(dtype=np.float64)
    ad_slope = ad_slope_s.to_numpy(dtype=np.float64)
    cmf = cmf_s.to_numpy(dtype=np.float64)
    mfi = mfi_s.to_numpy(dtype=np.float64)
    atr14 = atr14_s.to_numpy(dtype=np.float64)
    atr50 = atr50_s.to_numpy(dtype=np.float64)
    bb = bb_s.to_numpy(dtype=np.float64)
    hv20 = hv20_s.to_numpy(dtype=np.float64)
    hv60 = hv60_s.to_numpy(dtype=np.float64)
    dist_low = dist_low_s.to_numpy(dtype=np.float64)
    reg_slope = reg_slope_s.to_numpy(dtype=np.float64)
    reg_r2 = reg_r2_s.to_numpy(dtype=np.float64)
    rsi = rsi_s.to_numpy(dtype=np.float64)

    finite_close = np.isfinite(close)
    pair_trend = finite_close & np.isfinite(ma200)
    trend_available = (
        (_rolling_count(pair_trend, 252) >= 60)
        & pair_trend
        & (np.arange(n) >= 251)
    )
    pair_volume = np.isfinite(vol20) & np.isfinite(vol120)
    z_finite = np.isfinite(z)
    volume_available = (
        (
            ((_rolling_count(pair_volume, 252) >= int(_score.VOLUME_ACCUM_MIN_DAYS)) & pair_volume)
            | ((_rolling_count(z_finite, 252) >= 10) & z_finite)
        )
        & (np.arange(n) >= 119)
    )
    obv_finite = np.isfinite(obv)
    ad_pair = np.isfinite(ad) & np.isfinite(ad_slope)
    cmf_finite = np.isfinite(cmf)
    mfi_finite = np.isfinite(mfi)
    accumulation_available = (
        (
            ((_rolling_count(obv_finite, 252) >= 40) & obv_finite)
            | ((_rolling_count(ad_pair, 252) >= int(_score.AD_SLOPE_LOOKBACK)) & ad_pair)
            | ((_rolling_count(cmf_finite, 252) >= 20) & cmf_finite)
            | mfi_finite
        )
        & (np.arange(n) >= 59)
    )
    atr_pair = np.isfinite(atr14) & np.isfinite(atr50)
    hv_pair = np.isfinite(hv20) & np.isfinite(hv60)
    bb_finite = np.isfinite(bb)
    volatility_available = (
        (
            atr_pair
            | ((_rolling_count(bb_finite, 252) >= int(_score.BB_WIDTH_COMPRESSION_LOOKBACK)) & bb_finite)
            | hv_pair
        )
        & (np.arange(n) >= int(_score.BB_WIDTH_COMPRESSION_LOOKBACK) - 1)
    )
    structure_latest = finite_close & np.isfinite(high) & np.isfinite(low)
    structure_available = structure_latest & (np.arange(n) >= 251)
    coverage_count = (
        trend_available.astype(np.int8)
        + volume_available.astype(np.int8)
        + accumulation_available.astype(np.int8)
        + volatility_available.astype(np.int8)
        + structure_available.astype(np.int8)
    )
    indicator_coverage = coverage_count.astype(np.float64) / 5.0

    # Trend score: exact 252-row historical-frame semantics on dense mature data.
    trend = np.zeros(n, dtype=np.float64)
    ma60_old = ma200_s.shift(59).to_numpy(dtype=np.float64)
    slope_pct = ma200 / ma60_old - 1.0
    trend += np.where(
        trend_available & np.isfinite(slope_pct) & (slope_pct < 0.0),
        _clip01(np.abs(slope_pct) / 0.12) * 5.0,
        0.0,
    )
    below_pct = (ma200 - close) / ma200
    trend += np.where(
        trend_available & np.isfinite(below_pct) & (below_pct > 0.0),
        _clip01(below_pct / 0.30) * 6.0
        - _clip01(np.maximum(below_pct - 0.45, 0.0) / 0.30) * 3.0,
        0.0,
    )
    below = pair_trend & (close < ma200)
    days_below = np.minimum(_trailing_run(below), _rolling_count(pair_trend, 252))
    trend += np.where(
        trend_available,
        _clip01(days_below.astype(np.float64) / 250.0) * 3.0,
        0.0,
    )
    valid_close_for_trend = pd.Series(
        np.where(pair_trend, close, np.nan), index=index
    )
    peak = valid_close_for_trend.rolling(252, min_periods=1).max().to_numpy(dtype=np.float64)
    drawdown_depth = np.abs((close - peak) / peak)
    depth_mask = trend_available & (drawdown_depth >= 0.15) & (drawdown_depth <= 0.50)
    trend += np.where(
        depth_mask,
        _clip01(1.0 - np.abs(drawdown_depth - 0.32) / 0.25) * 3.0,
        0.0,
    )
    recovery_old = close_s.shift(19).to_numpy(dtype=np.float64)
    recovery_slope = close / recovery_old - 1.0
    trend += np.where(
        trend_available & np.isfinite(recovery_slope) & (recovery_slope > 0.0),
        _clip01(recovery_slope / 0.12) * 3.0,
        0.0,
    )
    trend = np.clip(trend, 0.0, 20.0)
    trend[~trend_available] = 0.0

    # Volume score.
    volume_score = np.zeros(n, dtype=np.float64)
    ratio = np.divide(
        vol20,
        vol120,
        out=np.full(n, np.nan, dtype=np.float64),
        where=np.isfinite(vol20) & np.isfinite(vol120) & (vol120 != 0.0),
    )
    ratio_finite = np.isfinite(ratio)
    qualifying = ratio_finite & (ratio >= float(_score.VOLUME_ACCUM_RATIO))
    consecutive = np.minimum(_trailing_run(qualifying), _rolling_count(ratio_finite, 252))
    enough_ratio = _rolling_count(ratio_finite, 252) >= int(_score.VOLUME_ACCUM_MIN_DAYS)
    active_run = enough_ratio & (consecutive >= int(_score.VOLUME_ACCUM_MIN_DAYS))
    volume_score += np.where(
        active_run,
        4.0
        + _clip01(
            (consecutive.astype(np.float64) - float(_score.VOLUME_ACCUM_MIN_DAYS)) / 80.0
        )
        * 6.0,
        0.0,
    )
    volume_score += np.where(
        enough_ratio & ratio_finite,
        _clip01((ratio - float(_score.VOLUME_ACCUM_RATIO)) / 0.8) * 3.0,
        0.0,
    )
    ratio_old = pd.Series(ratio, index=index).shift(19).to_numpy(dtype=np.float64)
    volume_score += np.where(
        (_rolling_count(ratio_finite, 252) >= 20) & np.isfinite(ratio_old),
        _clip01((ratio - ratio_old) / 0.5) * 4.0,
        0.0,
    )
    z_count30 = _rolling_count(z_finite, 30)
    z_positive30 = pd.Series(
        np.where(z_finite & (z > 0.0), 1.0, 0.0), index=index
    ).rolling(30, min_periods=1).sum().to_numpy(dtype=np.float64)
    volume_score += np.where(
        z_count30 >= 10,
        (z_positive30 / np.maximum(z_count30, 1)) * 3.0
        + _clip01(z / 2.0) * 2.0,
        0.0,
    )
    volume_score = np.clip(volume_score, 0.0, 25.0)
    volume_score[~volume_available] = 0.0

    # Accumulation score.
    accumulation = np.zeros(n, dtype=np.float64)
    first_price_low = close_s.shift(30).rolling(30, min_periods=30).min().to_numpy(dtype=np.float64)
    second_price_low = close_s.rolling(30, min_periods=30).min().to_numpy(dtype=np.float64)
    first_obv_low = obv_s.shift(30).rolling(30, min_periods=30).min().to_numpy(dtype=np.float64)
    second_obv_low = obv_s.rolling(30, min_periods=30).min().to_numpy(dtype=np.float64)
    near_low = (
        (second_price_low > 0.0)
        & ((close - second_price_low) / second_price_low <= 0.05)
    )
    price_retest = second_price_low <= first_price_low * 1.02
    obv_divergence = (second_obv_low > first_obv_low) & (obv >= second_obv_low)
    accumulation += np.where(near_low & price_retest & obv_divergence, 8.0, np.where(obv_divergence, 3.0, 0.0))

    ad_scale = ad_s.abs().rolling(int(_score.AD_SLOPE_LOOKBACK), min_periods=int(_score.AD_SLOPE_LOOKBACK)).median().to_numpy(dtype=np.float64)
    ad_scale = np.maximum(ad_scale, 1.0)
    accumulation += np.where(
        np.isfinite(ad_slope) & np.isfinite(ad_scale),
        _clip01(ad_slope / (ad_scale * 0.03)) * 5.0,
        0.0,
    )
    ad_max120 = ad_s.rolling(120, min_periods=1).max().to_numpy(dtype=np.float64)
    accumulation += np.where(
        np.isfinite(ad) & np.isfinite(ad_max120) & (ad >= ad_max120 * 0.95),
        1.0,
        0.0,
    )
    cmf_old = cmf_s.shift(19).to_numpy(dtype=np.float64)
    cmf_ready = _rolling_count(cmf_finite, 252) >= 20
    accumulation += np.where(cmf_ready, _clip01(cmf / 0.15) * 4.0, 0.0)
    accumulation += np.where(cmf_ready & np.isfinite(cmf_old), _clip01((cmf - cmf_old) / 0.10) * 2.0, 0.0)
    accumulation += np.where(
        np.isfinite(mfi),
        np.where((mfi >= 40.0) & (mfi <= 70.0), 3.0, np.where((mfi >= 30.0) & (mfi <= 80.0), 1.5, 0.0)),
        0.0,
    )
    accumulation = np.clip(accumulation, 0.0, 25.0)
    accumulation[~accumulation_available] = 0.0

    # Shared volatility-contraction score. Coverage algebra reduces exactly to
    # five points per available normalized component.
    atr_ratio = np.divide(atr14, atr50, out=np.full(n, np.nan), where=np.isfinite(atr14) & np.isfinite(atr50) & (atr50 > 0.0))
    hv_ratio = np.divide(hv20, hv60, out=np.full(n, np.nan), where=np.isfinite(hv20) & np.isfinite(hv60) & (hv60 > 0.0))
    lookback = max(20, int(_vol.BB_WIDTH_COMPRESSION_LOOKBACK))
    exclude = min(max(1, int(_vol.BB_CONTRACTION_EXCLUDE_RECENT)), max(1, lookback // 3))
    baseline = bb_s.shift(exclude).rolling(lookback - exclude, min_periods=lookback - exclude).median().to_numpy(dtype=np.float64)
    bb_ratio = np.divide(bb, baseline, out=np.full(n, np.nan), where=np.isfinite(bb) & np.isfinite(baseline) & (baseline > 0.0))
    bb_percentile = _rolling_endpoint_percentile(bb, lookback)
    atr_component = np.where(np.isfinite(atr_ratio), _clip01((1.0 - atr_ratio) / 0.35), 0.0)
    hv_component = np.where(np.isfinite(hv_ratio), _clip01((1.0 - hv_ratio) / 0.50), 0.0)
    bb_component = np.where(
        np.isfinite(bb_ratio),
        (
            _clip01((1.0 - bb_ratio) / 0.35)
            + np.where(np.isfinite(bb_percentile), _clip01((0.5 - bb_percentile) / 0.5), 0.0)
        )
        / 2.0,
        0.0,
    )
    volatility = np.clip((atr_component + bb_component + hv_component) * 5.0, 0.0, 15.0)
    volatility[~volatility_available] = 0.0

    # Structure score (FAST historical frames deliberately exclude VP columns).
    structure = np.zeros(n, dtype=np.float64)
    structure += np.where(
        np.isfinite(dist_low) & (dist_low >= 0.0) & (dist_low <= 20.0),
        np.where(dist_low < 8.0, dist_low / 8.0 * 5.0, np.where(dist_low <= 12.0, 5.0, (20.0 - dist_low) / 8.0 * 5.0)),
        0.0,
    )
    high45 = high_s.rolling(int(_score.CONSOLIDATION_DAYS), min_periods=int(_score.CONSOLIDATION_DAYS)).max().to_numpy(dtype=np.float64)
    low45 = low_s.rolling(int(_score.CONSOLIDATION_DAYS), min_periods=int(_score.CONSOLIDATION_DAYS)).min().to_numpy(dtype=np.float64)
    avg45 = close_s.rolling(int(_score.CONSOLIDATION_DAYS), min_periods=int(_score.CONSOLIDATION_DAYS)).mean().to_numpy(dtype=np.float64)
    range_pct = (high45 - low45) / avg45 * 100.0
    tight = _clip01(1.0 - range_pct / float(_score.CONSOLIDATION_MAX_RANGE_PCT))
    structure += np.where(
        np.isfinite(avg45) & (avg45 > 0.0) & np.isfinite(range_pct) & (range_pct <= float(_score.CONSOLIDATION_MAX_RANGE_PCT)),
        (0.2 + tight * 0.8) * 5.0,
        0.0,
    )
    structure += np.where(np.isfinite(reg_slope), _clip01(1.0 - np.abs(reg_slope) / 0.05) * 2.0, 0.0)
    structure += np.where(np.isfinite(reg_slope) & np.isfinite(reg_r2), _clip01(reg_r2) * 1.0, 0.0)
    structure = np.minimum(structure, 15.0)
    structure[~structure_available] = 0.0

    # Component maxima already sum to 100.  Missing dimensions remain zero;
    # they must not be renormalised into stronger evidence.
    component_sum = trend + volume_score + accumulation + volatility + structure
    total = component_sum

    # Value-trap risk.
    ret20 = (close / close_s.shift(20).to_numpy(dtype=np.float64) - 1.0) * 100.0
    ret60 = (close / close_s.shift(60).to_numpy(dtype=np.float64) - 1.0) * 100.0
    ret120 = (close / close_s.shift(120).to_numpy(dtype=np.float64) - 1.0) * 100.0
    trap = np.zeros(n, dtype=np.float64)
    trap += np.where(np.isfinite(ret120) & (ret120 < 0.0), _clip01(np.abs(ret120) / 45.0) * 15.0, 0.0)
    old_ma50 = ma50_s.shift(24).to_numpy(dtype=np.float64)
    ma_decline = np.isfinite(old_ma50) & (old_ma50 > 0.0) & (ma50 < old_ma50)
    trap += np.where(ma_decline, _clip01((old_ma50 - ma50) / old_ma50 / 0.12) * 12.0, 0.0)
    trap += np.where(np.isfinite(ma50) & (close < ma50) & np.isfinite(ret20) & (ret20 < 0.0), 8.0, 0.0)
    recent_low40 = close_s.rolling(40, min_periods=40).min().to_numpy(dtype=np.float64)
    prior_low40 = close_s.shift(40).rolling(40, min_periods=40).min().to_numpy(dtype=np.float64)
    lower_low = np.isfinite(prior_low40) & (prior_low40 > 0.0) & (recent_low40 < prior_low40 * 0.98)
    trap += np.where(lower_low, _clip01((prior_low40 - recent_low40) / prior_low40 / 0.12) * 15.0, 0.0)
    trap += np.where(np.isfinite(ret20) & np.isfinite(ret60) & (ret20 < 0.0) & (ret60 < 0.0), 10.0, 0.0)
    obv_old19 = obv_s.shift(19).to_numpy(dtype=np.float64)
    flow_available = np.isfinite(cmf).astype(np.int8) + np.isfinite(ad_slope).astype(np.int8) + (np.isfinite(obv) & np.isfinite(obv_old19)).astype(np.int8)
    flow_positive = (np.isfinite(cmf) & (cmf > 0.0)).astype(np.int8) + (np.isfinite(ad_slope) & (ad_slope > 0.0)).astype(np.int8) + (np.isfinite(obv) & np.isfinite(obv_old19) & (obv > obv_old19)).astype(np.int8)
    trap += np.where((flow_available > 0) & (flow_positive == 0), 25.0, np.where((flow_available > 0) & (flow_positive == 1), 10.0, np.where(flow_positive >= 2, -8.0, 0.0)))
    volume_mean20 = volume_s.rolling(20, min_periods=20).mean().to_numpy(dtype=np.float64)
    volume_prior40 = volume_s.shift(20).rolling(40, min_periods=40).mean().to_numpy(dtype=np.float64)
    dry = np.isfinite(volume_prior40) & (volume_prior40 > 0.0) & (volume_mean20 < volume_prior40 * 0.75)
    trap += np.where(dry & np.isfinite(ret20) & (ret20 < 0.0), 10.0, np.where(dry & np.isfinite(ret20) & (ret20 >= 0.0), -3.0, 0.0))
    recovery = np.isfinite(ma20) & np.isfinite(ma50) & (close >= ma20) & (ma20 >= ma50) & np.isfinite(ret20) & (ret20 > 0.0)
    trap += np.where(recovery, -15.0, np.where(np.isfinite(ret20) & (ret20 > 5.0) & (flow_positive >= 2), -8.0, 0.0))
    if is_etf:
        trap *= 0.80
    trap = np.clip(trap, 0.0, 100.0)

    # Breakout score.
    breakout = np.zeros(n, dtype=np.float64)
    trend15 = np.isfinite(ma20) & np.isfinite(ma50) & (close > ma20) & (ma20 > ma50)
    breakout += np.where(trend15, 15.0, np.where(np.isfinite(ma20) & (close > ma20), 8.0, 0.0))
    breakout += np.where(np.isfinite(ma200) & (close > ma200), 10.0, 0.0)
    resistance = high_s.shift(1).rolling(20, min_periods=20).max().to_numpy(dtype=np.float64)
    prior_volume20 = volume_s.shift(1).rolling(20, min_periods=20).mean().to_numpy(dtype=np.float64)
    price_break_raw = np.isfinite(resistance) & (close > resistance)
    breakout += np.where(price_break_raw, 25.0, 0.0)
    breakout += np.where(price_break_raw & (prior_volume20 > 0.0) & (volume >= prior_volume20 * 1.5), 15.0, 0.0)
    up = close_s.diff().gt(0.0)
    up_volume = volume_s.where(up).rolling(10, min_periods=1).mean().to_numpy(dtype=np.float64)
    down_volume = volume_s.where(~up).rolling(10, min_periods=1).mean().to_numpy(dtype=np.float64)
    breakout += np.where(np.isfinite(up_volume) & np.isfinite(down_volume) & (down_volume > 0.0) & (up_volume > down_volume * 1.15), 15.0, 0.0)
    recent_max5 = close_s.rolling(5, min_periods=5).max().to_numpy(dtype=np.float64)
    recent_min5 = close_s.rolling(5, min_periods=5).min().to_numpy(dtype=np.float64)
    prior_max15 = close_s.shift(5).rolling(15, min_periods=15).max().to_numpy(dtype=np.float64)
    prior_min15 = close_s.shift(5).rolling(15, min_periods=15).min().to_numpy(dtype=np.float64)
    recent_range = (recent_max5 - recent_min5) / np.maximum(close, 1e-9)
    prior_range = (prior_max15 - prior_min15) / np.maximum(close, 1e-9)
    breakout += np.where(np.isfinite(prior_range) & (prior_range > 0.0) & (recent_range < prior_range * 0.75), 10.0, 0.0)
    ma20_mean10 = ma20_s.rolling(10, min_periods=10).mean().to_numpy(dtype=np.float64)
    close_old9 = close_s.shift(9).to_numpy(dtype=np.float64)
    breakout += np.where(np.isfinite(ma20) & np.isfinite(ma20_mean10) & (close > close_old9) & (ma20 > ma20_mean10), 10.0, 0.0)
    breakout = np.clip(breakout, 0.0, 100.0)

    # Entry geometry/signal.
    decimals = int(_core.tradable_price_decimals(is_etf))
    rounded_resistance = np.round(resistance, decimals)
    support = low_s.rolling(20, min_periods=20).min().to_numpy(dtype=np.float64)
    volume_ratio = np.divide(volume, prior_volume20, out=np.full(n, np.nan), where=np.isfinite(prior_volume20) & (prior_volume20 > 0.0))
    obv_old5 = obv_s.shift(5).to_numpy(dtype=np.float64)
    flow_confirmed = (np.isfinite(cmf) & (cmf > 0.0)) | (np.isfinite(ad_slope) & (ad_slope > 0.0)) | (np.isfinite(obv) & np.isfinite(obv_old5) & (obv > obv_old5))
    volume_confirmed = np.isfinite(volume_ratio) & (volume_ratio >= float(_score.BREAKOUT_CONFIRM_MIN_VOLUME_RATIO))
    price_breakout = (breakout >= 75.0) & np.isfinite(rounded_resistance) & (close > rounded_resistance)
    effective_atr = np.where(np.isfinite(atr14) & (atr14 > 0.0), atr14, close * 0.03)
    support_anchor = support + effective_atr * 0.55
    support_anchor = np.where(np.isfinite(ma20) & (ma20 <= close), np.maximum(support_anchor, ma20), support_anchor)
    support_anchor = np.minimum(support_anchor, close)
    low_zone = np.round(np.maximum(support, support_anchor - effective_atr * 0.35), decimals)
    high_zone = np.round(np.minimum(rounded_resistance, support_anchor + effective_atr * 0.35), decimals)
    high_zone = np.where(high_zone < low_zone, low_zone, high_zone)
    entry_score = np.zeros(n, dtype=np.float64)
    entry_score += np.where(np.isfinite(ma20) & (close >= ma20), 20.0, 0.0)
    entry_score += np.where(np.isfinite(ma20) & np.isfinite(ma50) & (ma20 >= ma50), 20.0, 0.0)
    entry_score += np.where(np.isfinite(support) & (close >= support) & (close <= support + effective_atr * 1.5), 20.0, 0.0)
    entry_score += np.where(breakout >= 65.0, 25.0, np.where(breakout >= 45.0, 10.0, 0.0))
    close_old5 = close_s.shift(5).to_numpy(dtype=np.float64)
    entry_score += np.where(np.isfinite(close_old5) & (close >= close_old5), 15.0, 0.0)
    entry_score = np.clip(entry_score, 0.0, 100.0)
    inside = (close >= low_zone) & (close <= high_zone)
    signal = np.full(n, "AVOID", dtype=object)
    eligible = trap < 70.0
    signal[eligible & np.isfinite(rsi) & (rsi >= 78.0)] = "HOLD_WAIT"
    remaining = eligible & ~(np.isfinite(rsi) & (rsi >= 78.0))
    signal[remaining & price_breakout & volume_confirmed & flow_confirmed] = "BREAKOUT_CONFIRM"
    remaining &= ~(price_breakout & volume_confirmed & flow_confirmed)
    signal[remaining & price_breakout] = "PRICE_BREAKOUT"
    remaining &= ~price_breakout
    signal[remaining & (entry_score >= 70.0) & inside] = "BUY_NOW"
    selected = remaining & (entry_score >= 70.0) & inside
    remaining &= ~selected
    signal[remaining & (entry_score >= 50.0) & (close > high_zone)] = "WAIT_PULLBACK"
    selected = remaining & (entry_score >= 50.0) & (close > high_zone)
    remaining &= ~selected
    signal[remaining & (entry_score >= 50.0) & inside] = "HOLD_WAIT"
    selected = remaining & (entry_score >= 50.0) & inside
    remaining &= ~selected
    signal[remaining & (entry_score >= 35.0)] = "HOLD_WAIT"

    stop_anchor = np.where(price_breakout, rounded_resistance, support)
    stop = np.round(np.maximum(stop_anchor - effective_atr, 0.0), decimals)
    projected_target = np.round(np.where(price_breakout, close + effective_atr * 2.5, np.maximum(rounded_resistance, close)), decimals)

    # Execution quality score.
    execution_raw = np.zeros(n, dtype=np.float64)
    execution_support = np.where(price_breakout, resistance, support)
    distance_support_atr = np.maximum(0.0, close - execution_support) / np.maximum(effective_atr, 1e-9)
    execution_raw += (1.0 - _clip01(distance_support_atr / 3.0)) * 35.0
    ma_distance = np.abs(close - ma20) / np.maximum(effective_atr, 1e-9)
    execution_raw += np.where(np.isfinite(ma20), (1.0 - _clip01(ma_distance / 2.5)) * 20.0, 0.0)
    risk_distance = (close - stop) / close
    execution_raw += np.where(np.isfinite(risk_distance) & (risk_distance >= 0.02) & (risk_distance <= 0.08), 20.0, np.where(np.isfinite(risk_distance) & (risk_distance >= 0.01) & (risk_distance <= 0.12), 10.0, 0.0))
    reward = np.maximum(0.0, projected_target - close)
    risk_amount = np.maximum(close - stop, effective_atr * 0.25)
    reward_risk = np.divide(reward, risk_amount, out=np.zeros(n), where=risk_amount > 0.0)
    execution_raw += _clip01(reward_risk / 2.5) * 15.0
    execution_raw += np.where(np.isfinite(rsi) & (rsi >= 40.0) & (rsi <= 68.0), 10.0, np.where(np.isfinite(rsi) & (rsi >= 30.0) & (rsi <= 75.0), 5.0, 0.0))
    execution_raw = np.clip(execution_raw, 0.0, 100.0)

    # v51 orthogonal TriggerScore.
    trigger_raw = np.zeros(n, dtype=np.float64)
    clearance = (close / resistance - 1.0) * 100.0
    trigger_raw += np.where(np.isfinite(resistance) & (resistance > 0.0) & (clearance > 0.0), 35.0 + _clip01(clearance / 3.0) * 15.0, np.where(np.isfinite(resistance) & (resistance > 0.0) & (clearance >= -1.5), _clip01((clearance + 1.5) / 1.5) * 12.0, 0.0))
    trigger_volume_ratio = np.divide(volume, prior_volume20, out=np.full(n, np.nan), where=prior_volume20 > 0.0)
    trigger_raw += np.where(np.isfinite(trigger_volume_ratio), _clip01((trigger_volume_ratio - 1.0) / 1.25) * 25.0, 0.0)
    cmf_old5 = cmf_s.shift(5).to_numpy(dtype=np.float64)
    trigger_raw += np.where(np.isfinite(cmf) & np.isfinite(cmf_old5), _clip01((cmf - cmf_old5) / 0.12) * 10.0, 0.0)
    prior_ad = ad_slope_s.shift(1).rolling(5, min_periods=5).median().to_numpy(dtype=np.float64)
    trigger_raw += np.where(np.isfinite(ad_slope) & np.isfinite(prior_ad) & (ad_slope > 0.0) & (prior_ad <= 0.0), 8.0, np.where(np.isfinite(ad_slope) & np.isfinite(prior_ad) & (ad_slope > 0.0) & (ad_slope > prior_ad), 4.0, 0.0))
    obv5 = obv_s.shift(5).to_numpy(dtype=np.float64)
    obv10 = obv_s.shift(10).to_numpy(dtype=np.float64)
    recent_change = obv - obv5
    prior_change = obv5 - obv10
    trigger_raw += np.where(np.isfinite(recent_change) & np.isfinite(prior_change) & (recent_change > 0.0) & (recent_change > np.maximum(prior_change, 0.0)), 7.0, 0.0)
    trigger_raw = np.clip(trigger_raw, 0.0, 100.0)

    setup_coverage = 0.55 + 0.45 * indicator_coverage
    trigger_coverage = 0.75 + 0.25 * indicator_coverage
    execution_coverage = 0.70 + 0.30 * indicator_coverage
    base_score = np.clip(total * setup_coverage, 0.0, 100.0)
    trigger_score = np.clip(trigger_raw * trigger_coverage, 0.0, 100.0)
    execution_score = np.clip(execution_raw * execution_coverage, 0.0, 100.0)
    setup_weight, trigger_weight, execution_weight = _core._model_component_weights()
    final_score = np.clip(
        base_score * float(setup_weight)
        + trigger_score * float(trigger_weight)
        + execution_score * float(execution_weight),
        0.0,
        100.0,
    )
    final_score = np.minimum(final_score, 40.0 + 60.0 * indicator_coverage)
    final_score[coverage_count >= 4] = final_score[coverage_count >= 4]
    final_score[coverage_count <= 1] = 0.0

    return FastScoreMatrix(
        base_score=base_score,
        trigger_score=trigger_score,
        execution_score=execution_score,
        final_score=final_score,
        breakout_score=breakout,
        value_trap_risk=trap,
        entry_signal=signal,
    )


def _legacy_endpoint(
    enriched: pd.DataFrame,
    index: int,
    *,
    is_etf: bool,
    profile: Any,
) -> tuple[float, str, tuple[float, float, float]] | None:
    scoring_frame = _core._backtest_scoring_window(
        enriched,
        index,
        score_window=profile.score_window,
        include_volume_profile=False,
    )
    score = _core.score_ticker(scoring_frame, is_etf=is_etf)
    entry = _core.entry_point(
        scoring_frame,
        breakout=_core._finite_float(getattr(score, "breakout_score", np.nan), np.nan),
        volume_score=_core._finite_float(getattr(score, "volume", np.nan), np.nan),
        value_trap_risk_value=_core._finite_float(getattr(score, "value_trap_risk", np.nan), np.nan),
        price_decimals=_core.tradable_price_decimals(is_etf),
    )
    signal = str(entry.get("signal", "AVOID")).upper()
    if signal not in _core._BACKTEST_ACTIONABLE_SIGNALS:
        return None
    final = _core._finite_float(getattr(score, "final_score", np.nan), np.nan)
    if not np.isfinite(final):
        final = _core._finite_float(getattr(score, "total", np.nan), 0.0)
    components = (
        _core._finite_float(getattr(score, "base_score", np.nan), 0.0),
        _core._finite_float(getattr(score, "trigger_score", np.nan), 0.0),
        _core._finite_float(
            getattr(score, "execution_score", np.nan),
            _core._finite_float(getattr(score, "entry_score", np.nan), 0.0),
        ),
    )
    return float(final), signal, components


def _signal_evaluations(
    enriched: pd.DataFrame,
    cooldown: int = _core.BACKTEST_SIGNAL_COOLDOWN_DAYS,
    is_etf: bool = False,
    *,
    profile: Any | None = None,
    start_index: int | None = None,
    component_sink: dict[int, tuple[float, float, float]] | None = None,
) -> list[tuple[int, float, str]]:
    if profile is None or not bool(getattr(profile, "fast_prefilter", False)):
        return _LEGACY_SIGNAL_EVALUATIONS(
            enriched,
            cooldown=cooldown,
            is_etf=is_etf,
            profile=profile,
            start_index=start_index,
            component_sink=component_sink,
        )
    matrix = _fast_score_matrix(enriched, is_etf=is_etf)
    if matrix is None:
        return _LEGACY_SIGNAL_EVALUATIONS(
            enriched,
            cooldown=cooldown,
            is_etf=is_etf,
            profile=profile,
            start_index=start_index,
            component_sink=component_sink,
        )

    cooldown = max(1, int(profile.cooldown))
    candidates, breakout_flags = _core._candidate_endpoint_matrix(
        enriched,
        fast_prefilter=bool(profile.fast_prefilter),
    )
    minimum_index = max(251, int(start_index) if start_index is not None else 251)
    last_signal = minimum_index - cooldown
    last_evaluated = minimum_index - max(1, int(profile.candidate_gap))
    evaluations: list[tuple[int, float, str]] = []
    for raw_index in candidates:
        index = int(raw_index)
        if index < minimum_index:
            continue
        if index >= len(enriched) - _core.BACKTEST_OUTCOME_HORIZON_DAYS:
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

        if index < _FAST_VECTOR_START:
            legacy = _legacy_endpoint(
                enriched, index, is_etf=is_etf, profile=profile
            )
            if legacy is None:
                continue
            final, signal, components = legacy
        else:
            signal = str(matrix.entry_signal[index]).upper()
            if signal not in _core._BACKTEST_ACTIONABLE_SIGNALS:
                continue
            final = float(matrix.final_score[index])
            components = (
                float(matrix.base_score[index]),
                float(matrix.trigger_score[index]),
                float(matrix.execution_score[index]),
            )
        evaluations.append((index, final, signal))
        if component_sink is not None:
            component_sink[index] = components
        last_signal = index
    return evaluations


def install() -> None:
    global _INSTALLED, _LEGACY_SIGNAL_EVALUATIONS
    # Capture whatever FAST implementation is active at installation time. The
    # v80 bootstrap calls this after v78 fastpath installation.
    if not _INSTALLED:
        _LEGACY_SIGNAL_EVALUATIONS = _core._signal_evaluations
    _core._signal_evaluations = _signal_evaluations
    _INSTALLED = True


# Deliberately not auto-installed. It must wrap the v78 FAST path, not be
# overwritten by it during analytics import ordering.
