from __future__ import annotations

import unittest

import gui_v84 as gui


class GuiV84ThemeTests(unittest.TestCase):
    def test_primary_table_uses_layered_ranking_fields(self) -> None:
        columns = tuple(gui._core.DISPLAY_COLUMNS)
        self.assertIn("ResearchRank", columns)
        self.assertIn("TradeRank", columns)
        self.assertIn("AlphaScore", columns)
        self.assertIn("ExecutionState", columns)
        self.assertIn("SmoothTriggerScore", columns)
        self.assertNotIn("RankingScore", columns)

    def test_chinese_labels_and_palette_are_stable(self) -> None:
        self.assertEqual(gui._core.COLUMN_NAMES["ResearchRank"], "研究排名")
        self.assertEqual(gui._core.COLUMN_NAMES["TradeRank"], "交易排名")
        self.assertEqual(gui._core.COLUMN_NAMES["ExecutionState"], "执行状态")
        self.assertEqual(gui.背景, "#F1F2F4")
        self.assertEqual(gui.墨色, "#15171A")
        self.assertEqual(gui.强调红, "#E33D3D")

    def test_new_gui_reuses_stable_workstation_behavior(self) -> None:
        self.assertTrue(issubclass(gui.ResearchTerminalGUI, gui._legacy.DecisionScannerGUI))
        self.assertIs(gui.ScannerGUI, gui.ResearchTerminalGUI)


if __name__ == "__main__":
    unittest.main()
