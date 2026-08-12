from __future__ import annotations

import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import pandas as pd

import gui
import report as report_module
from scan_service import ScanRequest, execute_scan, prepare_universe
from scanner import ScanReport, ScanResult


class ScanServiceRegressionTests(unittest.TestCase):
    def test_specified_universe_is_normalized_deduplicated_and_scoped(self):
        request = ScanRequest(
            include_stocks=True,
            include_etfs=True,
            tickers=("000001", "000001.SZ", "159001", "159001.SZ"),
        )
        stocks, etfs = prepare_universe(request)
        self.assertEqual([item.ticker for item in stocks], ["000001.SZ"])
        self.assertEqual([item.ticker for item in etfs], ["159001.SZ"])

    def test_execute_scan_forwards_progress_cancel_and_exports_once(self):
        scan_report = ScanReport(
            results=[ScanResult(ticker="000001.SZ", name="测试")],
            total_tickers=1,
            successful=1,
        )
        run_scan = Mock(return_value=scan_report)
        export_all = Mock(
            return_value=(
                Path("Top50.csv"),
                Path("Top200.parquet"),
                Path("AllResults.csv"),
                Path("AllResults.parquet"),
            )
        )
        refresh_policy = Mock()
        callback = Mock()
        cancel_event = threading.Event()
        request = ScanRequest(tickers=("000001.SZ",), refresh_fundamentals=True)

        result = execute_scan(
            request,
            progress_callback=callback,
            cancel_event=cancel_event,
            run_scan_fn=run_scan,
            export_all_fn=export_all,
            refresh_policy_fn=refresh_policy,
        )

        self.assertIs(result.report, scan_report)
        self.assertEqual(result.stock_count, 1)
        self.assertEqual(result.etf_count, 0)
        refresh_policy.assert_called_once()
        self.assertIs(run_scan.call_args.kwargs["progress_callback"], callback)
        self.assertIs(run_scan.call_args.kwargs["cancel_event"], cancel_event)
        export_all.assert_called_once_with(
            scan_report.results,
            top_n_csv=50,
            top_n_parquet=200,
            data_source="tickflow",
        )

    def test_force_download_disables_resume_and_cache_first(self):
        scan_report = ScanReport()
        run_scan = Mock(return_value=scan_report)
        export_all = Mock(
            return_value=(Path("a"), Path("b"), Path("c"), Path("d"))
        )
        request = ScanRequest(
            tickers=("000001.SZ",),
            force_download=True,
            resume=True,
            cache_first=True,
        )
        execute_scan(
            request,
            run_scan_fn=run_scan,
            export_all_fn=export_all,
            refresh_policy_fn=Mock(),
        )
        self.assertFalse(run_scan.call_args.kwargs["resume"])
        self.assertFalse(run_scan.call_args.kwargs["cache_first"])

    def test_gui_build_scan_request_matches_visible_controls(self):
        scanner = object.__new__(gui.ScannerGUI)
        scanner.scope = Mock(get=Mock(return_value="仅股票"))
        scanner.tickers = Mock(get=Mock(return_value="000001.SZ, 000002.SZ"))
        scanner.force_download = Mock(get=Mock(return_value=False))
        scanner.no_resume = Mock(get=Mock(return_value=True))
        scanner.cache_first = Mock(get=Mock(return_value=True))
        scanner.refresh_fundamentals = Mock(get=Mock(return_value=True))
        scanner._selected_data_source = Mock(return_value="tickflow")
        request = scanner._build_scan_request()
        self.assertTrue(request.include_stocks)
        self.assertFalse(request.include_etfs)
        self.assertEqual(request.tickers, ("000001.SZ", "000002.SZ"))
        self.assertFalse(request.resume)
        self.assertTrue(request.cache_first)
        self.assertTrue(request.refresh_fundamentals)

    def test_output_contract_keeps_public_flat_columns(self):
        frame = report_module._results_to_dataframe(
            [ScanResult(ticker="000001.SZ", name="测试", close=10.0)]
        )
        required = {
            "Ticker",
            "Name",
            "Close",
            "Score",
            "BaseScore",
            "TriggerScore",
            "FinalScore",
            "EntrySignal",
            "QualityGate",
            "BacktestScore",
            "InstitutionalScore",
            "RankingScore",
        }
        self.assertTrue(required.issubset(frame.columns))
        # Execution quality is part of the auditable public score contract.
        self.assertIn("ExecutionScore", frame.columns)
        self.assertIn("ModelWeightSignature", frame.columns)
        self.assertNotIn("setup_score", frame.columns)
        self.assertNotIn("execution_score", frame.columns)

        with TemporaryDirectory() as directory:
            csv_path = Path(directory) / "contract.csv"
            parquet_path = Path(directory) / "contract.parquet"
            frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
            frame.to_parquet(parquet_path, index=False)
            csv_columns = pd.read_csv(csv_path, nrows=0, encoding="utf-8-sig").columns.tolist()
            parquet_columns = pd.read_parquet(parquet_path).columns.tolist()
            self.assertEqual(csv_columns, parquet_columns)
            self.assertEqual(csv_columns, frame.columns.tolist())


if __name__ == "__main__":
    unittest.main()
