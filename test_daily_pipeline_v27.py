from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import daily_pipeline


def _write_csv(path: Path, tickers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["Ticker", "Name"])
        for ticker in tickers:
            writer.writerow([ticker, ticker])


class DailyPipelineV27Tests(unittest.TestCase):
    def test_daily_pipeline_scans_then_backtests_then_verifies_split_top50(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)

            def scan_side_effect(args):
                self.assertTrue(args.no_resume)
                self.assertFalse(args.force_download)
                self.assertFalse(args.cache_first)
                self.assertFalse(args.stocks_only)
                self.assertFalse(args.etfs_only)
                runtime_output = Path(daily_pipeline.scanner_cli.OUTPUT_DIR)
                _write_csv(runtime_output / "AllResults.csv", ["000001.SZ", "159915.SZ"])
                return 0

            def backtest_side_effect(args):
                self.assertEqual(args.mode, "fast")
                self.assertEqual(args.workers, 4)
                self.assertEqual(
                    args.tickers_file.read_text(encoding="utf-8").splitlines(),
                    ["000001.SZ", "159915.SZ"],
                )
                runtime_output = Path(daily_pipeline.scanner_cli.OUTPUT_DIR)
                _write_csv(runtime_output / "Top50Mixed.csv", ["159915.SZ", "000001.SZ"])
                _write_csv(runtime_output / "Top50Stocks.csv", ["000001.SZ"])
                _write_csv(runtime_output / "Top50ETF.csv", ["159915.SZ"])
                (runtime_output / "BacktestSummary.json").write_text(
                    json.dumps({"engine": "process", "worker_count": 4, "cache_hits": 1}),
                    encoding="utf-8",
                )
                return 0

            with patch.object(daily_pipeline, "OUTPUT_DIR", output), patch.object(
                daily_pipeline.scanner_cli, "cmd_scan", side_effect=scan_side_effect
            ) as scan, patch.object(
                daily_pipeline.scanner_cli, "cmd_backtest", side_effect=backtest_side_effect
            ) as backtest:
                code = daily_pipeline.run_daily_pipeline(workers=4, quality_gates=False)

            self.assertEqual(code, 0)
            scan.assert_called_once()
            backtest.assert_called_once()
            manifest = json.loads((output / "DailyRunSummary.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["ticker_count"], 2)
            self.assertEqual(manifest["backtest_engine"], "process")
            self.assertEqual(manifest["backtest_workers"], 4)
            self.assertEqual(manifest["outputs"]["Top50Mixed.csv"], 2)
            self.assertEqual(manifest["outputs"]["Top50Stocks.csv"], 1)
            self.assertEqual(manifest["outputs"]["Top50ETF.csv"], 1)

    def test_daily_pipeline_stops_when_scan_fails(self):
        with patch.object(daily_pipeline.scanner_cli, "cmd_scan", return_value=2), patch.object(
            daily_pipeline.scanner_cli, "cmd_backtest"
        ) as backtest:
            code = daily_pipeline.run_daily_pipeline(workers=2, quality_gates=False)
        self.assertEqual(code, 2)
        backtest.assert_not_called()

    def test_daily_failure_discloses_discarded_staging_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)

            def failed_scan(_args):
                runtime_output = Path(daily_pipeline.scanner_cli.OUTPUT_DIR)
                (runtime_output / "_checkpoint.json").write_text(
                    "{}", encoding="utf-8"
                )
                return 2

            with patch.object(daily_pipeline, "OUTPUT_DIR", output), patch.object(
                daily_pipeline.scanner_cli,
                "cmd_scan",
                side_effect=failed_scan,
            ):
                code = daily_pipeline.run_daily_pipeline(
                    workers=1, quality_gates=False
                )

            self.assertEqual(code, 2)
            publication = json.loads(
                (output / "PublicationStatus.json").read_text(encoding="utf-8")
            )
            self.assertTrue(publication["scan_checkpoint_discarded"])
            staging = output / ".staging"
            self.assertFalse(staging.exists() and any(staging.iterdir()))

    def test_daily_pipeline_requires_all_three_final_lists(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)

            def scan_side_effect(_args):
                runtime_output = Path(daily_pipeline.scanner_cli.OUTPUT_DIR)
                _write_csv(runtime_output / "AllResults.csv", ["000001.SZ"])
                return 0

            def backtest_side_effect(_args):
                runtime_output = Path(daily_pipeline.scanner_cli.OUTPUT_DIR)
                _write_csv(runtime_output / "Top50Mixed.csv", ["000001.SZ"])
                _write_csv(runtime_output / "Top50Stocks.csv", ["000001.SZ"])
                return 0

            with patch.object(daily_pipeline, "OUTPUT_DIR", output), patch.object(
                daily_pipeline.scanner_cli, "cmd_scan", side_effect=scan_side_effect
            ), patch.object(
                daily_pipeline.scanner_cli, "cmd_backtest", side_effect=backtest_side_effect
            ):
                code = daily_pipeline.run_daily_pipeline(workers=1, quality_gates=False)

            self.assertEqual(code, 2)
            self.assertFalse((output / "DailyRunSummary.json").exists())


if __name__ == "__main__":
    unittest.main()
