"""v82 observational provenance for the multiplicative ranking chain.

The lifecycle engine calculates ``RankingScore`` with an initial DecisionState
factor and may then apply a separate research-tier reconciliation factor before
execution-only gates can demote the final exported DecisionState again.
Consequently the final decision label alone cannot explain which factors entered
the ranking score.

This module reconstructs the initial 1.00/0.88/0.55 Decision factor while
explicitly separating the later 1.00/0.94/0.88 tier reconciliation. It is
observational only: no score, tier, decision or threshold is modified.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import ENTRY_SIGNAL_MULTIPLIERS

RANKING_DECISION_PROVENANCE_VERSION = (
    "2026-08-21-v82-ranking-decision-provenance-v2"
)
_DECISION_LEVELS = np.asarray([0.55, 0.88, 1.00], dtype=float)
_DECISION_STATES = {0.55: "BLOCKED", 0.88: "OBSERVE", 1.00: "READY"}


def _number(frame: pd.DataFrame, column: str, default: float) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=float)
    return (
        pd.to_numeric(frame[column], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .fillna(default)
        .astype(float)
    )


def _entry_factor(frame: pd.DataFrame) -> pd.Series:
    signal = frame.get(
        "EntrySignal", pd.Series("AVOID", index=frame.index)
    ).fillna("AVOID").astype(str).str.strip().str.upper()
    mapping = {
        str(key).upper(): float(value)
        for key, value in ENTRY_SIGNAL_MULTIPLIERS.items()
    }
    return signal.map(mapping).fillna(float(mapping.get("AVOID", 0.50))).clip(
        1e-9, 1.0
    )


def _tier_reconciliation(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Recover the explicit post-ranking tier adjustment already stamped by lifecycle."""
    reason = frame.get(
        "RankingPenaltyReason", pd.Series("", index=frame.index)
    ).fillna("").astype(str)
    cautious = reason.str.contains("B级仅列谨慎候选", regex=False)
    demoted = reason.str.contains("研究等级未达A级执行门槛", regex=False)

    factor = pd.Series(1.0, index=frame.index, dtype=float)
    state = pd.Series("NONE", index=frame.index, dtype=object)
    factor.loc[cautious] = 0.94
    state.loc[cautious] = "B_TIER_CAUTION"
    factor.loc[demoted] = 0.88
    state.loc[demoted] = "RESEARCH_TIER_DEMOTION"
    return factor, state


def stamp_ranking_decision_provenance(frame: pd.DataFrame) -> pd.DataFrame:
    """Stamp ranking-time Decision and later tier factors without changing output."""
    if frame is None or frame.empty:
        return frame
    result = frame.copy()
    ranking = _number(result, "RankingScore", np.nan)
    base = _number(result, "CrossAssetScore", np.nan)
    entry = _entry_factor(result)
    hard = _number(result, "HardRiskPenalty", 1.0).clip(1e-9, 1.0)
    chase = _number(result, "ChaseRiskFactor", 1.0).clip(1e-9, 1.0)
    data = _number(result, "DataConfidenceFactor", 1.0).clip(1e-9, 1.0)
    recency_factor = _number(result, "SignalRecencyFactor", 1.0).clip(0.7, 1.0)
    recency = (0.8 + 0.2 * recency_factor).clip(1e-9, 1.0)
    readiness = _number(result, "ReadinessPenaltyFactor", 1.0).clip(1e-9, 1.0)
    tier_factor, tier_state = _tier_reconciliation(result)

    denominator = (
        base * entry * hard * chase * data * recency * readiness * tier_factor
    )
    raw = pd.Series(np.nan, index=result.index, dtype=float)
    valid = (
        ranking.notna()
        & np.isfinite(ranking)
        & denominator.notna()
        & np.isfinite(denominator)
        & denominator.gt(1e-12)
    )
    raw.loc[valid] = ranking.loc[valid] / denominator.loc[valid]

    snapped = pd.Series(np.nan, index=result.index, dtype=float)
    error = pd.Series(np.nan, index=result.index, dtype=float)
    if valid.any():
        values = raw.loc[valid].to_numpy(dtype=float)
        nearest = np.abs(values[:, None] - _DECISION_LEVELS[None, :]).argmin(axis=1)
        chosen = _DECISION_LEVELS[nearest]
        snapped.loc[valid] = chosen
        error.loc[valid] = np.abs(values - chosen)

    state = snapped.map(_DECISION_STATES).fillna("UNKNOWN")
    reconstructed = denominator * snapped.fillna(1.0)

    result["RankingDecisionFactorRaw"] = raw.round(6)
    result["RankingDecisionFactor"] = snapped.round(4)
    result["RankingDecisionStateAtScore"] = state
    result["RankingTierReconciliationFactor"] = tier_factor.round(4)
    result["RankingTierReconciliationState"] = tier_state
    result["RankingDecisionInferenceAbsError"] = error.round(6)
    result["RankingFormulaReconstructionAbsError"] = (
        ranking - reconstructed
    ).abs().round(6)
    result["RankingDecisionProvenanceVersion"] = (
        RANKING_DECISION_PROVENANCE_VERSION
    )
    return result
