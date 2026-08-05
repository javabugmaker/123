import argparse
import ast
import csv
import importlib.util
import sys
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, Mock, patch

import numpy as np
import pandas as pd

try:
    import tkinter
except ModuleNotFoundError:
    tkinter = MagicMock()
    tkinter.END = "end"
    tkinter.DISABLED = "disabled"
    tkinter.messagebox = MagicMock()
    tkinter.ttk = MagicMock()
    sys.modules["tkinter"] = tkinter
    sys.modules["tkinter.messagebox"] = tkinter.messagebox
    sys.modules["tkinter.ttk"] = tkinter.ttk

if importlib.util.find_spec("pyarrow") is None:
    pyarrow = MagicMock()
    pyarrow.__version__ = "0.0.0"
    pyarrow.parquet = MagicMock()
    sys.modules["pyarrow"] = pyarrow
    sys.modules["pyarrow.parquet"] = pyarrow.parquet

import analytics
import fundamental_data
import gui
import main
import scanner
import signal_lifecycle
from analytics import BacktestSummary, _ticker_backtest_rows, apply_backtest_ranking
from fundamental_quality import calculate_quality
from report import _institutional_tier
from score import ScoreBreakdown, _score_dimensions_available, score_structure
from downloader import TickerInfo, _cache_path, _log_download_progress
from filters import (
    filter_bear_market,
    filter_min_price,
    filter_min_volume,
    filter_volume_accumulation,
)
from indicators import (
    compute_moving_averages,
    compute_volume_mas,
    detect_wyckoff_phase,
)
from report import _results_to_dataframe, export_all
from scanner import ScanReport, ScanResult
from score import score_ticker


class RegressionTests(TestCase):
    def test_fundamental_quality_calculates_four_factors(self):
        quality = calculate_quality({
            "Ticker": "000001.SZ",
            "ROE": 12.0,
            "GrossMargin": 30.0,
            "InstitutionHoldingTrend": "increasing",
            "InstitutionHoldingPeriods": 3,
            "NetProfitY1": 30.0,
            "NetProfitY2": 20.0,
            "NetProfitY3": 20.0,
            "IndustryGrossMarginPercentile": 0.30,
        })

        self.assertEqual(quality.quality_score, 100.0)
        self.assertTrue(quality.quality_gate)
        self.assertEqual(quality.quality_reason, "全部通过")

    def test_fundamental_margin_percentile_is_industry_relative(self):
        frame = pd.DataFrame({
            "Industry": ["银行", "银行", "银行", "白酒", "白酒", "白酒"],
            "GrossMargin": [10.0, 20.0, 30.0, 50.0, 40.0, 30.0],
        })

        percentiles = fundamental_data._industry_margin_percentiles(frame)

        self.assertEqual(percentiles.round(4).tolist(), [1.0, 0.5, 0.0, 0.0, 0.5, 1.0])

    def test_institutional_tier_downgrades_when_quality_gate_fails(self):
        result = ScanResult(
            ticker="000001.SZ",
            institutional_score=90.0,
            quality_data_available=True,
            quality_gate=False,
            signal_recency_days=10,
            filter_details={"volume_accumulation": True},
        )

        self.assertEqual(_institutional_tier(result), "B级观察")

    def test_etf_skips_quality_tier_downgrade(self):
        result = ScanResult(
            ticker="510300.SH",
            is_etf=True,
            institutional_score=90.0,
            quality_data_available=True,
            quality_gate=False,
            signal_recency_days=10,
            filter_details={"volume_accumulation": True},
        )

        self.assertEqual(_institutional_tier(result), "A级机构启动")

    def test_gui_startup_loads_best_available_results(self):
        root = Mock()
        root.after.return_value = "log-job"
        variable = Mock()
        with patch("gui.tk.StringVar", return_value=variable), patch("gui.tk.BooleanVar", return_value=variable), patch.object(gui.ScannerGUI, "_configure_style"), patch.object(gui.ScannerGUI, "_build_ui"), patch.object(gui.ScannerGUI, "_load_best_available_results") as load_results:
            gui.ScannerGUI(root)

        root.protocol.assert_called_once()
        load_results.assert_called_once_with()

    def test_gui_clear_log_removes_existing_text(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner.log_text = Mock()

        scanner.clear_log()

        scanner.log_text.configure.assert_any_call(state=gui.tk.NORMAL)
        scanner.log_text.delete.assert_called_once_with("1.0", gui.tk.END)
        scanner.log_text.configure.assert_called_with(state=gui.tk.DISABLED)

    def test_gui_clear_filters_restores_default_values_and_refreshes_rows(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner.search = Mock()
        scanner.sector_filter = Mock()
        scanner.industry_filter = Mock()
        scanner.quality_filter = Mock()
        scanner._filter_job = "filter-job"
        scanner.root = Mock()
        scanner._render_cached_rows = Mock()

        scanner.clear_filters()

        scanner.search.set.assert_called_once_with("")
        scanner.sector_filter.set.assert_called_once_with("全部板块")
        scanner.industry_filter.set.assert_called_once_with("全部行业")
        scanner.quality_filter.set.assert_called_once_with("全部质量")
        scanner.root.after_cancel.assert_called_once_with("filter-job")
        self.assertIsNone(scanner._filter_job)
        scanner._render_cached_rows.assert_called_once_with()

    def test_gui_sector_change_refreshes_industry_options_from_cached_rows(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner.industry_filter = Mock()
        scanner._csv_headers = ["Sector", "Industry"]
        scanner._csv_rows = [["金融", "银行"], ["科技", "软件"]]
        scanner._update_filter_values = Mock()

        scanner._sector_changed()

        scanner.industry_filter.set.assert_called_once_with("全部行业")
        scanner._update_filter_values.assert_called_once_with(
            scanner._csv_headers, scanner._csv_rows
        )

    def test_gui_cancel_running_task_terminates_process_after_confirmation(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner.scan_running = True
        scanner.process = Mock()
        scanner.cancel_button = Mock()
        scanner.status = Mock()

        with patch("gui.messagebox.askyesno", return_value=True):
            scanner.cancel_running_task()

        scanner.process.terminate.assert_called_once_with()
        scanner.cancel_button.configure.assert_called_once_with(state=gui.tk.DISABLED)
        scanner.status.set.assert_called_once_with("正在取消任务")
        self.assertTrue(scanner._cancel_requested)

    def test_gui_on_close_keeps_window_open_when_running_task_is_not_confirmed(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner.scan_running = True
        scanner._shutdown = Mock()
        scanner._cancel_process = Mock()
        scanner._close_when_stopped = Mock()

        with patch("gui.messagebox.askyesno", return_value=False):
            scanner.on_close()

        scanner._shutdown.assert_not_called()
        scanner._cancel_process.assert_not_called()
        scanner._close_when_stopped.assert_not_called()

    def test_gui_shutdown_cancels_scheduled_jobs_and_destroys_window(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner._filter_job = "filter-job"
        scanner._log_job = "log-job"
        scanner.root = Mock()

        scanner._shutdown()

        scanner.root.after_cancel.assert_any_call("filter-job")
        scanner.root.after_cancel.assert_any_call("log-job")
        scanner.root.destroy.assert_called_once_with()
        self.assertIsNone(scanner._filter_job)
        self.assertIsNone(scanner._log_job)

    def test_gui_formats_numeric_table_values_and_quality_tags(self):
        scanner = object.__new__(gui.ScannerGUI)

        self.assertEqual(scanner._format_table_value("Close", "125.8"), "125.80")
        self.assertEqual(scanner._format_table_value("DistToLow52W", "3.5"), "3.50%")
        self.assertEqual(scanner._format_table_value("BacktestSamples", "1200"), "1,200")
        self.assertEqual(scanner._format_table_value("SignalDays", "3"), "3")
        self.assertEqual(scanner._format_table_value("ScoreConfidence", "0.87"), "87%")
        self.assertEqual(scanner._format_table_value("ScoreConfidencePct", "87"), "87%")
        self.assertEqual(scanner._format_table_value("BacktestWinRate20D", "0.75"), "75%")
        self.assertEqual(scanner._format_table_value("BacktestWinRate60D", "0.625"), "62%")
        self.assertEqual(scanner._quality_tag("强候选"), "quality-strong")
        self.assertEqual(scanner._quality_tag("候选"), "quality-candidate")
        self.assertEqual(scanner._quality_tag("观察"), "quality-watch")
        self.assertEqual(scanner._quality_tag("普通"), "quality-normal")

    def test_gui_market_overview_summarizes_filtered_lifecycle_rows(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner.market_overview = Mock()
        indexes = {"OpportunityScore": 0, "SignalDays": 1, "LifecycleStage": 2}
        rows = [["80", "3", "趋势确认"], ["40", "0", "机构吸筹"]]

        scanner._update_market_overview(rows, indexes)

        scanner.market_overview.set.assert_called_once_with(
            "市场概览：2 只 · 启动 0 · 可交易 0 · 最终均分 60.0"
        )

    def test_gui_page_navigation_updates_current_page(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner._current_page = 1
        scanner._render_cached_rows = Mock()

        scanner._show_previous_page()
        self.assertEqual(scanner._current_page, 0)
        scanner._render_cached_rows.assert_called_once_with()

        scanner._render_cached_rows.reset_mock()
        scanner._show_next_page()
        self.assertEqual(scanner._current_page, 1)
        scanner._render_cached_rows.assert_called_once_with()

    def test_gui_sorts_numeric_values_numerically(self):
        scanner = object.__new__(gui.ScannerGUI)
        indexes = {"Score": 0}
        rows = [["10"], ["2"], [""]]

        rows.sort(key=lambda row: scanner._sort_value("Score", row, indexes), reverse=True)
        rows.sort(key=lambda row: scanner._sort_value("Score", row, indexes)[0])

        self.assertEqual(rows, [["10"], ["2"], [""]])

    def test_gui_cell_text_handles_non_string_table_values(self):
        scanner = object.__new__(gui.ScannerGUI)
        indexes = {"Score": 0}

        self.assertEqual(scanner._cell_text(None), "")
        self.assertEqual(scanner._cell_text(True), "True")
        self.assertEqual(scanner._cell_text(12.5), "12.5")
        self.assertEqual(scanner._sort_value("Score", [12.5], indexes), (False, 12.5))

    def test_gui_load_csv_reloads_when_same_file_is_updated(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner._csv_path = None
        scanner._csv_mtime = None
        scanner._update_filter_values = Mock()
        scanner._render_cached_rows = Mock(return_value=True)
        scanner.status = Mock()

        with TemporaryDirectory() as temp_dir, patch("gui.OUTPUT_DIR", Path(temp_dir)):
            path = Path(temp_dir) / "AllResults.csv"
            path.write_text("Ticker,Close\n605499.SH,124.00\n", encoding="utf-8-sig")
            self.assertTrue(scanner.load_csv("AllResults.csv"))
            first_mtime = scanner._csv_mtime

            path.write_text("Ticker,Close\n605499.SH,125.82\n", encoding="utf-8-sig")
            self.assertTrue(scanner.load_csv("AllResults.csv"))

        self.assertNotEqual(scanner._csv_mtime, first_mtime)
        self.assertEqual(scanner._csv_rows, [["605499.SH", "125.82"]])
        self.assertEqual(scanner._update_filter_values.call_count, 2)

    def test_gui_refresh_results_resets_pagination_before_reloading(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner.current_file = "AllResults.csv"
        scanner._current_page = 1
        scanner._csv_path = Path("cached.csv")
        scanner._csv_mtime = (1, 1)
        scanner._filter_job = "filter-job"
        scanner.root = Mock()
        scanner.load_csv = Mock(return_value=True)
        scanner.status = Mock()

        self.assertTrue(scanner.refresh_results())

        scanner.root.after_cancel.assert_called_once_with("filter-job")
        self.assertIsNone(scanner._filter_job)
        self.assertEqual(scanner._current_page, 0)
        self.assertIsNone(scanner._csv_path)
        self.assertIsNone(scanner._csv_mtime)
        scanner.load_csv.assert_called_once_with("AllResults.csv")

    def test_gui_load_csv_clears_old_state_for_empty_or_unrenderable_results(self):
        for content in ("", "Unknown\nvalue\n"):
            scanner = object.__new__(gui.ScannerGUI)
            scanner._csv_headers = ["Ticker"]
            scanner._csv_rows = [["000001.SZ"]]
            scanner._csv_path = Path("cached.csv")
            scanner._csv_mtime = (1, 1)
            scanner.filtered_tickers = ["000001.SZ"]
            scanner._current_page = 1
            scanner._row_details = {"row-1": {"Ticker": "000001.SZ"}}
            scanner.status = Mock()
            scanner._clear_result_view = Mock(wraps=scanner._clear_result_view)

            with TemporaryDirectory() as temp_dir, patch("gui.OUTPUT_DIR", Path(temp_dir)):
                (Path(temp_dir) / "AllResults.csv").write_text(content, encoding="utf-8-sig")
                self.assertFalse(scanner.load_csv("AllResults.csv"))

            scanner._clear_result_view.assert_called_once_with()
            self.assertEqual(scanner._csv_headers, [])
            self.assertEqual(scanner._csv_rows, [])
            self.assertEqual(scanner.filtered_tickers, [])
            self.assertEqual(scanner._current_page, 0)
            self.assertEqual(scanner._row_details, {})
            scanner.status.set.assert_called_once_with("AllResults.csv 没有可展示结果")

    def test_gui_load_csv_pads_short_rows_without_reusing_old_values(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner._csv_headers = ["Ticker", "Close"]
        scanner._csv_rows = [["000001.SZ", "99"]]
        scanner._csv_path = None
        scanner._csv_mtime = None
        scanner.filtered_tickers = []
        scanner._row_details = {}
        scanner._update_filter_values = Mock()
        scanner.search = Mock()
        scanner.search.get.return_value = ""
        scanner.sector_filter = Mock()
        scanner.sector_filter.get.return_value = "全部板块"
        scanner.industry_filter = Mock()
        scanner.industry_filter.get.return_value = "全部行业"
        scanner.quality_filter = Mock()
        scanner.quality_filter.get.return_value = "全部质量"
        scanner.table = MagicMock()
        scanner.table.get_children.return_value = []
        scanner._row_details = {}
        scanner.status = Mock()
        scanner.current_file = "AllResults.csv"
        scanner.market_overview = Mock()
        scanner.page_summary = Mock()
        scanner.previous_page_button = Mock()
        scanner.next_page_button = Mock()
        scanner._sort_column = "Close"
        scanner._sort_descending = True
        scanner._render_cached_rows = Mock()

        with TemporaryDirectory() as temp_dir, patch("gui.OUTPUT_DIR", Path(temp_dir)):
            (Path(temp_dir) / "AllResults.csv").write_text(
                "Ticker,Close\n000002.SZ\n", encoding="utf-8-sig"
            )
            self.assertTrue(scanner.load_csv("AllResults.csv"))

        self.assertEqual(scanner._csv_rows, [["000002.SZ"]])
        scanner._render_cached_rows.assert_called_once_with()

    def test_gui_load_csv_clears_old_state_when_file_is_missing(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner._csv_headers = ["Ticker"]
        scanner._csv_rows = [["000001.SZ"]]
        scanner._csv_path = Path("cached.csv")
        scanner._csv_mtime = (1, 1)
        scanner.filtered_tickers = ["000001.SZ"]
        scanner._current_page = 1
        scanner._row_details = {"row-1": {"Ticker": "000001.SZ"}}
        scanner.status = Mock()
        scanner._clear_result_view = Mock(wraps=scanner._clear_result_view)

        with TemporaryDirectory() as temp_dir, patch("gui.OUTPUT_DIR", Path(temp_dir)):
            self.assertFalse(scanner.load_csv("AllResults.csv"))

        scanner._clear_result_view.assert_called_once_with()
        self.assertEqual(scanner._csv_headers, [])
        self.assertEqual(scanner._csv_rows, [])
        self.assertEqual(scanner.filtered_tickers, [])
        self.assertEqual(scanner._current_page, 0)
        self.assertEqual(scanner._row_details, {})
        scanner.status.set.assert_called_once_with("未找到 AllResults.csv")

    def test_gui_load_csv_clears_old_state_when_reading_fails(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner._csv_headers = ["Ticker"]
        scanner._csv_rows = [["000001.SZ"]]
        scanner._csv_path = Path("cached.csv")
        scanner._csv_mtime = (1, 1)
        scanner.filtered_tickers = ["000001.SZ"]
        scanner._current_page = 1
        scanner._row_details = {"row-1": {"Ticker": "000001.SZ"}}
        scanner.status = Mock()
        scanner._clear_result_view = Mock(wraps=scanner._clear_result_view)

        with TemporaryDirectory() as temp_dir, patch("gui.OUTPUT_DIR", Path(temp_dir)), patch("gui.messagebox.showerror") as showerror:
            (Path(temp_dir) / "AllResults.csv").write_bytes(b"\xff\xfe")
            self.assertFalse(scanner.load_csv("AllResults.csv"))

        scanner._clear_result_view.assert_called_once_with()
        self.assertEqual(scanner._csv_headers, [])
        self.assertEqual(scanner._csv_rows, [])
        self.assertEqual(scanner.filtered_tickers, [])
        self.assertEqual(scanner._current_page, 0)
        self.assertEqual(scanner._row_details, {})
        scanner.status.set.assert_called_once_with("读取 AllResults.csv 失败")
        showerror.assert_called_once()

    def test_gui_best_available_results_skips_empty_top50_and_loads_all_results(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner.load_csv = Mock(return_value=True)
        with TemporaryDirectory() as temp_dir, patch("gui.OUTPUT_DIR", Path(temp_dir)):
            output_dir = Path(temp_dir)
            (output_dir / "Top50.csv").write_text("Ticker,Score\n", encoding="utf-8-sig")
            (output_dir / "AllResults.csv").write_text("Ticker,Score\n000001.SZ,90\n", encoding="utf-8-sig")

            self.assertTrue(scanner._load_best_available_results())

        scanner.load_csv.assert_called_once_with("AllResults.csv")

    def test_cache_path_isolated_by_source(self):
        eastmoney = _cache_path("600000.SH", "eastmoney")
        sina = _cache_path("600000.SH", "sina")
        self.assertNotEqual(eastmoney, sina)
        self.assertTrue(str(eastmoney).endswith("600000.SH__eastmoney.parquet"))

    def test_invalid_latest_values_fail_basic_filters(self):
        frame = pd.DataFrame({"Close": [10, np.nan], "Volume": [1000, np.nan]})
        self.assertFalse(filter_min_price(frame).passed)
        self.assertFalse(filter_min_volume(frame).passed)

    def test_bear_filter_rejects_less_than_two_years(self):
        frame = pd.DataFrame({
            "Close": np.linspace(100, 50, 300),
            "MA200": np.linspace(100, 50, 300),
        })
        self.assertFalse(filter_bear_market(frame).passed)

    def test_non_finite_filter_values_are_rejected(self):
        frame = pd.DataFrame({
            "Close": [10.0] * 140,
            "Volume": [np.inf] * 140,
            "VolMA20": [np.inf] * 140,
            "VolMA120": [100.0] * 140,
        })

        self.assertFalse(filter_min_volume(frame).passed)
        self.assertFalse(filter_volume_accumulation(frame).passed)

    def test_non_finite_ad_slope_is_rejected(self):
        frame = pd.DataFrame({"AD_Slope": [0.1] * 29 + [np.inf]})

        from filters import filter_ad_slope_positive
        self.assertFalse(filter_ad_slope_positive(frame).passed)

    def test_export_dataframe_supports_backtest_fields(self):
        result = ScanResult(ticker="000001.SZ")
        frame = _results_to_dataframe([result])

        self.assertEqual(frame.loc[0, "Ticker"], "000001.SZ")
        self.assertTrue(pd.isna(frame.loc[0, "BacktestObjectiveValue"]))
        self.assertEqual(frame.loc[0, "UniverseType"], "current_survivor_pool")
        self.assertTrue(frame.loc[0, "SurvivorshipBiasWarning"])

    def test_export_all_writes_lifecycle_and_sorted_top_files(self):
        results = [
            ScanResult(
                ticker="000001.SZ",
                data_asof="2026-07-24",
                passed_filters=True,
                score=score_ticker(pd.DataFrame({"Close": [10.0]})),
                filter_details={"signal_count": 4},
            ),
            ScanResult(
                ticker="000002.SZ",
                data_asof="2026-07-24",
                passed_filters=True,
                score=score_ticker(pd.DataFrame({"Close": [10.0]})),
                filter_details={"signal_count": 3},
            ),
        ]
        results[0].score.total = 90.0
        results[0].score.trend = 25.0
        results[0].score.accumulation = 25.0
        results[0].score.structure = 20.0
        results[1].score.total = 80.0
        results[1].score.trend = 20.0
        results[1].score.accumulation = 20.0
        results[1].score.structure = 15.0

        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            with patch("report.OUTPUT_DIR", output_dir), patch("signal_lifecycle.OUTPUT_DIR", output_dir), patch("signal_lifecycle.HISTORY_FILE", output_dir / "SignalHistory.csv"), patch("signal_lifecycle.TRACKING_FILE", output_dir / "SignalTracking.csv"):
                export_all(results, top_n_csv=2, top_n_parquet=2)
            all_results = pd.read_csv(Path(temp_dir) / "AllResults.csv", encoding="utf-8-sig")
            opportunity = pd.read_csv(Path(temp_dir) / "Top2Opportunity.csv", encoding="utf-8-sig")
            sustained = pd.read_csv(Path(temp_dir) / "Top2SustainedSignals.csv", encoding="utf-8-sig")

        self.assertIn("OpportunityScore", all_results)
        self.assertIn("SignalDays", all_results)
        self.assertEqual(opportunity["Ticker"].tolist(), ["000001.SZ", "000002.SZ"])
        self.assertEqual(sustained["Ticker"].tolist(), ["000001.SZ", "000002.SZ"])

    def test_signal_lifecycle_preserves_quality_tier_downgrade(self):
        frame = pd.DataFrame({
            "Ticker": ["000001.SZ"],
            "DataAsOf": ["2026-07-24"],
            "Score": [90.0],
            "InstitutionalScore": [90.0],
            "VolumeScore": [15.0],
            "QualityDataAvailable": [True],
            "QualityGate": [False],
            "IsETF": [False],
        })

        with TemporaryDirectory() as temp_dir, patch.object(
            signal_lifecycle, "HISTORY_FILE", Path(temp_dir) / "SignalHistory.csv"
        ), patch.object(
            signal_lifecycle, "TRACKING_FILE", Path(temp_dir) / "SignalTracking.csv"
        ):
            result = signal_lifecycle.enrich_signal_lifecycle(frame)

        self.assertEqual(result.loc[0, "InstitutionalTier"], "C级价值观察")

    def test_gui_build_command_keeps_scope_for_specified_tickers(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner.tickers = Mock()
        scanner.tickers.get.return_value = "600036.SH,510300.SH"
        scanner.scope = Mock()
        scanner.scope.get.return_value = "仅股票"
        scanner.no_resume = Mock()
        scanner.no_resume.get.return_value = False
        scanner.force_download = Mock()
        scanner.force_download.get.return_value = False
        scanner.refresh_fundamentals = Mock()
        scanner.refresh_fundamentals.get.return_value = False
        scanner.data_source = Mock()
        scanner.data_source.get.return_value = "eastmoney"

        command = scanner.build_command()

        self.assertIn("--tickers", command)
        self.assertIn("--stocks-only", command)
        self.assertNotIn("--etfs-only", command)

    def test_gui_build_command_includes_requested_fundamental_refresh(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner.tickers = Mock()
        scanner.tickers.get.return_value = ""
        scanner.scope = Mock()
        scanner.scope.get.return_value = "全部股票和ETF"
        scanner.no_resume = Mock()
        scanner.no_resume.get.return_value = False
        scanner.force_download = Mock()
        scanner.force_download.get.return_value = False
        scanner.refresh_fundamentals = Mock()
        scanner.refresh_fundamentals.get.return_value = True
        scanner.data_source = Mock()
        scanner.data_source.get.return_value = "东方财富"

        command = scanner.build_command()

        self.assertIn("--refresh-fundamentals", command)

    def test_signal_lifecycle_same_trade_date_does_not_increment_signal_days(self):
        frame = pd.DataFrame({
            "Ticker": ["000001.SZ"],
            "DataAsOf": ["2026-07-24"],
            "Name": ["平安银行"],
            "Score": [60.0],
            "SignalCount": [4],
            "PassedFilters": [True],
        })

        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            with patch("signal_lifecycle.HISTORY_FILE", output_dir / "SignalHistory.csv"), patch("signal_lifecycle.TRACKING_FILE", output_dir / "SignalTracking.csv"), warnings.catch_warnings():
                warnings.simplefilter("error", FutureWarning)
                first = signal_lifecycle.enrich_signal_lifecycle(frame)
                second = signal_lifecycle.enrich_signal_lifecycle(frame)
            history = pd.read_csv(output_dir / "SignalHistory.csv", encoding="utf-8-sig")

        self.assertEqual(first.loc[0, "SignalDays"], 1)
        self.assertEqual(second.loc[0, "SignalDays"], 1)
        self.assertEqual(history["SignalDays"].tolist(), [1])

    def test_signal_lifecycle_increments_despite_mixed_data_dates(self):
        first_frame = pd.DataFrame({
            "Ticker": ["000001.SZ", "000002.SZ"],
            "DataAsOf": ["2026-07-24", "2026-07-24"],
            "Name": ["平安银行", "万科A"],
            "Score": [60.0, 60.0],
            "SignalCount": [4, 4],
            "PassedFilters": [True, True],
        })
        second_frame = first_frame.copy()
        second_frame.loc[0, "DataAsOf"] = "2026-07-27"

        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            with patch("signal_lifecycle.HISTORY_FILE", output_dir / "SignalHistory.csv"), patch("signal_lifecycle.TRACKING_FILE", output_dir / "SignalTracking.csv"):
                signal_lifecycle.enrich_signal_lifecycle(first_frame)
                result = signal_lifecycle.enrich_signal_lifecycle(second_frame)

        self.assertEqual(result.loc[0, "SignalDays"], 2)
        self.assertEqual(result.loc[0, "SignalStatus"], "WATCH")

    def test_signal_lifecycle_resets_when_ticker_was_absent_on_previous_trade_date(self):
        frame = pd.DataFrame({
            "Ticker": ["000001.SZ"],
            "DataAsOf": ["2026-07-24"],
            "Name": ["平安银行"],
            "Score": [60.0],
            "SignalCount": [4],
            "PassedFilters": [True],
        })
        history = pd.DataFrame({
            "TradeDate": ["2026-07-22", "2026-07-23"],
            "Ticker": ["000001.SZ", "000002.SZ"],
            "Name": ["平安银行", "万科A"],
            "Score": [60.0, 20.0],
            "OpportunityScore": [50.0, 20.0],
            "ScoreConfidence": [1.0, 1.0],
            "SignalActive": [True, False],
            "SignalStatus": ["NEW", ""],
            "SignalDays": [5, 0],
            "SignalStartDate": ["2026-07-18", ""],
            "Stage": ["机构吸筹", "底部观察"],
            "TrendScore": [10.0, 5.0],
            "AccumulationScore": [15.0, 5.0],
            "IndustryRelativeStrength": [0.0, 0.0],
            "SignalCount": [4, 1],
        })

        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            history.to_csv(output_dir / "SignalHistory.csv", index=False, encoding="utf-8-sig")
            with patch("signal_lifecycle.HISTORY_FILE", output_dir / "SignalHistory.csv"), patch("signal_lifecycle.TRACKING_FILE", output_dir / "SignalTracking.csv"):
                result = signal_lifecycle.enrich_signal_lifecycle(frame)

        self.assertEqual(result.loc[0, "SignalDays"], 1)
        self.assertEqual(result.loc[0, "SignalStatus"], "NEW")

    def test_apply_backtest_ranking_cleans_legacy_columns_on_repeated_calls(self):
        with patch("analytics.OUTPUT_DIR") as output_dir:
            from tempfile import TemporaryDirectory
            with TemporaryDirectory() as temp_dir:
                output_dir.__truediv__.side_effect = lambda name: __import__("pathlib").Path(temp_dir) / name
                all_results = output_dir / "AllResults.csv"
                pd.DataFrame({
                    "Ticker": ["000001.SZ", "600000.SH"], "Score": [60, 50], "PassedFilters": [True, False], "SignalCount": [3, 2],
                    "BacktestScore": [1, 2], "CompositeScore": [3, 4], "backtest_score": [5, 6], "composite_score": [7, 8], "samples": [1, 1],
                    "raw_objective_value_x": [8, 9], "raw_objective_value_y": [10, 11],
                }).to_csv(all_results, index=False, encoding="utf-8-sig")
                summary = BacktestSummary(by_ticker=[{
                    "ticker": "000001.SZ", "samples": 4, "win_rate_20d": 0.75, "win_rate_60d": 0.5,
                    "average_return_20d": 2.0, "average_return_60d": 4.0, "raw_objective_value": 4.0, "backtest_score": 80.0,
                }])
                with patch("pandas.DataFrame.to_parquet"):
                    apply_backtest_ranking(summary)
                    apply_backtest_ranking(summary)

                result = pd.read_csv(all_results, encoding="utf-8-sig")
                self.assertEqual(result.columns.tolist().count("BacktestScore"), 1)
                self.assertEqual(result.columns.tolist().count("CompositeScore"), 1)
                self.assertFalse(any(column.endswith(("_x", "_y")) for column in result.columns))
                self.assertNotIn("backtest_score", result.columns)
                self.assertNotIn("samples", result.columns)
                self.assertFalse(any(column.startswith("raw_objective_value") for column in result.columns))
                self.assertEqual(int(result.loc[result["Ticker"] == "000001.SZ", "BacktestSamples"].iloc[0]), 4)
    def test_resume_scan_restores_previous_results_with_missing_metrics(self):
        ticker = TickerInfo(ticker="000001.SZ")
        frame = pd.DataFrame({
            "Open": [10.0], "High": [11.0], "Low": [9.0], "Close": [10.0], "Volume": [1000.0],
        }, index=pd.to_datetime(["2026-07-21"]))
        previous = pd.DataFrame({
            "Ticker": ["000001.SZ"],
            "Name": ["平安银行"],
            "IsETF": [False],
            "Close": [10.0],
            "Score": [50.0],
            "TrendScore": [10.0],
            "VolumeScore": [10.0],
            "AccumulationScore": [10.0],
            "CompressionScore": [10.0],
            "StructureScore": [10.0],
            "BacktestScore": [None],
            "BacktestWinRate20D": [None],
            "BacktestWinRate60D": [None],
            "BacktestAverageReturn20D": [None],
            "BacktestAverageReturn60D": [None],
            "BacktestObjectiveValue": [None],
            "CompositeScore": [None],
            "OBV": [None],
            "CMF": [None],
            "AD": [None],
            "ATR14": [None],
            "RSI14": [None],
            "DistToLow52W": [None],
            "IndustryRelativeStrength": [None],
            "DataSource": ["eastmoney"],
        })
        metadata = pd.DataFrame({"DataSource": ["eastmoney"]})
        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            report_path = output_dir / "AllResults.parquet"
            cache_path = output_dir / "000001.SZ__eastmoney.parquet"
            cache_path.touch()
            report_path.touch()
            with patch.object(scanner, "OUTPUT_DIR", output_dir), patch.object(scanner, "_CHECKPOINT_PATH", output_dir / "_checkpoint.json"), patch.object(scanner, "load_checkpoint", return_value={"000001.SZ"}), patch.object(scanner, "_load_previous_tickers", return_value={"000001.SZ"}), patch.object(scanner, "_cache_path_for", return_value=cache_path), patch.object(scanner, "download_batch", return_value={"000001.SZ": frame}) as download_batch, patch.object(scanner, "enrich_results"), patch.object(scanner, "save_checkpoint"), patch.object(scanner, "clear_checkpoint") as clear_checkpoint, patch.object(scanner.pd, "read_parquet", side_effect=[metadata, previous]):
                report = scanner.run_scan(stock_universe=[ticker], etf_universe=[], data_source="eastmoney")

        clear_checkpoint.assert_called_once_with()
        self.assertEqual(download_batch.call_args.kwargs["skip_tickers"], {"000001.SZ"})
        self.assertEqual(report.successful, 1)
        self.assertEqual([result.ticker for result in report.results], ["000001.SZ"])
        self.assertTrue(pd.isna(report.results[0].backtest_score))

    def test_resume_scan_redownloads_checkpoint_ticker_without_previous_report(self):
        ticker = TickerInfo(ticker="000001.SZ")
        frame = pd.DataFrame(
            {
                "Open": [10.0],
                "High": [11.0],
                "Low": [9.0],
                "Close": [10.0],
                "Volume": [1000.0],
            },
            index=pd.to_datetime(["2026-07-21"]),
        )
        result = ScanResult(ticker="000001.SZ")
        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            with (
                patch.object(scanner, "OUTPUT_DIR", output_dir),
                patch.object(scanner, "_CHECKPOINT_PATH", output_dir / "_checkpoint.json"),
                patch.object(scanner, "load_checkpoint", return_value={"000001.SZ"}),
                patch.object(scanner, "_load_previous_tickers", return_value=set()),
                patch.object(
                    scanner, "download_batch", return_value={"000001.SZ": frame}
                ) as download_batch,
                patch.object(
                    scanner, "_analyse_one_ticker_from_df", return_value=(result, frame)
                ),
                patch.object(scanner, "enrich_results"),
                patch.object(scanner, "save_checkpoint"),
                patch.object(scanner, "clear_checkpoint"),
            ):
                report = scanner.run_scan(
                    stock_universe=[ticker], etf_universe=[], data_source="eastmoney"
                )

        self.assertEqual(download_batch.call_args.kwargs["skip_tickers"], set())
        self.assertEqual([item.ticker for item in report.results], ["000001.SZ"])

    def test_load_checkpoint_ignores_legacy_completed_scan(self):
        with TemporaryDirectory() as temp_dir:
            checkpoint_path = Path(temp_dir) / "_checkpoint.json"
            checkpoint_path.write_text(
                '{"processed": ["000001.SZ"], "data_source": "eastmoney", "scoring_version": "' + scanner.SCORING_VERSION + '"}',
                encoding="utf-8",
            )
            with patch.object(scanner, "_CHECKPOINT_PATH", checkpoint_path):
                self.assertEqual(scanner.load_checkpoint("eastmoney"), set())

    def test_load_checkpoint_ignores_a_previous_trade_date(self):
        with TemporaryDirectory() as temp_dir:
            checkpoint_path = Path(temp_dir) / "_checkpoint.json"
            checkpoint_path.write_text(
                '{"active": true, "processed": ["000001.SZ"], '
                '"trade_date": "2026-08-03", "data_source": "eastmoney", '
                '"scoring_version": "' + scanner.SCORING_VERSION + '"}',
                encoding="utf-8",
            )
            with patch.object(scanner, "_CHECKPOINT_PATH", checkpoint_path), patch.object(
                scanner, "_checkpoint_trade_date", return_value="2026-08-04"
            ):
                self.assertEqual(scanner.load_checkpoint("eastmoney"), set())

    def test_max_drawdown_ranking_prefers_shallower_losses(self):
        with TemporaryDirectory() as temp_dir, patch("analytics.OUTPUT_DIR", Path(temp_dir)), patch("pandas.DataFrame.to_parquet"):
            pd.DataFrame({
                "Ticker": ["000001.SZ", "600000.SH"],
                "Score": [50.0, 50.0],
                "PassedFilters": [True, True],
                "SignalCount": [4, 4],
            }).to_csv(Path(temp_dir) / "AllResults.csv", index=False, encoding="utf-8-sig")
            summary = BacktestSummary(
                objective="max_drawdown",
                by_ticker=[
                    {"ticker": "000001.SZ", "samples": 10, "backtest_score": 50.0, "objective_value": -5.0},
                    {"ticker": "600000.SH", "samples": 10, "backtest_score": 50.0, "objective_value": -20.0},
                ],
            )

            apply_backtest_ranking(summary)
            result = pd.read_csv(Path(temp_dir) / "AllResults.csv", encoding="utf-8-sig")

        self.assertEqual(result.iloc[0]["Ticker"], "000001.SZ")
        self.assertGreater(result.iloc[0]["CompositeScore"], result.iloc[1]["CompositeScore"])

    def test_wyckoff_phase_reuses_precomputed_moving_averages(self):
        index = pd.date_range("2020-01-01", periods=260)
        close = pd.Series(np.linspace(20.0, 10.0, 260), index=index)
        raw = pd.DataFrame(
            {
                "Open": close,
                "High": close + 1.0,
                "Low": close - 1.0,
                "Close": close,
                "Volume": np.linspace(1_000.0, 2_000.0, 260),
            },
            index=index,
        )
        precomputed = raw.copy()
        compute_moving_averages(precomputed)
        compute_volume_mas(precomputed)

        detect_wyckoff_phase(raw)
        detect_wyckoff_phase(precomputed)

        self.assertEqual(
            raw["WyckoffPhase"].iloc[-1],
            precomputed["WyckoffPhase"].iloc[-1],
        )

    def test_volume_profile_accepts_numpy_bool(self):
        frame = pd.DataFrame({
            "Close": np.full(252, 10.0),
            "High": np.full(252, 10.2),
            "Low": np.full(252, 9.8),
            "DistToLow52W": np.full(252, 5.0),
            "RegSlope": np.zeros(252),
            "RegR2": np.ones(252),
            "Above_HVN": np.array([np.bool_(True)] * 252),
            "DistToHVN_Pct": np.full(252, 2.0),
        })
        from score import score_structure
        self.assertGreaterEqual(score_structure(frame), 2.0)

    def test_score_ticker_returns_finite_scores_for_invalid_indicators(self):
        frame = pd.DataFrame({
            "Close": [10.0] * 252,
            "High": [11.0] * 252,
            "Low": [9.0] * 252,
            "Volume": [1000.0] * 252,
            "MA200": [np.nan] * 252,
            "VolMA20": [np.inf] * 252,
            "VolMA120": [-np.inf] * 252,
            "OBV": [np.nan] * 252,
            "AD": [np.inf] * 252,
            "AD_Slope": [np.nan] * 252,
            "CMF": [-np.inf] * 252,
            "MFI": [np.nan] * 252,
            "ATR14": [np.inf] * 252,
            "ATR50": [np.nan] * 252,
            "BB_Width": [np.inf] * 252,
            "HV20": [np.nan] * 252,
            "HV60": [-np.inf] * 252,
            "Low52W": [np.nan] * 252,
            "DistToLow52W": [np.nan] * 252,
            "RegSlope": [np.inf] * 252,
            "RegR2": [-np.inf] * 252,
            "Above_HVN": [True] * 252,
            "DistToHVN_Pct": [np.nan] * 252,
        })
        score = score_ticker(frame)
        self.assertTrue(all(np.isfinite(value) for value in score.__dict__.values()))
        self.assertTrue(all(np.isfinite(value) for value in score.to_dict().values()))

    def test_score_accumulation_requires_computable_indicator_history(self):
        frame = pd.DataFrame({
            "Close": [10.0] * 60,
            "OBV": [1.0] * 10 + [np.nan] * 50,
        })

        self.assertFalse(_score_dimensions_available(frame)[2])

    def test_score_structure_uses_configured_consolidation_range(self):
        frame = pd.DataFrame({
            "Close": [100.0] * 252,
            "High": [110.0] * 252,
            "Low": [90.0] * 252,
            "DistToLow52W": [10.0] * 252,
        })

        self.assertGreater(score_structure(frame), 0.0)

    def test_backtest_score_is_robust_to_single_extreme_return(self):
        returns = [2.0] * 9 + [200.0]
        frame = pd.DataFrame({
            "ticker": ["000001.SZ"] * len(returns),
            "return20": returns,
            "return60": returns,
            "net_return20": returns,
            "net_return60": returns,
            "drawdown20": [-5.0] * len(returns),
            "drawdown60": [-8.0] * len(returns),
            "benchmark_return20": [0.0] * len(returns),
            "benchmark_return60": [0.0] * len(returns),
        })

        result = _ticker_backtest_rows(frame)[0]

        self.assertLess(result["average_return_20d"], 30.0)

    def test_score_ticker_normalizes_using_available_indicator_weights(self):
        frame = pd.DataFrame({
            "Close": [10.0] * 252,
            "High": [11.0] * 252,
            "Low": [9.0] * 252,
            "Volume": [1000.0] * 252,
            "MA200": [np.nan] * 252,
            "VolMA20": [np.nan] * 252,
            "OBV": [np.nan] * 252,
            "ATR14": [np.nan] * 252,
        })

        score = score_ticker(frame)

        self.assertEqual(score.indicator_coverage, 0.2)
        self.assertEqual(score.total, score.structure / 15.0 * 100.0)

    def test_score_ticker_returns_zero_when_no_dimensions_are_available(self):
        frame = pd.DataFrame({"Close": [10.0] * 60})

        score = score_ticker(frame)

        self.assertEqual(score.indicator_coverage, 0.0)
        self.assertEqual(score.total, 0.0)

    def test_score_ticker_marks_stale_latest_indicators_unavailable(self):
        frame = pd.DataFrame({
            "Close": [10.0] * 252,
            "High": [11.0] * 252,
            "Low": [9.0] * 252,
            "MA200": [9.0] * 251 + [pd.NA],
            "VolMA20": [120.0] * 252,
            "VolMA120": [100.0] * 252,
            "OBV": [100.0] * 252,
            "ATR14": [pd.NA] * 252,
            "BB_Width": [pd.NA] * 252,
        })

        score = score_ticker(frame)

        self.assertEqual(score.trend, 0.0)
        self.assertEqual(score.indicator_coverage, 0.6)
        self.assertTrue(np.isfinite(score.total))

    def test_classify_style_uses_computed_roc_column(self):
        from score import classify_style

        frame = pd.DataFrame({
            "Close": [10.0] * 60,
            "ATR14": [0.3] * 60,
            "ROC": [12.0] * 60,
            "VolMA20": [100.0] * 60,
            "VolMA120": [100.0] * 60,
        })

        self.assertEqual(classify_style(frame), "趋势成长")

    def test_cmd_scan_classifies_specified_etfs(self):
        args = argparse.Namespace(
            tickers="600036.SH,510300.SH,159915",
            etfs_only=False,
            stocks_only=False,
            force_download=False,
            no_resume=False,
            data_source="eastmoney",
            cache_first=False,
            top=50,
            top_parquet=200,
        )
        report = ScanReport(successful=1)
        with patch("main.run_scan", return_value=report) as run_scan, patch("main.export_all", return_value=(Path("top.csv"), Path("top.parquet"), Path("all.csv"), Path("all.parquet"))), patch("main.print_terminal_report"), patch("main.print_scan_summary"):
            self.assertEqual(main.cmd_scan(args), 0)

        stock_universe = run_scan.call_args.kwargs["stock_universe"]
        etf_universe = run_scan.call_args.kwargs["etf_universe"]
        self.assertEqual([ticker.ticker for ticker in stock_universe], ["600036.SH"])
        self.assertEqual([ticker.ticker for ticker in etf_universe], ["510300.SH", "159915.SZ"])
        self.assertTrue(all(ticker.is_etf and ticker.asset_type == "etf" for ticker in etf_universe))

    def test_cmd_scan_respects_scope_for_specified_tickers(self):
        report = ScanReport(successful=1)
        common = {
            "tickers": "600036.SH,510300.SH",
            "force_download": False,
            "no_resume": False,
            "data_source": "eastmoney",
            "cache_first": False,
            "top": 50,
            "top_parquet": 200,
        }
        with patch("main.run_scan", return_value=report) as run_scan, patch(
            "main.export_all", return_value=(Path("top.csv"), Path("top.parquet"), Path("all.csv"), Path("all.parquet"))
        ), patch("main.print_terminal_report"), patch("main.print_scan_summary"):
            self.assertEqual(
                main.cmd_scan(argparse.Namespace(**common, stocks_only=True, etfs_only=False)),
                0,
            )
            self.assertEqual(
                [ticker.ticker for ticker in run_scan.call_args.kwargs["stock_universe"]],
                ["600036.SH"],
            )
            self.assertEqual(run_scan.call_args.kwargs["etf_universe"], [])

            self.assertEqual(
                main.cmd_scan(argparse.Namespace(**common, stocks_only=False, etfs_only=True)),
                0,
            )
            self.assertEqual(run_scan.call_args.kwargs["stock_universe"], [])
            self.assertEqual(
                [ticker.ticker for ticker in run_scan.call_args.kwargs["etf_universe"]],
                ["510300.SH"],
            )

    def test_cmd_scan_normalizes_and_deduplicates_specified_tickers(self):
        args = argparse.Namespace(
            tickers="600036,600036.SH,510300,510300.SH",
            etfs_only=False,
            stocks_only=False,
            force_download=False,
            no_resume=False,
            data_source="eastmoney",
            cache_first=False,
            top=50,
            top_parquet=200,
        )
        report = ScanReport(successful=1)
        with patch("main.run_scan", return_value=report) as run_scan, patch("main.export_all", return_value=(Path("top.csv"), Path("top.parquet"), Path("all.csv"), Path("all.parquet"))), patch("main.print_terminal_report"), patch("main.print_scan_summary"):
            self.assertEqual(main.cmd_scan(args), 0)

        self.assertEqual([ticker.ticker for ticker in run_scan.call_args.kwargs["stock_universe"]], ["600036.SH"])
        self.assertEqual([ticker.ticker for ticker in run_scan.call_args.kwargs["etf_universe"]], ["510300.SH"])

    def test_report_enriches_results_with_selected_data_source(self):
        args = argparse.Namespace(
            stocks_only=False,
            etfs_only=False,
            data_source="sina",
            top=50,
            top_parquet=200,
        )
        result = ScanResult(ticker="000001.SZ")
        with patch("main.build_ticker_universe", return_value=([TickerInfo(ticker="000001.SZ")], [])), patch("main.run_parallel_indicator_scan", return_value=[result]), patch("main.enrich_results") as enrich, patch("main.export_all", return_value=(Path("top.csv"), Path("top.parquet"), Path("all.csv"), Path("all.parquet"))), patch("main.print_terminal_report"):
            self.assertEqual(main.cmd_report(args), 0)

        enrich.assert_called_once_with([result], "sina")

    def test_parser_rejects_non_positive_report_limits(self):
        parser = main.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["scan", "--top", "0"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["report", "--top-parquet", "-1"])

    def test_parser_rejects_conflicting_scope_options(self):
        parser = main.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(["report", "--stocks-only", "--etfs-only"])
        with self.assertRaises(SystemExit):
            parser.parse_args(["download", "--stocks-only", "--etfs-only"])

    def test_backtest_objective_rows_build_derived_targets(self):
        frame = pd.DataFrame({
            "ticker": ["000001.SZ"],
            "return20": [5.0],
            "return60": [8.0],
            "benchmark_return20": [2.0],
            "benchmark_return60": [3.0],
            "net_return20": [4.0],
            "drawdown20": [-2.0],
            "drawdown60": [-6.0],
        })

        excess = analytics._ticker_backtest_rows(frame, "excess_return_20d")
        risk = analytics._ticker_backtest_rows(frame, "risk_adjusted")

        self.assertEqual(excess[0]["raw_objective_value"], 3.0)
        self.assertEqual(excess[0]["objective_value"], 0.3)
        self.assertEqual(risk[0]["raw_objective_value"], 2.0)
        self.assertEqual(risk[0]["objective_value"], 0.2)

    def test_backtest_net_excess_objective_deducts_costs_and_shrinks_small_samples(self):
        frame = pd.DataFrame({
            "ticker": ["000001.SZ", "000001.SZ"],
            "return20": [5.0, 3.0],
            "return60": [8.0, 6.0],
            "benchmark_return20": [2.0, 1.0],
            "benchmark_return60": [3.0, 2.0],
            "net_return20": [4.0, 2.0],
            "net_return60": [7.0, 5.0],
            "drawdown20": [-2.0, -2.0],
            "drawdown60": [-6.0, -6.0],
        })

        rows = analytics._ticker_backtest_rows(frame, "net_excess_return_20d")

        self.assertEqual(rows[0]["raw_objective_value"], 1.5)
        self.assertEqual(rows[0]["objective_value"], 0.3)
    def test_filter_signal_count_excludes_bear_market_context(self):
        result = __import__("filters").AllFilterResults()
        result.min_price.passed = True
        result.min_volume.passed = True
        result.min_market_cap.passed = True
        result.sufficient_history.passed = True
        result.bear_market.passed = True
        result.consolidation.passed = True
        result.volume_accumulation.passed = True
        result.obv_divergence.passed = True

        self.assertTrue(result.all_passed())
        self.assertEqual(result.signal_count(), 3)

    def test_volume_filter_does_not_mutate_input_frame(self):
        frame = pd.DataFrame({
            "VolMA20": [120.0] * 140,
            "VolMA120": [100.0] * 140,
        })
        columns_before = list(frame.columns)

        filter_volume_accumulation(frame)

        self.assertEqual(list(frame.columns), columns_before)

    def test_backtest_drawdown_includes_entry_open_price(self):
        frame = pd.DataFrame({
            "Open": np.full(320, 100.0),
            "High": np.full(320, 100.0),
            "Low": np.full(320, 100.0),
            "Close": np.full(320, 100.0),
            "Volume": np.full(320, 1000.0),
        }, index=pd.date_range("2020-01-01", periods=320))
        frame.loc[frame.index[251], "Close"] = 90.0
        frame.loc[frame.index[251], "Low"] = 80.0
        with patch.object(analytics, "_load_cache", return_value=frame), patch.object(analytics, "compute_all_indicators", side_effect=lambda data: data), patch.object(analytics, "_signal_points", return_value=[250]), patch.object(analytics, "score_ticker", return_value=Mock(total=50.0)):
            samples = analytics._backtest_one_ticker("600036.SH", "eastmoney")

        self.assertEqual(len(samples), 1)
        self.assertAlmostEqual(samples[0]["drawdown20"], -20.0)
        self.assertAlmostEqual(samples[0]["drawdown60"], -20.0)

    def test_backtest_uses_final_score_when_available(self):
        frame = pd.DataFrame({
            "Open": np.full(320, 100.0),
            "High": np.full(320, 101.0),
            "Low": np.full(320, 99.0),
            "Close": np.full(320, 100.0),
            "Volume": np.full(320, 1000.0),
        }, index=pd.date_range("2020-01-01", periods=320))

        with patch.object(analytics, "_load_cache", return_value=frame), patch.object(
            analytics, "compute_all_indicators", side_effect=lambda data: data
        ), patch.object(analytics, "_signal_points", return_value=[250]), patch.object(
            analytics, "score_ticker", return_value=ScoreBreakdown(total=90.0, final_score=60.0)
        ):
            samples = analytics._backtest_one_ticker("000001.SZ", "eastmoney")

        self.assertEqual(samples[0]["score"], 60.0)

    def test_backtest_fails_explicitly_when_benchmark_is_unavailable(self):
        with patch.object(analytics, "_load_benchmark_frames", return_value={}):
            summary = analytics.run_historical_backtest(["000001.SZ"])

        self.assertTrue(summary.insufficient_test_data)
        self.assertIn("无法加载基准数据", summary.error or "")
        self.assertIsNone(summary.split_dates.get("test_start"))

    def test_backtest_uses_benchmark_trading_calendar_for_split_dates(self):
        benchmark_frame = pd.DataFrame({"Close": np.arange(10, dtype=float) + 100}, index=pd.bdate_range("2020-01-01", periods=10))
        captured_splits = []

        def backtest_one(*args):
            captured_splits.append(args[-1])
            return []

        with TemporaryDirectory() as temp_dir, patch.object(analytics, "OUTPUT_DIR", Path(temp_dir)), patch.object(analytics, "_load_benchmark_frames", return_value={"沪深300": benchmark_frame}), patch.object(analytics, "_backtest_one_ticker", side_effect=backtest_one):
            summary = analytics.run_historical_backtest(["000001.SZ"], test_ratio=0.2, validation_ratio=0.2)

        self.assertEqual(summary.split_dates["global_start"], "2020-01-01")
        self.assertEqual(summary.split_dates["validation_end"], "2020-01-09")
        self.assertEqual(summary.split_dates["test_start"], "2020-01-13")
        self.assertEqual(captured_splits, [(pd.Timestamp("2020-01-09"), pd.Timestamp("2020-01-13"))])

    def test_entry_date_equal_weight_stats_weights_each_entry_day_equally(self):
        samples = pd.DataFrame({
            "entry_date": ["2020-01-02", "2020-01-02", "2020-01-03"],
            "return20": [10.0, 30.0, 0.0],
            "return60": [20.0, 40.0, 10.0],
            "benchmark_return20": [2.0, 6.0, 0.0],
            "benchmark_return60": [4.0, 8.0, 2.0],
            "net_return20": [9.0, 29.0, -1.0],
            "net_return60": [19.0, 39.0, 9.0],
            "drawdown20": [-3.0, -5.0, -1.0],
            "drawdown60": [-6.0, -8.0, -2.0],
        })

        stats = analytics._entry_date_equal_weight_stats(samples)

        self.assertEqual(stats["entry_dates"], 2)
        self.assertEqual(stats["samples"], 3)
        self.assertAlmostEqual(stats["average_return_20d"], 10.0)
        self.assertAlmostEqual(stats["average_excess_return_20d"], 8.0)
        self.assertAlmostEqual(stats["maximum_drawdown_60d"], -7.0)

    def test_backtest_recomputes_indicators_for_historical_scores(self):
        frame = pd.DataFrame({
            "Open": np.full(320, 10.0),
            "High": np.full(320, 11.0),
            "Low": np.full(320, 9.0),
            "Close": np.full(320, 10.0),
            "Volume": np.full(320, 1000.0),
        }, index=pd.date_range("2020-01-01", periods=320))

        def add_indicators(data):
            enriched = data.copy()
            enriched["VolMA20"] = 2.0
            enriched["VolMA120"] = 1.0
            enriched["CMF"] = 1.0
            enriched["MA50"] = 10.0
            return enriched

        with patch.object(analytics, "_load_cache", return_value=frame), patch.object(analytics, "compute_all_indicators", side_effect=add_indicators) as compute, patch.object(analytics, "_signal_points", return_value=[200, 220]), patch.object(analytics, "score_ticker", return_value=Mock(total=1.0)) as score:
            analytics._backtest_one_ticker("000001.SZ", "eastmoney")

        self.assertEqual([item.args[0].shape[0] for item in compute.call_args_list], [320])
        self.assertEqual([item.args[0].shape[0] for item in score.call_args_list], [201, 221])

    def test_failed_signal_history_reduces_factor_when_both_horizons_lose(self):
        frame = pd.DataFrame({
            "ticker": ["000001.SZ"] * 10,
            "return20": [-20.0] * 10,
            "return60": [-40.0] * 10,
            "benchmark_return20": [0.0] * 10,
            "benchmark_return60": [0.0] * 10,
            "net_return20": [-20.0] * 10,
            "net_return60": [-40.0] * 10,
            "drawdown20": [-20.0] * 10,
            "drawdown60": [-40.0] * 10,
        })

        rows = analytics._ticker_backtest_rows(frame)

        self.assertAlmostEqual(rows[0]["failure_signal_factor"], 0.468, places=4)

    def test_sector_confirmation_uses_leave_one_out_peer_momentum(self):
        stronger = ScanResult(
            ticker="000001.SZ",
            industry="银行",
            score=ScoreBreakdown(total=80.0),
        )
        weaker = ScanResult(
            ticker="000002.SZ",
            industry="银行",
            score=ScoreBreakdown(total=80.0),
        )
        index = pd.date_range("2026-05-01", periods=62, freq="D")
        stronger_frame = pd.DataFrame({
            "Close": [100.0] * 61 + [110.0],
            "Open": [100.0] * 61 + [110.0],
            "High": [100.0] * 61 + [110.0],
            "Low": [100.0] * 61 + [110.0],
            "Volume": [1000.0] * 62,
        }, index=index)
        weaker_frame = pd.DataFrame({
            "Close": [100.0] * 62,
            "Open": [100.0] * 62,
            "High": [100.0] * 62,
            "Low": [100.0] * 62,
            "Volume": [1000.0] * 62,
        }, index=index)

        with patch.object(analytics, "_load_benchmark_frames", return_value={}), patch.object(analytics, "_benchmark_regime", return_value=("震荡", "基准数据不足")):
            analytics.enrich_results(
                [stronger, weaker],
                "eastmoney",
                frames={
                    "000001.SZ": stronger_frame,
                    "000002.SZ": weaker_frame,
                },
            )

        self.assertEqual(stronger.industry_momentum_60d, 0.0)
        self.assertEqual(stronger.industry_relative_strength, 10.0)
        self.assertEqual(stronger.sector_confirmation_factor, 0.6)
        self.assertEqual(weaker.industry_momentum_60d, 10.0)
        self.assertEqual(weaker.industry_relative_strength, -10.0)
        self.assertEqual(weaker.sector_confirmation_factor, 0.8)

    def test_enrichment_blends_available_quality_score(self):
        result = ScanResult(
            ticker="000001.SZ",
            industry="银行",
            score=ScoreBreakdown(total=80.0),
            final_score=80.0,
            quality_data_available=True,
            quality_score=20.0,
        )
        index = pd.date_range("2026-05-01", periods=62, freq="D")
        frame = pd.DataFrame({
            "Close": [100.0] * 62,
            "Open": [100.0] * 62,
            "High": [101.0] * 62,
            "Low": [99.0] * 62,
            "Volume": [1000.0] * 62,
        }, index=index)

        with patch.object(analytics, "_load_benchmark_frames", return_value={}), patch.object(analytics, "_benchmark_regime", return_value=("震荡", "基准数据不足")), patch.object(analytics, "_breakout_quality_factor", return_value=1.0):
            analytics.enrich_results([result], "eastmoney", frames={"000001.SZ": frame})

        self.assertEqual(result.sector_confirmation_factor, 1.0)
        self.assertEqual(result.institutional_score, 62.0)

    def test_report_sorts_by_institutional_score(self):
        results = [
            ScanResult(
                ticker="000001.SZ",
                score=ScoreBreakdown(total=90.0),
                institutional_score=30.0,
                passed_filters=True,
            ),
            ScanResult(
                ticker="000002.SZ",
                score=ScoreBreakdown(total=70.0),
                institutional_score=60.0,
                passed_filters=True,
            ),
        ]

        frame = __import__("report")._results_to_dataframe(results)

        self.assertEqual(frame.loc[0, "Ticker"], "000002.SZ")

    def test_composite_score_preserves_75_25_weighting(self):
        with TemporaryDirectory() as temp_dir, patch("analytics.OUTPUT_DIR", Path(temp_dir)), patch("pandas.DataFrame.to_parquet"):
            pd.DataFrame({
                "Ticker": ["000001.SZ"],
                "Score": [80.0],
                "PassedFilters": [True],
                "SignalCount": [4],
            }).to_csv(Path(temp_dir) / "AllResults.csv", index=False, encoding="utf-8-sig")
            summary = BacktestSummary(by_ticker=[{
                "ticker": "000001.SZ",
                "samples": 10,
                "backtest_score": 100.0,
                "objective_value": 10.0,
            }])

            apply_backtest_ranking(summary)
            result = pd.read_csv(Path(temp_dir) / "AllResults.csv", encoding="utf-8-sig")

        self.assertEqual(result.loc[0, "CompositeScore"], 85.0)
        self.assertEqual(result.loc[0, "FailureSignalFactor"], 1.0)
        self.assertEqual(result.loc[0, "InstitutionalScore"], 85.0)

    def test_composite_score_uses_final_score_when_available(self):
        with TemporaryDirectory() as temp_dir, patch("analytics.OUTPUT_DIR", Path(temp_dir)), patch("pandas.DataFrame.to_parquet"):
            pd.DataFrame({
                "Ticker": ["000001.SZ"],
                "Score": [90.0],
                "FinalScore": [40.0],
                "PassedFilters": [True],
                "SignalCount": [4],
            }).to_csv(Path(temp_dir) / "AllResults.csv", index=False, encoding="utf-8-sig")
            summary = BacktestSummary(by_ticker=[{
                "ticker": "000001.SZ",
                "samples": 10,
                "backtest_score": 100.0,
                "objective_value": 10.0,
            }])

            apply_backtest_ranking(summary)
            result = pd.read_csv(Path(temp_dir) / "AllResults.csv", encoding="utf-8-sig")

        self.assertEqual(result.loc[0, "CompositeScore"], 55.0)

    def test_composite_score_falls_back_to_raw_score_without_backtest_samples(self):
        with TemporaryDirectory() as temp_dir, patch("analytics.OUTPUT_DIR", Path(temp_dir)), patch("pandas.DataFrame.to_parquet"):
            pd.DataFrame({
                "Ticker": ["000001.SZ", "600000.SH"],
                "Score": [80.0, 60.0],
                "PassedFilters": [True, True],
                "SignalCount": [4, 4],
            }).to_csv(Path(temp_dir) / "AllResults.csv", index=False, encoding="utf-8-sig")
            summary = BacktestSummary(by_ticker=[{
                "ticker": "000001.SZ",
                "samples": 10,
                "backtest_score": 100.0,
                "objective_value": 10.0,
            }])

            apply_backtest_ranking(summary)
            result = pd.read_csv(Path(temp_dir) / "AllResults.csv", encoding="utf-8-sig")

        missing_backtest = result.loc[result["Ticker"] == "600000.SH"].iloc[0]
        self.assertTrue(pd.isna(missing_backtest["BacktestScore"]))
        self.assertTrue(pd.isna(missing_backtest["BacktestObjectiveValue"]))
        self.assertEqual(missing_backtest["CompositeScore"], 60.0)

    def test_composite_score_ignores_one_or_two_backtest_samples(self):
        with TemporaryDirectory() as temp_dir, patch("analytics.OUTPUT_DIR", Path(temp_dir)), patch("pandas.DataFrame.to_parquet"):
            pd.DataFrame({
                "Ticker": ["000001.SZ"],
                "Score": [40.0],
                "PassedFilters": [True],
                "SignalCount": [4],
            }).to_csv(Path(temp_dir) / "AllResults.csv", index=False, encoding="utf-8-sig")
            summary = BacktestSummary(by_ticker=[{
                "ticker": "000001.SZ",
                "samples": 2,
                "backtest_score": 100.0,
                "objective_value": 10.0,
            }])

            apply_backtest_ranking(summary)
            result = pd.read_csv(Path(temp_dir) / "AllResults.csv", encoding="utf-8-sig")

        self.assertEqual(result.loc[0, "BacktestReliability"], 0.0)
        self.assertEqual(result.loc[0, "CompositeScore"], 40.0)

    def test_institutional_score_uses_tempered_confirmation_multipliers(self):
        with TemporaryDirectory() as temp_dir, patch("analytics.OUTPUT_DIR", Path(temp_dir)), patch("pandas.DataFrame.to_parquet"):
            pd.DataFrame({
                "Ticker": ["000001.SZ"],
                "Score": [95.0],
                "PassedFilters": [True],
                "SignalCount": [4],
                "SectorConfirmationFactor": [0.5],
            }).to_csv(Path(temp_dir) / "AllResults.csv", index=False, encoding="utf-8-sig")
            summary = BacktestSummary(by_ticker=[{
                "ticker": "000001.SZ",
                "samples": 10,
                "backtest_score": 90.0,
                "objective_value": 10.0,
                "failure_signal_factor": 0.4,
            }])

            apply_backtest_ranking(summary)
            result = pd.read_csv(Path(temp_dir) / "AllResults.csv", encoding="utf-8-sig")

        self.assertEqual(result.loc[0, "CompositeScore"], 95.0)
        self.assertEqual(result.loc[0, "FailureAdjustedScore"], 77.9)
        self.assertEqual(result.loc[0, "InstitutionalScore"], 66.215)

    def test_institutional_score_applies_signal_recency_factor_and_tier(self):
        with TemporaryDirectory() as temp_dir, patch("analytics.OUTPUT_DIR", Path(temp_dir)), patch("pandas.DataFrame.to_parquet"):
            pd.DataFrame({
                "Ticker": ["000001.SZ"],
                "Score": [95.0],
                "PassedFilters": [True],
                "SignalCount": [4],
                "VolumeScore": [15.0],
                "SignalStartDate": ["2026-07-11"],
                "DataAsOf": ["2026-07-31"],
            }).to_csv(Path(temp_dir) / "AllResults.csv", index=False, encoding="utf-8-sig")
            summary = BacktestSummary(by_ticker=[{
                "ticker": "000001.SZ",
                "samples": 10,
                "backtest_score": 95.0,
                "objective_value": 10.0,
            }])

            apply_backtest_ranking(summary)
            result = pd.read_csv(Path(temp_dir) / "AllResults.csv", encoding="utf-8-sig")

        self.assertEqual(result.loc[0, "SignalRecencyDays"], 20)
        self.assertEqual(result.loc[0, "SignalRecencyFactor"], 0.8)
        self.assertEqual(result.loc[0, "InstitutionalScore"], 91.8)
        self.assertEqual(result.loc[0, "InstitutionalTier"], "A级机构启动")

    def test_backtest_ranking_preserves_breakout_quality_factor(self):
        with TemporaryDirectory() as temp_dir, patch("analytics.OUTPUT_DIR", Path(temp_dir)), patch("pandas.DataFrame.to_parquet"):
            pd.DataFrame({
                "Ticker": ["000001.SZ"],
                "Score": [80.0],
                "BreakoutQualityFactor": [0.2],
                "PassedFilters": [True],
                "SignalCount": [4],
            }).to_csv(Path(temp_dir) / "AllResults.csv", index=False, encoding="utf-8-sig")
            summary = BacktestSummary(by_ticker=[{
                "ticker": "000001.SZ",
                "samples": 10,
                "backtest_score": 100.0,
                "objective_value": 10.0,
            }])

            apply_backtest_ranking(summary)
            result = pd.read_csv(Path(temp_dir) / "AllResults.csv", encoding="utf-8-sig")

        self.assertEqual(result.loc[0, "BreakoutQualityFactor"], 0.2)
        self.assertEqual(result.loc[0, "InstitutionalScore"], 71.4)

    def test_breakout_quality_rewards_confirmed_platform_breakout(self):
        frame = pd.DataFrame({
            "Close": [10.0] * 20 + [12.0],
            "High": [10.5] * 20 + [12.1],
            "Low": [9.5] * 20 + [10.0],
            "Volume": [1000.0] * 20 + [2000.0],
        })

        self.assertEqual(analytics._breakout_quality_factor(frame), 0.9905)

    def test_breakout_quality_penalizes_weak_close_without_breakout(self):
        frame = pd.DataFrame({
            "Close": [10.0] * 20 + [9.6],
            "High": [10.5] * 20 + [10.4],
            "Low": [9.5] * 20 + [9.5],
            "Volume": [1000.0] * 21,
        })

        self.assertLess(analytics._breakout_quality_factor(frame), 0.4)

    def test_research_reports_group_tiers_and_calculate_factor_ic(self):
        history = pd.DataFrame({
            "InstitutionalTier": ["A级机构启动", "D级陷阱池", "A级机构启动"],
            "InstitutionalScore": [90.0, 50.0, 80.0],
            "Score": [88.0, 52.0, 78.0],
            "OpportunityScore": [85.0, 50.0, 75.0],
            "BreakoutQualityFactor": [1.0, 0.2, 0.8],
            "SignalRecencyFactor": [1.0, 0.7, 0.9],
            "SectorConfirmationFactor": [1.0, 0.8, 0.9],
            "FailureSignalFactor": [1.0, 0.7, 0.9],
            "TrendScore": [20.0, 5.0, 16.0],
            "AccumulationScore": [20.0, 5.0, 16.0],
            "IndustryRelativeStrength": [5.0, -5.0, 3.0],
            "Return20D": [10.0, -5.0, 4.0],
            "Return60D": [20.0, -10.0, 8.0],
            "MaxDrawdown20D": [-2.0, -12.0, -4.0],
            "MaxDrawdown60D": [-4.0, -20.0, -8.0],
        })

        with TemporaryDirectory() as temp_dir, patch("analytics.OUTPUT_DIR", Path(temp_dir)):
            tier_path, ic_path = analytics.write_research_reports(history)
            tier_report = pd.read_csv(tier_path, encoding="utf-8-sig")
            ic_report = pd.read_csv(ic_path, encoding="utf-8-sig")

        self.assertEqual(tier_report.loc[0, "InstitutionalTier"], "A级机构启动")
        self.assertIn("InstitutionalScore", ic_report["Factor"].tolist())

    def test_institutional_tier_distinguishes_waiting_from_value_trap(self):
        result = ScanResult(ticker="000001.SZ", score=ScoreBreakdown(total=60.0))

        frame = __import__("report")._results_to_dataframe([result])

        self.assertEqual(frame.loc[0, "InstitutionalTier"], "D级等待确认")

        result.value_trap_risk = 60.0
        frame = __import__("report")._results_to_dataframe([result])

        self.assertEqual(frame.loc[0, "InstitutionalTier"], "D级陷阱池")

    def test_enrichment_refreshes_close_from_latest_cached_bar(self):
        result = ScanResult(ticker="605499.SH", close=128.17)
        frame = pd.DataFrame({
            "Open": [128.0, 126.77],
            "High": [130.4, 128.77],
            "Low": [126.0, 125.45],
            "Close": [128.17, 125.82],
            "Volume": [5360695.0, 3839317.0],
        }, index=pd.to_datetime(["2026-07-23", "2026-07-24"]))

        with patch.object(analytics, "_load_benchmark_frames", return_value={}), patch.object(analytics, "_benchmark_regime", return_value=("震荡", "基准数据不足")):
            analytics.enrich_results([result], "eastmoney", frames={"605499.SH": frame})

        self.assertEqual(result.close, 125.82)
        self.assertEqual(result.data_asof, "2026-07-24")

    def test_enrichment_uses_realtime_close_after_market_close(self):
        result = ScanResult(ticker="000858.SZ", close=78.56)
        frame = pd.DataFrame({
            "Open": [78.0],
            "High": [79.0],
            "Low": [77.5],
            "Close": [78.56],
            "Volume": [1000.0],
        }, index=pd.DatetimeIndex([
            (pd.Timestamp.now(tz="Asia/Shanghai").normalize() - pd.offsets.BDay(1)).tz_localize(None)
        ]))

        with patch.object(analytics, "_load_benchmark_frames", return_value={}), patch.object(analytics, "_benchmark_regime", return_value=("震荡", "基准数据不足")), patch.object(analytics, "_is_a_share_market_closed", return_value=True), patch.object(analytics, "_fetch_eastmoney_realtime_price", return_value=78.0):
            analytics.enrich_results([result], "eastmoney", frames={"000858.SZ": frame})

        self.assertEqual(result.close, 78.0)
        self.assertEqual(result.data_asof, pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d"))

    def test_enrichment_keeps_daily_close_when_realtime_close_is_unavailable(self):
        result = ScanResult(ticker="000858.SZ", close=78.56)
        frame = pd.DataFrame({
            "Open": [78.0],
            "High": [79.0],
            "Low": [77.5],
            "Close": [78.56],
            "Volume": [1000.0],
        }, index=pd.to_datetime(["2026-07-30"]))

        with patch.object(analytics, "_load_benchmark_frames", return_value={}), patch.object(analytics, "_benchmark_regime", return_value=("震荡", "基准数据不足")), patch.object(analytics, "_is_a_share_market_closed", return_value=True), patch.object(analytics, "_fetch_eastmoney_realtime_price", return_value=None):
            analytics.enrich_results([result], "eastmoney", frames={"000858.SZ": frame})

        self.assertEqual(result.close, 78.56)

    def test_analysis_reuses_indicators_for_scan_and_enrichment(self):
        frame = pd.DataFrame({
            "Open": np.full(252, 10.0),
            "High": np.full(252, 11.0),
            "Low": np.full(252, 9.0),
            "Close": np.full(252, 10.0),
            "Volume": np.full(252, 1000.0),
        }, index=pd.date_range("2020-01-01", periods=252))
        enriched = frame.copy()
        enriched["MA200"] = 10.0
        ticker = TickerInfo(ticker="510300.SH", is_etf=True, asset_type="etf")

        with patch.object(scanner, "compute_all_indicators", return_value=enriched) as compute:
            result, returned_frame = scanner._analyse_one_ticker_from_df(ticker, frame)

        self.assertFalse(result.error)
        self.assertIsNotNone(returned_frame)
        self.assertEqual(
            list(returned_frame.columns),
            ["Open", "High", "Low", "Close", "Volume"],
        )
        compute.assert_called_once()

    def test_backtest_requires_explicit_tickers(self):
        args = argparse.Namespace(tickers=None, tickers_file=None, data_source="eastmoney")
        with patch("main.run_historical_backtest") as run_backtest:
            self.assertEqual(main.cmd_backtest(args), 2)
        run_backtest.assert_not_called()

    def test_backtest_allows_any_number_of_unique_tickers(self):
        tickers = [f"{index:06d}.SZ" for index in range(49)]
        args = argparse.Namespace(tickers=",".join(tickers), tickers_file=None, data_source="eastmoney")
        summary = Mock(samples=0, win_rate_20d=0.0, average_return_20d=0.0, average_return_60d=0.0)
        with patch("main.run_historical_backtest", return_value=summary) as run_backtest, patch("main.apply_backtest_ranking"):
            self.assertEqual(main.cmd_backtest(args), 0)
        self.assertEqual(run_backtest.call_args.args[0], tickers)

    def test_backtest_runs_exactly_50_explicit_tickers(self):
        tickers = [f"{index:06d}.SZ" for index in range(50)]
        args = argparse.Namespace(tickers=",".join(tickers), tickers_file=None, data_source="eastmoney")
        summary = Mock(samples=0, win_rate_20d=0.0, average_return_20d=0.0, average_return_60d=0.0)
        with patch("main.run_historical_backtest", return_value=summary) as run_backtest, patch("main.apply_backtest_ranking"):
            self.assertEqual(main.cmd_backtest(args), 0)
        run_backtest.assert_called_once_with(
            tickers,
            source="eastmoney",
            workers=None,
            objective="net_excess_return_20d",
            benchmark="沪深300",
            commission=0.0003,
            stamp_duty=0.0005,
            slippage=0.001,
            test_ratio=0.2,
            validation_ratio=0.2,
        )

    def test_backtest_all_results_uses_every_unique_result_ticker(self):
        summary = Mock(samples=0, win_rate_20d=0.0, average_return_20d=0.0, average_return_60d=0.0)
        args = argparse.Namespace(tickers=None, tickers_file=None, all_results=True, data_source="eastmoney")
        with TemporaryDirectory() as temp_dir, patch("main.OUTPUT_DIR", Path(temp_dir)), patch("main.run_historical_backtest", return_value=summary) as run_backtest, patch("main.apply_backtest_ranking"):
            (Path(temp_dir) / "AllResults.csv").write_text("Ticker\n000001.SZ\n000002.SZ\n000001.SZ\n", encoding="utf-8-sig")
            self.assertEqual(main.cmd_backtest(args), 0)
        self.assertEqual(run_backtest.call_args.args[0], ["000001.SZ", "000002.SZ"])
        self.assertEqual(run_backtest.call_args.kwargs["workers"], None)

    def test_gui_top50_write_replaces_old_file_and_preserves_filter_order(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner._csv_headers = ["Ticker", "Score"]
        scanner._csv_rows = [["000001.SZ", "90"], ["000002.SZ", "80"], ["000003.SZ", "70"]]
        scanner._csv_path = Path("cached.csv")
        with TemporaryDirectory() as temp_dir, patch("gui.OUTPUT_DIR", Path(temp_dir)):
            path = Path(temp_dir) / "Top50.csv"
            path.write_text("", encoding="utf-8")
            scanner._write_top50_csv(["000003.SZ", "000001.SZ"])
            with path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.reader(file))
            self.assertEqual(rows, [["Ticker", "Score"], ["000003.SZ", "70"], ["000001.SZ", "90"]])
            self.assertIsNone(scanner._csv_path)
            self.assertFalse((Path(temp_dir) / ".Top50.csv.tmp").exists())

    def test_gui_top50_write_failure_keeps_existing_file(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner._csv_headers = ["Ticker", "Score"]
        scanner._csv_rows = [["000001.SZ", "90"]]
        scanner._csv_path = None
        with TemporaryDirectory() as temp_dir, patch("gui.OUTPUT_DIR", Path(temp_dir)), patch("gui.os.replace", side_effect=OSError("replace failed")):
            path = Path(temp_dir) / "Top50.csv"
            path.write_text("old", encoding="utf-8")
            with self.assertRaises(OSError):
                scanner._write_top50_csv(["000001.SZ"])
            self.assertEqual(path.read_text(encoding="utf-8"), "old")
            self.assertFalse((Path(temp_dir) / ".Top50.csv.tmp").exists())

    def test_gui_backtest_uses_all_current_filtered_tickers(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner.scan_running = False
        scanner.filtered_tickers = [f"{index:06d}.SZ" for index in range(60)]
        scanner.data_source = Mock()
        scanner.data_source.get.return_value = "eastmoney"
        scanner.start_button = Mock()
        scanner.progress = Mock()
        scanner.append_log = Mock()
        scanner.run_process = Mock()
        scanner._atomic_write_text = Mock()
        expected = scanner.filtered_tickers

        with patch("gui.threading.Thread") as thread, patch("gui.messagebox.showerror") as showerror:
            scanner.start_backtest()

        self.assertEqual(scanner._atomic_write_text.call_args.args[1], "\n".join(expected) + "\n")
        showerror.assert_not_called()
        thread.return_value.start.assert_called_once_with()

    def test_gui_backtest_allows_current_filter_with_fewer_than_50(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner.scan_running = False
        scanner.filtered_tickers = [f"{index:06d}.SZ" for index in range(49)]
        scanner.data_source = Mock()
        scanner.data_source.get.return_value = "eastmoney"
        scanner.start_button = Mock()
        scanner.progress = Mock()
        scanner.append_log = Mock()
        scanner.run_process = Mock()
        scanner._atomic_write_text = Mock()

        with patch("gui.threading.Thread") as thread, patch("gui.messagebox.showerror") as showerror:
            scanner.start_backtest()

        self.assertEqual(scanner._atomic_write_text.call_args.args[1].count("\n"), 49)
        showerror.assert_not_called()
        thread.return_value.start.assert_called_once_with()

    def test_gui_render_limits_table_rows_but_keeps_all_filtered_tickers(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner._filter_job = None
        scanner._csv_headers = ["Ticker", "Score"]
        scanner._csv_rows = [[f"{index:06d}.SZ", str(index)] for index in range(600)]
        scanner.search = Mock()
        scanner.search.get.return_value = ""
        scanner.sector_filter = Mock()
        scanner.sector_filter.get.return_value = "全部板块"
        scanner.industry_filter = Mock()
        scanner.industry_filter.get.return_value = "全部行业"
        scanner.quality_filter = Mock()
        scanner.quality_filter.get.return_value = "全部质量"
        scanner.table = MagicMock()
        scanner.table.get_children.return_value = []
        scanner.table.insert.side_effect = [f"row-{index}" for index in range(600)]
        scanner._row_details = {}
        scanner.status = Mock()
        scanner.current_file = "AllResults.csv"
        scanner._current_page = 0
        scanner.page_summary = Mock()
        scanner.previous_page_button = Mock()
        scanner.next_page_button = Mock()

        self.assertTrue(scanner._render_cached_rows())

        self.assertEqual(len(scanner.filtered_tickers), 600)
        self.assertEqual(scanner.filtered_tickers[-1], "000599.SZ")
        self.assertEqual(scanner.table.insert.call_count, gui.MAX_RENDERED_ROWS)
        self.assertEqual(len(scanner._row_details), gui.MAX_RENDERED_ROWS)
        scanner.status.set.assert_called_once_with(
            "AllResults.csv · 命中 600 / 600 条 · 第 1 / 2 页 · 双击查看详情"
        )

        scanner.table.insert.reset_mock()
        scanner._show_next_page()

        self.assertEqual(scanner.table.insert.call_count, 100)
        self.assertEqual(len(scanner._row_details), 100)
        scanner.page_summary.set.assert_called_with("第 2 / 2 页 · 501-600 条")

    def test_download_progress_logs_first_interval_and_final_updates(self):
        with patch("downloader.logger.info") as info:
            _log_download_progress(1, 250, 1, 0)
            _log_download_progress(3, 250, 3, 0)
            _log_download_progress(250, 250, 245, 5)

        self.assertEqual(info.call_count, 2)
        self.assertEqual(info.call_args_list[0].args[1:], (1, 250, 1, 0))
        self.assertEqual(info.call_args_list[1].args[1:], (250, 250, 245, 5))

    def test_gui_download_progress_updates_determinate_bar(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner.log_text = MagicMock()
        scanner.progress = MagicMock()
        scanner.status = Mock()
        scanner.backtest_running = False

        scanner.append_log("[INFO] DOWNLOAD progress: 64/100 (60 succeeded, 4 no-data/failed).\n")

        scanner.progress.stop.assert_called_once_with()
        scanner.progress.configure.assert_called_once_with(mode="determinate", maximum=100, value=64)
        scanner.status.set.assert_called_once_with("下载进度 64/100 · 成功 60 · 无数据/失败 4")

    def test_all_tqdm_calls_disable_non_tty_stderr(self):
        for filename, expected_calls in (("downloader.py", 2), ("scanner.py", 2)):
            tree = ast.parse(Path(filename).read_text(encoding="utf-8"))
            calls = [
                node for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "tqdm"
            ]
            self.assertEqual(len(calls), expected_calls)
            for call in calls:
                disable = next((keyword.value for keyword in call.keywords if keyword.arg == "disable"), None)
                self.assertIsNotNone(disable)
                self.assertEqual(ast.unparse(disable), "not sys.stderr.isatty()")


if __name__ == "__main__":
    import unittest
    unittest.main()
