from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

import config
import report
import signal_lifecycle_core as lifecycle_core


class V40OutputSemanticsTests(unittest.TestCase):
    @staticmethod
    def _ranked_fixture() -> pd.DataFrame:
        rows = [
            {
                "Ticker": "000001.SZ", "Name": "RankLeader", "AssetType": "stock", "IsETF": False,
                "Industry": "行业A", "ModelClassification": "行业A", "RankingScore": 99.0,
                "OpportunityScore": 20.0, "EntrySignal": "WAIT_PULLBACK", "EntrySignalPriority": 3.0,
                "EntryScore": 60.0, "BreakoutScore": 80.0, "PriceBreakout": False,
                "BreakoutVolumeConfirmed": True, "BreakoutFlowConfirmed": True,
                "RankingEligibility": "观察", "SignalDays": 2, "Error": "",
            },
            {
                "Ticker": "000002.SZ", "Name": "OpportunityLeader", "AssetType": "stock", "IsETF": False,
                "Industry": "行业B", "ModelClassification": "行业B", "RankingScore": 55.0,
                "OpportunityScore": 95.0, "EntrySignal": "BUY_NOW", "EntrySignalPriority": 5.0,
                "EntryScore": 95.0, "BreakoutScore": 20.0, "PriceBreakout": False,
                "BreakoutVolumeConfirmed": False, "BreakoutFlowConfirmed": False,
                "RankingEligibility": "观察", "SignalDays": 1, "Error": "",
            },
            {
                "Ticker": "000003.SZ", "Name": "BreakoutLeader", "AssetType": "stock", "IsETF": False,
                "Industry": "行业C", "ModelClassification": "行业C", "RankingScore": 65.0,
                "OpportunityScore": 50.0, "EntrySignal": "BREAKOUT_CONFIRM", "EntrySignalPriority": 4.0,
                "EntryScore": 75.0, "BreakoutScore": 100.0, "PriceBreakout": True,
                "BreakoutVolumeConfirmed": True, "BreakoutFlowConfirmed": True,
                "RankingEligibility": "谨慎候选", "SignalDays": 3, "Error": "",
            },
            {
                "Ticker": "000004.SZ", "Name": "FakeBreakout", "AssetType": "stock", "IsETF": False,
                "Industry": "行业D", "ModelClassification": "行业D", "RankingScore": 70.0,
                "OpportunityScore": 40.0, "EntrySignal": "WAIT_PULLBACK", "EntrySignalPriority": 3.0,
                "EntryScore": 80.0, "BreakoutScore": 99.0, "PriceBreakout": False,
                "BreakoutVolumeConfirmed": True, "BreakoutFlowConfirmed": True,
                "RankingEligibility": "观察", "SignalDays": 4, "Error": "",
            },
            {
                "Ticker": "000005.SZ", "Name": "Sustained", "AssetType": "stock", "IsETF": False,
                "Industry": "行业E", "ModelClassification": "行业E", "RankingScore": 50.0,
                "OpportunityScore": 80.0, "EntrySignal": "HOLD_WAIT", "EntrySignalPriority": 2.0,
                "EntryScore": 30.0, "BreakoutScore": 30.0, "PriceBreakout": False,
                "BreakoutVolumeConfirmed": False, "BreakoutFlowConfirmed": False,
                "RankingEligibility": "观察", "SignalDays": 12, "Error": "",
            },
            {
                "Ticker": "000006.SZ", "Name": "RiskHighScore", "AssetType": "stock", "IsETF": False,
                "Industry": "行业F", "ModelClassification": "行业F", "RankingScore": 120.0,
                "OpportunityScore": 120.0, "EntrySignal": "BUY_NOW", "EntrySignalPriority": 5.0,
                "EntryScore": 100.0, "BreakoutScore": 100.0, "PriceBreakout": True,
                "BreakoutVolumeConfirmed": True, "BreakoutFlowConfirmed": True,
                "RankingEligibility": "风险过滤", "SignalDays": 20, "ValueTrapRisk": 95.0, "Error": "",
            },
        ]
        return pd.DataFrame(rows)

    def test_specialized_candidate_views_rank_by_purpose_and_keep_risk_out(self):
        frame = self._ranked_fixture()
        with TemporaryDirectory() as temp_dir, patch.object(
            report, "_rank_valid_candidates", return_value=frame.copy()
        ):
            output_dir = Path(temp_dir)
            report.refresh_candidate_exports(
                frame, top_n_csv=3, top_n_parquet=3, output_dir=output_dir
            )
            mixed = pd.read_csv(output_dir / "Top3Mixed.csv", encoding="utf-8-sig")
            opportunity = pd.read_csv(output_dir / "Top3Opportunity.csv", encoding="utf-8-sig")
            entry = pd.read_csv(output_dir / "Top3EntryCandidates.csv", encoding="utf-8-sig")
            breakout = pd.read_csv(output_dir / "Top3BreakoutCandidates.csv", encoding="utf-8-sig")
            sustained = pd.read_csv(output_dir / "Top3SustainedSignals.csv", encoding="utf-8-sig")

        self.assertEqual(mixed.loc[0, "Ticker"], "000001.SZ")
        self.assertNotIn("000006.SZ", mixed["Ticker"].tolist())
        self.assertEqual(opportunity.loc[0, "Ticker"], "000002.SZ")
        self.assertNotIn("000006.SZ", opportunity["Ticker"].tolist())
        self.assertEqual(entry.loc[0, "Ticker"], "000002.SZ")
        self.assertEqual(breakout["Ticker"].tolist(), ["000003.SZ"])
        self.assertEqual(sustained.loc[0, "Ticker"], "000005.SZ")
        self.assertNotIn("000006.SZ", sustained["Ticker"].tolist())
        self.assertEqual(opportunity["CandidateView"].unique().tolist(), ["OPPORTUNITY"])
        self.assertEqual(opportunity["CandidateViewRank"].tolist(), [1, 2, 3])
        self.assertEqual(opportunity["ResearchPoolRank"].tolist(), [1, 2, 3])

    def test_final_explanation_sync_removes_stale_b_tier_wording(self):
        frame = pd.DataFrame([
            {
                "RankingReason": "B级量价资金突破确认，谨慎候选；回测样本不足，不参与校准",
                "RankingPenaltyReason": "B级仅列谨慎候选",
                "TradeReadinessReason": "买点、质量、数据与综合评分均满足执行条件",
                "BacktestEligibleForRanking": False,
                "BacktestConfidenceTier": "样本不足",
                "BacktestStatus": "SAMPLES",
            }
        ])
        strong = pd.Series([True])
        cautious = pd.Series([False])
        override = pd.Series([False])
        lifecycle_core._sync_final_explanations(frame, strong, cautious, override)
        self.assertNotIn("谨慎候选", str(frame.loc[0, "RankingReason"]))
        self.assertIn("买点、质量、数据与综合评分均满足执行条件", str(frame.loc[0, "RankingReason"]))
        self.assertIn("回测样本不足", str(frame.loc[0, "RankingReason"]))
        self.assertNotIn("B级仅列谨慎候选", str(frame.loc[0, "RankingPenaltyReason"]))

    def test_integrity_gate_rejects_recommended_row_with_stale_cautious_reason(self):
        frame = pd.DataFrame([
            {
                "Ticker": "000001.SZ", "ResearchEligible": True, "HardGatePassed": True,
                "RankingEligibility": "推荐", "SignalStatus": "WATCH", "SignalDays": 2,
                "QualityReason": "行业自适应硬门槛通过", "QualityGate": True,
                "RankingReason": "B级量价资金突破确认，谨慎候选",
                "RankingPenaltyReason": "B级仅列谨慎候选",
            }
        ])
        with self.assertRaisesRegex(ValueError, "stale cautious explanation"):
            report.validate_decision_integrity(frame)

    def test_v40_is_pipeline_only_and_does_not_invalidate_v39_scoring_cache(self):
        self.assertIn("v39", config.SCORING_VERSION)
        self.assertNotIn("v40", config.SCORING_VERSION)
        self.assertIn("v40", config.PIPELINE_VERSION)
        self.assertIn("v40", config.DECISION_INTEGRITY_VERSION)
        self.assertIn("v40", config.OUTPUT_CONTRACT_VERSION)
        self.assertIn("v38", config.FUNDAMENTAL_GATE_VERSION)


if __name__ == "__main__":
    unittest.main()
