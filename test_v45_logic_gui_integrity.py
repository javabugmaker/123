from __future__ import annotations

import unittest
from unittest.mock import MagicMock, Mock

import numpy as np
import pandas as pd

import config
import gui
import gui_core
from report import validate_decision_integrity


class V45LogicGuiIntegrityTests(unittest.TestCase):
    def test_v45_provenance_marks_logic_output_and_gui_boundaries(self) -> None:
        self.assertIn("v45", config.SCORING_VERSION)
        self.assertIn("v45", config.PIPELINE_VERSION)
        self.assertIn("v45", config.DECISION_INTEGRITY_VERSION)
        self.assertIn("v45", config.OUTPUT_CONTRACT_VERSION)
        self.assertIn("v45", config.GUI_VERSION)

    def test_integrity_rejects_missing_current_execution_metrics(self) -> None:
        base = {
            "Ticker": "TEST.SZ",
            "RankingEligibility": "推荐",
            "DecisionState": "READY",
            "TradeReadiness": "推荐",
            "SignalStatus": "ACTIVE",
            "SignalDays": 1,
            "EntrySignal": "BREAKOUT_CONFIRM",
            "BreakoutVolumeConfirmed": True,
            "BreakoutFlowConfirmed": True,
            "BreakoutVolumeRatio": 1.30,
            "QualityApplicable": True,
            "QualityGate": True,
            "QualityHardDataComplete": True,
            "StopDistancePct": 5.0,
            "RewardRiskRatio": 2.0,
        }
        for field in (
            "BreakoutVolumeRatio",
            "StopDistancePct",
            "RewardRiskRatio",
        ):
            row = dict(base)
            row[field] = np.nan
            with self.assertRaisesRegex(ValueError, "Decision integrity violation"):
                validate_decision_integrity(pd.DataFrame([row]))

        row = dict(base)
        row["BreakoutFlowConfirmed"] = False
        with self.assertRaisesRegex(ValueError, "event-volume confirmation"):
            validate_decision_integrity(pd.DataFrame([row]))

        row = dict(base)
        row.pop("BreakoutFlowConfirmed")
        with self.assertRaisesRegex(ValueError, "incomplete confirmation schema"):
            validate_decision_integrity(pd.DataFrame([row]))

    def test_candidate_view_rank_is_preferred_for_gui_derivation(self) -> None:
        instance = gui.DecisionScannerGUI.__new__(gui.DecisionScannerGUI)
        instance._csv_headers = [
            "CandidateViewRank",
            "ResearchPoolRank",
            "OverallRank",
            "AssetType",
            "Ticker",
        ]
        instance._csv_rows = [["3", "9", "17", "stock", "000001.SZ"]]
        instance._ensure_derived_columns()
        indexes = instance._csv_indexes
        self.assertEqual(
            instance._csv_rows[0][indexes["DisplayRank"]],
            "3",
        )

    def test_current_rank_tracks_the_visible_sort_order(self) -> None:
        instance = gui_core.ScannerGUI.__new__(gui_core.ScannerGUI)
        instance._filter_job = None
        instance._csv_headers = ["DisplayRank", "Ticker", "RankingScore"]
        instance._csv_rows = [
            ["1", "A.SZ", "40"],
            ["16", "B.SZ", "42"],
            ["9", "C.SZ", "41"],
        ]
        instance._csv_indexes = {
            header: index for index, header in enumerate(instance._csv_headers)
        }
        instance._csv_search_text = ["", "", ""]
        instance._display_headers = list(instance._csv_headers)
        instance._display_indexes = [0, 1, 2]
        instance._table_headers = ()
        instance._sort_column = "RankingScore"
        instance._sort_descending = True
        instance._current_page = 0
        instance.search = Mock(get=Mock(return_value=""))
        instance.sector_filter = Mock(get=Mock(return_value="全部板块"))
        instance.industry_filter = Mock(get=Mock(return_value="全部行业"))
        instance.quality_filter = Mock(get=Mock(return_value="全部质量"))
        instance.table = MagicMock()
        instance.table.get_children.return_value = ()
        instance.table.insert.side_effect = ["row-1", "row-2", "row-3"]
        instance._row_details = {}
        instance.status = Mock()
        instance.current_file = "Top50Mixed.csv"

        self.assertTrue(instance._render_cached_rows())
        rendered_values = [
            call.kwargs["values"] for call in instance.table.insert.call_args_list
        ]
        self.assertEqual([values[0] for values in rendered_values], ["1", "2", "3"])
        self.assertEqual([values[1] for values in rendered_values], ["B.SZ", "C.SZ", "A.SZ"])

    def test_etf_prices_keep_three_decimal_gui_precision(self) -> None:
        instance = gui.DecisionScannerGUI.__new__(gui.DecisionScannerGUI)
        self.assertEqual(
            instance._format_asset_price("1.373", {"AssetType": "etf"}),
            "1.373",
        )
        self.assertEqual(
            instance._format_asset_price("1.323", {"IsETF": "True"}),
            "1.323",
        )
        self.assertEqual(
            instance._format_asset_price("9.153", {"AssetType": "stock"}),
            "9.15",
        )

    def test_new_view_defaults_to_candidate_order(self) -> None:
        instance = gui.DecisionScannerGUI.__new__(gui.DecisionScannerGUI)
        instance.current_file = "Top50Stocks.csv"
        instance._new_signal_only = False
        instance._sort_column = "RankingScore"
        instance._sort_descending = True
        instance._current_page = 3
        instance.view_title = Mock()
        instance._call_core_with_legacy_output_dir = Mock(return_value=True)
        instance._ensure_derived_columns = Mock()
        instance._set_display_columns_for_file = Mock()
        instance._render_cached_rows = Mock(return_value=True)
        instance._infer_nav_key = Mock(return_value=None)

        self.assertTrue(instance.load_csv("Top50Mixed.csv"))
        self.assertEqual(instance._sort_column, "DisplayRank")
        self.assertFalse(instance._sort_descending)
        self.assertEqual(instance._current_page, 0)

    def test_rendered_rows_auto_select_and_preserve_ticker(self) -> None:
        instance = gui.DecisionScannerGUI.__new__(gui.DecisionScannerGUI)
        instance.table = MagicMock()
        instance.table.get_children.return_value = ("row-a", "row-b")
        instance._row_details = {
            "row-a": {"Ticker": "A.SZ"},
            "row-b": {"Ticker": "B.SZ"},
        }
        instance._update_decision_card = Mock()
        instance._reset_decision_card_if_needed = Mock()

        instance._restore_table_selection("B.SZ")

        instance.table.selection_set.assert_called_once_with("row-b")
        instance.table.focus.assert_called_once_with("row-b")
        instance.table.see.assert_called_once_with("row-b")
        instance._update_decision_card.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
