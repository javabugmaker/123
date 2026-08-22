from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import analytics
import score


class ContinuousTriggerSemanticsTests(unittest.TestCase):
    @staticmethod
    def _frame(clearance_pct: float) -> pd.DataFrame:
        resistance = 100.0
        close = np.full(21, 95.0, dtype=float)
        close[-1] = resistance * (1.0 + clearance_pct / 100.0)
        return pd.DataFrame(
            {
                "Close": close,
                "High": np.full(21, resistance, dtype=float),
                "Volume": np.full(21, 1_000_000.0, dtype=float),
            }
        )

    def test_trigger_is_continuous_across_resistance(self) -> None:
        just_below = score.trigger_event_score(self._frame(-0.01))
        just_above = score.trigger_event_score(self._frame(0.01))

        self.assertGreater(just_above, just_below)
        self.assertLess(
            just_above - just_below,
            2.0,
            "A few basis points around resistance must not create a score cliff",
        )

    def test_trigger_price_component_remains_monotone(self) -> None:
        clearances = (-1.5, -0.5, -0.01, 0.0, 0.01, 0.5, 1.5, 3.0)
        scores = [
            score.trigger_event_score(self._frame(clearance))
            for clearance in clearances
        ]
        self.assertTrue(
            all(right >= left for left, right in zip(scores, scores[1:])),
            scores,
        )


class ExecutableBacktestSignalTests(unittest.TestCase):
    def test_pending_pullback_does_not_create_next_open_sample(self) -> None:
        signals = analytics._BACKTEST_ACTIONABLE_SIGNALS
        self.assertEqual(signals, frozenset({"BUY_NOW", "BREAKOUT_CONFIRM"}))
        self.assertNotIn("WAIT_PULLBACK", signals)

    def test_immediate_entry_signals_remain_backtestable(self) -> None:
        signals = analytics._BACKTEST_ACTIONABLE_SIGNALS
        self.assertIn("BUY_NOW", signals)
        self.assertIn("BREAKOUT_CONFIRM", signals)


if __name__ == "__main__":
    unittest.main()
