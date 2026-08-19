from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

import scan_service
import scanner
import scanner_resume_v59 as resume
from downloader import TickerInfo
from performance_cache import market_cache_state
from score import ScoreBreakdown


def _frame(dates: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [value + 0.5 for value in closes],
            "Low": [value - 0.5 for value in closes],
            "Close": closes,
            "Volume": [1_000_000.0] * len(closes),
            "Amount": [10_000_000.0] * len(closes),
        },
        index=pd.to_datetime(dates),
    )


class CheckpointIntegrityTests(unittest.TestCase):
    def tearDown(self) -> None:
        resume._reset_session()

    def test_scan_service_installs_v59_scanner_contract(self) -> None:
        self.assertIs(scan_service.run_scan, scanner.run_scan)
        self.assertIs(scanner.run_scan, resume.run_scan)
        self.assertIs(scanner.load_checkpoint, resume.load_checkpoint)

    def test_checkpoint_round_trip_restores_current_run_result_snapshot(self) -> None:
        frame = _frame(["2026-08-18", "2026-08-19"], [10.0, 10.5])
        result = scanner.ScanResult(
            ticker="000001.SZ",
            close=10.5,
            passed_filters=True,
            score=ScoreBreakdown(total=42.0, final_score=41.0),
        )

        with TemporaryDirectory() as temp_dir, patch.object(
            scanner, "_CHECKPOINT_PATH", Path(temp_dir) / "_checkpoint.json"
        ), patch.object(scanner, "_checkpoint_trade_date", return_value="2026-08-19"):
            scanner.save_checkpoint(
                {"000001.SZ"},
                "tickflow",
                results=[result],
                market_frames={"000001.SZ": frame},
            )
            loaded = scanner.load_checkpoint("tickflow")

        self.assertIsInstance(loaded, resume.CheckpointState)
        self.assertEqual(set(loaded), {"000001.SZ"})
        self.assertEqual(loaded.snapshots["000001.SZ"].close, 10.5)
        self.assertEqual(loaded.snapshots["000001.SZ"].score.total, 42.0)
        self.assertEqual(
            loaded.market_states["000001.SZ"], market_cache_state(frame)
        )

    def test_legacy_ticker_only_checkpoint_is_ignored(self) -> None:
        with TemporaryDirectory() as temp_dir:
            checkpoint = Path(temp_dir) / "_checkpoint.json"
            checkpoint.write_text(
                json.dumps(
                    {
                        "active": True,
                        "processed": ["000001.SZ"],
                        "trade_date": "2026-08-19",
                        "data_source": "tickflow",
                        "scoring_version": scanner.SCORING_VERSION,
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(scanner, "_CHECKPOINT_PATH", checkpoint), patch.object(
                scanner, "_checkpoint_trade_date", return_value="2026-08-19"
            ):
                loaded = scanner.load_checkpoint("tickflow")

        self.assertEqual(loaded, set())
        self.assertIsInstance(loaded, resume.CheckpointState)

    def test_checkpoint_contract_change_invalidates_snapshot(self) -> None:
        frame = _frame(["2026-08-19"], [10.5])
        result = scanner.ScanResult(ticker="000001.SZ", close=10.5)

        with TemporaryDirectory() as temp_dir:
            checkpoint = Path(temp_dir) / "_checkpoint.json"
            with patch.object(scanner, "_CHECKPOINT_PATH", checkpoint), patch.object(
                scanner, "_checkpoint_trade_date", return_value="2026-08-19"
            ):
                scanner.save_checkpoint(
                    {"000001.SZ"},
                    "tickflow",
                    results=[result],
                    market_frames={"000001.SZ": frame},
                )
                payload = json.loads(checkpoint.read_text(encoding="utf-8"))
                payload["contract"]["pipeline_version"] = "obsolete-pipeline"
                checkpoint.write_text(json.dumps(payload), encoding="utf-8")
                loaded = scanner.load_checkpoint("tickflow")

        self.assertEqual(loaded, set())

    def test_checkpoint_market_date_changes_across_same_day_close(self) -> None:
        before_close = datetime(
            2026, 8, 19, 14, 59, tzinfo=ZoneInfo("Asia/Shanghai")
        )
        after_close = datetime(
            2026, 8, 19, 15, 1, tzinfo=ZoneInfo("Asia/Shanghai")
        )

        self.assertEqual(resume._checkpoint_trade_date(before_close), "2026-08-18")
        self.assertEqual(resume._checkpoint_trade_date(after_close), "2026-08-19")

    def test_resume_reuses_only_snapshot_with_identical_refreshed_market_state(self) -> None:
        ticker = TickerInfo(ticker="000001.SZ")
        frame = _frame(["2026-08-18", "2026-08-19"], [10.0, 10.5])
        snapshot = scanner.ScanResult(
            ticker="000001.SZ", close=10.5, passed_filters=True
        )
        state = resume.CheckpointState(
            {"000001.SZ"},
            snapshots={"000001.SZ": snapshot},
            market_states={"000001.SZ": market_cache_state(frame)},
        )

        with patch.object(scanner, "load_checkpoint", return_value=state), patch.object(
            scanner, "download_batch", return_value={"000001.SZ": frame}
        ) as download, patch.object(
            scanner, "_analyse_one_ticker_from_df"
        ) as analyse, patch.object(
            scanner, "enrich_results"
        ), patch.object(
            scanner, "save_checkpoint"
        ), patch.object(
            scanner, "clear_checkpoint"
        ), patch.object(scanner.pd, "read_parquet") as read_parquet:
            report = scanner.run_scan(
                stock_universe=[ticker],
                etf_universe=[],
                data_source="tickflow",
            )

        analyse.assert_not_called()
        read_parquet.assert_not_called()
        self.assertEqual(download.call_args.kwargs["skip_tickers"], set())
        self.assertEqual(report.successful, 1)
        self.assertEqual(len(report.results), 1)
        self.assertEqual(report.results[0].close, 10.5)

    def test_market_change_invalidates_snapshot_and_reanalyses_instead_of_mixing(self) -> None:
        ticker = TickerInfo(ticker="000001.SZ")
        old_frame = _frame(["2026-08-18"], [10.0])
        current_frame = _frame(["2026-08-18", "2026-08-19"], [10.0, 11.0])
        old_snapshot = scanner.ScanResult(ticker="000001.SZ", close=10.0)
        new_result = scanner.ScanResult(ticker="000001.SZ", close=11.0)
        state = resume.CheckpointState(
            {"000001.SZ"},
            snapshots={"000001.SZ": old_snapshot},
            market_states={"000001.SZ": market_cache_state(old_frame)},
        )

        with patch.object(scanner, "load_checkpoint", return_value=state), patch.object(
            scanner, "download_batch", return_value={"000001.SZ": current_frame}
        ), patch.object(
            scanner,
            "_analyse_one_ticker_from_df",
            return_value=(new_result, current_frame),
        ) as analyse, patch.object(
            scanner, "enrich_results"
        ), patch.object(
            scanner, "save_checkpoint"
        ), patch.object(
            scanner, "clear_checkpoint"
        ), patch.object(scanner.pd, "read_parquet") as read_parquet:
            report = scanner.run_scan(
                stock_universe=[ticker],
                etf_universe=[],
                data_source="tickflow",
            )

        analyse.assert_called_once()
        read_parquet.assert_not_called()
        self.assertEqual(report.successful, 1)
        self.assertEqual(len(report.results), 1)
        self.assertEqual(report.results[0].close, 11.0)

    def test_enrichment_failure_retains_checkpoint_for_retry(self) -> None:
        ticker = TickerInfo(ticker="000001.SZ")
        frame = _frame(["2026-08-19"], [11.0])
        result = scanner.ScanResult(ticker="000001.SZ", close=11.0)
        state = resume.CheckpointState()

        with patch.object(scanner, "load_checkpoint", return_value=state), patch.object(
            scanner, "download_batch", return_value={"000001.SZ": frame}
        ), patch.object(
            scanner,
            "_analyse_one_ticker_from_df",
            return_value=(result, frame),
        ), patch.object(
            scanner, "enrich_results", side_effect=ValueError("enrichment failed")
        ), patch.object(
            scanner, "save_checkpoint"
        ) as save, patch.object(
            scanner, "clear_checkpoint"
        ) as clear:
            report = scanner.run_scan(
                stock_universe=[ticker],
                etf_universe=[],
                data_source="tickflow",
            )

        self.assertEqual(report.successful, 1)
        self.assertTrue(save.called)
        clear.assert_not_called()


if __name__ == "__main__":
    unittest.main()
