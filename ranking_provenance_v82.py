"""v82 observational provenance for the multiplicative ranking chain.

The lifecycle engine can reconcile ``DecisionState`` again after ``RankingScore``
has already been calculated.  Consequently the final exported decision is not
necessarily the state whose 1.00/0.88/0.55 multiplier entered the score.

This module reconstructs that *ranking-time* multiplier from the exported score
chain, snaps it to the only allowed decision levels and stamps explicit audit
fields.  It is observational only: no score, tier, decision or threshold is
modified.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import ENTRY_SIGNAL_MULTIPLIERS

RANKING_DECISION_PROVENANCE_VERSION = (
    "2026-08-21-v82-ranking-decision-provenance-v1"
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


def stamp_ranking_decision_provenance(frame: pd.DataFrame) -> pd.DataFrame:
    """Stamp the exact decision factor implied by the published score chain."""
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

    denominator = base * entry * hard * chase * data * recency * readiness
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
    result["RankingDecisionInferenceAbsError"] = error.round(6)
    result["RankingFormulaReconstructionAbsError"] = (
        ranking - reconstructed
    ).abs().round(6)
    result["RankingDecisionProvenanceVersion"] = (
        RANKING_DECISION_PROVENANCE_VERSION
    )
    return result
