from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

import config
import gui
import report
import signal_lifecycle


class V41OutputIntegrityTests(unittest.TestCase):
    @staticmethod
    def _ranked_fixture() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "Ticker": "000001.SZ",
                    "Name": "股票A",
                    "AssetType": "stock",
                    "IsETF": False,
                    "Industry": "行业A",
                    "RankingScore": 90.0,
                    "InstitutionalScore": 60.0,
                    "FinalScore": 60.0,
                    "EntrySignal": "WAIT_PULLBACK",
                    "RankingEligibility": "观察",
                    "DecisionState": "OBSERVE",
                    "TradeReadiness": "观察",
                    "HardGatePassed": True,
                    "UniverseEligible": True,
                    "QualityApplicable": True,
                    "QualityGate": True,
                    "Error": "",
                },
                {
                    "Ticker": "510001.SH",
                    "Name": "ETF-A",
                    "AssetType": "etf",
                    "IsETF": True,
                    "Industry": "主题A",
                    "RankingScore": 80.0,
                    "InstitutionalScore": 55.0,
                    "FinalScore": 55.0,
                    "EntrySignal": "WAIT_PULLBACK",
                    "RankingEligibility": "观察",
                    "DecisionState": "OBSERVE",
                    "TradeReadiness": "观察",
                    "HardGatePassed": True,
                    "UniverseEligible": True,
                    "QualityApplicable": False,
                    "QualityGate": True,
                    "Error": "",
                },
                {
                    "Ticker": "000002.SZ",
                    "Name": "股票B",
                    "AssetType": "stock",
                    "IsETF": False,
                    "Industry": "行业B",
                    "RankingScore": 70.0,
                    "InstitutionalScore": 50.0,
                    "FinalScore": 50.0,
                    "EntrySignal": "WAIT_PULLBACK",
                    "RankingEligibility": "观察",
                    "DecisionState": "OBSERVE",
                    "TradeReadiness": "观察",
                    "HardGatePassed": True,
                    "UniverseEligible": True,
                    "QualityApplicable": True,
                    "QualityGate": False,
                    "Error": "",
                },
            ]
        )

    def test_every_candidate_format_uses_one_metadata_schema(self):
        captured_parquet: list[pd.DataFrame] = []

        def capture_parquet(frame: pd.DataFrame, _path: Path) -> None:
            captured_parquet.append(frame.copy())

        with TemporaryDirectory() as temp_dir, patch.object(
            report, "_atomic_write_parquet", side_effect=capture_parquet
        ):
            destination = Path(temp_dir)
            report.refresh_candidate_exports(
                self._ranked_fixture(),
                top_n_csv=3,
                top_n_parquet=3,
                output_dir=destination,
            )
            candidates = [
                pd.read_csv(destination / name, encoding="utf-8-sig")
                for name in ("Top3.csv", "Top3Mixed.csv", "Top3Stocks.csv", "Top3ETF.csv")
            ]

        expected_tail = [
            "CandidateView",
            "CandidateViewRank",
            "ResearchPoolRank",
            "ResearchDiversityPenalty",
        ]
        self.assertEqual(len(captured_parquet), 1)
        parquet = captured_parquet[0]
        self.assertEqual(parquet.columns[-4:].tolist(), expected_tail)
        self.assertEqual(parquet["CandidateView"].unique().tolist(), ["RANKED_RESEARCH"])
        self.assertEqual(parquet["CandidateViewRank"].tolist(), [1, 2, 3])
        for candidate in candidates:
            self.assertEqual(candidate.columns[-4:].tolist(), expected_tail)
            self.assertEqual(
                candidate["CandidateViewRank"].tolist(),
                list(range(1, len(candidate) + 1)),
            )
        self.assertEqual(candidates[0].columns.tolist(), candidates[1].columns.tolist())

    def test_integrity_gate_rejects_state_and_actionability_conflicts(self):
        mismatch = self._ranked_fixture().head(1).copy()
        mismatch.loc[0, "RankingEligibility"] = "推荐"
        with self.assertRaisesRegex(ValueError, "decision state disagrees"):
            report.validate_decision_integrity(mismatch)

        quality_fail = self._ranked_fixture().tail(1).copy()
        quality_fail.loc[quality_fail.index[0], "DecisionState"] = "READY"
        quality_fail.loc[quality_fail.index[0], "RankingEligibility"] = "推荐"
        quality_fail.loc[quality_fail.index[0], "TradeReadiness"] = "推荐"
        with self.assertRaisesRegex(ValueError, "failed the quality gate"):
            report.validate_decision_integrity(quality_fail)

    def test_final_action_copy_cannot_say_follow_trend_after_quality_block(self):
        frame = pd.DataFrame(
            [
                {
                    "DecisionState": "OBSERVE",
                    "ActionSuggestion": "顺势跟踪",
                    "RiskNote": "结构仍需确认",
                    "TradeReadinessReason": "质量门槛未通过或数据不足，转为观察",
                    "HardRiskReason": "",
                }
            ]
        )
        index = frame.index
        signal_lifecycle._sync_final_action_text(
            frame,
            signal=pd.Series("BUY_NOW", index=index),
            strong_ready=pd.Series(False, index=index),
            cautious_ready=pd.Series(False, index=index),
            quality_action_block=pd.Series(True, index=index),
            terminal=pd.Series(False, index=index),
            weakening=pd.Series(False, index=index),
        )
        self.assertEqual(frame.loc[0, "ActionSuggestion"], "仅研究观察，等待基本面改善")
        self.assertEqual(frame.loc[0, "RiskNote"], "质量门槛未通过或数据不足，转为观察")

        frame.loc[0, "ActionSuggestion"] = "顺势跟踪"
        signal_lifecycle._sync_final_action_text(
            frame,
            signal=pd.Series("HOLD_WAIT", index=index),
            strong_ready=pd.Series(False, index=index),
            cautious_ready=pd.Series(False, index=index),
            quality_action_block=pd.Series(False, index=index),
            terminal=pd.Series(False, index=index),
            weakening=pd.Series(False, index=index),
        )
        self.assertEqual(frame.loc[0, "ActionSuggestion"], "继续观察，等待条件改善")

    def test_v41_changes_output_contract_without_changing_scoring_boundary(self):
        self.assertIn("v41", config.PIPELINE_VERSION)
        self.assertIn("v41", config.DECISION_INTEGRITY_VERSION)
        self.assertIn("v41", config.OUTPUT_CONTRACT_VERSION)
        self.assertIn("v39", config.SCORING_VERSION)
        self.assertNotIn("v41", config.SCORING_VERSION)
        self.assertIn("v38", config.FUNDAMENTAL_GATE_VERSION)
        self.assertEqual(gui.COLUMN_NAMES["EntrySignal"], "技术信号")


if __name__ == "__main__":
    unittest.main()
