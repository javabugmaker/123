"""v94 canonical FAST/EXACT scoring consistency.

The live/EXACT v89 score uses a smooth breakout-price transition around prior
20-day resistance. The v80 whole-ticker FAST matrix still carried the old
12->35 point discontinuity. This module wraps only the FAST matrix and
recomputes TriggerScore/final score with the same vectorised smooth-step math
used by production execution diagnostics.

Candidate scheduling remains intentionally faster/sparser in FAST mode; the
score of an evaluated endpoint now shares the same trigger formula as the live
and EXACT paths.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import analytics_core as _analytics
import backtest_fastscore_v80 as _fast
import score_core as _score
from execution_integrity_v87 import smooth_breakout_price_component

SCORING_CONSISTENCY_VERSION = (
    "2026-08-23-v94-fast-exact-smooth-trigger-equivalence-v1"
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


def _indicator_coverage(frame: pd.DataFrame) -> np.ndarray:
    """Vectorised copy of endpoint dimension-availability semantics."""
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
        & (_rolling_count(trend_pair, 252) >= 60)
    )

    volume_pair = np.isfinite(vol20) & np.isfinite(vol120)
    z_finite = np.isfinite(z)
    volume = (positions >= 119) & (
        (
            volume_pair
            & (
                _rolling_count(volume_pair, 252)
                >= int(_score.VOLUME_ACCUM_MIN_DAYS)
            )
        )
        | (z_finite & (_rolling_count(z_finite, 252) >= 10))
    )

    obv_finite = np.isfinite(obv)
    ad_pair = np.isfinite(ad) & np.isfinite(ad_slope)
    cmf_finite = np.isfinite(cmf)
    mfi_finite = np.isfinite(mfi)
    accumulation = (positions >= 59) & (
        (obv_finite & (_rolling_count(obv_finite, 252) >= 40))
        | (
            ad_pair
            & (
                _rolling_count(ad_pair, 252)
                >= int(_score.AD_SLOPE_LOOKBACK)
            )
        )
        | (cmf_finite & (_rolling_count(cmf_finite, 252) >= 20))
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
                _rolling_count(bb_finite, 252)
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


def _fast_score_matrix(frame: pd.DataFrame, *, is_etf: bool):
    matrix = _ORIGINAL_FAST_SCORE_MATRIX(frame, is_etf=is_etf)
    if matrix is None:
        return None

    _raw_trigger, trigger_score = canonical_trigger_score_matrix(frame)
    coverage = _indicator_coverage(frame)
    setup_weight, trigger_weight, execution_weight = _score._model_component_weights()
    final_score = np.clip(
        matrix.base_score * float(setup_weight)
        + trigger_score * float(trigger_weight)
        + matrix.execution_score * float(execution_weight),
        0.0,
        100.0,
    )
    final_score = np.minimum(final_score, 40.0 + 60.0 * coverage)
    return _fast.FastScoreMatrix(
        base_score=matrix.base_score,
        trigger_score=trigger_score,
        execution_score=matrix.execution_score,
        final_score=final_score,
        breakout_score=matrix.breakout_score,
        value_trap_risk=matrix.value_trap_risk,
        entry_signal=matrix.entry_signal,
    )


def install() -> None:
    global _INSTALLED, _ORIGINAL_FAST_SCORE_MATRIX
    if _INSTALLED:
        return
    _ORIGINAL_FAST_SCORE_MATRIX = _fast._fast_score_matrix
    _fast._fast_score_matrix = _fast_score_matrix
    _analytics.SCORING_CONSISTENCY_VERSION = SCORING_CONSISTENCY_VERSION
    _INSTALLED = True
