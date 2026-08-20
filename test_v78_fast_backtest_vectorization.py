from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import analytics
import analytics_core
import backtest_acceleration_v77
import backtest_fastpath_v78 as fastpath
import backtest_fastscore_v80 as fastscore
import cache_acceleration_v77
from indicators import compute_all_indicators


def _endpoint_frame(rows: int = 430) -> pd.DataFrame:
    rng = np.random.default_rng(20260820)
    returns = rng.normal(0.0004, 0.014, rows)
    close = 20.0 * np.cumprod(1.0 + returns)
    close_series = pd.Series(close)
    high = close * (1.0 + rng.uniform(0.002, 0.025, rows))
    low = close * (1.0 - rng.uniform(0.002, 0.025, rows))
    volume = rng.integers(500_000, 8_000_000, rows).astype(float)
    direction = np.sign(np.diff(close, prepend=close[0]))
    obv = np.cumsum(direction * volume)
    return pd.DataFrame(
        {
            "Close": close,
            "High": high,
            "Low": low,
            "Volume": volume,
            "MA20": close_series.rolling(20, min_periods=1).mean().to_numpy(),
            "MA50": close_series.rolling(50, min_periods=1).mean().to_numpy(),
            "MA200": close_series.rolling(200, min_periods=1).mean().to_numpy(),
            "ATR14": pd.Series(high - low).rolling(14, min_periods=1).mean().to_numpy(),
            "RSI14": np.clip(50.0 + rng.normal(0.0, 10.0, rows), 10.0, 90.0),
            "CMF": np.clip(rng.normal(0.02, 0.12, rows), -0.5, 0.5),
            "AD_Slope": rng.normal(0.0, 1.0, rows),
            "OBV": obv,
        },
        index=pd.bdate_range("2025-01-02", periods=rows),
    )


def _raw_frame(rows: int = 390) -> pd.DataFrame:
    rng = np.random.default_rng(78)
    returns = rng.normal(0.0005, 0.013, rows)
    close = 18.0 * np.cumprod(1.0 + returns)
    open_ = close * (1.0 + rng.normal(0.0, 0.004, rows))
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0.002, 0.018, rows))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0.002, 0.018, rows))
    volume = rng.integers(800_000, 9_000_000, rows).astype(float)
    return pd.DataFrame(
        {
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
            "Amount": volume * close,
        },
        index=pd.bdate_range("2024-12-02", periods=rows),
    )


class FastBacktestVectorizationTests(unittest.TestCase):
    def test_vectorized_quick_gate_has_no_legacy_false_negatives(self) -> None:
        frame = _endpoint_frame()
        profile = analytics_core._resolve_backtest_profile("fast", len(frame))
        for is_etf in (False, True):
            vectorized = fastpath._fast_quick_gate(frame, is_etf=is_etf)
            self.assertIsNotNone(vectorized)
            assert vectorized is not None
            gate, price_breakout = vectorized
            for index in range(251, len(frame) - 60, 3):
                historical = analytics_core._backtest_scoring_window(
                    frame,
                    index,
                    score_window=profile.score_window,
                    include_volume_profile=False,
                )
                breakout = analytics_core.breakout_score(historical)
                trap = analytics_core.value_trap_risk(historical)
                entry = analytics_core.entry_point(
                    historical,
                    breakout=breakout,
                    volume_score=None,
                    value_trap_risk_value=trap,
                    price_decimals=analytics_core.tradable_price_decimals(is_etf),
                )
                signal = str(entry.get("signal", "AVOID")).upper()
                expected_gate = (
                    signal in analytics_core._BACKTEST_ACTIONABLE_SIGNALS
                    or bool(entry.get("price_breakout", False))
                )
                if expected_gate:
                    self.assertTrue(bool(gate[index]), msg=f"index={index}")
                self.assertEqual(
                    bool(price_breakout[index]),
                    bool(entry.get("price_breakout", False)),
                    msg=f"index={index}",
                )

    def test_fast_signal_evaluations_match_legacy_results(self) -> None:
        enriched = compute_all_indicators(_raw_frame())
        profile = analytics_core._resolve_backtest_profile("fast", len(enriched))
        legacy_components: dict[int, tuple[float, float, float]] = {}
        fast_components: dict[int, tuple[float, float, float]] = {}
        expected = fastpath._LEGACY_SIGNAL_EVALUATIONS(
            enriched,
            is_etf=False,
            profile=profile,
            start_index=251,
            component_sink=legacy_components,
        )
        actual = fastpath._signal_evaluations(
            enriched,
            is_etf=False,
            profile=profile,
            start_index=251,
            component_sink=fast_components,
        )
        self.assertEqual(
            [(index, signal) for index, _score, signal in actual],
            [(index, signal) for index, _score, signal in expected],
        )
        self.assertEqual(
            [index for index, _score, _signal in actual], list(fast_components)
        )
        self.assertEqual(
            [index for index, _score, _signal in expected], list(legacy_components)
        )
        for (_index_a, score_a, _signal_a), (_index_b, score_b, _signal_b) in zip(
            actual, expected
        ):
            self.assertAlmostEqual(score_a, score_b, places=10)

    def test_real_analytics_runtime_keeps_v78_gate_but_v80_owns_fast_scoring(self) -> None:
        self.assertIs(analytics_core._signal_evaluations, fastscore._signal_evaluations)
        self.assertIs(
            analytics_core.market_cache_state,
            backtest_acceleration_v77.market_cache_state,
        )
        self.assertIs(
            backtest_acceleration_v77._LEGACY_MARKET_CACHE_STATE,
            cache_acceleration_v77.market_cache_state,
        )
        self.assertEqual(
            analytics.PERFORMANCE_ENGINE_VERSION,
            "2026-08-20-v80-vectorized-backtest-workstation-v1",
        )


if __name__ == "__main__":
    unittest.main()
