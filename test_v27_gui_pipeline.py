from __future__ import annotations

import inspect
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import analytics
import gui

# Keep this as a normal connector commit so the final PR receives real matrix CI.


class V27GuiPipelineTests(unittest.TestCase):
    def test_daily_pipeline_is_first_class_gui_action(self):
        source = inspect.getsource(gui.DecisionScannerGUI._build_ui)
        self.assertIn("⚡ 今日一键更新", source)
        self.assertIn("command=self.start_daily_pipeline", source)
        method = inspect.getsource(gui.DecisionScannerGUI.start_daily_pipeline)
        self.assertIn("--backtest-mode", method)
        self.assertIn('"fast"', method)

    def test_recent_signal_status_translations_are_complete(self):
        instance = gui.DecisionScannerGUI.__new__(gui.DecisionScannerGUI)
        expected = {
            "NEW": "新出现",
            "STRENGTHEN": "正在增强",
            "CONFIRMED": "持续确认",
            "WATCH": "观察中",
            "WEAKEN": "正在转弱",
            "FAILED": "已失效",
            "EXPIRED": "已过期",
        }
        for raw, translated in expected.items():
            self.assertEqual(instance._format_table_value("SignalStatus", raw), translated)

    def test_two_to_seven_tickers_use_thread_pool(self):
        benchmark = pd.DataFrame(
            {"Close": [100.0] * 400},
            index=pd.date_range("2025-01-01", periods=400, freq="B"),
        )
        tickers = [f"00000{index}.SZ" for index in range(1, 5)]
        with tempfile.TemporaryDirectory() as directory, patch.object(
            analytics, "OUTPUT_DIR", Path(directory)
        ), patch.object(
            analytics, "_load_benchmark_frames", return_value={"沪深300": benchmark}
        ), patch.object(
            analytics, "_backtest_one_ticker_cached", return_value=([], False)
        ) as worker, patch.object(
            analytics, "BACKTEST_PROCESS_MIN_TICKERS", 8
        ):
            summary = analytics.run_historical_backtest(
                tickers,
                workers=4,
                mode="exact",
            )
        expected_workers = min(4, max(1, (os.cpu_count() or 2) - 1), len(tickers))
        self.assertEqual(summary.engine, "thread")
        self.assertEqual(summary.worker_count, expected_workers)
        self.assertGreaterEqual(summary.worker_count, 2)
        self.assertEqual(worker.call_count, 4)

    def test_single_ticker_remains_sequential_to_preserve_exact_semantics(self):
        profile = analytics._resolve_backtest_profile("exact", 1)
        self.assertEqual(analytics._adaptive_worker_count(1, None, profile), 1)


if __name__ == "__main__":
    unittest.main()
