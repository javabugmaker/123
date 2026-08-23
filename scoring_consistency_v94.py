"""v94/v97 canonical FAST/EXACT scoring consistency.

v94 removed the old FAST TriggerScore resistance cliff. v95 migrated Volume and
Accumulation onto their nominal 25-point scales, moved HVN to diagnostics and
expanded the historical setup context to 504 bars.

v97 fixes an import-order hole: re-entrant v79 acceleration can rebind scalar
Volume/Accumulation kernels after the scale facade first loads. This module is
installed after v80/v79 bootstrapping and therefore re-asserts the canonical
scalar scale before wrapping the vectorised FAST matrix. FAST and EXACT then
share one formula boundary instead of silently running different score scales.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import analytics_core as _analytics
import backtest_fastscore_v80 as _fast
import score_core as _score
import score_scale_migration_v95 as _scale
from execution_integrity_v87 import smooth_breakout_price_component

SCORING_CONSISTENCY_VERSION = (
    "2026-08-23-v97-fast-exact-504-full-scale-reentrant-equivalence-v3"
)

_INSTALLED = False
_ORIGINAL_FAST_SCORE_MATRIX: Any = None


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )


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


def _indicator_coverage(frame: pd.DataFrame) -> np.ndarray:
    """Vectorised copy of scalar dimension-availability semantics."""
    n = len(frame)
    positions = np.arange(n)
    close = _numeric(frame, "Close").to_numpy(dtype=np.float64)
    high = _numeric(frame, "High").to_numpy(dtype=np.float64)
    low = _numeric(frame, "Low").to_numpy(dtype=np.float64)
    ma200 = _numeric(frame, "MA200").to_numpy(dtype=np.float64)
    vol20 = _numeric(frame, "VolMA20").to_numpy(dtype=np.float64)
    vol120 = _numeric(frame, "VolMA120").to_numpy(dtype=np.float64)
    z = _numeric(frame, "VolZScore").to_numpy(dtype=np.float64)
    obv = _numeric(frame, "OBV").to_numpy(dtype=np.float64)
    ad = _numeric(frame, "AD").to_numpy(dtype=np.float64)
    ad_slope = _numeric(frame, "AD_Slope").to_numpy(dtype=np.float64)
    cmf = _numeric(frame, "CMF").to_numpy(dtype=np.float64)
    mfi = _numeric(frame, "MFI").to_numpy(dtype=np.float64)
    atr14 = _numeric(frame, "ATR14").to_numpy(dtype=np.float64)
    atr50 = _numeric(frame, "ATR50").to_numpy(dtype=np.float64)
    bb = _numeric(frame, "BB_Width").to_numpy(dtype=np.float64)
    hv20 = _numeric(frame, "HV20").to_numpy(dtype=np.float64)
    hv60 = _numeric(frame, "HV60").to_numpy(dtype=np.float64)

    trend_pair = np.isfinite(close) & np.isfinite(ma200)
    trend = (
        (positions >= 251)
        & trend_pair
        & (_rolling_count(trend_pair, 504) >= 60)
    )

    volume_pair = np.isfinite(vol20) & np.isfinite(vol120)
    z_finite = np.isfinite(z)
    volume = (positions >= 119) & (
        (
            volume_pair
            & (_rolling_count(volume_pair, 504) >= int(_score.VOLUME_ACCUM_MIN_DAYS))
        )
        | (z_finite & (_rolling_count(z_finite, 504) >= 10))
    )

    obv_finite = np.isfinite(obv)
    ad_pair = np.isfinite(ad) & np.isfinite(ad_slope)
    cmf_finite = np.isfinite(cmf)
    mfi_finite = np.isfinite(mfi)
    accumulation = (positions >= 59) & (
        (obv_finite & (_rolling_count(obv_finite, 504) >= 40))
        | (ad_pair & (_rolling_count(ad_pair, 504) >= int(_score.AD_SLOPE_LOOKBACK)))
        | (cmf_finite & (_rolling_count(cmf_finite, 504) >= 20))
        | mfi_finite
    )

    atr_pair = np.isfinite(atr14) & np.isfinite(atr50)
    hv_pair = np.isfinite(hv20) & np.isfinite(hv60)
    bb_finite = np.isfinite(bb)
    volatility = (
        positions >= int(_score.BB_WIDTH_COMPRESSION_LOOKBACK) - 1
    ) & (
        atr_pair
        | hv_pair
        | (
            bb_finite
            & (
                _rolling_count(bb_finite, 504)
                >= int(_score.BB_WIDTH_COMPRESSION_LOOKBACK)
            )
        )
    )

    structure = (
        (positions >= 251)
        & np.isfinite(close)
        & np.isfinite(high)
        & np.isfinite(low)
    )
    count = (
        trend.astype(np.int8)
        + volume.astype(np.int8)
        + accumulation.astype(np.int8)
        + volatility.astype(np.int8)
        + structure.astype(np.int8)
    )
    return count.astype(np.float64) / 5.0


def canonical_trigger_score_matrix(
    frame: pd.DataFrame,
) -> tuple[np.ndarray, np.ndarray]:
    """Return canonical raw and coverage-adjusted TriggerScore for every row."""
    n = len(frame)
    close_s = _numeric(frame, "Close")
    high_s = _numeric(frame, "High")
    volume_s = _numeric(frame, "Volume")
    cmf_s = _numeric(frame, "CMF")
    ad_slope_s = _numeric(frame, "AD_Slope")
    obv_s = _numeric(frame, "OBV")

    close = close_s.to_numpy(dtype=np.float64)
    volume = volume_s.to_numpy(dtype=np.float64)
    cmf = cmf_s.to_numpy(dtype=np.float64)
    ad_slope = ad_slope_s.to_numpy(dtype=np.float64)
    obv = obv_s.to_numpy(dtype=np.float64)

    resistance = high_s.shift(1).rolling(20, min_periods=20).max().to_numpy(
        dtype=np.float64
    )
    clearance = np.divide(
        close,
        resistance,
        out=np.full(n, np.nan, dtype=np.float64),
        where=np.isfinite(close) & np.isfinite(resistance) & (resistance > 0.0),
    )
    clearance = (clearance - 1.0) * 100.0
    price_points, _confirmation = smooth_breakout_price_component(clearance)
    raw = np.asarray(price_points, dtype=np.float64).copy()

    prior_volume20 = volume_s.shift(1).rolling(20, min_periods=20).mean().to_numpy(
        dtype=np.float64
    )
    volume_ratio = np.divide(
        volume,
        prior_volume20,
        out=np.full(n, np.nan, dtype=np.float64),
        where=np.isfinite(prior_volume20) & (prior_volume20 > 0.0),
    )
    raw += np.where(
        np.isfinite(volume_ratio),
        np.clip((volume_ratio - 1.0) / 1.25, 0.0, 1.0) * 25.0,
        0.0,
    )

    cmf_old5 = cmf_s.shift(5).to_numpy(dtype=np.float64)
    raw += np.where(
        np.isfinite(cmf) & np.isfinite(cmf_old5),
        np.clip((cmf - cmf_old5) / 0.12, 0.0, 1.0) * 10.0,
        0.0,
    )

    prior_ad = ad_slope_s.shift(1).rolling(5, min_periods=5).median().to_numpy(
        dtype=np.float64
    )
    raw += np.where(
        np.isfinite(ad_slope)
        & np.isfinite(prior_ad)
        & (ad_slope > 0.0)
        & (prior_ad <= 0.0),
        8.0,
        np.where(
            np.isfinite(ad_slope)
            & np.isfinite(prior_ad)
            & (ad_slope > 0.0)
            & (ad_slope > prior_ad),
            4.0,
            0.0,
        ),
    )

    obv5 = obv_s.shift(5).to_numpy(dtype=np.float64)
    obv10 = obv_s.shift(10).to_numpy(dtype=np.float64)
    recent_change = obv - obv5
    prior_change = obv5 - obv10
    raw += np.where(
        np.isfinite(recent_change)
        & np.isfinite(prior_change)
        & (recent_change > 0.0)
        & (recent_change > np.maximum(prior_change, 0.0)),
        7.0,
        0.0,
    )
    raw = np.clip(raw, 0.0, 100.0)
    coverage = _indicator_coverage(frame)
    adjusted = np.clip(raw * (0.75 + 0.25 * coverage), 0.0, 100.0)
    return raw, adjusted


def _legacy_volume_score_matrix(frame: pd.DataFrame) -> np.ndarray:
    """Vectorised unscaled VolumeScore used only for deterministic scale delta."""
    n = len(frame)
    vol20 = _numeric(frame, "VolMA20").to_numpy(dtype=np.float64)
    vol120 = _numeric(frame, "VolMA120").to_numpy(dtype=np.float64)
    z = _numeric(frame, "VolZScore").to_numpy(dtype=np.float64)

    ratio = np.divide(
        vol20,
        vol120,
        out=np.full(n, np.nan, dtype=np.float64),
        where=np.isfinite(vol20) & np.isfinite(vol120) & (vol120 != 0.0),
    )
    ratio_finite = np.isfinite(ratio)
    qualifying = ratio_finite & (ratio >= float(_score.VOLUME_ACCUM_RATIO))
    consecutive = _trailing_run(qualifying)
    enough_ratio = _rolling_count(ratio_finite, 504) >= int(
        _score.VOLUME_ACCUM_MIN_DAYS
    )
    active_run = enough_ratio & (
        consecutive >= int(_score.VOLUME_ACCUM_MIN_DAYS)
    )
    result = np.where(
        active_run,
        4.0
        + np.clip(
            (consecutive.astype(np.float64) - float(_score.VOLUME_ACCUM_MIN_DAYS))
            / 80.0,
            0.0,
            1.0,
        )
        * 6.0,
        0.0,
    )
    result += np.where(
        enough_ratio & ratio_finite,
        np.clip((ratio - float(_score.VOLUME_ACCUM_RATIO)) / 0.8, 0.0, 1.0)
        * 3.0,
        0.0,
    )
    ratio_old = pd.Series(ratio, index=frame.index).shift(19).to_numpy(
        dtype=np.float64
    )
    result += np.where(
        (_rolling_count(ratio_finite, 504) >= 20) & np.isfinite(ratio_old),
        np.clip((ratio - ratio_old) / 0.5, 0.0, 1.0) * 4.0,
        0.0,
    )

    z_finite = np.isfinite(z)
    z_count30 = _rolling_count(z_finite, 30)
    z_positive30 = (
        pd.Series(
            np.where(z_finite & (z > 0.0), 1.0, 0.0), index=frame.index
        )
        .rolling(30, min_periods=1)
        .sum()
        .to_numpy(dtype=np.float64)
    )
    result += np.where(
        z_count30 >= 10,
        (z_positive30 / np.maximum(z_count30, 1)) * 3.0
        + np.clip(z / 2.0, 0.0, 1.0) * 2.0,
        0.0,
    )
    available = (np.arange(n) >= 119) & (
        (enough_ratio & ratio_finite) | (z_finite & (z_count30 >= 10))
    )
    result = np.clip(result, 0.0, _scale.VOLUME_RAW_MAX)
    result[~available] = 0.0
    return result


def _legacy_accumulation_score_matrix(frame: pd.DataFrame) -> np.ndarray:
    """Vectorised unscaled AccumulationScore for deterministic scale delta."""
    n = len(frame)
    close_s = _numeric(frame, "Close")
    obv_s = _numeric(frame, "OBV")
    ad_s = _numeric(frame, "AD")
    ad_slope_s = _numeric(frame, "AD_Slope")
    cmf_s = _numeric(frame, "CMF")
    mfi_s = _numeric(frame, "MFI")

    close = close_s.to_numpy(dtype=np.float64)
    obv = obv_s.to_numpy(dtype=np.float64)
    ad = ad_s.to_numpy(dtype=np.float64)
    ad_slope = ad_slope_s.to_numpy(dtype=np.float64)
    cmf = cmf_s.to_numpy(dtype=np.float64)
    mfi = mfi_s.to_numpy(dtype=np.float64)
    result = np.zeros(n, dtype=np.float64)

    first_price_low = (
        close_s.shift(30).rolling(30, min_periods=30).min().to_numpy(dtype=np.float64)
    )
    second_price_low = close_s.rolling(30, min_periods=30).min().to_numpy(
        dtype=np.float64
    )
    first_obv_low = (
        obv_s.shift(30).rolling(30, min_periods=30).min().to_numpy(dtype=np.float64)
    )
    second_obv_low = obv_s.rolling(30, min_periods=30).min().to_numpy(
        dtype=np.float64
    )
    near_low = (second_price_low > 0.0) & (
        (close - second_price_low) / second_price_low <= 0.05
    )
    price_retest = second_price_low <= first_price_low * 1.02
    obv_divergence = (second_obv_low > first_obv_low) & (obv >= second_obv_low)
    result += np.where(
        near_low & price_retest & obv_divergence,
        8.0,
        np.where(obv_divergence, 3.0, 0.0),
    )

    lookback = int(_score.AD_SLOPE_LOOKBACK)
    ad_scale = (
        ad_s.abs()
        .rolling(lookback, min_periods=lookback)
        .median()
        .to_numpy(dtype=np.float64)
    )
    ad_scale = np.maximum(ad_scale, 1.0)
    result += np.where(
        np.isfinite(ad_slope) & np.isfinite(ad_scale),
        np.clip(ad_slope / (ad_scale * 0.03), 0.0, 1.0) * 5.0,
        0.0,
    )
    ad_max120 = ad_s.rolling(120, min_periods=1).max().to_numpy(dtype=np.float64)
    result += np.where(
        np.isfinite(ad) & np.isfinite(ad_max120) & (ad >= ad_max120 * 0.95),
        1.0,
        0.0,
    )

    cmf_finite = np.isfinite(cmf)
    cmf_ready = _rolling_count(cmf_finite, 504) >= 20
    cmf_old = cmf_s.shift(19).to_numpy(dtype=np.float64)
    result += np.where(
        cmf_ready,
        np.clip(cmf / 0.15, 0.0, 1.0) * 4.0,
        0.0,
    )
    result += np.where(
        cmf_ready & np.isfinite(cmf_old),
        np.clip((cmf - cmf_old) / 0.10, 0.0, 1.0) * 2.0,
        0.0,
    )
    result += np.where(
        np.isfinite(mfi),
        np.where(
            (mfi >= 40.0) & (mfi <= 70.0),
            3.0,
            np.where((mfi >= 30.0) & (mfi <= 80.0), 1.5, 0.0),
        ),
        0.0,
    )

    obv_finite = np.isfinite(obv)
    ad_pair = np.isfinite(ad) & np.isfinite(ad_slope)
    mfi_finite = np.isfinite(mfi)
    available = (np.arange(n) >= 59) & (
        (obv_finite & (_rolling_count(obv_finite, 504) >= 40))
        | (ad_pair & (_rolling_count(ad_pair, 504) >= lookback))
        | (cmf_finite & cmf_ready)
        | mfi_finite
    )
    result = np.clip(result, 0.0, _scale.ACCUMULATION_RAW_MAX)
    result[~available] = 0.0
    return result


def _trend_504_delta(frame: pd.DataFrame) -> np.ndarray:
    """Return setup-point change from v80 252-row peak to 504-row context."""
    close = _numeric(frame, "Close")
    ma200 = _numeric(frame, "MA200")
    pair = close.notna().to_numpy() & ma200.notna().to_numpy()
    values = np.where(pair, close.to_numpy(dtype=np.float64), np.nan)
    series = pd.Series(values, index=frame.index)
    peak252 = series.rolling(252, min_periods=1).max().to_numpy(dtype=np.float64)
    peak504 = series.rolling(504, min_periods=1).max().to_numpy(dtype=np.float64)
    price = close.to_numpy(dtype=np.float64)

    def depth_points(peak: np.ndarray) -> np.ndarray:
        depth = np.abs(
            np.divide(
                price - peak,
                peak,
                out=np.zeros(len(frame), dtype=np.float64),
                where=np.isfinite(peak) & (peak > 0.0) & np.isfinite(price),
            )
        )
        mask = (depth >= 0.15) & (depth <= 0.50)
        return np.where(
            mask,
            np.clip(1.0 - np.abs(depth - 0.32) / 0.25, 0.0, 1.0) * 3.0,
            0.0,
        )

    available = (
        (np.arange(len(frame)) >= 251)
        & pair
        & (_rolling_count(pair, 504) >= 60)
    )
    delta = depth_points(peak504) - depth_points(peak252)
    return np.where(available, delta, 0.0)


def _canonical_base_score(
    frame: pd.DataFrame,
    legacy_base: np.ndarray,
    coverage: np.ndarray,
) -> np.ndarray:
    legacy_volume = _legacy_volume_score_matrix(frame)
    legacy_accum = _legacy_accumulation_score_matrix(frame)
    setup_delta = (
        legacy_volume * (float(_scale.VOLUME_SCALE) - 1.0)
        + legacy_accum * (float(_scale.ACCUMULATION_SCALE) - 1.0)
        + _trend_504_delta(frame)
    )
    setup_coverage = 0.55 + 0.45 * coverage
    return np.clip(legacy_base + setup_delta * setup_coverage, 0.0, 100.0)


def _fast_score_matrix(frame: pd.DataFrame, *, is_etf: bool):
    matrix = _ORIGINAL_FAST_SCORE_MATRIX(frame, is_etf=is_etf)
    if matrix is None:
        return None

    _raw_trigger, trigger_score = canonical_trigger_score_matrix(frame)
    coverage = _indicator_coverage(frame)
    base_score = _canonical_base_score(frame, matrix.base_score, coverage)
    setup_weight, trigger_weight, execution_weight = _score._model_component_weights()
    final_score = np.clip(
        base_score * float(setup_weight)
        + trigger_score * float(trigger_weight)
        + matrix.execution_score * float(execution_weight),
        0.0,
        100.0,
    )
    final_score = np.minimum(final_score, 40.0 + 60.0 * coverage)
    return _fast.FastScoreMatrix(
        base_score=base_score,
        trigger_score=trigger_score,
        execution_score=matrix.execution_score,
        final_score=final_score,
        breakout_score=matrix.breakout_score,
        value_trap_risk=matrix.value_trap_risk,
        entry_signal=matrix.entry_signal,
    )


def install() -> None:
    global _INSTALLED, _ORIGINAL_FAST_SCORE_MATRIX

    # v79 can be re-installed by analytics/worker bootstraps. Re-assert v95
    # scalar bindings every time this consistency layer is requested.
    _scale.install()
    if not _INSTALLED:
        _ORIGINAL_FAST_SCORE_MATRIX = _fast._fast_score_matrix
        _fast._fast_score_matrix = _fast_score_matrix
        _INSTALLED = True
    _analytics.SCORING_CONSISTENCY_VERSION = SCORING_CONSISTENCY_VERSION
