from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

import analytics


def _market_frame(rows: int = 320) -> pd.DataFrame:
    index = pd.bdate_range("2025-01-01", periods=rows)
    return pd.DataFrame(
        {
            "Open": [10.0] * rows,
            "High": [10.5] * rows,
            "Low": [9.5] * rows,
            "Close": [10.2] * rows,
            "Volume": [1_000_000.0] * rows,
            "Amount": [10_200_000.0] * rows,
        },
        index=index,
    )


class BenchmarkCacheIntegrityTests(unittest.TestCase):
    def test_missing_current_benchmark_bypasses_cached_excess_returns(self) -> None:
        frame = _market_frame()
        sentinel = [
            {
                "ticker": "000001.SZ",
                "benchmark_return20": float("nan"),
                "benchmark_return60": float("nan"),
            }
        ]

        with patch.object(analytics, "_load_cache", return_value=frame), patch.object(
            analytics, "_backtest_one_ticker", return_value=sentinel
        ) as compute, patch.object(
            analytics,
            "load_backtest_cache_state",
            side_effect=AssertionError("cached benchmark samples must not be read"),
        ):
            samples, cache_hit = analytics._backtest_one_ticker_cached(
                "000001.SZ",
                "tickflow",
                None,
                0.0001,
                0.0005,
                0.001,
                (None, None),
                benchmark_name="沪深300",
            )

        self.assertFalse(cache_hit)
        self.assertEqual(samples, sentinel)
        compute.assert_called_once()
        self.assertIsNone(compute.call_args.args[2])

    def test_empty_benchmark_frame_is_also_unavailable(self) -> None:
        frame = _market_frame()
        with patch.object(analytics, "_load_cache", return_value=frame), patch.object(
            analytics, "_backtest_one_ticker", return_value=[]
        ) as compute, patch.object(
            analytics,
            "load_backtest_cache_state",
            side_effect=AssertionError("empty benchmark must not permit cache reuse"),
        ):
            samples, cache_hit = analytics._backtest_one_ticker_cached(
                "000001.SZ",
                "tickflow",
                pd.DataFrame(),
                0.0001,
                0.0005,
                0.001,
                (None, None),
            )

        self.assertEqual(samples, [])
        self.assertFalse(cache_hit)
        compute.assert_called_once()


if __name__ == "__main__":
    unittest.main()
