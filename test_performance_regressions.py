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
    BACKTEST_AUTO_EXACT_MAX_TICKERS,
    BACKTEST_CHUNK_SIZE,
    BACKTEST_FAST_COOLDOWN_DAYS,
    BACKTEST_FAST_SCORE_WINDOW_BARS,
    BACKTEST_MAX_PROCESSES,
    BACKTEST_PROGRESS_INTERVAL,
    BACKTEST_SCORE_WINDOW_BARS,
)


class TestPerformanceConfiguration(unittest.TestCase):
    def test_backtest_limits_are_bounded(self):
        self.assertGreaterEqual(BACKTEST_SCORE_WINDOW_BARS, 504)
        self.assertGreaterEqual(BACKTEST_FAST_SCORE_WINDOW_BARS, 252)
        self.assertLessEqual(BACKTEST_FAST_SCORE_WINDOW_BARS, BACKTEST_SCORE_WINDOW_BARS)
        self.assertGreaterEqual(BACKTEST_FAST_COOLDOWN_DAYS, 20)
        self.assertEqual(BACKTEST_AUTO_EXACT_MAX_TICKERS, 100)
        self.assertGreaterEqual(BACKTEST_MAX_PROCESSES, 2)
        self.assertGreaterEqual(BACKTEST_CHUNK_SIZE, 1)
        self.assertLessEqual(BACKTEST_PROGRESS_INTERVAL, 50)

    def test_gui_understands_backtest_progress(self):
        line = (
            "Backtesting progress: 250/5981 tickers, 422 samples. "
            "4.2% | mode=FAST | cache=10 | elapsed=3m10s | ETA=1h02m | rate=1.30 ticker/s"
        )
        match = gui_core.BACKTEST_PROGRESS_RE.search(line)
        self.assertIsNotNone(match)
        self.assertEqual(match.groups(), ("250", "5981", "422"))
        self.assertEqual(gui_core.BACKTEST_ETA_RE.search(line).group(1).strip(), "1h02m")
        self.assertEqual(gui_core.BACKTEST_MODE_RE.search(line).group(1), "FAST")

    def test_auto_mode_uses_exact_for_top50_and_fast_for_full_market(self):
        self.assertEqual(analytics._resolve_backtest_profile("auto", 50).name, "exact")
        self.assertEqual(analytics._resolve_backtest_profile("auto", 100).name, "exact")
        self.assertEqual(analytics._resolve_backtest_profile("auto", 101).name, "fast")
        self.assertEqual(analytics._resolve_backtest_profile("auto", 5985).name, "fast")


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

    def test_indicator_cache_incrementally_appends_new_daily_bar(self):
        base_index = pd.date_range("2023-01-02", periods=800, freq="B")
        base = pd.DataFrame(
            {
                "Open": range(800),
                "High": [value + 2 for value in range(800)],
                "Low": [max(0, value - 1) for value in range(800)],
                "Close": [value + 1 for value in range(800)],
                "Volume": [1000 + value for value in range(800)],
            },
            index=base_index,
            dtype=float,
        )
        extended = pd.concat(
            [
                base,
                pd.DataFrame(
                    {"Open": [800.0], "High": [802.0], "Low": [799.0], "Close": [801.0], "Volume": [1800.0]},
                    index=[base_index[-1] + pd.offsets.BDay(1)],
                ),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "raw.parquet"
            source.write_bytes(b"raw")
            indicator_dir = Path(directory) / "indicators"
            with patch.object(performance_cache, "INDICATOR_CACHE_DIR", indicator_dir):
                calls: list[int] = []

                def compute(value):
                    calls.append(len(value))
                    result = value.copy()
                    result["MA20"] = result["Close"].rolling(20, min_periods=1).mean()
                    result["OBV"] = result["Volume"].cumsum()
                    result["AD"] = result["Volume"].cumsum() * 0.5
                    return result

                performance_cache.load_or_compute_indicators(
                    "000001.SZ", base, compute, source_path=source
                )
                source.write_bytes(b"raw-extended")
                result, reused = performance_cache.load_or_compute_indicators(
                    "000001.SZ", extended, compute, source_path=source
                )
                self.assertTrue(reused)
                self.assertEqual(len(result), len(extended))
                self.assertEqual(calls[0], len(base))
                self.assertLess(calls[1], len(extended))


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
