from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

import performance_cache


def _market_frame(rows: int = 40) -> pd.DataFrame:
    index = pd.bdate_range("2026-06-01", periods=rows)
    values = [10.0 + i * 0.1 for i in range(rows)]
    return pd.DataFrame(
        {
            "Open": values,
            "High": [value + 0.4 for value in values],
            "Low": [value - 0.4 for value in values],
            "Close": [value + 0.1 for value in values],
            "Volume": [1_000_000.0 + i for i in range(rows)],
            "Amount": [10_000_000.0 + i * 1000 for i in range(rows)],
        },
        index=index,
    )


class CacheHistoryIntegrityTests(unittest.TestCase):
    def test_full_history_fingerprint_detects_revision_outside_tail(self) -> None:
        original = _market_frame()
        revised = original.copy()
        revised.iloc[2, revised.columns.get_loc("Close")] += 1.0

        self.assertEqual(
            performance_cache.market_tail_fingerprint(original),
            performance_cache.market_tail_fingerprint(revised),
        )
        self.assertNotEqual(
            performance_cache.market_history_fingerprint(original),
            performance_cache.market_history_fingerprint(revised),
        )

    def test_prefix_match_rejects_older_revision_even_when_last_12_rows_match(self) -> None:
        original = _market_frame()
        state = performance_cache.market_cache_state(original)
        extended = pd.concat(
            [
                original,
                pd.DataFrame(
                    {
                        "Open": [14.0],
                        "High": [14.4],
                        "Low": [13.6],
                        "Close": [14.1],
                        "Volume": [1_100_000.0],
                        "Amount": [15_000_000.0],
                    },
                    index=[original.index[-1] + pd.offsets.BDay(1)],
                ),
            ]
        )
        self.assertTrue(performance_cache.market_prefix_matches(extended, state))

        revised = extended.copy()
        revised.iloc[1, revised.columns.get_loc("Volume")] += 50_000.0
        self.assertFalse(performance_cache.market_prefix_matches(revised, state))

    def test_indicator_cache_rebuilds_when_older_market_row_changes(self) -> None:
        source = _market_frame()
        revised = source.copy()
        revised.iloc[0, revised.columns.get_loc("Low")] -= 1.0
        calls = 0

        def compute(frame: pd.DataFrame) -> pd.DataFrame:
            nonlocal calls
            calls += 1
            result = frame.copy()
            result["SyntheticIndicator"] = result["Low"].expanding().min()
            return result

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "000001.SZ.parquet"
            source_path.write_text("state-a", encoding="utf-8")
            indicator_dir = root / "indicators"

            with patch.object(performance_cache, "INDICATOR_CACHE_DIR", indicator_dir):
                first, first_hit = performance_cache.load_or_compute_indicators(
                    "000001.SZ", source, compute, source_path=source_path
                )
                self.assertFalse(first_hit)
                self.assertEqual(calls, 1)

                source_path.write_text("state-b-longer", encoding="utf-8")
                os.utime(source_path, None)
                second, second_hit = performance_cache.load_or_compute_indicators(
                    "000001.SZ", revised, compute, source_path=source_path
                )

        self.assertFalse(second_hit)
        self.assertEqual(calls, 2)
        self.assertNotEqual(
            float(first["SyntheticIndicator"].iloc[-1]),
            float(second["SyntheticIndicator"].iloc[-1]),
        )

    def test_same_file_signature_still_rebuilds_on_content_revision(self) -> None:
        source = _market_frame()
        revised = source.copy()
        revised.iloc[3, revised.columns.get_loc("Close")] += 2.0
        calls = 0

        def compute(frame: pd.DataFrame) -> pd.DataFrame:
            nonlocal calls
            calls += 1
            result = frame.copy()
            result["SyntheticIndicator"] = result["Close"].expanding().mean()
            return result

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_path = root / "000001.SZ.parquet"
            source_path.write_text("unchanged-signature-file", encoding="utf-8")
            indicator_dir = root / "indicators"

            with patch.object(performance_cache, "INDICATOR_CACHE_DIR", indicator_dir):
                first, first_hit = performance_cache.load_or_compute_indicators(
                    "000001.SZ", source, compute, source_path=source_path
                )
                self.assertFalse(first_hit)
                original_stat = source_path.stat()

                # Do not touch source_path at all: size, mtime and path identity
                # remain the same while the in-memory OHLCV history is revised.
                second, second_hit = performance_cache.load_or_compute_indicators(
                    "000001.SZ", revised, compute, source_path=source_path
                )
                after_stat = source_path.stat()

        self.assertEqual(original_stat.st_size, after_stat.st_size)
        self.assertEqual(original_stat.st_mtime_ns, after_stat.st_mtime_ns)
        self.assertFalse(second_hit)
        self.assertEqual(calls, 2)
        self.assertNotEqual(
            float(first["SyntheticIndicator"].iloc[-1]),
            float(second["SyntheticIndicator"].iloc[-1]),
        )

    def test_cache_namespaces_advance_under_stronger_revision_contract(self) -> None:
        self.assertEqual(performance_cache.INDICATOR_CACHE_VERSION, "v7")
        self.assertEqual(performance_cache.BACKTEST_CACHE_VERSION, "v10")
        self.assertIn("history-fingerprint", performance_cache.MARKET_DATA_CACHE_NAMESPACE)


if __name__ == "__main__":
    unittest.main()
