from __future__ import annotations

import unittest

import gui_v85
import web_report


class BacktestVisibilityV92Tests(unittest.TestCase):
    def test_gui_compact_label_exposes_score_weight_and_delta(self) -> None:
        label = gui_v85.backtest_calibration_label(
            {
                "BacktestScore": 68.4,
                "BacktestEffectiveWeight": 0.12,
                "CompositeScore": 52.0,
                "FinalScore": 50.0,
            }
        )
        self.assertIn("S68.4", label)
        self.assertIn("W12%", label)
        self.assertIn("Δ+2.0", label)

    def test_gui_detail_exposes_complete_production_chain(self) -> None:
        label = gui_v85.backtest_detail_label(
            {
                "BacktestScore": 68.4,
                "BacktestAdjustedScore": 64.7,
                "BacktestEffectiveWeight": 0.12,
                "CompositeScore": 52.0,
                "FinalScore": 50.0,
                "BacktestSamples": 37,
                "BacktestEffectiveSamples": 20.5,
                "BacktestConfidenceTier": "中可信度",
            }
        )
        for token in (
            "回测分 68.4",
            "校准分 64.7",
            "权重 12%",
            "回测后 52.0",
            "Δ+2.0",
            "n=37/eff=20.5",
            "中可信度",
        ):
            self.assertIn(token, label)

    def test_gui_places_production_calibration_in_primary_columns(self) -> None:
        columns = gui_v85._v92_display_columns()
        self.assertIn(gui_v85.BACKTEST_CALIBRATION_COLUMN, columns)
        self.assertIn(gui_v85.RESONANCE_HISTORY_COLUMN, columns)
        self.assertLess(
            columns.index(gui_v85.BACKTEST_CALIBRATION_COLUMN),
            columns.index(gui_v85.RESONANCE_HISTORY_COLUMN),
        )

    def test_web_production_table_exposes_backtest_fields(self) -> None:
        html = web_report._production_backtest(
            [
                {
                    "ResearchRank": "3",
                    "Ticker": "000001.SZ",
                    "Name": "示例",
                    "BacktestScore": "68.4",
                    "BacktestAdjustedScore": "64.7",
                    "BacktestEffectiveWeight": "0.12",
                    "CompositeScore": "52.0",
                    "FinalScore": "50.0",
                    "BacktestSamples": "37",
                    "BacktestEffectiveSamples": "20.5",
                    "BacktestConfidenceTier": "中可信度",
                }
            ],
            {"mode": "FAST", "objective": "net_excess_return_20d"},
        )
        for token in (
            "回测分",
            "校准分",
            "权重",
            "综合分",
            "+2.0",
            "37",
            "中可信度",
        ):
            self.assertIn(token, html)

    def test_web_keeps_production_and_experimental_backtests_separate(self) -> None:
        production = web_report._production_backtest([], {})
        resonance = web_report._resonance({})
        self.assertIn("参与当前排名", production)
        self.assertIn("不进入排名", resonance)


if __name__ == "__main__":
    unittest.main()
