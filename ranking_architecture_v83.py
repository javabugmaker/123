"""v83 layered research/execution ranking and smooth-breakout shadow diagnostics.

This module is intentionally observational. It does not rewrite the legacy
``RankingScore``, ``DecisionState`` or production ``TriggerScore``. Instead it
separates research alpha from execution policy and exposes a continuous shadow
breakout calculation so future out-of-sample evidence can decide whether the
legacy cliff should be retired.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import MODEL_EXECUTION_WEIGHT, MODEL_SETUP_WEIGHT, MODEL_TRIGGER_WEIGHT
from execution_integrity_v87 import (
    legacy_breakout_price_component,
    smooth_breakout_price_component,
)

RANKING_ARCHITECTURE_VERSION = "2026-08-21-v83-layered-research-execution-v1"
SMOOTH_TRIGGER_VERSION = "2026-08-21-v83-smooth-breakout-shadow-v1"

_STATE_PRIORITY = {
    "READY": 0,
    "CAUTIOUS": 1,
    "OBSERVE": 2,
    "BLOCKED": 3,
}


def _numeric(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=float)
    return (
        pd.to_numeric(frame[column], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .fillna(default)
    )


def _bool_series(frame: pd.DataFrame, column: str, default: bool = False) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=bool)
    values = frame[column]
    if pd.api.types.is_bool_dtype(values.dtype):
        return values.astype("boolean").fillna(default).astype(bool)
    normalized = values.astype("string").fillna("true" if default else "false")
    return normalized.str.strip().str.lower().isin({"true", "1", "yes", "y", "是"})


def _asset_group(frame: pd.DataFrame) -> pd.Series:
    is_etf = _bool_series(frame, "IsETF", False)
    if "AssetType" in frame.columns:
        is_etf |= (
            frame["AssetType"].fillna("").astype(str).str.strip().str.lower().eq("etf")
        )
    return pd.Series(np.where(is_etf, "ETF", "STOCK"), index=frame.index, dtype=object)


def _model_weights(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Resolve the row's actual accepted model signature without hard-coding defaults."""
    setup = pd.Series(float(MODEL_SETUP_WEIGHT), index=frame.index, dtype=float)
    trigger = pd.Series(float(MODEL_TRIGGER_WEIGHT), index=frame.index, dtype=float)
    execution = pd.Series(float(MODEL_EXECUTION_WEIGHT), index=frame.index, dtype=float)
    if "ModelWeightSignature" not in frame.columns:
        return setup, trigger, execution

    signature = frame["ModelWeightSignature"].fillna("").astype(str).str.strip()
    parts = signature.str.split(":", n=2, expand=True)
    if parts.shape[1] != 3:
        return setup, trigger, execution

    parsed_setup = pd.to_numeric(parts.iloc[:, 0], errors="coerce")
    parsed_trigger = pd.to_numeric(parts.iloc[:, 1], errors="coerce")
    parsed_execution = pd.to_numeric(parts.iloc[:, 2], errors="coerce")
    total = parsed_setup + parsed_trigger + parsed_execution
    valid = (
        parsed_setup.ge(0.0)
        & parsed_trigger.ge(0.0)
        & parsed_execution.ge(0.0)
        & total.sub(1.0).abs().le(1e-4)
    )
    setup = parsed_setup.where(valid, setup)
    trigger = parsed_trigger.where(valid, trigger)
    execution = parsed_execution.where(valid, execution)
    return setup, trigger, execution


def _legacy_breakout_price_component(clearance_pct: np.ndarray) -> np.ndarray:
    """Compatibility alias for the single canonical v87 implementation."""
    return legacy_breakout_price_component(clearance_pct)


def _smooth_breakout_price_component(
    clearance_pct: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Compatibility alias for the single canonical v87 implementation."""
    return smooth_breakout_price_component(clearance_pct)


def _rank_within_asset(score: pd.Series, asset: pd.Series) -> tuple[pd.Series, pd.Series]:
    valid = score.notna() & np.isfinite(score)
    ranked = score.where(valid)
    rank = ranked.groupby(asset, sort=False).rank(method="min", ascending=False)
    # Ascending percentile intentionally makes the strongest score approach 100.
    percentile = (
        ranked.groupby(asset, sort=False).rank(method="average", pct=True) * 100.0
    )
    return rank.astype("Int64"), percentile.round(2)


def _trade_rank(alpha: pd.Series, asset: pd.Series, state: pd.Series) -> pd.Series:
    """Lexicographic rank: execution state first, then research alpha."""
    bucket = state.map(_STATE_PRIORITY).fillna(4).astype(np.int8)
    working = pd.DataFrame(
        {
            "_position": np.arange(len(alpha), dtype=np.int64),
            "_asset": asset.to_numpy(dtype=object),
            "_bucket": bucket.to_numpy(dtype=np.int8),
            "_alpha": alpha.fillna(-np.inf).to_numpy(dtype=np.float64),
        }
    )
    ordered = working.sort_values(
        ["_asset", "_bucket", "_alpha", "_position"],
        ascending=[True, True, False, True],
        kind="mergesort",
    )
    ordered["_rank"] = ordered.groupby("_asset", sort=False).cumcount() + 1
    output = np.empty(len(alpha), dtype=np.int64)
    output[ordered["_position"].to_numpy(dtype=np.int64)] = ordered["_rank"].to_numpy(
        dtype=np.int64
    )
    return pd.Series(output, index=alpha.index, dtype="Int64")


def stamp_layered_ranking(frame: pd.DataFrame) -> pd.DataFrame:
    """Add v83 research/execution fields without changing production decisions."""
    if frame is None or frame.empty:
        return frame
    result = frame.copy()
    asset = _asset_group(result)
    setup_weight, trigger_weight, execution_weight = _model_weights(result)

    base = _numeric(result, "BaseScore", 0.0).clip(0.0, 100.0)
    trigger = _numeric(result, "TriggerScore", 0.0).clip(0.0, 100.0)
    execution = _numeric(result, "ExecutionScore", 0.0).clip(0.0, 100.0)
    coverage = _numeric(result, "ScoreCoverage", 1.0).clip(0.0, 1.0)
    alpha_raw = base * setup_weight + trigger * trigger_weight + execution * execution_weight
    coverage_cap = 40.0 + 60.0 * coverage
    reconstructed_alpha = pd.Series(
        np.minimum(
            alpha_raw.to_numpy(dtype=np.float64),
            coverage_cap.to_numpy(dtype=np.float64),
        ),
        index=result.index,
    ).clip(0.0, 100.0)

    canonical_alpha = _numeric(result, "FinalScore", np.nan)
    canonical_alpha = canonical_alpha.where(canonical_alpha.notna(), reconstructed_alpha)
    result["AlphaScoreRaw"] = alpha_raw.round(4)
    result["AlphaScore"] = canonical_alpha.clip(0.0, 100.0).round(4)
    result["AlphaFormulaReconstructionAbsError"] = (
        reconstructed_alpha.sub(canonical_alpha).abs().round(6)
    )
    result["AlphaSetupWeight"] = setup_weight.round(4)
    result["AlphaTriggerWeight"] = trigger_weight.round(4)
    result["AlphaExecutionWeight"] = execution_weight.round(4)
    result["ResearchAssetClass"] = asset

    research_rank, research_percentile = _rank_within_asset(result["AlphaScore"], asset)
    result["ResearchRank"] = research_rank
    result["ResearchPercentile"] = research_percentile
    result["AssetClassRank"] = research_rank

    legacy_score = _numeric(result, "RankingScore", np.nan)
    legacy_rank, _ = _rank_within_asset(legacy_score, asset)
    result["LegacyRankingAssetRank"] = legacy_rank
    result["ResearchVsLegacyRankDelta"] = (
        legacy_rank.astype("Float64") - research_rank.astype("Float64")
    ).round(0).astype("Int64")

    decision = (
        result.get("DecisionState", pd.Series("OBSERVE", index=result.index))
        .fillna("OBSERVE")
        .astype(str)
        .str.strip()
        .str.upper()
    )
    result["ExecutionState"] = decision
    result["ExecutionPriorityBucket"] = decision.map(_STATE_PRIORITY).fillna(4).astype(int)
    result["ExecutionEligible"] = decision.isin({"READY", "CAUTIOUS"})
    result["ExecutionHardGatePassed"] = _bool_series(result, "HardGatePassed", True)
    result["TradeRank"] = _trade_rank(result["AlphaScore"], asset, decision)

    is_etf = asset.eq("ETF")
    hard_data_complete = _bool_series(result, "QualityHardDataComplete", True)
    quality_policy = _bool_series(result, "QualityGate", True)
    result["QualityDataIntegrityPassed"] = is_etf | hard_data_complete
    result["QualityPolicyGatePassed"] = is_etf | quality_policy
    result["QualityLayerStatus"] = np.select(
        [
            is_etf,
            ~result["QualityDataIntegrityPassed"],
            ~result["QualityPolicyGatePassed"],
        ],
        ["NOT_APPLICABLE", "DATA_INCOMPLETE", "POLICY_FAIL"],
        default="PASS",
    )

    close = _numeric(result, "Close", np.nan)
    breakout = _numeric(result, "BreakoutBuyPrice", np.nan)
    valid_breakout = close.gt(0.0) & breakout.gt(0.0)
    clearance = pd.Series(np.nan, index=result.index, dtype=float)
    clearance.loc[valid_breakout] = (
        close.loc[valid_breakout] / breakout.loc[valid_breakout] - 1.0
    ) * 100.0
    clearance_values = clearance.to_numpy(dtype=np.float64)
    legacy_price = _legacy_breakout_price_component(clearance_values)
    smooth_price, confirmation = _smooth_breakout_price_component(clearance_values)
    legacy_price[~valid_breakout.to_numpy(dtype=bool)] = 0.0
    smooth_price[~valid_breakout.to_numpy(dtype=bool)] = 0.0
    confirmation[~valid_breakout.to_numpy(dtype=bool)] = 0.0

    trigger_coverage = 0.75 + 0.25 * coverage.to_numpy(dtype=np.float64)
    trigger_delta = (smooth_price - legacy_price) * trigger_coverage
    smooth_trigger = np.clip(
        trigger.to_numpy(dtype=np.float64) + trigger_delta,
        0.0,
        100.0,
    )
    result["BreakoutClearancePct"] = clearance.round(4)
    result["LegacyBreakoutPriceComponent"] = np.round(legacy_price, 4)
    result["SmoothBreakoutPriceComponent"] = np.round(smooth_price, 4)
    result["BreakoutPriceConfirmationScore"] = np.round(confirmation, 2)
    result["SmoothTriggerScore"] = np.round(smooth_trigger, 4)
    result["SmoothTriggerDelta"] = np.round(
        smooth_trigger - trigger.to_numpy(dtype=np.float64), 4
    )
    result["SmoothTriggerApproximate"] = trigger.ge(99.999)

    smooth_alpha_raw = (
        base.to_numpy(dtype=np.float64) * setup_weight.to_numpy(dtype=np.float64)
        + smooth_trigger * trigger_weight.to_numpy(dtype=np.float64)
        + execution.to_numpy(dtype=np.float64) * execution_weight.to_numpy(dtype=np.float64)
    )
    smooth_alpha = np.minimum(
        smooth_alpha_raw,
        coverage_cap.to_numpy(dtype=np.float64),
    )
    result["SmoothAlphaScore"] = np.round(np.clip(smooth_alpha, 0.0, 100.0), 4)
    smooth_rank, smooth_pct = _rank_within_asset(result["SmoothAlphaScore"], asset)
    result["SmoothResearchRank"] = smooth_rank
    result["SmoothResearchPercentile"] = smooth_pct
    result["SmoothResearchRankDelta"] = (
        research_rank.astype("Float64") - smooth_rank.astype("Float64")
    ).round(0).astype("Int64")

    result["RankingArchitectureVersion"] = RANKING_ARCHITECTURE_VERSION
    result["SmoothTriggerVersion"] = SMOOTH_TRIGGER_VERSION
    result["ResearchRankingPolicy"] = "ALPHA_ONLY_WITHIN_ASSET"
    result["TradeRankingPolicy"] = "EXECUTION_STATE_THEN_ALPHA_WITHIN_ASSET"
    return result
