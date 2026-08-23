from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

import analytics  # noqa: F401 - installs the canonical runtime
import analytics_core as core
import backtest_sample_acceleration_v80 as sample_v80
import backtest_vectorization_v98 as v98
import conditional_fill_v96 as conditional
import score_core
import scoring_consistency_v94 as consistency


class V98FastScoreIntegrityTests(unittest.TestCase):
    def test_dense_trend_uses_full_504_bar_peak(self) -> None:
        dates = pd.bdate_range("2023-01-02", periods=600)
        close = np.full(600, 100.0)
        close[250] = 150.0
        frame = pd.DataFrame(
            {
                "Close": close,
                "MA200": np.full(600, 100.0),
            },
            index=dates,
        )

        score252 = v98._dense_trend_score(frame, peak_window=252)[-1]
        score504 = v98._dense_trend_score(frame, peak_window=504)[-1]
        scalar = score_core.score_trend(frame.iloc[-504:])

        self.assertGreater(score504, score252 + 2.0)
        self.assertAlmostEqual(float(score504), float(scalar), places=10)

    def test_canonical_trigger_wrapper_captures_corrected_fast_kernel(self) -> None:
        self.assertIs(consistency._ORIGINAL_FAST_SCORE_MATRIX, v98._fast_score_matrix)


class V98TradeDateVectorizationTests(unittest.TestCase):
    def test_datetime_index_uses_vector_path_without_timezone_leak(self) -> None:
        index = pd.date_range("2026-01-05 14:30", periods=4, freq="D", tz="Asia/Shanghai")
        frame = pd.DataFrame(index=index)
        values = v98._date_array(frame)
        expected = index.tz_localize(None).normalize().to_numpy(dtype="datetime64[ns]")
        np.testing.assert_array_equal(values, expected)

    def test_numeric_index_stays_undated(self) -> None:
        frame = pd.DataFrame(index=pd.RangeIndex(4))
        values = v98._date_array(frame)
        self.assertTrue(np.isnat(values).all())


class V98DrawdownVectorizationTests(unittest.TestCase):
    def test_selected_drawdown_curves_match_guarded_scalar_reducer(self) -> None:
        rng = np.random.default_rng(982026)
        close = 20.0 * np.cumprod(1.0 + rng.normal(0.0002, 0.01, 140))
        low = close * (1.0 - rng.uniform(0.001, 0.02, len(close)))
        starts = np.asarray([10, 25, 50], dtype=np.int64)
        entry = close[starts] * np.asarray([1.01, 0.995, 1.02])
        curves = v98._selected_drawdown_curves(
            starts,
            entry,
            close,
            low,
            max_forward=70,
        )

        for row, start in enumerate(starts):
            for offset in (20, 60):
                expected = sample_v80._drawdown_percent(
                    float(entry[row]),
                    close,
                    low,
                    int(start),
                    int(start + offset),
                )
                self.assertAlmostEqual(
                    float(curves[row, offset]), float(expected), places=12
                )

    def test_invalid_mid_window_value_preserves_nan_semantics(self) -> None:
        close = np.linspace(10.0, 12.0, 100)
        low = close - 0.2
        close[35] = np.nan
        starts = np.asarray([20], dtype=np.int64)
        entry = np.asarray([10.5])
        curves = v98._selected_drawdown_curves(
            starts, entry, close, low, max_forward=60
        )
        expected = sample_v80._drawdown_percent(10.5, close, low, 20, 60)
        self.assertTrue(np.isnan(expected))
        self.assertTrue(np.isnan(curves[0, 40]))


class V98ImmediateExecutionParityTests(unittest.TestCase):
    @staticmethod
    def _frame(rows: int = 380) -> pd.DataFrame:
        dates = pd.bdate_range("2024-01-02", periods=rows)
        close = np.linspace(10.0, 12.0, rows)
        return pd.DataFrame(
            {
                "Open": close * 1.001,
                "High": close * 1.01,
                "Low": close * 0.99,
                "Close": close,
                "Volume": np.full(rows, 2_000_000.0),
            },
            index=dates,
        )

    def test_batched_immediate_execution_matches_v80_sample_contract(self) -> None:
        frame = self._frame()
        profile = SimpleNamespace(name="exact")

        def fake_evaluations(*_args, component_sink=None, **_kwargs):
            if component_sink is not None:
                component_sink[300] = (51.0, 22.0, 18.0)
            return [(300, 61.0, "BUY_NOW")]

        with patch.object(
            core,
            "load_or_compute_indicators",
            return_value=(frame, False),
        ), patch.object(
            core,
            "_cache_path",
            return_value=Path("missing.parquet"),
        ), patch.object(
            core,
            "_signal_evaluations",
            side_effect=fake_evaluations,
        ), patch.object(
            core,
            "point_in_time_eligibility",
            return_value=(True, "verified"),
        ):
            legacy = v98._ORIGINAL_SAMPLE_BACKTEST(
                "000001.SZ", "cache", None, profile=profile, frame=frame
            )
            vectorized = v98._backtest_one_ticker(
                "000001.SZ", "cache", None, profile=profile, frame=frame
            )

        self.assertEqual(len(vectorized), len(legacy))
        self.assertEqual(len(vectorized), 1)
        for field in (
            "entry_signal",
            "signal_date",
            "entry_date",
            "exit20_date",
            "exit60_date",
            "exit20_delay_days",
            "exit60_delay_days",
            "sample_weight",
        ):
            self.assertEqual(vectorized[0][field], legacy[0][field])
        for field in (
            "entry_price",
            "round_trip_cost20_pct",
            "round_trip_cost60_pct",
            "return20",
            "return60",
            "net_return20",
            "net_return60",
            "drawdown20",
            "drawdown60",
            "score",
            "setup_score",
            "trigger_score",
            "execution_score",
        ):
            self.assertAlmostEqual(
                float(vectorized[0][field]), float(legacy[0][field]), places=10
            )


class V98WaitPullbackIntegrityTests(unittest.TestCase):
    @staticmethod
    def _frame(rows: int = 380) -> pd.DataFrame:
        dates = pd.bdate_range("2024-01-02", periods=rows)
        frame = pd.DataFrame(
            {
                "Open": np.full(rows, 110.0),
                "High": np.full(rows, 111.0),
                "Low": np.full(rows, 109.0),
                "Close": np.full(rows, 110.0),
                "Volume": np.full(rows, 1_000_000.0),
                "MA20": np.full(rows, 108.0),
                "ATR14": np.full(rows, 2.0),
            },
            index=dates,
        )
        # Signal is row 300. T+1 is suspended, while T+2 is tradeable and
        # touches a 100-102 limit zone from above.
        frame.iloc[301, frame.columns.get_loc("Volume")] = 0.0
        frame.iloc[302, frame.columns.get_loc("Open")] = 104.0
        frame.iloc[302, frame.columns.get_loc("High")] = 105.0
        frame.iloc[302, frame.columns.get_loc("Low")] = 101.0
        frame.iloc[302, frame.columns.get_loc("Close")] = 104.0
        return frame

    def test_batch_wait_order_survives_suspended_t1_and_fills_t2(self) -> None:
        frame = self._frame()
        fill_index, fill_price, fill_delay, fill_basis = v98._wait_fill_batch(
            "000001.SZ",
            frame,
            np.asarray([300]),
            np.asarray([100.0]),
            np.asarray([102.0]),
            is_etf=False,
        )
        self.assertEqual(int(fill_index[0]), 302)
        self.assertAlmostEqual(float(fill_price[0]), 102.0)
        self.assertEqual(int(fill_delay[0]), 2)
        self.assertEqual(str(fill_basis[0]), "LIMIT_AT_ZONE_HIGH")

    def test_wait_sample_generation_has_no_t1_tradeability_gate(self) -> None:
        frame = self._frame()
        profile = SimpleNamespace(name="exact")

        def fake_evaluations(*_args, component_sink=None, **_kwargs):
            if component_sink is not None:
                component_sink[300] = (52.0, 24.0, 17.0)
            return [(300, 63.0, "WAIT_PULLBACK")]

        zone_low = np.full(len(frame), 100.0)
        zone_high = np.full(len(frame), 102.0)
        with patch.object(
            core,
            "_signal_evaluations",
            side_effect=fake_evaluations,
        ), patch.object(
            core,
            "point_in_time_eligibility",
            return_value=(True, "verified"),
        ), patch.object(
            v98,
            "_entry_zone_arrays",
            return_value=(zone_low, zone_high),
        ):
            samples = v98._wait_samples(
                "000001.SZ",
                frame,
                None,
                0.00025,
                0.0005,
                0.001,
                (None, None),
                profile=profile,
                signal_start_index=None,
                sample_min_signal_index=None,
            )

        self.assertEqual(len(samples), 1)
        sample = samples[0]
        self.assertEqual(sample["entry_signal"], "WAIT_PULLBACK")
        self.assertEqual(sample["entry_fill_delay_days"], 2)
        self.assertEqual(sample["entry_date"], frame.index[302].strftime("%Y-%m-%d"))
        self.assertAlmostEqual(float(sample["entry_price"]), 102.0)

    def test_conditional_wrapper_runs_immediate_backtest_only_once(self) -> None:
        frame = self._frame()
        original = Mock(return_value=[])
        with patch.object(
            conditional,
            "_ORIGINAL_BACKTEST_ONE_TICKER",
            original,
        ), patch.object(
            conditional,
            "_load_enriched",
            return_value=frame,
        ), patch.object(
            v98,
            "_wait_samples",
            return_value=[],
        ):
            result = v98._conditional_backtest_one_ticker(
                "000001.SZ", "cache", None, frame=frame
            )
        self.assertEqual(result, [])
        self.assertEqual(original.call_count, 1)


if __name__ == "__main__":
    unittest.main()
