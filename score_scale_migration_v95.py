"""v95/v97 canonical setup-score scale migration.

The stable setup model nominally allocates 20/25/25/15/15 points, but the
positive terms of Volume and Accumulation can reach only 22 and 23 points.
v95 maps those dimensions to their documented 25-point ranges and removes HVN
proximity from alpha scoring.

v97 makes the installer deliberately re-entrant. Older acceleration facades are
also re-entrant and can rebind ``score_core.score_volume`` / ``score_accumulation``
after this module first loads. Every install call therefore re-asserts the
canonical scale while capturing the unscaled reference functions only once.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import score_core as _core

SCORE_SCALE_MIGRATION_VERSION = (
    "2026-08-23-v97-volume-accumulation-full-scale-hvn-diagnostic-reentrant-v2"
)
VOLUME_RAW_MAX = 22.0
ACCUMULATION_RAW_MAX = 23.0
VOLUME_NOMINAL_MAX = 25.0
ACCUMULATION_NOMINAL_MAX = 25.0
VOLUME_SCALE = VOLUME_NOMINAL_MAX / VOLUME_RAW_MAX
ACCUMULATION_SCALE = ACCUMULATION_NOMINAL_MAX / ACCUMULATION_RAW_MAX

_INSTALLED = False
_ORIGINAL_SCORE_VOLUME: Any = None
_ORIGINAL_SCORE_ACCUMULATION: Any = None


def _scale_dimension(value: float, raw_max: float, nominal_max: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(number):
        return 0.0
    return float(np.clip(number * nominal_max / raw_max, 0.0, nominal_max))


def score_volume(df: pd.DataFrame) -> float:
    """Map the legacy 0..22 positive-evidence range onto nominal 0..25."""
    if _ORIGINAL_SCORE_VOLUME is None:
        return 0.0
    raw = float(_ORIGINAL_SCORE_VOLUME(df))
    return _scale_dimension(raw, VOLUME_RAW_MAX, VOLUME_NOMINAL_MAX)


def score_accumulation(df: pd.DataFrame) -> float:
    """Map the legacy 0..23 positive-evidence range onto nominal 0..25."""
    if _ORIGINAL_SCORE_ACCUMULATION is None:
        return 0.0
    raw = float(_ORIGINAL_SCORE_ACCUMULATION(df))
    return _scale_dimension(raw, ACCUMULATION_RAW_MAX, ACCUMULATION_NOMINAL_MAX)


def score_structure(df: pd.DataFrame) -> float:
    """Canonical 15-point structure score with VP/HVN diagnostic-only."""
    if len(df) < 252 or not all(
        column in df.columns for column in ("Close", "High", "Low")
    ):
        return 0.0

    score = 0.0
    if "Low52W" in df.columns and "DistToLow52W" in df.columns:
        dist_low = df["DistToLow52W"].iloc[-1]
        if _core._is_finite(dist_low) and 0 <= float(dist_low) <= 20:
            distance = float(dist_low)
            if distance < 8:
                score += distance / 8.0 * 5.0
            elif distance <= 12:
                score += 5.0
            else:
                score += (20.0 - distance) / 8.0 * 5.0

    if len(df) >= int(_core.CONSOLIDATION_DAYS):
        recent = df.iloc[-int(_core.CONSOLIDATION_DAYS) :]
        high = pd.to_numeric(recent["High"], errors="coerce").max()
        low = pd.to_numeric(recent["Low"], errors="coerce").min()
        avg_price = pd.to_numeric(recent["Close"], errors="coerce").mean()
        if np.isfinite(avg_price) and float(avg_price) > 0:
            range_pct = (float(high) - float(low)) / float(avg_price) * 100.0
            if np.isfinite(range_pct) and range_pct <= float(
                _core.CONSOLIDATION_MAX_RANGE_PCT
            ):
                tightness = _core._clamp(
                    1.0 - range_pct / float(_core.CONSOLIDATION_MAX_RANGE_PCT),
                    0.0,
                    1.0,
                )
                score += (0.2 + tightness * 0.8) * 5.0

    if "RegSlope" in df.columns:
        reg_slope = df["RegSlope"].iloc[-1]
        if _core._is_finite(reg_slope):
            score += (
                _core._clamp(1.0 - abs(float(reg_slope)) / 0.05, 0.0, 1.0)
                * 2.0
            )
            if "RegR2" in df.columns:
                r2 = df["RegR2"].iloc[-1]
                if _core._is_finite(r2):
                    score += _core._clamp(float(r2), 0.0, 1.0)

    return float(min(score, 15.0))


def install() -> None:
    """Capture unscaled kernels once and always re-assert canonical bindings."""
    global _INSTALLED, _ORIGINAL_SCORE_VOLUME, _ORIGINAL_SCORE_ACCUMULATION

    # Capture only kernels that are not this module's own wrappers. On the first
    # call these are normally the latest v79 accelerated exact-formula kernels.
    if _ORIGINAL_SCORE_VOLUME is None and _core.score_volume is not score_volume:
        _ORIGINAL_SCORE_VOLUME = _core.score_volume
    if (
        _ORIGINAL_SCORE_ACCUMULATION is None
        and _core.score_accumulation is not score_accumulation
    ):
        _ORIGINAL_SCORE_ACCUMULATION = _core.score_accumulation

    _core.score_volume = score_volume
    _core.score_accumulation = score_accumulation
    _core.score_structure = score_structure
    _core.SCORE_SCALE_MIGRATION_VERSION = SCORE_SCALE_MIGRATION_VERSION
    _INSTALLED = True
