import argparse
import ast
import csv
import importlib.util
import sys
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

import gui
import main
import analytics
from analytics import BacktestSummary, apply_backtest_ranking
from config import OUTPUT_DIR
from report import _results_to_dataframe
from scanner import ScanResult
from downloader import _cache_path, _load_cache
from filters import filter_bear_market, filter_min_price, filter_min_volume
from score import score_ticker
from scanner import ScanReport


class RegressionTests(TestCase):
    def test_gui_startup_loads_best_available_results(self):
        root = Mock()
        root.after.return_value = "log-job"
        variable = Mock()
        with patch("gui.tk.StringVar", return_value=variable), patch("gui.tk.BooleanVar", return_value=variable), patch.object(gui.ScannerGUI, "_configure_style"), patch.object(gui.ScannerGUI, "_build_ui"), patch.object(gui.ScannerGUI, "_load_best_available_results") as load_results:
            gui.ScannerGUI(root)

        load_results.assert_called_once_with()

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
        self.assertTrue(str(eastmoney).endswith("600000.SH__eastmoney.csv"))

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

    def test_export_dataframe_supports_backtest_fields(self):
        result = ScanResult(ticker="000001.SZ")
        frame = _results_to_dataframe([result])

        self.assertEqual(frame.loc[0, "Ticker"], "000001.SZ")
        self.assertTrue(pd.isna(frame.loc[0, "BacktestObjectiveValue"]))
        self.assertEqual(frame.loc[0, "UniverseType"], "current_survivor_pool")
        self.assertTrue(frame.loc[0, "SurvivorshipBiasWarning"])

    def test_apply_backtest_ranking_cleans_legacy_columns_on_repeated_calls(self):
        with patch("analytics.OUTPUT_DIR") as output_dir:
            from tempfile import TemporaryDirectory
            with TemporaryDirectory() as temp_dir:
                output_dir.__truediv__.side_effect = lambda name: __import__("pathlib").Path(temp_dir) / name
                all_results = output_dir / "AllResults.csv"
                pd.DataFrame({
                    "Ticker": ["000001.SZ", "600000.SH"], "Score": [60, 50], "PassedFilters": [True, False], "SignalCount": [3, 2],
                    "BacktestScore": [1, 2], "CompositeScore": [3, 4], "backtest_score": [5, 6], "composite_score": [7, 8], "samples": [1, 1],
                }).to_csv(all_results, index=False, encoding="utf-8-sig")
                summary = BacktestSummary(by_ticker=[{
                    "ticker": "000001.SZ", "samples": 4, "win_rate_20d": 0.75, "win_rate_60d": 0.5,
                    "average_return_20d": 2.0, "average_return_60d": 4.0, "backtest_score": 80.0,
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
                self.assertEqual(int(result.loc[result["Ticker"] == "000001.SZ", "BacktestSamples"].iloc[0]), 4)
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

    def test_score_ticker_shrinks_incomplete_indicator_scores_toward_neutral(self):
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
        raw_total = score.trend + score.volume + score.accumulation + score.volatility + score.structure

        self.assertLess(score.indicator_coverage, 1.0)
        self.assertAlmostEqual(score.total, 50.0 + (raw_total - 50.0) * score.indicator_coverage)

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
        self.assertEqual([ticker.ticker for ticker in etf_universe], ["510300.SH", "159915"])
        self.assertTrue(all(ticker.is_etf and ticker.asset_type == "etf" for ticker in etf_universe))

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

    def test_backtest_recomputes_indicators_for_full_history_and_each_historical_prefix(self):
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
        self.assertEqual([item.args[0].shape[0] for item in compute.call_args_list], [320, 201, 221])
        self.assertEqual([item.args[0].shape[0] for item in score.call_args_list], [201, 221])

    def test_backtest_requires_explicit_tickers(self):
        args = argparse.Namespace(tickers=None, tickers_file=None, data_source="eastmoney")
        with patch("main.run_historical_backtest") as run_backtest:
            self.assertEqual(main.cmd_backtest(args), 2)
        run_backtest.assert_not_called()

    def test_backtest_requires_exactly_50_unique_tickers(self):
        tickers = [f"{index:06d}.SZ" for index in range(49)]
        args = argparse.Namespace(tickers=",".join(tickers), tickers_file=None, data_source="eastmoney")
        with patch("main.run_historical_backtest") as run_backtest:
            self.assertEqual(main.cmd_backtest(args), 2)
        run_backtest.assert_not_called()

    def test_backtest_runs_exactly_50_explicit_tickers(self):
        tickers = [f"{index:06d}.SZ" for index in range(50)]
        args = argparse.Namespace(tickers=",".join(tickers), tickers_file=None, data_source="eastmoney")
        summary = Mock(samples=0, win_rate_20d=0.0, average_return_20d=0.0, average_return_60d=0.0)
        with patch("main.run_historical_backtest", return_value=summary) as run_backtest, patch("main.apply_backtest_ranking"):
            self.assertEqual(main.cmd_backtest(args), 0)
        run_backtest.assert_called_once_with(tickers, source="eastmoney")

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

    def test_gui_backtest_uses_first_50_from_larger_current_filter(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner.scan_running = False
        scanner.filtered_tickers = [f"{index:06d}.SZ" for index in range(60)]
        scanner.data_source = Mock()
        scanner.data_source.get.return_value = "eastmoney"
        scanner.start_button = Mock()
        scanner.progress = Mock()
        scanner.append_log = Mock()
        scanner.run_process = Mock()
        written_tickers = []
        scanner._write_top50_csv = lambda tickers: written_tickers.append(list(tickers))
        scanner._atomic_write_text = Mock()
        expected = scanner.filtered_tickers[:50]

        with patch("gui.threading.Thread") as thread, patch("gui.messagebox.showerror") as showerror:
            scanner.start_backtest()

        self.assertEqual(written_tickers, [expected])
        self.assertEqual(scanner._atomic_write_text.call_args.args[1], "\n".join(expected) + "\n")
        showerror.assert_not_called()
        thread.return_value.start.assert_called_once_with()

    def test_gui_backtest_rejects_current_filter_with_fewer_than_50(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner.scan_running = False
        scanner.filtered_tickers = [f"{index:06d}.SZ" for index in range(49)]
        scanner._write_top50_csv = Mock()

        with patch("gui.messagebox.showerror") as showerror:
            scanner.start_backtest()

        scanner._write_top50_csv.assert_not_called()
        showerror.assert_called_once()
        self.assertIn("至少需要 50 个标的", showerror.call_args.args[1])

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
        scanner.table.insert.side_effect = [f"row-{index}" for index in range(gui.MAX_RENDERED_ROWS)]
        scanner._row_details = {}
        scanner.status = Mock()
        scanner.current_file = "AllResults.csv"

        self.assertTrue(scanner._render_cached_rows())

        self.assertEqual(len(scanner.filtered_tickers), 600)
        self.assertEqual(scanner.filtered_tickers[-1], "000599.SZ")
        self.assertEqual(scanner.table.insert.call_count, gui.MAX_RENDERED_ROWS)
        self.assertEqual(len(scanner._row_details), gui.MAX_RENDERED_ROWS)
        scanner.status.set.assert_called_once_with(
            f"AllResults.csv · 命中 600 / 600 条 · 实际渲染 {gui.MAX_RENDERED_ROWS} 条 · 双击查看详情"
        )

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
