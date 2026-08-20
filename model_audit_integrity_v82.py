"""v82 correction layer for full-universe decision-factor perturbation audits.

``signal_lifecycle_core`` applies a research-tier reconciliation *after* the
initial DecisionState multiplier has already entered RankingScore.  The original
v82 audit inferred Decision directly from the final score and therefore could
mistake the 0.88 tier-demotion factor for an OBSERVE Decision factor.

This installer keeps the existing audit/report machinery but replaces only the
scenario-construction step.  It consumes the same explicit ranking provenance
used by canonical outputs, so Decision and tier reconciliation are audited as
separate multiplicative legs.  No production score is changed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import model_audit as _audit
from ranking_provenance_v82 import stamp_ranking_decision_provenance

MODEL_AUDIT_INTEGRITY_VERSION = (
    "2026-08-21-v82-decision-tier-separation-v2"
)
_LEGACY_BUILD_SCENARIOS = _audit.build_scenarios
_INSTALLED = False


def _factor(values: pd.Series, default: float = 1.0) -> pd.Series:
    return (
        pd.to_numeric(values, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .fillna(default)
        .clip(lower=1e-9)
    )


def build_scenarios(frame: pd.DataFrame):
    """Run the stable audit, then correct Decision/Tier decomposition."""
    scenarios, diagnostics = _LEGACY_BUILD_SCENARIOS(frame)
    stamped = stamp_ranking_decision_provenance(frame)

    ranking = _audit._number(frame, "RankingScore", np.nan)
    readiness = _factor(
        frame.get("ReadinessPenaltyFactor", pd.Series(1.0, index=frame.index))
    )
    decision = _factor(
        stamped.get("RankingDecisionFactor", pd.Series(1.0, index=frame.index))
    )
    tier = _factor(
        stamped.get(
            "RankingTierReconciliationFactor",
            pd.Series(1.0, index=frame.index),
        )
    )

    corrected = []
    for scenario in scenarios:
        if scenario.name == "no_decision":
            corrected.append(
                _audit.Scenario(
                    scenario.name,
                    "Remove ranking-time Decision factor; retain tier reconciliation",
                    _audit._safe_divide(ranking, decision),
                )
            )
        elif scenario.name == "no_readiness_or_decision":
            corrected.append(
                _audit.Scenario(
                    scenario.name,
                    "Remove Readiness and ranking-time Decision; retain tier reconciliation",
                    _audit._safe_divide(
                        _audit._safe_divide(ranking, readiness), decision
                    ),
                )
            )
        else:
            corrected.append(scenario)

    diagnostics["InferredDecisionFactor"] = decision
    diagnostics["DecisionInferenceAbsError"] = pd.to_numeric(
        stamped.get(
            "RankingDecisionInferenceAbsError",
            pd.Series(np.nan, index=frame.index),
        ),
        errors="coerce",
    )
    diagnostics["RankingTierReconciliationFactor"] = tier
    diagnostics["RankingTierReconciliationState"] = stamped.get(
        "RankingTierReconciliationState",
        pd.Series("NONE", index=frame.index),
    ).astype(str)
    diagnostics["RankingDecisionStateAtScore"] = stamped.get(
        "RankingDecisionStateAtScore",
        pd.Series("UNKNOWN", index=frame.index),
    ).astype(str)

    reconstructed = (
        diagnostics["CrossAssetScore"]
        * diagnostics["EntryFactor"]
        * diagnostics["HardRiskFactor"]
        * diagnostics["ChaseRiskFactor"]
        * diagnostics["DataConfidenceFactor"]
        * diagnostics["RecencyMultiplier"]
        * diagnostics["ReadinessPenaltyFactor"]
        * decision
        * tier
    )
    diagnostics["ReconstructedRankingScore"] = reconstructed
    diagnostics["ReconstructionAbsError"] = (ranking - reconstructed).abs()
    diagnostics["ModelAuditIntegrityVersion"] = MODEL_AUDIT_INTEGRITY_VERSION
    return corrected, diagnostics


def install() -> None:
    """Install the decomposition correction once on the model_audit module."""
    global _INSTALLED
    if _INSTALLED:
        return
    _audit._legacy_build_scenarios_v82 = _LEGACY_BUILD_SCENARIOS
    _audit.build_scenarios = build_scenarios
    _audit.AUDIT_VERSION = MODEL_AUDIT_INTEGRITY_VERSION
    _audit.MODEL_AUDIT_INTEGRITY_VERSION = MODEL_AUDIT_INTEGRITY_VERSION
    _INSTALLED = True
