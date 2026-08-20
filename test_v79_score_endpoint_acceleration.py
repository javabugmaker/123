from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

import analytics
import filters
import score
import score_acceleration_v79 as score_cache
import score_core
import score_endpoint_acceleration_v79 as endpoint
import volatility_state
from indicators import compute_all_indicators


def _frame(*, holes: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(7902)
    rows = 720
    returns = rng.normal(0.00025, 0.016, rows)
    close = 21.0 * np.cumprod(1.0 + returns)
    open_ = close * (1.0 + rng.normal(0.0, 0.004, rows))
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0.002, 0.022, rows))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0.002, 0.022, rows))
    volume = rng.integers(600_000, 14_000_000, rows).astype(float)
    enriched = compute_all_indicators(
        pd.DataFrame(
            {
                "Open": open_,
                "High": high,
                "Low": low,
                "Close": close,
                "Volume": volume,
                "Amount": volume * close,
            },
            index=pd.bdate_range("2023-08-01", periods=rows),
        )
    )
    if holes:
        for column, positions in {
            "Close": (318, 451),
            "High": (329,),
            "Volume": (341, 487),
            "MA50": (366,),
            "CMF": (399,),
            "OBV": (420,),
        }.items():
            if column in enriched.columns:
                enriched.iloc[list(positions), enriched.columns.get_loc(column)] = np.nan
    return enriched


class EndpointKernelEquivalenceTests(unittest.TestCase):
    def setUp(self) -> None:
        score_cache.clear_thread_score_cache()
        score_cache.install()
        endpoint.install()

    def tearDown(self) -> None:
        score_cache.clear_thread_score_cache()
        score_cache.install()
        endpoint.install()

    def test_value_trap_matches_stable_for_stock_and_etf(self) -> None:
        for frame in (_frame(), _frame(holes=True)):
            for is_etf in (False, True):
                score_cache.clear_thread_score_cache()
                expected = endpoint._LEGACY_VALUE_TRAP_RISK(frame, is_etf=is_etf)
                score_cache.clear_thread_score_cache()
                actual = endpoint.value_trap_risk(frame, is_etf=is_etf)
                self.assertAlmostEqual(actual, expected, places=10)

    def test_breakout_score_matches_stable(self) -> None:
        for frame in (_frame(), _frame(holes=True)):
            score_cache.clear_thread_score_cache()
            expected = endpoint._LEGACY_BREAKOUT_SCORE(frame)
            score_cache.clear_thread_score_cache()
            actual = endpoint.breakout_score(frame)
            self.assertAlmostEqual(actual, expected, places=10)

    def test_execution_quality_matches_stable(self) -> None:
        for frame in (_frame(), _frame(holes=True)):
            trap = endpoint._LEGACY_VALUE_TRAP_RISK(frame)
            breakout = endpoint._LEGACY_BREAKOUT_SCORE(frame)
            entry = score.entry_point(
                frame,
                breakout=breakout,
                value_trap_risk_value=trap,
                price_decimals=2,
            )
            score_cache.clear_thread_score_cache()
            expected = endpoint._LEGACY_EXECUTION_QUALITY_SCORE(frame, entry)
            score_cache.clear_thread_score_cache()
            actual = endpoint.execution_quality_score(frame, entry)
            self.assertAlmostEqual(actual, expected, places=10)

    def test_public_runtime_uses_endpoint_kernels(self) -> None:
        endpoint.install()
        self.assertIs(score_core.value_trap_risk, endpoint.value_trap_risk)
        self.assertIs(score_core.breakout_score, endpoint.breakout_score)
        self.assertIs(
            score_core.execution_quality_score,
            endpoint.execution_quality_score,
        )
        self.assertEqual(
            analytics.PERFORMANCE_ENGINE_VERSION,
            "2026-08-20-v80-vectorized-backtest-workstation-v1",
        )


class SharedVolatilityStateTests(unittest.TestCase):
    def setUp(self) -> None:
        score_cache.clear_thread_score_cache()
        score_cache.install()

    def tearDown(self) -> None:
        score_cache.clear_thread_score_cache()
        score_cache.install()

    def test_none_and_empty_frames_fail_soft(self) -> None:
        none_state = score_cache.evaluate_volatility_contraction(None)
        empty_state = score_cache.evaluate_volatility_contraction(pd.DataFrame())
        self.assertEqual(none_state.available_components, 0)
        self.assertEqual(empty_state.available_components, 0)

    def test_filter_resolves_volatility_function_dynamically(self) -> None:
        frame = _frame()
        state = volatility_state.VolatilityContractionState(
            atr_ratio=0.8,
            atr_contracting=True,
            available_components=1,
        )
        dynamic = Mock(return_value=state)
        with patch.object(
            volatility_state,
            "evaluate_volatility_contraction",
            dynamic,
        ):
            result = filters.filter_volatility_contraction(frame)
        self.assertTrue(result.passed)
        self.assertEqual(dynamic.call_count, 1)

    def test_filter_then_score_reuses_same_cached_state(self) -> None:
        frame = _frame()
        filters.filter_volatility_contraction(frame)
        cached_after_filter = score_cache.evaluate_volatility_contraction(frame)
        score.score_volatility(frame)
        cached_after_score = score_cache.evaluate_volatility_contraction(frame)
        self.assertIs(cached_after_filter, cached_after_score)


if __name__ == "__main__":
    unittest.main()
