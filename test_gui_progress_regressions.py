from __future__ import annotations

import queue
import threading
import unittest
from unittest.mock import Mock, patch

import pandas as pd

import downloader
import gui_core
import scanner
from downloader import TickerInfo


class GuiProgressRegressionTests(unittest.TestCase):
    def test_download_batch_reports_intermediate_outer_batch_progress(self) -> None:
        tickers = [TickerInfo(ticker=f"{index:06d}.SZ") for index in range(1001)]
        frame = pd.DataFrame(
            {
                "Open": [1.0, 1.0],
                "High": [1.0, 1.0],
                "Low": [1.0, 1.0],
                "Close": [1.0, 1.0],
                "Volume": [1.0, 1.0],
                "Amount": [1.0, 1.0],
            },
            index=pd.to_datetime(["2026-08-06", "2026-08-07"]),
        )
        events: list[tuple[int, int, int, int]] = []

        def fake_batch(symbols, count=None):
            del count
            return {symbol: frame for symbol in symbols}

        with (
            patch.object(downloader, "_batch_fetch", side_effect=fake_batch) as batch_fetch,
            patch.object(downloader, "_save_cache"),
            patch.object(downloader, "_record_market_manifest"),
            patch.object(downloader, "_flush_market_manifest"),
            # This fixture verifies outer-batch progress only. Its fixed 2026-08-07
            # dates must not enter the real post-close settlement contract.
            patch.object(downloader, "_is_a_share_market_closed", return_value=False),
        ):
            result = downloader.download_batch(
                tickers,
                force=True,
                progress_callback=lambda *values: events.append(values),
            )

        self.assertEqual(len(result), len(tickers))
        self.assertGreaterEqual(len(events), 4)
        self.assertEqual(events[-1][:2], (len(tickers), len(tickers)))
        self.assertTrue(any(0 < current < total for current, total, _, _ in events))
        max_outer_batch = downloader.TICKFLOW_BATCH_SIZE * downloader.TICKFLOW_MAX_WORKERS
        self.assertTrue(
            all(len(call.args[0]) <= max_outer_batch for call in batch_fetch.call_args_list)
        )

    def test_run_scan_forwards_downloader_progress_to_structured_callback(self) -> None:
        events: list[tuple[str, int, int, str]] = []

        def fake_download_batch(*args, **kwargs):
            callback = kwargs["progress_callback"]
            callback(3, 10, 3, 0)
            return {}

        with (
            patch.object(scanner, "download_batch", side_effect=fake_download_batch),
            patch.object(scanner, "load_checkpoint", return_value=set()),
            patch.object(scanner, "clear_checkpoint"),
            patch.object(scanner, "enrich_results"),
        ):
            scanner.run_scan(
                stock_universe=[TickerInfo(ticker="000001.SZ")],
                etf_universe=[],
                resume=False,
                progress_callback=lambda *values: events.append(values),
            )

        self.assertTrue(
            any(stage == "download" and current == 3 and total == 10 for stage, current, total, _ in events)
        )
        self.assertTrue(any(stage == "analyse" and current == 0 for stage, current, _, _ in events))

    def test_inprocess_worker_never_calls_tk_from_worker_thread(self) -> None:
        gui = gui_core.ScannerGUI.__new__(gui_core.ScannerGUI)
        gui._scan_event_queue = queue.Queue()
        gui._scan_completion_queue = queue.Queue()
        gui._scan_cancel_event = threading.Event()
        gui._cancel_requested = False
        gui._log_queue = queue.Queue()
        gui._last_scan_execution = None
        gui._scan_execution_mode = "inprocess"
        gui.root = Mock()
        result = object()
        with patch("scan_service.execute_scan", return_value=result):
            gui._run_scan_inprocess(object(), ["python", "main.py", "scan"])
        gui.root.after.assert_not_called()
        self.assertIs(gui._last_scan_execution, result)
        self.assertEqual(gui._scan_completion_queue.get_nowait(), ("finished", 0))

    def test_structured_progress_updates_bar_status_and_visible_log(self) -> None:
        gui = gui_core.ScannerGUI.__new__(gui_core.ScannerGUI)
        gui.progress = Mock()
        gui.status = Mock()
        gui.append_log = Mock()
        gui._last_scan_progress_text = ""

        gui._apply_scan_progress_event(
            "download", 500, 5985, "TickFlow 行情 500/5985 · 可用 497 · 无数据/失败 3"
        )

        gui.progress.configure.assert_called_with(
            mode="determinate", maximum=5985, value=500
        )
        status = gui.status.set.call_args.args[0]
        self.assertIn("500/5985", status)
        gui.append_log.assert_called_once()


if __name__ == "__main__":
    unittest.main()
