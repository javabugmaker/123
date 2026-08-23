"""v100 production backtest mathematical integrity helpers.

The module keeps several production concerns orthogonal:

* production signal semantics: peer calibration used by live ranking may only
  fall back through levels that preserve ``entry_signal``;
* overlap independence: a crowded market day receives at most one unit of
  cross-sectional influence;
* historical-universe evidence quality: provisional point-in-time membership
  is discounted after overlap balancing so date normalization cannot erase the
  uncertainty haircut;
* finite profit-factor evidence: an all-winning held-out sample saturates at the
  same 3.0 cap used by ranking instead of becoming NaN during serialization;
* split provenance: summaries explicitly disclose that train/validation/test
  boundaries are purged by the complete 60-day outcome window.

The signal-semantic filter remains scoped to the production analytics resolver.
The generic ``model_calibration.calibration_details_for_frame`` research API
keeps its normal asset/global fallback hierarchy. The compatibility bootstrap
still installs the narrow legacy-executor boundary; modern hot paths are
untouched.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import analytics_compat_v97 as _analytics_compat
import analytics_core as _core

PRODUCTION_BACKTEST_MATH_VERSION = (
    "2026-08-23-v100-production-backtest-edge-integrity-v1"
)
PROVISIONAL_EVIDENCE_WEIGHT = 0.25
PROFIT_FACTOR_SCORE_CAP = 3.0
BACKTEST_SPLIT_POLICY = "purged_by_complete_60d_outcome_window_v1"
_SIGNAL_LEVEL_TOKEN = "signal"

_INSTALLED = False
_ORIGINAL_PREPARE_SAMPLES: Any = None
_ORIGINAL_CALIBRATION_DETAILS: Any = None
_ORIGINAL_WEIGHTED_PROFIT_FACTOR: Any = None
_ORIGINAL_SUMMARY_TO_DICT: Any = None


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
    """Balance overlap first, then apply PIT evidence quality."""
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
    """Keep peer priors whose hierarchy preserves entry-signal meaning."""
    if not rows:
        return []
    return [
        dict(row)
        for row in rows
        if _SIGNAL_LEVEL_TOKEN
        in str(row.get("level", "") or "").strip().lower()
        and bool(str(row.get("entry_signal", "") or "").strip())
    ]


def install(analytics_module: Any, model_calibration_module: Any) -> None:
    """Install production calibration weights and finite edge-case semantics."""
    global _INSTALLED
    global _ORIGINAL_PREPARE_SAMPLES, _ORIGINAL_CALIBRATION_DETAILS
    global _ORIGINAL_WEIGHTED_PROFIT_FACTOR, _ORIGINAL_SUMMARY_TO_DICT

    _analytics_compat.install()
    if _INSTALLED:
        return

    _ORIGINAL_PREPARE_SAMPLES = model_calibration_module._prepare_samples
    _ORIGINAL_CALIBRATION_DETAILS = (
        model_calibration_module.calibration_details_for_frame
    )
    _ORIGINAL_WEIGHTED_PROFIT_FACTOR = _core._weighted_profit_factor
    _ORIGINAL_SUMMARY_TO_DICT = _core.BacktestSummary.to_dict

    def prepare_samples(frame: pd.DataFrame) -> pd.DataFrame:
        result = _ORIGINAL_PREPARE_SAMPLES(frame)
        result["calibration_weight"] = date_balanced_evidence_weights(result)
        return result

    def production_calibration_details_for_frame(
        frame: pd.DataFrame,
        rows: list[dict[str, Any]] | None,
    ) -> pd.DataFrame:
        return _ORIGINAL_CALIBRATION_DETAILS(
            frame,
            signal_semantic_calibration_rows(rows),
        )

    def weighted_profit_factor(values: pd.Series, weights: pd.Series) -> float:
        value = float(_ORIGINAL_WEIGHTED_PROFIT_FACTOR(values, weights))
        if np.isposinf(value):
            return float(PROFIT_FACTOR_SCORE_CAP)
        return value

    def summary_to_dict(summary: Any) -> dict[str, Any]:
        result = dict(_ORIGINAL_SUMMARY_TO_DICT(summary))
        result["split_policy"] = BACKTEST_SPLIT_POLICY
        return result

    model_calibration_module._prepare_samples = prepare_samples
    analytics_module._date_balanced_weights = date_balanced_evidence_weights
    analytics_module.calibration_details_for_frame = (
        production_calibration_details_for_frame
    )
    analytics_module.production_calibration_details_for_frame = (
        production_calibration_details_for_frame
    )
    _core._weighted_profit_factor = weighted_profit_factor
    _core.BacktestSummary.to_dict = summary_to_dict
    analytics_module.PRODUCTION_BACKTEST_MATH_VERSION = PRODUCTION_BACKTEST_MATH_VERSION
    analytics_module.PROFIT_FACTOR_SCORE_CAP = PROFIT_FACTOR_SCORE_CAP
    analytics_module.BACKTEST_SPLIT_POLICY = BACKTEST_SPLIT_POLICY
    _INSTALLED = True
