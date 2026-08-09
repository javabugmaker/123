from __future__ import annotations

import inspect
import unittest

import gui

# v25 contract: the GUI is a trading-decision surface, not a research audit table.


class GuiCleanV25Tests(unittest.TestCase):
    def test_main_table_replaces_research_diagnostics_with_recent_entry_state(self):
        self.assertIn("SignalStatus", gui.DISPLAY_COLUMNS)
        self.assertIn("SignalDays", gui.DISPLAY_COLUMNS)
        self.assertNotIn("QualityGate", gui.DISPLAY_COLUMNS)
        self.assertNotIn("BacktestConfidenceTier", gui.DISPLAY_COLUMNS)
        self.assertNotIn("PassedFilters", gui.DISPLAY_COLUMNS)
        self.assertNotIn("FinalScore", gui.DISPLAY_COLUMNS)
        self.assertNotIn("RankingReason", gui.DISPLAY_COLUMNS)
        self.assertEqual(gui.COLUMN_NAMES["EntrySignal"], "当前买点")
        self.assertEqual(gui.COLUMN_NAMES["SignalStatus"], "近期买点")
        self.assertEqual(gui.COLUMN_NAMES["SignalDays"], "持续天数")

    def test_filter_bar_drops_fundamental_and_backtest_controls(self):
        source = inspect.getsource(gui.DecisionScannerGUI._build_ui_filters)
        self.assertNotIn("fundamental_filter", source)
        self.assertNotIn("backtest_filter", source)
        self.assertNotIn("fundamental_box", source)
        self.assertNotIn("backtest_box", source)
        self.assertNotIn("回测可信度", source)
        self.assertIn("最低分", source)
        self.assertIn("重置", source)
        self.assertIn("刷新", source)

    def test_recent_entry_status_is_human_readable(self):
        instance = gui.DecisionScannerGUI.__new__(gui.DecisionScannerGUI)
        self.assertEqual(
            instance._format_table_value("SignalStatus", "NEW"),
            "新出现",
        )
        self.assertEqual(
            instance._format_table_value("SignalStatus", "ACTIVE"),
            "持续有效",
        )
        self.assertEqual(
            instance._format_table_value("SignalStatus", "FAILED"),
            "已失效",
        )


if __name__ == "__main__":
    unittest.main()
