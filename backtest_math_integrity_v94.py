"""v94 mathematical integrity helpers for production backtest calibration.

This module keeps three concepts orthogonal:

* signal semantics: peer calibration may only fall back through levels that
  preserve ``entry_signal``; an executable breakout prior must never boost an
  unrelated WAIT/HOLD/AVOID state;
* overlap independence: a crowded market day receives at most one unit of
  cross-sectional influence;
* historical-universe evidence quality: provisional point-in-time membership
  is discounted *after* overlap balancing so date normalization cannot erase
  the uncertainty haircut.

v94 deliberately patches the calibration resolver instead of wrapping the
public ``analytics.apply_backtest_ranking`` transaction.  The latter is a
spawn/publication contract owned by the analytics facade and must keep its
module identity and generic test-double compatibility.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

PRODUCTION_BACKTEST_MATH_VERSION = (
    "2026-08-23-v94-signal-semantic-peer-pit-weight-orthogonality-v2"
)
PROVISIONAL_EVIDENCE_WEIGHT = 0.25
_SIGNAL_LEVEL_TOKEN = "signal"

_INSTALLED = False
_ORIGINAL_PREPARE_SAMPLES: Any = None
_ORIGINAL_CALIBRATION_DETAILS: Any = None


def _numeric(values: Any, index: pd.Index, default: float) -> pd.Series:
    if not isinstance(values, pd.Series):
        values = pd.Series(values, index=index)
    return (
        pd.to_numeric(values, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .fillna(default)
    )


def universe_evidence_weight(frame: pd.DataFrame) -> pd.Series:
    """Return independent PIT evidence quality in [0, 1]."""
    if "universe_evidence_weight" in frame.columns:
        return _numeric(frame["universe_evidence_weight"], frame.index, 1.0).clip(
            0.0, 1.0
        )

    status = (
        frame.get(
            "universe_snapshot_status",
            pd.Series("ELIGIBLE", index=frame.index),
        )
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )
    quality = pd.Series(1.0, index=frame.index, dtype=float)
    quality.loc[status.eq("PROVISIONAL")] = float(PROVISIONAL_EVIDENCE_WEIGHT)
    quality.loc[status.isin({"INELIGIBLE", "EXCLUDED"})] = 0.0
    return quality


def date_balanced_evidence_weights(frame: pd.DataFrame) -> pd.Series:
    """Balance overlap first, then apply PIT evidence quality.

    v93 stores provisional uncertainty inside ``sample_weight``. Dividing by
    the quality factor recovers the independent within-ticker spacing weight,
    allowing the date cluster denominator to be computed without normalising
    the 25% uncertainty haircut back toward 100%.
    """
    if frame is None or frame.empty:
        return pd.Series(dtype=float)

    quality = universe_evidence_weight(frame)
    base = _numeric(
        frame.get("sample_weight", pd.Series(1.0, index=frame.index)),
        frame.index,
        0.0,
    ).clip(lower=0.0, upper=1.0)
    recoverable = quality.gt(0.0)
    spacing_weight = pd.Series(0.0, index=frame.index, dtype=float)
    spacing_weight.loc[recoverable] = (
        base.loc[recoverable] / quality.loc[recoverable]
    ).clip(lower=0.0, upper=1.0)

    dates = pd.to_datetime(
        frame.get("entry_date", pd.Series(pd.NaT, index=frame.index)),
        errors="coerce",
    ).dt.normalize()
    daily_independence = spacing_weight.groupby(dates).transform("sum")
    balanced = spacing_weight.div(daily_independence.clip(lower=1.0)) * quality
    return balanced.where(dates.notna(), 0.0).fillna(0.0)


def signal_semantic_calibration_rows(
    rows: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Keep only peer priors whose hierarchy preserves entry-signal meaning."""
    if not rows:
        return []
    result: list[dict[str, Any]] = []
    for row in rows:
        level = str(row.get("level", "") or "").strip().lower()
        signal = str(row.get("entry_signal", "") or "").strip().upper()
        if _SIGNAL_LEVEL_TOKEN not in level or not signal:
            continue
        result.append(dict(row))
    return result


def install(analytics_module: Any, model_calibration_module: Any) -> None:
    """Install shared weighting and signal-preserving peer resolution.

    The public ranking transaction is intentionally untouched.  Filtering the
    calibration rows at the resolver boundary applies the same semantic rule to
    every caller while preserving the analytics facade's spawn-safe identity.
    """
    global _INSTALLED, _ORIGINAL_PREPARE_SAMPLES, _ORIGINAL_CALIBRATION_DETAILS
    if _INSTALLED:
        return

    _ORIGINAL_PREPARE_SAMPLES = model_calibration_module._prepare_samples
    _ORIGINAL_CALIBRATION_DETAILS = model_calibration_module.calibration_details_for_frame

    def prepare_samples(frame: pd.DataFrame) -> pd.DataFrame:
        result = _ORIGINAL_PREPARE_SAMPLES(frame)
        result["calibration_weight"] = date_balanced_evidence_weights(result)
        return result

    def calibration_details_for_frame(
        frame: pd.DataFrame,
        rows: list[dict[str, Any]] | None,
    ) -> pd.DataFrame:
        return _ORIGINAL_CALIBRATION_DETAILS(
            frame,
            signal_semantic_calibration_rows(rows),
        )

    model_calibration_module._prepare_samples = prepare_samples
    model_calibration_module.calibration_details_for_frame = calibration_details_for_frame
    analytics_module._date_balanced_weights = date_balanced_evidence_weights
    analytics_module.calibration_details_for_frame = calibration_details_for_frame
    analytics_module.PRODUCTION_BACKTEST_MATH_VERSION = PRODUCTION_BACKTEST_MATH_VERSION
    _INSTALLED = True
