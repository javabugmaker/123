from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from ranking_architecture_v83 import (
    _legacy_breakout_price_component,
    _smooth_breakout_price_component,
    stamp_layered_ranking,
)


class LayeredRankingV83Tests(unittest.TestCase):
    def _frame(self) -> pd.DataFrame:
        # Runtime-calibrated signature intentionally differs from config defaults.
        signature = "0.6000:0.1500:0.2500"
        return pd.DataFrame(
            {
                "Ticker": ["000001.SZ", "000002.SZ", "510300.SH", "510500.SH"],
                "IsETF": [False, False, True, True],
                "AssetType": ["stock", "stock", "etf", "etf"],
                "BaseScore": [60.0, 55.0, 50.0, 50.0],
                "TriggerScore": [80.0, 70.0, 60.0, 40.0],
                "ExecutionScore": [40.0, 40.0, 40.0, 40.0],
                "ScoreCoverage": [1.0, 1.0, 1.0, 1.0],
                "FinalScore": [58.0, 53.5, 49.0, 46.0],
                "ModelWeightSignature": [signature] * 4,
                # Legacy penalties intentionally reverse the two stock rows.
                "RankingScore": [20.0, 40.0, 35.0, 30.0],
                "DecisionState": ["OBSERVE", "READY", "READY", "OBSERVE"],
                "HardGatePassed": [True, False, True, True],
                "QualityHardDataComplete": [True, True, True, True],
                "QualityGate": [False, True, True, True],
                "QualityScore": [40.0, 80.0, np.nan, np.nan],
                "Close": [10.001, 9.999, 4.001, 3.999],
                "BreakoutBuyPrice": [10.0, 10.0, 4.0, 4.0],
            }
        )

    def test_stamp_is_observational_for_production_fields(self) -> None:
        source = self._frame()
        legacy_ranking = source["RankingScore"].copy()
        legacy_decision = source["DecisionState"].copy()
        legacy_trigger = source["TriggerScore"].copy()

        result = stamp_layered_ranking(source)

        pd.testing.assert_series_equal(result["RankingScore"], legacy_ranking)
        pd.testing.assert_series_equal(result["DecisionState"], legacy_decision)
        pd.testing.assert_series_equal(result["TriggerScore"], legacy_trigger)

    def test_research_rank_uses_alpha_not_readiness_penalties(self) -> None:
        result = stamp_layered_ranking(self._frame())

        self.assertEqual(int(result.loc[0, "ResearchRank"]), 1)
        self.assertEqual(int(result.loc[1, "ResearchRank"]), 2)
        self.assertEqual(float(result.loc[0, "ResearchPercentile"]), 100.0)
        self.assertEqual(int(result.loc[0, "LegacyRankingAssetRank"]), 2)
        self.assertEqual(int(result.loc[1, "LegacyRankingAssetRank"]), 1)
        self.assertEqual(int(result.loc[0, "ResearchVsLegacyRankDelta"]), 1)
        self.assertEqual(result.loc[0, "QualityLayerStatus"], "POLICY_FAIL")

    def test_trade_rank_is_state_then_alpha_within_asset(self) -> None:
        result = stamp_layered_ranking(self._frame())

        # READY stock ranks ahead of higher-alpha OBSERVE stock for execution.
        self.assertEqual(int(result.loc[1, "TradeRank"]), 1)
        self.assertEqual(int(result.loc[0, "TradeRank"]), 2)
        # ETF ranking is independent from the stock pool.
        self.assertEqual(int(result.loc[2, "TradeRank"]), 1)
        self.assertEqual(int(result.loc[3, "TradeRank"]), 2)
        self.assertFalse(bool(result.loc[1, "ExecutionHardGatePassed"]))

    def test_quality_data_integrity_is_not_mislabeled_as_hard_policy_gate(self) -> None:
        source = self._frame()
        source.loc[1, "QualityHardDataComplete"] = False
        result = stamp_layered_ranking(source)

        self.assertFalse(bool(result.loc[1, "QualityDataIntegrityPassed"]))
        self.assertTrue(bool(result.loc[1, "QualityPolicyGatePassed"]))
        self.assertEqual(result.loc[1, "QualityLayerStatus"], "DATA_INCOMPLETE")

    def test_runtime_weight_signature_reconstructs_canonical_alpha(self) -> None:
        result = stamp_layered_ranking(self._frame())

        self.assertAlmostEqual(float(result.loc[0, "AlphaSetupWeight"]), 0.60)
        self.assertAlmostEqual(float(result.loc[0, "AlphaTriggerWeight"]), 0.15)
        self.assertAlmostEqual(float(result.loc[0, "AlphaExecutionWeight"]), 0.25)
        self.assertAlmostEqual(float(result.loc[0, "AlphaScore"]), 58.0)
        self.assertLess(float(result.loc[0, "AlphaFormulaReconstructionAbsError"]), 1e-9)

    def test_smooth_breakout_removes_zero_percent_cliff_and_matches_edges(self) -> None:
        clearance = np.array([-0.5, -0.001, 0.0, 0.001, 0.5], dtype=float)
        legacy = _legacy_breakout_price_component(clearance)
        smooth, confirmation = _smooth_breakout_price_component(clearance)

        self.assertAlmostEqual(float(smooth[0]), float(legacy[0]), places=12)
        self.assertAlmostEqual(float(smooth[-1]), float(legacy[-1]), places=12)
        self.assertGreater(float(legacy[3] - legacy[2]), 20.0)
        self.assertLess(float(np.max(np.abs(np.diff(smooth[1:4])))), 0.1)
        self.assertTrue(np.all(np.diff(confirmation) >= 0.0))


if __name__ == "__main__":
    unittest.main()
