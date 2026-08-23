"""v96/v97 production calibration mathematics.

The production layer removes three sources of unintended non-stationarity:

1. Objective evidence uses fixed economic bounds rather than a percentile of
   whichever tickers happened to be in the current run.
2. Reliability determines evidence weight once; the evidence score is not first
   shrunk toward neutral and then blended by a second reliability factor.
3. FailureSignalFactor remains an audit diagnostic because downside/net excess,
   profit factor and drawdown already encode historical failure.

v97 also makes post-processing idempotent across CLI/GUI/direct analytics entry
points. Previous derived columns are stripped, the immutable pre-backtest
InstitutionalScore anchor is restored, and accidental merge suffixes are
canonicalized before the stable inner pass is re-run.

Very old callers may still feed the pre-EntrySignal flat CSV contract. Those
fixtures remain on the stable legacy postprocessor; the v96 mathematical rewrite
is applied only to the current result contract. This keeps backwards API
compatibility without reintroducing legacy math into production output.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import analytics_core as _core
import model_calibration as _model_calibration

CALIBRATION_MATH_VERSION = (
    "2026-08-23-v97-fixed-objective-single-reliability-idempotent-v3"
)

_INSTALLED = False
_ORIGINAL_LEGACY_APPLY: Any = None
_V96_DERIVED_COLUMNS = frozenset(
    {
        "BacktestObjectiveScoreFixed",
        "BacktestObjectiveScaleVersion",
        "BacktestEvidenceScoreRaw",
        "BacktestLocalEvidenceWeight",
        "BacktestPeerEvidenceWeight",
        "FailureSignalDiagnosticFactor",
        "FailurePenaltyApplied",
        "CalibrationMathVersion",
        "AssetPercentile",
        "CrossAssetAdjustment",
        "CrossAssetScore",
        "InstitutionalPercentile",
        "InstitutionalRank",
        "InstitutionalTier",
        "InstitutionalTierReason",
        "RankingScore",
        "OverallRank",
        "RankingEligibility",
        "RankingReason",
    }
)


def fixed_objective_score(values: pd.Series, objective: str) -> pd.Series:
    """Map objective values to a stable 0..100 economic scale."""
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
        return ((numeric.clip(-25.0, 35.0) + 25.0) / 60.0 * 100.0).clip(
            0.0, 100.0
        )
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


def _numeric(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    values = frame.get(column, pd.Series(default, index=frame.index))
    return pd.to_numeric(values, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )


def _current_result_contract() -> bool:
    """Return whether AllResults uses the current lifecycle/result schema."""
    path = _core.OUTPUT_DIR / "AllResults.csv"
    if not path.exists():
        return False
    try:
        columns = pd.read_csv(path, encoding="utf-8-sig", nrows=0).columns
    except (OSError, ValueError, UnicodeError):
        return False
    # EntrySignal is the decisive boundary: old public flat fixtures predate the
    # Setup/Trigger/Execution lifecycle. Every current scanner export owns it.
    return "EntrySignal" in columns


def _canonicalize_merge_suffixes(frame: pd.DataFrame) -> pd.DataFrame:
    """Collapse pandas merge suffixes in bulk, preferring the newest (_y) data."""
    result = frame.copy()
    bases = {
        column[:-2]
        for column in result.columns
        if column.endswith("_x") or column.endswith("_y")
    }
    for base in bases:
        left = f"{base}_x"
        right = f"{base}_y"
        if base in result.columns:
            result = result.drop(
                columns=[column for column in (left, right) if column in result.columns]
            )
            continue
        right_values = result[right] if right in result.columns else None
        left_values = result[left] if left in result.columns else None
        if right_values is not None and left_values is not None:
            result[base] = right_values.combine_first(left_values)
        elif right_values is not None:
            result[base] = right_values
        elif left_values is not None:
            result[base] = left_values
        result = result.drop(
            columns=[column for column in (left, right) if column in result.columns]
        )
    return result


def _sanitize_previous_output() -> None:
    """Restore the immutable pre-backtest surface before a repeated pass."""
    path = _core.OUTPUT_DIR / "AllResults.csv"
    if not path.exists():
        return
    frame = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    if frame.empty:
        return
    frame = _canonicalize_merge_suffixes(frame)
    anchor = _numeric(frame, "PreBacktestInstitutionalScore")
    current = _numeric(frame, "InstitutionalScore")
    if anchor.notna().any():
        frame["InstitutionalScore"] = anchor.where(anchor.notna(), current)
    frame = frame.drop(
        columns=[column for column in _V96_DERIVED_COLUMNS if column in frame.columns],
        errors="ignore",
    )
    from report import _atomic_write_csv

    _atomic_write_csv(frame, path)


def _rewrite_calibration_math(summary: Any) -> None:
    """Rewrite only the evidence-combination layer after stable postprocess."""
    if not hasattr(summary, "objective"):
        return
    path = _core.OUTPUT_DIR / "AllResults.csv"
    if not path.exists():
        return

    frame = pd.read_csv(path, encoding="utf-8-sig", low_memory=False).copy()
    if frame.empty or "BacktestScore" not in frame.columns:
        return
    frame = _canonicalize_merge_suffixes(frame)

    objective_raw = _numeric(frame, "BacktestObjectiveValue")
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
    peer_weight = peer_weight.where(peer_confidence.gt(0.0), 0.0)

    evidence_total = local_weight + peer_weight
    evidence_score = pd.Series(
        float(_core.BACKTEST_NEUTRAL_SCORE), index=frame.index, dtype=float
    )
    has_evidence = evidence_total.gt(0.0)
    evidence_score.loc[has_evidence] = (
        backtest_component.loc[has_evidence] * local_weight.loc[has_evidence]
        + peer_score.loc[has_evidence] * peer_weight.loc[has_evidence]
    ) / evidence_total.loc[has_evidence]

    effective_weight = pd.Series(
        np.maximum(
            local_weight.to_numpy(dtype=float),
            peer_weight.to_numpy(dtype=float),
        ),
        index=frame.index,
        dtype=float,
    ).clip(
        0.0,
        max(
            float(_core.BACKTEST_NORMAL_WEIGHT),
            float(_core.GLOBAL_CALIBRATION_MAX_WEIGHT),
        ),
    )

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
    frame["FailureAdjustedScore"] = composite.round(4)

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
    """Install v97 math beneath the public transactional ranking facade."""
    global _INSTALLED, _ORIGINAL_LEGACY_APPLY
    if _INSTALLED:
        analytics_module.calibration_stability_stats = single_shrink_stability_stats
        return
    original = getattr(analytics_module, "_legacy_apply_backtest_ranking", None)
    if not callable(original):
        return
    _ORIGINAL_LEGACY_APPLY = original

    def legacy_apply_backtest_ranking(summary: Any, top_n: int = 50) -> None:
        current_contract = _current_result_contract()
        _sanitize_previous_output()
        _ORIGINAL_LEGACY_APPLY(summary, top_n=top_n)
        if current_contract:
            _rewrite_calibration_math(summary)

    analytics_module._legacy_apply_backtest_ranking = legacy_apply_backtest_ranking
    analytics_module.calibration_stability_stats = single_shrink_stability_stats
    analytics_module.CALIBRATION_MATH_VERSION = CALIBRATION_MATH_VERSION
    _INSTALLED = True


install(_core)
