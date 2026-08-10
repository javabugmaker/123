from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import gui
import gui_core
import model_calibration
import scanner
import score
from downloader import TickerInfo
from scanner import ScanCancelled


class ModelV2RegressionTests(unittest.TestCase):
    def _market_frame(self, recovering: bool) -> pd.DataFrame:
        index = pd.date_range("2025-01-02", periods=320, freq="B")
        if recovering:
            first = np.linspace(20.0, 10.0, 220)
            second = np.linspace(10.0, 13.0, 100)
        else:
            first = np.linspace(20.0, 12.0, 220)
            second = np.linspace(12.0, 8.0, 100)
        close = np.concatenate([first, second])
        volume = np.linspace(2_000_000, 1_200_000, len(index))
        frame = pd.DataFrame(
            {
                "Open": close * 0.998,
                "High": close * 1.015,
                "Low": close * 0.985,
                "Close": close,
                "Volume": volume,
                "Amount": volume * close,
            },
            index=index,
        )
        from indicators import compute_all_indicators

        return compute_all_indicators(frame)

    def test_value_trap_distinguishes_recovery_from_continued_deterioration(self):
        recovering = self._market_frame(True)
        deteriorating = self._market_frame(False)
        recovery_risk = score.value_trap_risk(recovering)
        deterioration_risk = score.value_trap_risk(deteriorating)
        self.assertLess(recovery_risk, deterioration_risk)

    def test_global_calibration_uses_peer_evidence(self):
        rows = []
        dates = pd.date_range("2022-01-03", periods=120, freq="B")
        for index, date in enumerate(dates):
            rows.append(
                {
                    "ticker": f"000{index % 20:03d}.SZ",
                    "asset_type": "stock",
                    "entry_signal": "BREAKOUT_CONFIRM",
                    "score": 72.0 + index % 5,
                    "entry_date": date,
                    "sample_weight": 1.0,
                    "net_return20": 5.0,
                    "benchmark_return20": 1.0,
                    "net_return60": 8.0,
                    "benchmark_return60": 2.0,
                }
            )
        calibration = model_calibration.build_global_calibration(pd.DataFrame(rows), min_samples=20)
        peer_score, confidence, level = model_calibration.resolve_global_calibration(
            "stock", "BREAKOUT_CONFIRM", 74.0, calibration
        )
        self.assertGreater(peer_score, 50.0)
        self.assertGreater(confidence, 0.0)
        self.assertNotEqual(level, "none")

    def test_component_calibration_never_uses_test_to_select_weights(self):
        rows = []
        for split, count in (("validation", 80), ("test", 80)):
            for index in range(count):
                setup = float(index % 20) * 4.0
                trigger = float((index * 7) % 20) * 4.0
                execution = float((index * 11) % 20) * 4.0
                target = setup * 0.08 if split == "validation" else -setup * 0.08
                rows.append(
                    {
                        "split": split,
                        "entry_date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=index),
                        "ticker": "000001.SZ",
                        "asset_type": "stock",
                        "entry_signal": "WAIT_PULLBACK",
                        "score": 60.0,
                        "sample_weight": 1.0,
                        "setup_score": setup,
                        "trigger_score": trigger,
                        "execution_score": execution,
                        "net_return20": target,
                        "benchmark_return20": 0.0,
                        "net_return60": target,
                        "benchmark_return60": 0.0,
                    }
                )
        result = model_calibration.calibrate_component_weights(pd.DataFrame(rows))
        self.assertEqual(result.validation_samples, 80)
        self.assertEqual(result.test_samples, 80)
        self.assertGreaterEqual(result.setup_weight, 0.45)
        self.assertLessEqual(result.setup_weight, 0.70)
        self.assertGreaterEqual(result.execution_weight, 0.10)
        self.assertLessEqual(result.execution_weight, 0.25)

    def test_gui_is_a_real_subclass_not_a_monkey_patch_alias(self):
        self.assertTrue(issubclass(gui.DecisionScannerGUI, gui_core.ScannerGUI))
        self.assertIs(gui.ScannerGUI, gui.DecisionScannerGUI)
        self.assertIsNot(gui.ScannerGUI, gui_core.ScannerGUI)

    def test_run_scan_cancel_event_is_honored_before_network_work(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ScanCancelled):
            scanner.run_scan(
                stock_universe=[TickerInfo("000001.SZ")],
                etf_universe=[],
                cancel_event=cancel,
            )

    def test_run_scan_progress_callback_reports_prepare_stage(self):
        events: list[tuple[str, int, int, str]] = []
        callback = lambda stage, current, total, message: events.append(
            (stage, current, total, message)
        )
        with patch.object(scanner, "download_batch", return_value={}), patch.object(
            scanner, "clear_checkpoint"
        ), patch.object(scanner, "enrich_results"):
            report = scanner.run_scan(
                stock_universe=[TickerInfo("000001.SZ")],
                etf_universe=[],
                progress_callback=callback,
                resume=False,
            )
        self.assertEqual(report.total_tickers, 1)
        self.assertTrue(events)
        self.assertEqual(events[0][0], "prepare")


if __name__ == "__main__":
    unittest.main()
