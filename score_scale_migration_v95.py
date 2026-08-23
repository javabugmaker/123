"""v95 canonical setup-score scale migration.

The stable setup model nominally allocates 20/25/25/15/15 points, but the
positive terms of Volume and Accumulation can reach only 22 and 23 points.
That makes a documented 100-point setup scale top out near 95 before coverage.

v95 maps those two raw dimensions onto their documented 25-point ranges with a
strict linear transform.  It also removes HVN proximity from alpha scoring.
Volume-profile/HVN state remains available as diagnostics, but no longer makes
EXACT/live scores depend on a feature that the vectorised FAST engine does not
materialise historically.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import score_core as _core

SCORE_SCALE_MIGRATION_VERSION = (
    "2026-08-23-v95-volume-accumulation-full-scale-hvn-diagnostic-v1"
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
    if not np.isfinite(float(value)):
        return 0.0
    return float(np.clip(float(value) * nominal_max / raw_max, 0.0, nominal_max))


def score_volume(df: pd.DataFrame) -> float:
    """Map the legacy 0..22 positive-evidence range onto the nominal 0..25 range."""
    raw = float(_ORIGINAL_SCORE_VOLUME(df))
    return _scale_dimension(raw, VOLUME_RAW_MAX, VOLUME_NOMINAL_MAX)


def score_accumulation(df: pd.DataFrame) -> float:
    """Map the legacy 0..23 positive-evidence range onto the nominal 0..25 range."""
    raw = float(_ORIGINAL_SCORE_ACCUMULATION(df))
    return _scale_dimension(raw, ACCUMULATION_RAW_MAX, ACCUMULATION_NOMINAL_MAX)


def score_structure(df: pd.DataFrame) -> float:
    """Canonical 15-point structure score with VP/HVN kept diagnostic-only.

    This is the stable structure formula minus the optional 2-point
    ``Above_HVN`` proximity term.  Removing that term makes live, EXACT and FAST
    scoring invariant to whether the expensive historical volume profile was
    materialised, while leaving the public VP/HVN columns untouched for audit
    and research.
    """
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
            score += _core._clamp(1.0 - abs(float(reg_slope)) / 0.05, 0.0, 1.0) * 2.0
            if "RegR2" in df.columns:
                r2 = df["RegR2"].iloc[-1]
                if _core._is_finite(r2):
                    score += _core._clamp(float(r2), 0.0, 1.0)

    return float(min(score, 15.0))


def install() -> None:
    global _INSTALLED, _ORIGINAL_SCORE_VOLUME, _ORIGINAL_SCORE_ACCUMULATION
    if _INSTALLED:
        return
    _ORIGINAL_SCORE_VOLUME = _core.score_volume
    _ORIGINAL_SCORE_ACCUMULATION = _core.score_accumulation
    _core.score_volume = score_volume
    _core.score_accumulation = score_accumulation
    _core.score_structure = score_structure
    _core.SCORE_SCALE_MIGRATION_VERSION = SCORE_SCALE_MIGRATION_VERSION
    _INSTALLED = True
