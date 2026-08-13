from __future__ import annotations

import unittest

import pandas as pd

import analytics
import gui

# Connector-gated v28 cross-platform CI.


class V28GuiBacktestTests(unittest.TestCase):
    def test_main_table_uses_display_rank_and_industry_topic(self):
        self.assertIn("DisplayRank", gui.DISPLAY_COLUMNS)
        self.assertIn("IndustryTopic", gui.DISPLAY_COLUMNS)
        self.assertNotIn("OverallRank", gui.DISPLAY_COLUMNS)
        self.assertNotIn("Industry", gui.DISPLAY_COLUMNS)
        self.assertEqual(gui.COLUMN_NAMES["DisplayRank"], "当前排名")
        self.assertEqual(gui.COLUMN_NAMES["IndustryTopic"], "行业 / 主题")

    def test_derived_rank_topic_and_compact_institution_strength(self):
        instance = gui.DecisionScannerGUI.__new__(gui.DecisionScannerGUI)
        instance._csv_headers = [
            "OverallRank", "ResearchPoolRank", "Ticker", "AssetType", "IsETF",
            "Industry", "ETFTheme", "ModelClassification", "Sector", "EntrySignal",
            "EntryZone", "BreakoutBuyPrice", "InstitutionalTier", "InstitutionalScore",
        ]
        instance._csv_rows = [[
            "12", "2", "516500.SH", "etf", "True", "", "医药医疗", "宽基ETF", "ETF",
            "BREAKOUT_CONFIRM", "0.64-0.65", "0.66", "A级机构启动", "42.71",
        ]]
        instance._ensure_derived_columns()
        indexes = instance._csv_indexes
        row = instance._csv_rows[0]
        self.assertEqual(row[indexes["DisplayRank"]], "2")
        self.assertEqual(row[indexes["IndustryTopic"]], "医药医疗")
        self.assertEqual(row[indexes["ReferenceBuyPrice"]], "0.66")
        self.assertEqual(row[indexes["InstitutionalStrength"]], "A · 42.71")

    def test_stock_topic_falls_back_to_industry(self):
        instance = gui.DecisionScannerGUI.__new__(gui.DecisionScannerGUI)
        instance._csv_headers = [
            "OverallRank", "Ticker", "AssetType", "IsETF", "Industry", "ETFTheme",
            "ModelClassification", "Sector", "EntrySignal", "EntryZone", "BreakoutBuyPrice",
            "InstitutionalTier", "InstitutionalScore",
        ]
        instance._csv_rows = [[
            "7", "000001.SZ", "stock", "False", "银行", "", "金融", "金融",
            "WAIT_PULLBACK", "10.20-10.20", "", "B级观察", "33.50",
        ]]
        instance._ensure_derived_columns()
        indexes = instance._csv_indexes
        row = instance._csv_rows[0]
        self.assertEqual(row[indexes["DisplayRank"]], "7")
        self.assertEqual(row[indexes["IndustryTopic"]], "银行")
        self.assertEqual(row[indexes["ReferenceBuyPrice"]], "10.20")
        self.assertEqual(row[indexes["InstitutionalStrength"]], "B · 33.50")

    def test_fast_evidence_gate_reduces_exact_refinement_pool(self):
        frame = pd.DataFrame(
            [
                {"Ticker": "A", "EntrySignal": "BREAKOUT_CONFIRM", "RankingEligibility": "推荐", "RankingScore": 60.0},
                {"Ticker": "B", "EntrySignal": "WAIT_PULLBACK", "RankingEligibility": "谨慎候选", "RankingScore": 55.0},
                {"Ticker": "C", "EntrySignal": "WAIT_PULLBACK", "RankingEligibility": "观察", "RankingScore": 50.0},
                {"Ticker": "D", "EntrySignal": "WAIT_PULLBACK", "RankingEligibility": "观察", "RankingScore": 45.0},
                {"Ticker": "E", "EntrySignal": "WAIT_PULLBACK", "RankingEligibility": "风险过滤", "RankingScore": 70.0},
            ]
        )
        fast_rows = [
            {"ticker": "A", "entry_signal": "BREAKOUT_CONFIRM", "samples": 1},
            {"ticker": "B", "entry_signal": "WAIT_PULLBACK", "samples": 5},
            {"ticker": "C", "entry_signal": "WAIT_PULLBACK", "samples": 10},
            {"ticker": "D", "entry_signal": "WAIT_PULLBACK", "samples": 20},
            {"ticker": "E", "entry_signal": "WAIT_PULLBACK", "samples": 30},
        ]
        pool = analytics._select_exact_refinement_pool(frame, fast_rows, top_n=3)
        self.assertEqual(pool["Ticker"].tolist(), ["B", "C"])
        self.assertGreaterEqual(int(pool["_FastSamples"].min()), analytics._minimum_fast_samples_for_exact_refinement())

    def test_fast_minimum_accounts_for_sparse_fast_sampling(self):
        # FAST uses a 40-day cooldown while EXACT uses 20 days, so five FAST
        # observations are approximately the evidence floor for ten EXACT samples.
        self.assertEqual(analytics._minimum_fast_samples_for_exact_refinement(), 5)


if __name__ == "__main__":
    unittest.main()
