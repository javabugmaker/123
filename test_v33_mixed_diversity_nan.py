from __future__ import annotations

import re
import unittest

import numpy as np
import pandas as pd

import config
import report


# Regression contract for mixed-list nullable ETF provenance.
class V33MixedDiversityNaNTests(unittest.TestCase):
    def test_clean_group_key_normalizes_nullable_values(self):
        for value in (None, np.nan, pd.NA, "nan", "NaN", "none", "<NA>", ""):
            self.assertEqual(report._clean_group_key(value), "")
        self.assertEqual(report._clean_group_key(" 银行 "), "银行")

    def test_stock_nan_etf_keys_do_not_collapse_mixed_pool(self):
        rows = []
        for index in range(10):
            rows.append(
                {
                    "Ticker": f"{index + 1:06d}.SZ",
                    "Name": f"股票{index + 1}",
                    "AssetType": "stock",
                    "IsETF": False,
                    "Industry": f"行业{index + 1}",
                    "ModelClassification": f"行业{index + 1}",
                    "ETFTheme": np.nan,
                    "ETFTrackingKey": np.nan,
                    "ThemeCluster": np.nan,
                    "RankingScore": 100.0 - index,
                }
            )
        result = report._diversify_ranked_candidates(pd.DataFrame(rows), 10)
        self.assertEqual(len(result), 10)
        self.assertEqual(result["Ticker"].tolist(), [f"{index + 1:06d}.SZ" for index in range(10)])

    def test_etf_tracking_cap_still_applies_only_to_etfs(self):
        rows = [
            {
                "Ticker": "000001.SZ",
                "Name": "股票A",
                "AssetType": "stock",
                "IsETF": False,
                "Industry": "银行",
                "ModelClassification": "银行",
                "ETFTheme": np.nan,
                "ETFTrackingKey": "SAME",
                "RankingScore": 100.0,
            },
            {
                "Ticker": "000002.SZ",
                "Name": "股票B",
                "AssetType": "stock",
                "IsETF": False,
                "Industry": "食品",
                "ModelClassification": "食品",
                "ETFTheme": np.nan,
                "ETFTrackingKey": "SAME",
                "RankingScore": 99.0,
            },
            {
                "Ticker": "510001.SH",
                "Name": "ETF1",
                "AssetType": "etf",
                "IsETF": True,
                "ETFTheme": "医药医疗",
                "ETFTrackingKey": "TRACK",
                "RankingScore": 98.0,
            },
            {
                "Ticker": "510002.SH",
                "Name": "ETF2",
                "AssetType": "etf",
                "IsETF": True,
                "ETFTheme": "医药医疗",
                "ETFTrackingKey": "TRACK",
                "RankingScore": 97.0,
            },
        ]
        result = report._diversify_ranked_candidates(pd.DataFrame(rows), 4)
        tickers = result["Ticker"].tolist()
        self.assertIn("000001.SZ", tickers)
        self.assertIn("000002.SZ", tickers)
        self.assertIn("510001.SH", tickers)
        self.assertNotIn("510002.SH", tickers)

    def test_engineering_version_advances_without_model_change(self):
        match = re.search(r"-v(\d+)-", config.PIPELINE_VERSION)
        self.assertIsNotNone(match)
        self.assertGreaterEqual(int(match.group(1)), 33)
        self.assertIn("v24", config.SCORING_VERSION)


if __name__ == "__main__":
    unittest.main()
