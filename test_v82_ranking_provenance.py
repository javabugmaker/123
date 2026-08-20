from __future__ import annotations

import unittest

import pandas as pd

from backtest_rank_integrity_v82 import install_single_recency_ranking_guard
from model_audit import build_scenarios
from ranking_provenance_v82 import stamp_ranking_decision_provenance


class _DummyAnalyticsModule:
    def finalize_signal_ranking(self, frame: pd.DataFrame) -> pd.DataFrame:
        return frame.copy()


class RankingDecisionProvenanceV82Tests(unittest.TestCase):
    def _frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "Ticker": ["000001.SZ"],
                "CrossAssetScore": [100.0],
                "EntrySignal": ["BUY_NOW"],
                "HardRiskPenalty": [1.0],
                "ChaseRiskFactor": [1.0],
                "DataConfidenceFactor": [1.0],
                "SignalRecencyFactor": [1.0],
                "ReadinessPenaltyFactor": [0.82],
                # 100 * 1.00 * 1 * 1 * 1 * 1 * 0.82 * 0.88
                "RankingScore": [72.16],
                # Final execution reconciliation can differ from the state
                # whose multiplier entered RankingScore.
                "DecisionState": ["READY"],
            }
        )

    def test_stamp_preserves_score_and_final_decision(self) -> None:
        source = self._frame()
        result = stamp_ranking_decision_provenance(source)

        self.assertAlmostEqual(result.loc[0, "RankingScore"], 72.16)
        self.assertEqual(result.loc[0, "DecisionState"], "READY")
        self.assertAlmostEqual(result.loc[0, "RankingDecisionFactor"], 0.88)
        self.assertEqual(result.loc[0, "RankingDecisionStateAtScore"], "OBSERVE")
        self.assertAlmostEqual(result.loc[0, "RankingTierReconciliationFactor"], 1.0)
        self.assertAlmostEqual(
            result.loc[0, "RankingFormulaReconstructionAbsError"], 0.0
        )

    def test_tier_demotion_is_not_mislabelled_as_initial_observe(self) -> None:
        source = self._frame()
        # Same final numeric score can also arise from an initially READY row
        # that is subsequently demoted by the research-tier reconciliation.
        source.loc[0, "DecisionState"] = "OBSERVE"
        source.loc[0, "RankingPenaltyReason"] = "研究等级未达A级执行门槛"
        result = stamp_ranking_decision_provenance(source)

        self.assertAlmostEqual(result.loc[0, "RankingDecisionFactor"], 1.0)
        self.assertEqual(result.loc[0, "RankingDecisionStateAtScore"], "READY")
        self.assertAlmostEqual(result.loc[0, "RankingTierReconciliationFactor"], 0.88)
        self.assertEqual(
            result.loc[0, "RankingTierReconciliationState"],
            "RESEARCH_TIER_DEMOTION",
        )
        self.assertAlmostEqual(
            result.loc[0, "RankingFormulaReconstructionAbsError"], 0.0
        )

    def test_model_audit_retains_tier_factor_when_removing_decision(self) -> None:
        source = self._frame()
        source.loc[0, "DecisionState"] = "OBSERVE"
        source.loc[0, "RankingPenaltyReason"] = "研究等级未达A级执行门槛"
        scenarios, diagnostics = build_scenarios(source)

        self.assertAlmostEqual(diagnostics.loc[0, "InferredDecisionFactor"], 1.0)
        self.assertAlmostEqual(
            diagnostics.loc[0, "RankingTierReconciliationFactor"], 0.88
        )
        no_decision = next(item for item in scenarios if item.name == "no_decision")
        self.assertAlmostEqual(no_decision.score.loc[0], 72.16)
        no_both = next(
            item for item in scenarios if item.name == "no_readiness_or_decision"
        )
        self.assertAlmostEqual(no_both.score.loc[0], 88.0)

    def test_installed_integrity_guard_stamps_live_ranking_output(self) -> None:
        module = _DummyAnalyticsModule()
        install_single_recency_ranking_guard(module)
        result = module.finalize_signal_ranking(self._frame())

        self.assertIn("RankingDecisionFactor", result.columns)
        self.assertIn("RankingDecisionStateAtScore", result.columns)
        self.assertAlmostEqual(result.loc[0, "RankingDecisionFactor"], 0.88)
        self.assertEqual(result.loc[0, "RankingDecisionStateAtScore"], "OBSERVE")
        self.assertEqual(result.loc[0, "DecisionState"], "READY")


if __name__ == "__main__":
    unittest.main()
