from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import pandas as pd

import analytics_core
import backtest_acceleration_v77 as accelerated


class BacktestAccelerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.previous_context = dict(
            getattr(analytics_core, "_BACKTEST_WORKER_CONTEXT", {})
        )

    def tearDown(self) -> None:
        analytics_core._BACKTEST_WORKER_CONTEXT = self.previous_context

    def test_worker_benchmark_market_state_is_computed_once(self) -> None:
        benchmark = pd.DataFrame(
            {"Close": [1.0, 1.1]},
            index=pd.to_datetime(["2026-08-18", "2026-08-19"]),
        )
        analytics_core._BACKTEST_WORKER_CONTEXT = {"benchmark_frame": benchmark}
        legacy = Mock(return_value={"rows": 2, "last": "2026-08-19"})

        with patch.object(accelerated, "_LEGACY_MARKET_CACHE_STATE", legacy):
            first = accelerated.market_cache_state(benchmark)
            second = accelerated.market_cache_state(benchmark)

        self.assertEqual(first, second)
        self.assertEqual(legacy.call_count, 1)

    def test_worker_benchmark_prefix_result_is_memoized_by_cached_state(self) -> None:
        benchmark = pd.DataFrame(
            {"Close": [1.0, 1.1]},
            index=pd.to_datetime(["2026-08-18", "2026-08-19"]),
        )
        analytics_core._BACKTEST_WORKER_CONTEXT = {"benchmark_frame": benchmark}
        state = {
            "rows": 1,
            "first": "2026-08-18",
            "last": "2026-08-18",
            "tail_fingerprint": "tail",
            "history_fingerprint": "history",
        }
        legacy = Mock(return_value=True)

        with patch.object(accelerated, "_LEGACY_MARKET_PREFIX_MATCHES", legacy):
            self.assertTrue(accelerated.market_prefix_matches(benchmark, state))
            self.assertTrue(accelerated.market_prefix_matches(benchmark, dict(state)))

        self.assertEqual(legacy.call_count, 1)

    def test_ticker_frames_never_use_benchmark_memoization(self) -> None:
        benchmark = pd.DataFrame({"Close": [1.0]})
        ticker = pd.DataFrame({"Close": [2.0]})
        analytics_core._BACKTEST_WORKER_CONTEXT = {"benchmark_frame": benchmark}
        legacy = Mock(return_value={"rows": 1})

        with patch.object(accelerated, "_LEGACY_MARKET_CACHE_STATE", legacy):
            accelerated.market_cache_state(ticker)
            accelerated.market_cache_state(ticker)

        self.assertEqual(legacy.call_count, 2)


if __name__ == "__main__":
    unittest.main()
