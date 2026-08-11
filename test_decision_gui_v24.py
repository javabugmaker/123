from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import pandas as pd

import gui
import report
import signal_lifecycle
from config import SCORING_VERSION


# These regressions preserve the public v24 tier/execution contract while later
# model versions may improve how scores are constructed and normalized.
class DecisionGuiV24Tests(TestCase):
    @staticmethod
    def _decision_frame() -> pd.DataFrame:
        scores = [90.0, 80.0, 70.0, 60.0, 50.0, 40.0, 35.0, 32.0, 28.0, 20.0]
        return pd.DataFrame(
            {
                "Ticker": [f"TEST{i:02d}.SZ" for i in range(len(scores))],
                "Score": scores,
                "FinalScore": scores,
                "InstitutionalScore": scores,
                "EntrySignal": ["BREAKOUT_CONFIRM"] * len(scores),
                "BreakoutVolumeConfirmed": [True] * len(scores),
                "BreakoutFlowConfirmed": [True] * len(scores),
                "PassedFilters": [True] * len(scores),
                "UniverseEligible": [True] * len(scores),
                "QualityGate": [True] * len(scores),
                "QualityDataCompleteness": [1.0] * len(scores),
                "ScoreCoverage": [1.0] * len(scores),
                "DataTradingAgeDays": [0] * len(scores),
                "SignalRecencyDays": [1] * len(scores),
                "LifecycleStage": ["趋势确认"] * len(scores),
                "AssetType": ["stock"] * len(scores),
                "IsETF": [False] * len(scores),
            }
        )

    def test_tier_and_execution_eligibility_are_consistent(self):
        result = signal_lifecycle.finalize_signal_ranking(self._decision_frame())
        by_score = result.set_index("InstitutionalScore")
        self.assertEqual(by_score.loc[90.0, "InstitutionalTier"], "A级机构启动")
        self.assertEqual(by_score.loc[90.0, "DecisionState"], "READY")
        self.assertEqual(by_score.loc[90.0, "RankingEligibility"], "推荐")
        self.assertEqual(by_score.loc[70.0, "InstitutionalTier"], "B级观察")
        self.assertEqual(by_score.loc[70.0, "DecisionState"], "CAUTIOUS")
        self.assertEqual(by_score.loc[70.0, "RankingEligibility"], "谨慎候选")
        self.assertEqual(by_score.loc[50.0, "InstitutionalTier"], "C级价值观察")
        self.assertEqual(by_score.loc[50.0, "DecisionState"], "OBSERVE")
        self.assertEqual(by_score.loc[50.0, "RankingEligibility"], "观察")

    def test_b_tier_buy_now_is_observation_not_cautious(self):
        frame = self._decision_frame()
        frame.loc[2, "EntrySignal"] = "BUY_NOW"
        result = signal_lifecycle.finalize_signal_ranking(frame).set_index("Ticker")
        row = result.loc["TEST02.SZ"]
        self.assertEqual(row["InstitutionalTier"], "B级观察")
        self.assertEqual(row["DecisionState"], "OBSERVE")
        self.assertEqual(row["RankingEligibility"], "观察")

    def test_split_exports_publish_mixed_stocks_and_etfs(self):
        frame = pd.DataFrame(
            {
                "Ticker": ["000001.SZ", "000002.SZ", "510300.SH", "159915.SZ"],
                "Name": ["股票一", "股票二", "沪深300ETF", "创业板ETF"],
                "AssetType": ["stock", "stock", "etf", "etf"],
                "IsETF": [False, False, True, True],
                "RankingScore": [90.0, 80.0, 85.0, 75.0],
                "InstitutionalScore": [90.0, 80.0, 85.0, 75.0],
                "FinalScore": [90.0, 80.0, 85.0, 75.0],
                "Score": [90.0, 80.0, 85.0, 75.0],
                "RankingEligibility": ["推荐", "观察", "推荐", "观察"],
                "EntrySignal": ["BUY_NOW", "WAIT_PULLBACK", "BREAKOUT_CONFIRM", "WAIT_PULLBACK"],
                "Industry": ["银行", "软件", "宽基", "宽基"],
                "Sector": ["金融", "科技", "ETF", "ETF"],
            }
        )
        with TemporaryDirectory() as temp_dir, patch.object(report, "_atomic_write_parquet"):
            destination = Path(temp_dir)
            report.refresh_candidate_exports(frame, top_n_csv=3, top_n_parquet=3, output_dir=destination)
            mixed = pd.read_csv(destination / "Top3Mixed.csv", encoding="utf-8-sig")
            stocks = pd.read_csv(destination / "Top3Stocks.csv", encoding="utf-8-sig")
            etfs = pd.read_csv(destination / "Top3ETF.csv", encoding="utf-8-sig")
            legacy = pd.read_csv(destination / "Top3.csv", encoding="utf-8-sig")
        self.assertEqual(set(stocks["AssetType"].str.lower()), {"stock"})
        self.assertEqual(set(etfs["AssetType"].str.lower()), {"etf"})
        self.assertEqual(legacy["Ticker"].tolist(), mixed["Ticker"].tolist())

    def test_gui_main_table_is_decision_focused(self):
        self.assertIn("AssetType", gui.DISPLAY_COLUMNS)
        self.assertNotIn("ValueTrapRisk", gui.DISPLAY_COLUMNS)
        self.assertNotIn("ChaseRiskScore", gui.DISPLAY_COLUMNS)
        self.assertNotIn("BacktestSamples", gui.DISPLAY_COLUMNS)
        self.assertNotIn("QualityDataCompleteness", gui.DISPLAY_COLUMNS)

    def test_model_version_is_at_least_v24(self):
        version = int(SCORING_VERSION.split("-v", 1)[1].split("-", 1)[0])
        self.assertGreaterEqual(version, 24)


if __name__ == "__main__":
    import unittest

    unittest.main()
