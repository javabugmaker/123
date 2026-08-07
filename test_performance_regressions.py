from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import analytics
import gui_core
import performance_cache
from config import (
    BACKTEST_CHUNK_SIZE,
    BACKTEST_MAX_PROCESSES,
    BACKTEST_PROGRESS_INTERVAL,
    BACKTEST_SCORE_WINDOW_BARS,
)


class TestPerformanceConfiguration(unittest.TestCase):
    def test_backtest_limits_are_bounded(self):
        self.assertGreaterEqual(BACKTEST_SCORE_WINDOW_BARS, 504)
        self.assertGreaterEqual(BACKTEST_MAX_PROCESSES, 2)
        self.assertGreaterEqual(BACKTEST_CHUNK_SIZE, 1)
        self.assertLessEqual(BACKTEST_PROGRESS_INTERVAL, 50)

    def test_gui_understands_backtest_progress(self):
        line = (
            "Backtesting progress: 250/5981 tickers, 422 samples. "
            "4.2% | cache=10 | elapsed=3m10s | ETA=1h02m | rate=1.30 ticker/s"
        )
        match = gui_core.BACKTEST_PROGRESS_RE.search(line)
        self.assertIsNotNone(match)
        self.assertEqual(match.groups(), ("250", "5981", "422"))
        self.assertEqual(gui_core.BACKTEST_ETA_RE.search(line).group(1).strip(), "1h02m")


class TestPersistentPerformanceCache(unittest.TestCase):
    def test_backtest_key_changes_with_market_signature(self):
        one = performance_cache.backtest_cache_key({"price_signature": "a", "cost": 1})
        two = performance_cache.backtest_cache_key({"price_signature": "b", "cost": 1})
        self.assertNotEqual(one, two)
        self.assertEqual(
            one,
            performance_cache.backtest_cache_key({"price_signature": "a", "cost": 1}),
        )

    def test_indicator_cache_reuses_same_source_file(self):
        frame = pd.DataFrame(
            {
                "Open": [1.0, 2.0],
                "High": [2.0, 3.0],
                "Low": [0.5, 1.5],
                "Close": [1.5, 2.5],
                "Volume": [100.0, 200.0],
            },
            index=pd.to_datetime(["2026-08-06", "2026-08-07"]),
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "raw.parquet"
            source.write_bytes(b"raw")
            indicator_dir = Path(directory) / "indicators"
            with patch.object(performance_cache, "INDICATOR_CACHE_DIR", indicator_dir):
                calls = {"count": 0}

                def compute(value):
                    calls["count"] += 1
                    result = value.copy()
                    result["MA20"] = result["Close"]
                    return result

                first, first_hit = performance_cache.load_or_compute_indicators(
                    "000001.SZ", frame, compute, source_path=source
                )
                second, second_hit = performance_cache.load_or_compute_indicators(
                    "000001.SZ", frame, compute, source_path=source
                )
                self.assertFalse(first_hit)
                self.assertTrue(second_hit)
                self.assertEqual(calls["count"], 1)
                pd.testing.assert_frame_equal(first, second, check_freq=False)


class TestBacktestHotPath(unittest.TestCase):
    def test_signal_points_is_compatibility_projection(self):
        frame = pd.DataFrame(
            {
                "Close": [10.0] * 40,
                "VolMA20": [2.0] * 40,
                "VolMA120": [1.0] * 40,
                "CMF": [0.1] * 40,
                "MA50": [10.0] * 40,
            }
        )
        points = analytics._signal_points(frame, cooldown=10)
        self.assertEqual(points, analytics._legacy_signal_points(frame, 10))

    def test_small_backtest_does_not_force_process_pool(self):
        benchmark = pd.DataFrame(
            {"Close": [100.0] * 400},
            index=pd.date_range("2025-01-01", periods=400, freq="B"),
        )
        with patch.object(analytics, "_load_benchmark_frames", return_value={"沪深300": benchmark}), patch.object(
            analytics, "_backtest_one_ticker_cached", return_value=([], False)
        ) as worker, patch.object(analytics, "BACKTEST_PROCESS_MIN_TICKERS", 100):
            summary = analytics.run_historical_backtest(["000001.SZ"], workers=8)
        worker.assert_called_once()
        self.assertEqual(summary.engine, "sequential")


if __name__ == "__main__":
    unittest.main()
