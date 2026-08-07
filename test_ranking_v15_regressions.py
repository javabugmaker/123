import unittest

import pandas as pd

from report import _diversify_ranked_candidates, _rank_valid_candidates
from scanner import ScanResult


class RankingV15RegressionTests(unittest.TestCase):
    def test_ranking_score_beats_soft_eligibility_but_risk_stays_last(self):
        frame = pd.DataFrame(
            [
                {"Ticker": "LOW", "Error": "", "RankingEligibility": "推荐", "RankingScore": 30.0, "InstitutionalScore": 31.0, "BacktestAdjustedScore": 50.0, "EntrySignalPriority": 4.0, "FinalScore": 31.0, "Score": 31.0},
                {"Ticker": "HIGH", "Error": "", "RankingEligibility": "观察", "RankingScore": 43.0, "InstitutionalScore": 44.0, "BacktestAdjustedScore": 51.0, "EntrySignalPriority": 3.0, "FinalScore": 44.0, "Score": 44.0},
                {"Ticker": "RISK", "Error": "", "RankingEligibility": "风险过滤", "RankingScore": 99.0, "InstitutionalScore": 99.0, "BacktestAdjustedScore": 80.0, "EntrySignalPriority": 5.0, "FinalScore": 99.0, "Score": 99.0},
            ]
        )
        ranked = _rank_valid_candidates(frame)
        self.assertEqual(ranked["Ticker"].tolist(), ["HIGH", "LOW", "RISK"])

    def test_etf_theme_diversity_caps_repeated_medical_etfs(self):
        rows = []
        for i in range(5):
            rows.append({"Ticker": f"ETF{i}", "Name": f"创新药ETF{i}", "IsETF": True, "AssetType": "etf"})
        for i in range(5):
            rows.append({"Ticker": f"STK{i}", "Name": f"股票{i}", "IsETF": False, "AssetType": "stock"})
        diversified = _diversify_ranked_candidates(pd.DataFrame(rows), 6, max_per_theme=2)
        self.assertEqual(len(diversified), 6)
        self.assertLessEqual((diversified["ETFTheme"] == "医药医疗").sum(), 2)
        self.assertEqual(diversified["ResearchPoolRank"].tolist(), list(range(1, 7)))

    def test_scan_result_exposes_backtest_provenance_fields(self):
        result = ScanResult("000001.SZ")
        self.assertEqual(result.backtest_mode, "")
        self.assertFalse(result.backtest_cache_hit)
        self.assertEqual(result.backtest_last_evaluated_date, "")
        self.assertEqual(result.backtest_engine, "")


if __name__ == "__main__":
    unittest.main()
