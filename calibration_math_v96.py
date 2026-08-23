"""v96 production calibration mathematics.

This layer fixes three sources of unintended non-stationarity / double shrink:

1. Objective evidence is mapped through a fixed economic scale instead of a
   percentile rank of whichever tickers happened to be present in the current
   backtest run.
2. Reliability determines *evidence weight once*.  The evidence score itself is
   not first shrunk toward neutral and then blended by another reliability-
   proportional weight (the former effective R^2 behaviour).
3. FailureSignalFactor remains an audit diagnostic.  Net-excess loss, downside,
   profit factor and drawdown already enter the backtest component, so applying
   a second directional-loss multiplier would count the same bad history twice.

Peer-calibration confidence uses the walk-forward stable-fold share once.  The
v57/v90 compatibility wrapper had squared that share for UNSTABLE histories.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import analytics_core as _core
import model_calibration as _model_calibration

CALIBRATION_MATH_VERSION = (
    "2026-08-23-v96-fixed-objective-single-reliability-failure-diagnostic-v1"
)

_INSTALLED = False
_ORIGINAL_LEGACY_APPLY: Any = None


def fixed_objective_score(values: pd.Series, objective: str) -> pd.Series:
    """Map objective values to a stable 0..100 economic scale.

    These bounds are the same robust ranges already used elsewhere by the
    backtest score and are independent of the size/composition of the run.
    """
    numeric = pd.to_numeric(values, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    name = str(objective or "net_excess_return_20d").strip().lower()
    if name == "max_drawdown":
        return (100.0 - numeric.abs().clip(0.0, 50.0) * 2.0).clip(0.0, 100.0)
    if name == "risk_adjusted":
        return pd.Series(
            50.0 + 50.0 * np.tanh(numeric.to_numpy(dtype=float) / 2.0),
            index=numeric.index,
            dtype=float,
        ).clip(0.0, 100.0)
    if "60" in name:
        # Consistent with the 60-day excess-return bounds in ticker scoring.
        return ((numeric.clip(-25.0, 35.0) + 25.0) / 60.0 * 100.0).clip(
            0.0, 100.0
        )
    # 20-day return/excess objectives use the established +/-15% robust range.
    return ((numeric.clip(-15.0, 15.0) + 15.0) / 30.0 * 100.0).clip(
        0.0, 100.0
    )


def single_shrink_stability_stats(
    rows: list[dict[str, Any]] | None,
    *,
    minimum_folds: int = 3,
) -> dict[str, Any]:
    """Use the stable-fold confidence share once, never squared."""
    governed = dict(
        _model_calibration.calibration_stability_stats(
            rows,
            minimum_folds=minimum_folds,
        )
    )
    try:
        multiplier = float(governed.get("confidence_multiplier", 1.0) or 0.0)
    except (TypeError, ValueError):
        multiplier = 0.0
    governed["raw_confidence_multiplier"] = round(
        float(np.clip(multiplier, 0.0, 1.0)), 4
    )
    governed["confidence_multiplier"] = governed["raw_confidence_multiplier"]
    governed["confidence_governance"] = "single-stable-fold-share-v2"
    return governed


def _bool_series(frame: pd.DataFrame, column: str) -> pd.Series:
    values = frame.get(column, pd.Series(False, index=frame.index))
    return (
        values.fillna(False)
        .astype(str)
        .str.strip()
        .str.lower()
        .isin({"true", "1", "yes", "y", "是"})
    )


def _numeric(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    values = frame.get(column, pd.Series(default, index=frame.index))
    return pd.to_numeric(values, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )


def _rewrite_calibration_math(summary: Any) -> None:
    """Rewrite only the evidence-combination layer after stable postprocess.

    The public analytics wrapper has already staged the complete result set.
    This function runs inside that transaction and then re-finalises the full
    universe, so publication remains atomic.
    """
    if not hasattr(summary, "objective"):
        return
    path = _core.OUTPUT_DIR / "AllResults.csv"
    if not path.exists():
        return

    frame = pd.read_csv(path, encoding="utf-8-sig", low_memory=False).copy()
    if frame.empty or "BacktestScore" not in frame.columns:
        return

    objective_raw = _numeric(frame, "BacktestRawObjectiveValue")
    legacy_objective = _numeric(frame, "BacktestObjectiveValue")
    objective_raw = objective_raw.where(objective_raw.notna(), legacy_objective)
    objective_score = fixed_objective_score(objective_raw, str(summary.objective))
    objective_score = objective_score.fillna(float(_core.BACKTEST_NEUTRAL_SCORE))
    frame["BacktestObjectiveScoreFixed"] = objective_score.round(4)
    frame["BacktestObjectiveScaleVersion"] = "fixed-economic-bounds-v1"

    backtest_score = _numeric(frame, "BacktestScore").fillna(
        float(_core.BACKTEST_NEUTRAL_SCORE)
    )
    profit_factor = _numeric(frame, "BacktestNetExcessProfitFactor")
    if profit_factor.isna().all():
        profit_factor = _numeric(frame, "BacktestProfitFactor")
    profit_factor_score = (
        profit_factor.clip(lower=0.0, upper=3.0) / 3.0 * 100.0
    ).fillna(50.0)
    drawdown = _numeric(frame, "BacktestMaxDrawdown60D")
    drawdown_score = (
        100.0 - drawdown.abs().clip(lower=0.0, upper=50.0) * 2.0
    ).fillna(50.0)
    backtest_component = (
        backtest_score * 0.50
        + objective_score * 0.25
        + profit_factor_score * 0.15
        + drawdown_score * 0.10
    ).clip(0.0, 100.0)
    frame["BacktestEvidenceScoreRaw"] = backtest_component.round(4)

    reliability = _numeric(frame, "BacktestReliability", 0.0).fillna(0.0).clip(
        0.0, 1.0
    )
    local_weight = (
        reliability * float(_core.BACKTEST_NORMAL_WEIGHT)
    ).clip(0.0, float(_core.BACKTEST_NORMAL_WEIGHT))

    peer_score = _numeric(frame, "GlobalCalibrationScore").fillna(
        float(_core.BACKTEST_NEUTRAL_SCORE)
    ).clip(0.0, 100.0)
    peer_confidence = _numeric(frame, "GlobalCalibrationConfidence", 0.0).fillna(
        0.0
    ).clip(0.0, 1.0)
    peer_weight = (
        peer_confidence * float(_core.GLOBAL_CALIBRATION_MAX_WEIGHT)
    ).clip(0.0, float(_core.GLOBAL_CALIBRATION_MAX_WEIGHT))

    local_available = _numeric(frame, "BacktestSamples", 0.0).fillna(0.0).ge(
        float(_core.BACKTEST_MIN_SAMPLES_FOR_RANKING)
    )
    local_weight = local_weight.where(local_available, 0.0)
    peer_available = peer_confidence.gt(0.0)
    peer_weight = peer_weight.where(peer_available, 0.0)

    evidence_total = local_weight + peer_weight
    evidence_score = pd.Series(
        float(_core.BACKTEST_NEUTRAL_SCORE), index=frame.index, dtype=float
    )
    has_evidence = evidence_total.gt(0.0)
    evidence_score.loc[has_evidence] = (
        backtest_component.loc[has_evidence] * local_weight.loc[has_evidence]
        + peer_score.loc[has_evidence] * peer_weight.loc[has_evidence]
    ) / evidence_total.loc[has_evidence]

    # Local and peer estimates share much of the same history. Do not pretend
    # they are independent by adding their weights; retain the strongest bounded
    # evidence budget while blending their estimates inside that budget.
    effective_weight = pd.Series(
        np.maximum(
            local_weight.to_numpy(dtype=float),
            peer_weight.to_numpy(dtype=float),
        ),
        index=frame.index,
        dtype=float,
    ).clip(0.0, max(float(_core.BACKTEST_NORMAL_WEIGHT), float(_core.GLOBAL_CALIBRATION_MAX_WEIGHT)))

    final_score = _numeric(frame, "FinalScore")
    raw_score = final_score.where(final_score.notna(), _numeric(frame, "Score", 0.0))
    raw_score = raw_score.fillna(0.0).clip(0.0, 100.0)
    old_failure_adjusted = _numeric(frame, "FailureAdjustedScore")

    composite = (
        raw_score * (1.0 - effective_weight)
        + evidence_score * effective_weight
    ).clip(0.0, 100.0)
    frame["BacktestLocalEvidenceWeight"] = local_weight.round(4)
    frame["BacktestPeerEvidenceWeight"] = peer_weight.round(4)
    frame["BacktestEffectiveWeight"] = effective_weight.round(4)
    frame["BacktestAdjustedScore"] = evidence_score.round(4)
    frame["CompositeScore"] = composite.round(4)

    failure_factor = _numeric(frame, "FailureSignalFactor", 1.0).fillna(1.0).clip(
        0.0, 1.0
    )
    frame["FailureSignalDiagnosticFactor"] = failure_factor.round(4)
    frame["FailurePenaltyApplied"] = False
    # The diagnostic survives, but production score receives no second loss hit.
    frame["FailureAdjustedScore"] = composite.round(4)

    # Preserve every non-backtest policy transformation from the stable engine.
    # Only replace the old calibration ratio with the corrected one.
    reference = raw_score.replace(0.0, np.nan)
    old_ratio = (old_failure_adjusted / reference).replace(
        [np.inf, -np.inf], np.nan
    ).fillna(1.0).clip(0.70, 1.30)
    new_ratio = (composite / reference).replace(
        [np.inf, -np.inf], np.nan
    ).fillna(1.0).clip(0.70, 1.30)
    correction = (new_ratio / old_ratio.replace(0.0, np.nan)).replace(
        [np.inf, -np.inf], np.nan
    ).fillna(1.0).clip(0.70 / 1.30, 1.30 / 0.70)
    for column in ("TechnicalInstitutionalScore", "InstitutionalScore"):
        if column in frame.columns:
            current = _numeric(frame, column)
            frame[column] = (current * correction).clip(0.0, 100.0).round(4)

    frame["CalibrationMathVersion"] = CALIBRATION_MATH_VERSION
    frame = _core.finalize_signal_ranking(frame)

    from report import _atomic_write_csv, _atomic_write_parquet, refresh_candidate_exports

    _atomic_write_csv(frame, path)
    refresh_candidate_exports(frame, output_dir=_core.OUTPUT_DIR)
    _atomic_write_parquet(frame, _core.OUTPUT_DIR / "AllResults.parquet")


def install(analytics_module: Any) -> None:
    """Install the v96 math beneath the public transactional ranking facade."""
    global _INSTALLED, _ORIGINAL_LEGACY_APPLY
    if _INSTALLED:
        return
    _ORIGINAL_LEGACY_APPLY = analytics_module._legacy_apply_backtest_ranking

    def legacy_apply_backtest_ranking(summary: Any, top_n: int = 50) -> None:
        _ORIGINAL_LEGACY_APPLY(summary, top_n=top_n)
        _rewrite_calibration_math(summary)

    analytics_module._legacy_apply_backtest_ranking = legacy_apply_backtest_ranking
    analytics_module.calibration_stability_stats = single_shrink_stability_stats
    analytics_module.CALIBRATION_MATH_VERSION = CALIBRATION_MATH_VERSION
    _INSTALLED = True
