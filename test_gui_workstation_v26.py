from __future__ import annotations

import inspect
import unittest

import gui

# Normal connector commit used to gate the final cross-platform PR CI.


class GuiWorkstationV26Tests(unittest.TestCase):
    def test_compact_table_uses_derived_decision_columns(self):
        self.assertIn("ReferenceBuyPrice", gui.DISPLAY_COLUMNS)
        self.assertIn("InstitutionalStrength", gui.DISPLAY_COLUMNS)
        self.assertIn("SignalStatus", gui.DISPLAY_COLUMNS)
        self.assertIn("SignalDays", gui.DISPLAY_COLUMNS)
        # Keep the historical public display contract for downstream callers,
        # while the actual v26 Treeview removes the long explanation column.
        self.assertIn("TradeReadinessReason", gui.DISPLAY_COLUMNS)
        display_source = inspect.getsource(gui.DecisionScannerGUI._set_display_columns_for_file)
        self.assertIn('columns.remove("TradeReadinessReason")', display_source)
        self.assertNotIn("QualityGate", gui.DISPLAY_COLUMNS)
        self.assertNotIn("BacktestConfidenceTier", gui.DISPLAY_COLUMNS)
        self.assertEqual(gui.COLUMN_NAMES["ReferenceBuyPrice"], "参考买点")
        self.assertEqual(gui.COLUMN_NAMES["InstitutionalStrength"], "机构强度")

    def test_top50_stock_and_etf_are_first_class_navigation(self):
        source = inspect.getsource(gui.DecisionScannerGUI._build_ui)
        self.assertIn("综合 Top50", source)
        self.assertIn("股票 Top50", source)
        self.assertIn("ETF Top50", source)
        self.assertIn("强推荐", source)
        self.assertIn("新买点", source)
        self.assertEqual(gui.NAV_FILES["stocks"], "Top50Stocks.csv")
        self.assertEqual(gui.NAV_FILES["etf"], "Top50ETF.csv")

    def test_scan_and_backtest_remain_primary_actions(self):
        source = inspect.getsource(gui.DecisionScannerGUI._build_ui)
        self.assertIn("▶ 开始扫描", source)
        self.assertIn("▶ 运行回测", source)
        self.assertIn("■ 停止", source)
        self.assertIn("扫描模式", source)
        self.assertIn("快速", source)
        self.assertIn("完整刷新", source)

    def test_backtest_scope_matches_high_frequency_workflow(self):
        source = inspect.getsource(gui.DecisionScannerGUI._build_ui)
        for label in (
            "当前页面",
            "当前筛选",
            "股票 Top50",
            "ETF Top50",
            "综合 Top50",
            "强推荐",
            "新买点",
            "当前选中标的",
        ):
            self.assertIn(label, source)

    def test_engineering_controls_and_log_are_collapsible(self):
        source = inspect.getsource(gui.DecisionScannerGUI._build_ui)
        self.assertIn("高级设置", source)
        self.assertIn("更多筛选", source)
        self.assertIn("日志 ›", source)
        self.assertIn("扫描完成后回测强推荐", source)

    def test_new_signal_filter_is_explicit(self):
        instance = gui.DecisionScannerGUI.__new__(gui.DecisionScannerGUI)
        self.assertEqual(instance._format_table_value("SignalStatus", "NEW"), "新出现")
        self.assertEqual(instance._format_table_value("SignalStatus", "ACTIVE"), "持续有效")
        self.assertEqual(instance._format_table_value("SignalStatus", "FAILED"), "已失效")


if __name__ == "__main__":
    unittest.main()
