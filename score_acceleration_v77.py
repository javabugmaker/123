"""v77 scoring hot-path acceleration without changing score formulas."""

from __future__ import annotations

import numpy as np
import pandas as pd

import score_core as _score

_INSTALLED = False


def _has_finite_values_fast(
    df: pd.DataFrame,
    columns: tuple[str, ...],
    minimum: int = 1,
) -> bool:
    if not all(column in df.columns for column in columns) or df.empty:
        return False
    arrays = [
        pd.to_numeric(df[column], errors="coerce").to_numpy(dtype=np.float64)
        for column in columns
    ]
    if len(arrays) == 1:
        finite_rows = np.isfinite(arrays[0])
    else:
        finite_rows = np.logical_and.reduce([np.isfinite(array) for array in arrays])
    return bool(
        int(np.count_nonzero(finite_rows)) >= int(minimum)
        and bool(finite_rows[-1])
    )


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


def score_volume(df: pd.DataFrame) -> float:
    """Equivalent volume score with vectorised trailing-run counting."""
    if len(df) < 120:
        return 0.0
    score = 0.0
    if "VolMA20" in df.columns and "VolMA120" in df.columns:
        vol_ma20 = df["VolMA20"].replace([np.inf, -np.inf], np.nan)
        vol_ma120 = df["VolMA120"].replace([np.inf, -np.inf], np.nan)
        ratio_series = (vol_ma20 / vol_ma120.replace(0, np.nan)).dropna()
        if len(ratio_series) >= _score.VOLUME_ACCUM_MIN_DAYS:
            ratio_values = ratio_series.to_numpy(dtype=np.float64)
            qualifying = ratio_values >= float(_score.VOLUME_ACCUM_RATIO)
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
            ratio_now = float(ratio_values[-1])
            score += _score._clamp(
                (ratio_now - _score.VOLUME_ACCUM_RATIO) / 0.8
            ) * 3.0
            if len(ratio_values) >= 20:
                ratio_change = float(ratio_values[-1] - ratio_values[-20])
                score += _score._clamp(ratio_change / 0.5) * 4.0
    if "VolZScore" in df.columns:
        z_recent = (
            df["VolZScore"].replace([np.inf, -np.inf], np.nan).dropna().iloc[-30:]
        )
        if len(z_recent) >= 10:
            z_values = z_recent.to_numpy(dtype=np.float64)
            z_now = float(z_values[-1])
            positive_days = float(np.mean(z_values > 0.0))
            score += positive_days * 3.0
            score += _score._clamp(z_now / 2.0) * 2.0
    return _score._clamp(score, 0.0, 25.0)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _score._score_dimensions_available = _score_dimensions_available
    _score.score_volume = score_volume
    _INSTALLED = True


install()
