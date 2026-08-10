from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

import config
import report

# Connector-origin commit used to trigger the final Ubuntu/Windows matrix.


class V32AssetTop50RankingTests(unittest.TestCase):
    def _frame(self) -> pd.DataFrame:
        rows: list[dict[str, object]] = []
        for index in range(60):
            rows.append(
                {
                    "Ticker": f"{index + 1:06d}.SZ",
                    "Name": f"测试股票{index + 1}",
                    "AssetType": "stock",
                    "IsETF": False,
                    "Industry": "银行",
                    "Sector": "金融",
                    "ModelClassification": "银行",
                    "RankingScore": 100.0 - index,
                    "InstitutionalScore": 80.0 - index / 10.0,
                    "FinalScore": 70.0 - index / 10.0,
                    "Score": 60.0 - index / 10.0,
                    "RankingEligibility": "推荐" if index == 0 else "观察",
                    "Error": "",
                    "RunId": "v32-test",
                    "DataAsOf": "2026-08-07",
                }
            )
        for index in range(60):
            rows.append(
                {
                    "Ticker": f"51{index:04d}.SH",
                    "Name": f"测试ETF{index + 1}",
                    "AssetType": "etf",
                    "IsETF": True,
                    "Industry": "医药医疗",
                    "Sector": "医药医疗",
                    "ETFTheme": "医药医疗",
                    "ModelClassification": "医药医疗",
                    "RankingScore": 90.0 - index,
                    "InstitutionalScore": 75.0 - index / 10.0,
                    "FinalScore": 65.0 - index / 10.0,
                    "Score": 55.0 - index / 10.0,
                    "RankingEligibility": "观察",
                    "Error": "",
                    "RunId": "v32-test",
                    "DataAsOf": "2026-08-07",
                }
            )
        return pd.DataFrame(rows)

    def test_split_asset_lists_fill_top50_even_when_one_industry_repeats(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)
            report.refresh_candidate_exports(
                self._frame(),
                top_n_csv=50,
                top_n_parquet=20,
                output_dir=destination,
            )
            stocks = pd.read_csv(destination / "Top50Stocks.csv")
            etfs = pd.read_csv(destination / "Top50ETF.csv")

        self.assertEqual(len(stocks), 50)
        self.assertEqual(len(etfs), 50)
        self.assertEqual(stocks["ResearchPoolRank"].tolist(), list(range(1, 51)))
        self.assertEqual(etfs["ResearchPoolRank"].tolist(), list(range(1, 51)))
        self.assertTrue(stocks["ResearchDiversityPenalty"].eq(1.0).all())
        self.assertTrue(etfs["ResearchDiversityPenalty"].eq(1.0).all())
        self.assertEqual(stocks.iloc[0]["Ticker"], "000001.SZ")
        self.assertEqual(stocks.iloc[0]["RankingEligibility"], "推荐")
        self.assertEqual(stocks.iloc[1]["RankingEligibility"], "观察")

    def test_mixed_list_still_uses_diversity_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)
            report.refresh_candidate_exports(
                self._frame(),
                top_n_csv=50,
                top_n_parquet=20,
                output_dir=destination,
            )
            mixed = pd.read_csv(destination / "Top50Mixed.csv")
            stocks = pd.read_csv(destination / "Top50Stocks.csv")

        self.assertEqual(len(stocks), 50)
        self.assertLess(len(mixed.loc[mixed["AssetType"].astype(str).str.lower().eq("stock")]), 50)

    def test_engineering_version_advances_without_model_change(self):
        self.assertRegex(config.PIPELINE_VERSION, r"-v(?:3[2-9]|[4-9][0-9]+)-")
        self.assertIn("v24", config.SCORING_VERSION)


if __name__ == "__main__":
    unittest.main()
