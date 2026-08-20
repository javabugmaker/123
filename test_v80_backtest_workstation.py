from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

import analytics
import analytics_core as core
import backtest_alignment as alignment
import backtest_alignment_acceleration_v80 as alignment_fast
import backtest_cache_acceleration_v80 as cache_fast
import backtest_fastscore_v80 as fastscore
import backtest_sample_acceleration_v80 as sample_fast
import backtest_sample_guard_v80 as sample_guard
import backtest_worker_tuning_v80 as worker_tuning
import tradeability
import tradeability_acceleration_v80 as trade_fast
from indicators import compute_all_indicators


def _raw_frame(rows: int = 900) -> pd.DataFrame:
    rng = np.random.default_rng(802026)
    returns = rng.normal(0.00025, 0.013, rows)
    close = 18.0 * np.cumprod(1.0 + returns)
    open_price = close * (1.0 + rng.normal(0.0, 0.0035, rows))
    high = np.maximum(open_price, close) * (1.0 + rng.uniform(0.002, 0.018, rows))
    low = np.minimum(open_price, close) * (1.0 - rng.uniform(0.002, 0.018, rows))
    volume = rng.integers(900_000, 15_000_000, rows).astype(float)
    return pd.DataFrame(
        {
            "Open": open_price,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
            "Amount": volume * close,
        },
        index=pd.bdate_range("2018-01-02", periods=rows),
    )


def _tradeability_frame() -> pd.DataFrame:
    dates = pd.bdate_range("2020-08-17", periods=14)
    close = np.full(len(dates), 10.0)
    open_price = np.full(len(dates), 10.0)
    high = np.full(len(dates), 10.2)
    low = np.full(len(dates), 9.8)
    volume = np.full(len(dates), 1_000_000.0)
    volume[3] = 0.0
    open_price[6] = 11.0
    high[6] = 11.0
    low[6] = 11.0
    close[6] = 11.0
    open_price[9] = 9.0
    high[9] = 9.0
    low[9] = 9.0
    close[9] = 9.0
    return pd.DataFrame(
        {
            "Open": open_price,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        },
        index=dates,
    )


def _legacy_align(
    samples: list[dict[str, object]],
    benchmark: pd.DataFrame | None,
) -> list[dict[str, object]]:
    aligned: list[dict[str, object]] = []
    for source in samples:
        item = dict(source)
        entry_open = alignment._LEGACY_PRICE_ON_DATE(
            benchmark,
            item.get("entry_date"),
            "Open",
        )
        item["benchmark_entry_basis"] = "OPEN"
        item["benchmark_entry_price"] = entry_open
        valid_entry = np.isfinite(entry_open) and entry_open > 0.0
        statuses: list[str] = []
        for horizon in (20, 60):
            exit_close = alignment._LEGACY_PRICE_ON_DATE(
                benchmark,
                item.get(f"exit{horizon}_date"),
                "Close",
            )
            if valid_entry and np.isfinite(exit_close) and exit_close > 0.0:
                item[f"benchmark_return{horizon}"] = (
                    float(exit_close / entry_open - 1.0) * 100.0
                )
                statuses.append("ALIGNED")
            else:
                item[f"benchmark_return{horizon}"] = np.nan
                statuses.append("MISSING")
        item["benchmark_alignment_status"] = (
            "ALIGNED"
            if all(status == "ALIGNED" for status in statuses)
            else "INCOMPLETE"
        )
        aligned.append(item)
    return aligned


class FastScoreMatrixEquivalenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.enriched = compute_all_indicators(_raw_frame())

    def _assert_endpoint(self, index: int, *, is_etf: bool) -> None:
        matrix = fastscore._fast_score_matrix(self.enriched, is_etf=is_etf)
        self.assertIsNotNone(matrix)
        assert matrix is not None
        profile = core._resolve_backtest_profile("fast", 6000)
        historical = core._backtest_scoring_window(
            self.enriched,
            index,
            score_window=profile.score_window,
            include_volume_profile=False,
        )
        legacy_score = core.score_ticker(historical, is_etf=is_etf)
        legacy_entry = core.entry_point(
            historical,
            breakout=core._finite_float(
                getattr(legacy_score, "breakout_score", np.nan), np.nan
            ),
            volume_score=core._finite_float(
                getattr(legacy_score, "volume", np.nan), np.nan
            ),
            value_trap_risk_value=core._finite_float(
                getattr(legacy_score, "value_trap_risk", np.nan), np.nan
            ),
            price_decimals=core.tradable_price_decimals(is_etf),
        )
        pairs = (
            (matrix.base_score[index], legacy_score.base_score, "base_score"),
            (matrix.trigger_score[index], legacy_score.trigger_score, "trigger_score"),
            (
                matrix.execution_score[index],
                legacy_score.execution_score,
                "execution_score",
            ),
            (matrix.final_score[index], legacy_score.final_score, "final_score"),
            (
                matrix.breakout_score[index],
                legacy_score.breakout_score,
                "breakout_score",
            ),
            (
                matrix.value_trap_risk[index],
                legacy_score.value_trap_risk,
                "value_trap_risk",
            ),
        )
        for actual, expected, field in pairs:
            self.assertAlmostEqual(
                float(actual),
                float(expected),
                places=8,
                msg=f"{field}@{index}, etf={is_etf}",
            )
        self.assertEqual(
            str(matrix.entry_signal[index]).upper(),
            str(legacy_entry.get("signal", "AVOID")).upper(),
        )

    def test_stock_endpoints_match_legacy_score_engine(self) -> None:
        for index in (360, 480, 620, 760):
            self._assert_endpoint(index, is_etf=False)

    def test_etf_endpoints_match_legacy_score_engine(self) -> None:
        for index in (360, 480, 620, 760):
            self._assert_endpoint(index, is_etf=True)

    def test_internal_history_hole_fails_closed_to_legacy_path(self) -> None:
        frame = self.enriched.copy()
        frame.iloc[500, frame.columns.get_loc("CMF")] = np.nan
        self.assertIsNone(fastscore._fast_score_matrix(frame, is_etf=False))


class TradeabilityEquivalenceTests(unittest.TestCase):
    def setUp(self) -> None:
        trade_fast.clear_tradeability_cache()
        trade_fast.install()

    def tearDown(self) -> None:
        trade_fast.clear_tradeability_cache()
        trade_fast.install()

    def test_entry_and_exit_matrix_matches_stable_helpers(self) -> None:
        frame = _tradeability_frame()
        cases = (
            ("600000.SH", False),
            ("300001.SZ", False),
            ("588000.SH", True),
        )
        with patch("downloader.get_price_limit_evidence", return_value={}):
            for ticker, is_etf in cases:
                trade_fast.clear_tradeability_cache()
                for index in range(1, len(frame)):
                    expected_entry = tradeability._LEGACY_IS_ENTRY_TRADEABLE(
                        ticker, frame, index, is_etf=is_etf
                    )
                    actual_entry = trade_fast.is_entry_tradeable(
                        ticker, frame, index, is_etf=is_etf
                    )
                    self.assertEqual(actual_entry, expected_entry)
                    expected_exit = tradeability._LEGACY_IS_EXIT_TRADEABLE(
                        ticker, frame, index, is_etf=is_etf
                    )
                    actual_exit = trade_fast.is_exit_tradeable(
                        ticker, frame, index, is_etf=is_etf
                    )
                    self.assertEqual(actual_exit, expected_exit)

    def test_exit_resolution_matches_stable_delay_semantics(self) -> None:
        frame = _tradeability_frame()
        with patch("downloader.get_price_limit_evidence", return_value={}):
            for intended in (3, 6, 8, 9, 12):
                with patch.object(
                    tradeability,
                    "is_exit_tradeable",
                    tradeability._LEGACY_IS_EXIT_TRADEABLE,
                ):
                    expected = tradeability._LEGACY_RESOLVE_EXIT_INDEX(
                        "600000.SH",
                        frame,
                        intended,
                        is_etf=False,
                        max_delay_days=3,
                    )
                trade_fast.install()
                trade_fast.clear_tradeability_cache()
                actual = trade_fast.resolve_exit_index(
                    "600000.SH",
                    frame,
                    intended,
                    is_etf=False,
                    max_delay_days=3,
                )
                self.assertEqual(actual, expected)


class BenchmarkAlignmentEquivalenceTests(unittest.TestCase):
    def setUp(self) -> None:
        alignment_fast.clear_benchmark_alignment_cache()
        alignment_fast.install()

    def test_lookup_alignment_matches_v51_open_to_close_contract(self) -> None:
        benchmark = pd.DataFrame(
            {
                "Open": [100.0, 101.0, 102.0, 103.0, 104.0],
                "Close": [100.5, 101.5, 102.5, 103.5, 104.5],
            },
            index=pd.to_datetime(
                [
                    "2026-01-05",
                    "2026-01-06",
                    "2026-01-06",
                    "2026-01-07",
                    "2026-01-08",
                ]
            ),
        )
        samples: list[dict[str, object]] = [
            {
                "entry_date": "2026-01-06",
                "exit20_date": "2026-01-07",
                "exit60_date": "2026-01-08",
            },
            {
                "entry_date": "2026-01-05",
                "exit20_date": "2026-01-08",
                "exit60_date": "2026-01-09",
            },
        ]
        expected = _legacy_align(samples, benchmark)
        actual = alignment_fast.align_benchmark_returns(samples, benchmark)
        pd.testing.assert_frame_equal(
            pd.DataFrame(actual).sort_index(axis=1),
            pd.DataFrame(expected).sort_index(axis=1),
            check_dtype=False,
        )

    def test_already_aligned_cache_returns_without_recomputation(self) -> None:
        samples = [
            {
                "benchmark_entry_basis": "OPEN",
                "benchmark_alignment_status": "ALIGNED",
                "benchmark_return20": 1.0,
                "benchmark_return60": 2.0,
            }
        ]
        result = alignment_fast.align_benchmark_returns(samples, pd.DataFrame())
        self.assertIs(result, samples)


class CacheAndNumericalGuardTests(unittest.TestCase):
    def test_hot_same_state_does_not_rehash_prefix(self) -> None:
        frame = _raw_frame(12)
        state = {
            "rows": 12,
            "first": "2026-01-01",
            "last": "2026-01-12",
            "history_fingerprint": "same-fingerprint",
        }
        with patch.object(
            core,
            "market_prefix_matches",
            side_effect=AssertionError("hot cache must not prefix-hash again"),
        ):
            valid, exact = cache_fast._state_prefix_ok(frame, state, dict(state))
        self.assertTrue(valid)
        self.assertTrue(exact)

    def test_history_revision_still_runs_prefix_integrity_check(self) -> None:
        frame = _raw_frame(12)
        current = {
            "rows": 12,
            "first": "2026-01-01",
            "last": "2026-01-12",
            "history_fingerprint": "new-history",
        }
        cached = dict(current)
        cached["history_fingerprint"] = "old-history"
        with patch.object(core, "market_prefix_matches", return_value=False) as prefix:
            valid, exact = cache_fast._state_prefix_ok(frame, current, cached)
        self.assertFalse(valid)
        self.assertFalse(exact)
        prefix.assert_called_once_with(frame, cached)

    def test_drawdown_fast_path_matches_stable_finite_and_nan_semantics(self) -> None:
        closes = np.array([10.0, 10.5, 10.2, 11.0, 10.8], dtype=np.float64)
        lows = np.array([9.8, 10.2, 9.9, 10.7, 10.5], dtype=np.float64)
        entry = 10.0
        legacy_prices = np.concatenate(([entry], closes))
        legacy_lows = np.concatenate(([entry], lows))
        expected = float(
            ((legacy_lows / np.maximum.accumulate(legacy_prices) - 1.0).min())
            * 100.0
        )
        actual = sample_guard.drawdown_percent(entry, closes, lows, 0, len(closes) - 1)
        self.assertAlmostEqual(actual, expected, places=12)

        lows_with_nan = lows.copy()
        lows_with_nan[2] = np.nan
        self.assertTrue(
            np.isnan(
                sample_guard.drawdown_percent(
                    entry,
                    closes,
                    lows_with_nan,
                    0,
                    len(closes) - 1,
                )
            )
        )

    def test_worker_bootstrap_installs_numerical_guard(self) -> None:
        self.assertIs(sample_fast._drawdown_percent, sample_guard.drawdown_percent)


class WorkstationSchedulingTests(unittest.TestCase):
    def test_6c12t_defaults_use_physical_cores_without_smt_oversubscription(self) -> None:
        runtime = SimpleNamespace(
            logical_cpus=12,
            estimated_physical_cores=6,
            backtest_processes=6,
        )
        with (
            patch.object(worker_tuning, "runtime_profile", return_value=runtime),
            patch.dict(
                os.environ,
                {"INSTITUTION_SCANNER_BACKTEST_PROCESSES": ""},
                clear=False,
            ),
        ):
            fast_profile = worker_tuning._LEGACY_RESOLVE_PROFILE("fast", 6000)
            exact_profile = worker_tuning._LEGACY_RESOLVE_PROFILE("exact", 6000)
            self.assertEqual(
                worker_tuning.adaptive_worker_count(6000, None, fast_profile), 7
            )
            self.assertEqual(
                worker_tuning.adaptive_worker_count(6000, None, exact_profile), 6
            )
            self.assertEqual(
                worker_tuning.resolve_backtest_profile("fast", 6000).chunk_size,
                100,
            )
            self.assertEqual(
                worker_tuning.resolve_backtest_profile("exact", 6000).chunk_size,
                16,
            )

    def test_runtime_reports_v80_and_fastscore_is_installed_last(self) -> None:
        self.assertEqual(
            analytics.PERFORMANCE_ENGINE_VERSION,
            "2026-08-20-v80-vectorized-backtest-workstation-v1",
        )
        self.assertIs(core._signal_evaluations, fastscore._signal_evaluations)
        self.assertIs(tradeability.is_entry_tradeable, trade_fast.is_entry_tradeable)
        self.assertIs(sample_fast._drawdown_percent, sample_guard.drawdown_percent)


if __name__ == "__main__":
    unittest.main()
