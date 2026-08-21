from __future__ import annotations

import unittest

import gui_v85 as gui
from v85_terminal_config import COLORS, LAYOUT, NAV_ITEMS, TERMINAL_VERSION


class GuiV85ThemeTests(unittest.TestCase):
    def test_v85_reuses_stable_v84_workstation_and_model_columns(self) -> None:
        self.assertTrue(issubclass(gui.ResearchBriefingGUI, gui._v84.ResearchTerminalGUI))
        self.assertIs(gui.ScannerGUI, gui.ResearchBriefingGUI)
        self.assertIn("ResearchRank", gui._v84.V84_DISPLAY_COLUMNS)
        self.assertIn("TradeRank", gui._v84.V84_DISPLAY_COLUMNS)
        self.assertNotIn("RankingScore", gui._v84.V84_DISPLAY_COLUMNS)

    def test_shared_presentation_contract_is_compact_and_stable(self) -> None:
        self.assertEqual(COLORS["background"], "#F1F2F4")
        self.assertEqual(COLORS["ink"], "#15171A")
        self.assertEqual(COLORS["red"], "#E33D3D")
        self.assertEqual(LAYOUT["window"], "1366x768")
        self.assertEqual(NAV_ITEMS[0], ("mixed", "综合"))
        self.assertIn("v85", TERMINAL_VERSION)

    def test_v85_only_overrides_presentation_hooks(self) -> None:
        local_methods = set(gui.ResearchBriefingGUI.__dict__)
        forbidden = {"start_daily_pipeline", "start_scan", "start_backtest", "run_process"}
        self.assertTrue(forbidden.isdisjoint(local_methods))


if __name__ == "__main__":
    unittest.main()
