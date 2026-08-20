from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import pandas as pd

import analytics_core
import backtest_acceleration_v77 as worker_accel
import backtest_incremental_v78 as incremental
import cache_acceleration_v77
import historical_lookup_acceleration_v78 as history_accel
import universe_cache_acceleration_v78 as universe_accel


class UniverseMetadataAccelerationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._metadata_before = dict(universe_accel._downloader._INSTRUMENT_META)
        self._state_before = universe_accel._LAST_FILE_STATE
        universe_accel._load_for_state.cache_clear()
        universe_accel._price_limit_evidence_tuple.cache_clear()

    def tearDown(self) -> None:
        universe_accel._load_for_state.cache_clear()
        universe_accel._price_limit_evidence_tuple.cache_clear()
        universe_accel._downloader._INSTRUMENT_META.clear()
        universe_accel._downloader._INSTRUMENT_META.update(self._metadata_before)
        universe_accel._LAST_FILE_STATE = self._state_before

    def test_universe_json_loader_runs_once_per_file_state(self) -> None:
        payload = {
            "stocks": ["000001.SZ", "000002.SZ"],
            "etfs": [],
            "metadata": {"000001.SZ": {"name": "A"}},
        }
        legacy = Mock(return_value=payload)
        with patch.object(
            universe_accel, "_file_state", return_value=(100, 200)
        ), patch.object(
            universe_accel, "_LEGACY_LOAD_UNIVERSE_CACHE", legacy
        ):
            universe_accel._LAST_FILE_STATE = (100, 200)
            first = universe_accel.load_universe_cache()
            second = universe_accel.load_universe_cache()
            third = universe_accel.load_universe_cache()

        self.assertIs(first, second)
        self.assertIs(second, third)
        self.assertEqual(legacy.call_count, 1)

    def test_price_limit_evidence_is_memoized_per_ticker(self) -> None:
        legacy = Mock(
            return_value={
                "ticker": "000001.SZ",
                "pct": 0.10,
                "source": "explicit_ratio_metadata",
            }
        )
        with patch.object(
            universe_accel, "_LEGACY_GET_PRICE_LIMIT_EVIDENCE", legacy
        ):
            first = universe_accel.get_price_limit_evidence("000001.SZ", False)
            second = universe_accel.get_price_limit_evidence("000001.SZ", False)
        self.assertEqual(first, second)
        self.assertEqual(legacy.call_count, 1)

    def test_file_change_invalidates_metadata_and_price_limit_cache(self) -> None:
        payload = {"stocks": [], "etfs": [], "metadata": {}}
        legacy_loader = Mock(return_value=payload)
        legacy_limit = Mock(
            return_value={
                "ticker": "000001.SZ",
                "pct": None,
                "source": "exchange_fallback",
            }
        )
        universe_accel._downloader._INSTRUMENT_META["SENTINEL"] = {"name": "old"}
        with patch.object(
            universe_accel, "_LEGACY_LOAD_UNIVERSE_CACHE", legacy_loader
        ), patch.object(
            universe_accel, "_LEGACY_GET_PRICE_LIMIT_EVIDENCE", legacy_limit
        ):
            with patch.object(universe_accel, "_file_state", return_value=(1, 10)):
                universe_accel._LAST_FILE_STATE = (1, 10)
                universe_accel.load_universe_cache()
                universe_accel.get_price_limit_evidence("000001.SZ", False)
            with patch.object(universe_accel, "_file_state", return_value=(2, 11)):
                universe_accel.load_universe_cache()
                universe_accel.get_price_limit_evidence("000001.SZ", False)

        self.assertNotIn("SENTINEL", universe_accel._downloader._INSTRUMENT_META)
        self.assertEqual(legacy_loader.call_count, 2)
        self.assertEqual(legacy_limit.call_count, 2)


class HistoricalLookupAccelerationTests(unittest.TestCase):
    def tearDown(self) -> None:
        history_accel.clear_historical_lookup_acceleration()

    def test_snapshot_directory_signature_is_not_scanned_per_sample(self) -> None:
        entries = {
            "000001.SZ": (
                (pd.Timestamp("2025-01-01"), True, ""),
                (pd.Timestamp("2026-01-01"), False, "ST"),
            )
        }
        key = ("cache/historical_universe", (("u.csv", 1, 10),))
        with patch.object(
            history_accel._history, "_snapshot_cache_key", return_value=key
        ) as scan, patch.object(
            history_accel._history, "_load_snapshot_index", return_value=entries
        ) as load:
            history_accel.clear_historical_lookup_acceleration()
            self.assertEqual(
                history_accel.point_in_time_eligibility(
                    "000001.SZ", pd.Timestamp("2025-06-01")
                ),
                (True, "eligible"),
            )
            self.assertEqual(
                history_accel.point_in_time_eligibility(
                    "000001.SZ", pd.Timestamp("2026-06-01")
                ),
                (False, "ST"),
            )
            history_accel.point_in_time_eligibility(
                "000001.SZ", pd.Timestamp("2025-08-01")
            )

        self.assertEqual(scan.call_count, 1)
        self.assertEqual(load.call_count, 1)


class IncrementalBacktestAccelerationTests(unittest.TestCase):
    def test_maturity_rewind_covers_horizon_exit_delay_and_t1(self) -> None:
        expected = (
            max(60, int(analytics_core.BACKTEST_OUTCOME_HORIZON_DAYS))
            + int(analytics_core.BACKTEST_MAX_EXIT_DELAY_DAYS)
            + 2
        )
        self.assertEqual(incremental._maturity_rewind_bars(), expected)

    def test_daily_append_recomputes_far_less_than_fixed_360_tail(self) -> None:
        old_rows = 2600
        new_rows = 2601
        cutoff = max(251, old_rows - incremental._maturity_rewind_bars())
        warmup = max(
            251,
            cutoff
            - max(
                int(analytics_core.BACKTEST_FAST_COOLDOWN_DAYS),
                int(analytics_core.BACKTEST_OUTCOME_HORIZON_DAYS),
                int(analytics_core.BACKTEST_FAST_CANDIDATE_GAP_DAYS),
            ),
        )
        recomputed = new_rows - warmup
        self.assertLess(recomputed, 180)
        self.assertLess(recomputed, 360)

    def test_long_gap_starts_before_old_cache_end_and_covers_all_new_bars(self) -> None:
        old_rows = 2000
        new_rows = 2400
        cutoff = max(251, old_rows - incremental._maturity_rewind_bars())
        self.assertLess(cutoff, old_rows)
        self.assertGreater(new_rows - cutoff, new_rows - old_rows)

    def test_spawn_worker_bundle_composes_fast_hash_and_lookup_modules(self) -> None:
        worker_accel.install()
        self.assertTrue(incremental._INSTALLED)
        self.assertTrue(history_accel._INSTALLED)
        self.assertIs(
            worker_accel._LEGACY_MARKET_CACHE_STATE,
            cache_acceleration_v77.market_cache_state,
        )
        self.assertIs(
            analytics_core.point_in_time_eligibility,
            history_accel.point_in_time_eligibility,
        )


if __name__ == "__main__":
    unittest.main()
