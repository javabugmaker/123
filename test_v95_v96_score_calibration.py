from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import analytics
import analytics_core
import backtest_profile_alignment_v95 as profile_v95
import calibration_math_v96 as calibration_v96
import conditional_fill_v96 as fill_v96
import score_core
import score_scale_migration_v95 as scale_v95
import score_threshold_migration_v95 as thresholds_v95


class V95ScoreScaleMigrationTests(unittest.TestCase):
    def test_nominal_component_maxima_are_reachable(self) -> None:
        self.assertAlmostEqual(
            scale_v95._scale_dimension(22.0, 22.0, 25.0), 25.0, places=8
        )
        self.assertAlmostEqual(
            scale_v95._scale_dimension(23.0, 23.0, 25.0), 25.0, places=8
        )

    def test_absolute_thresholds_preserve_attainable_score_space(self) -> None:
        self.assertAlmostEqual(
            thresholds_v95.SETUP_ACTIVE_THRESHOLD,
            35.0 * 100.0 / 95.0,
            places=4,
        )
        self.assertAlmostEqual(
            thresholds_v95.TIER_A,
            35.0 * 100.0 / 97.0,
            places=4,
        )
        self.assertAlmostEqual(
            thresholds_v95.TIER_C,
            25.0 * 100.0 / 97.0,
            places=4,
        )

    def test_hvn_is_diagnostic_not_structure_alpha(self) -> None:
        dates = pd.date_range("2024-01-01", periods=300, freq="B")
        close = pd.Series(np.linspace(10.0, 10.5, len(dates)), index=dates)
        base = pd.DataFrame(
            {
                "Close": close,
                "High": close + 0.1,
                "Low": close - 0.1,
                "Low52W": 9.0,
                "DistToLow52W": 10.0,
                "RegSlope": 0.0,
                "RegR2": 0.8,
                "Above_HVN": False,
                "DistToHVN_Pct": np.nan,
            },
            index=dates,
        )
        with_hvn = base.copy()
        with_hvn["Above_HVN"] = True
        with_hvn["DistToHVN_Pct"] = 1.0
        self.assertAlmostEqual(
            score_core.score_structure(base),
            score_core.score_structure(with_hvn),
            places=8,
        )

    def test_fast_and_exact_use_same_504_bar_scoring_context(self) -> None:
        profile_v95.install()
        fast = analytics_core._resolve_backtest_profile("fast", 1000)
        exact = analytics_core._resolve_backtest_profile("exact", 10)
        self.assertEqual(fast.score_window, 504)
        self.assertEqual(exact.score_window, 504)
        self.assertFalse(fast.historical_volume_profile)
        self.assertFalse(exact.historical_volume_profile)


class V96CalibrationMathTests(unittest.TestCase):
    def test_fixed_objective_score_is_run_universe_invariant(self) -> None:
        singleton = calibration_v96.fixed_objective_score(
            pd.Series([5.0]), "net_excess_return_20d"
        ).iloc[0]
        expanded = calibration_v96.fixed_objective_score(
            pd.Series([-20.0, 5.0, 40.0, 1.0]), "net_excess_return_20d"
        ).iloc[1]
        self.assertAlmostEqual(float(singleton), float(expanded), places=12)

    def test_fixed_objective_bounds_are_monotone(self) -> None:
        scores = calibration_v96.fixed_objective_score(
            pd.Series([-50.0, -15.0, 0.0, 15.0, 50.0]),
            "net_excess_return_20d",
        )
        self.assertTrue(np.all(np.diff(scores.to_numpy(dtype=float)) >= 0.0))
        self.assertAlmostEqual(float(scores.iloc[0]), 0.0)
        self.assertAlmostEqual(float(scores.iloc[2]), 50.0)
        self.assertAlmostEqual(float(scores.iloc[-1]), 100.0)

    def test_stability_confidence_is_not_squared(self) -> None:
        rows = [
            {"rank_ic": 0.10, "top_bottom_spread20": 1.0},
            {"rank_ic": 0.08, "top_bottom_spread20": 0.5},
            {"rank_ic": -0.03, "top_bottom_spread20": -0.2},
        ]
        stats = calibration_v96.single_shrink_stability_stats(rows)
        self.assertAlmostEqual(float(stats["stable_fold_ratio"]), 2.0 / 3.0, places=4)
        self.assertAlmostEqual(float(stats["confidence_multiplier"]), 2.0 / 3.0, places=4)
        self.assertNotAlmostEqual(
            float(stats["confidence_multiplier"]), (2.0 / 3.0) ** 2, places=3
        )

    def test_public_apply_backtest_ranking_facade_identity_survives_v96(self) -> None:
        self.assertEqual(analytics.apply_backtest_ranking.__module__, "analytics")


class V96ConditionalFillTests(unittest.TestCase):
    @staticmethod
    def _frame() -> pd.DataFrame:
        dates = pd.date_range("2025-01-02", periods=10, freq="B")
        return pd.DataFrame(
            {
                "Open": [110.0] * 10,
                "High": [111.0] * 10,
                "Low": [109.0] * 10,
                "Close": [110.0] * 10,
                "Volume": [1_000_000.0] * 10,
            },
            index=dates,
        )

    def test_wait_pullback_fills_only_when_future_bar_touches_zone(self) -> None:
        frame = self._frame()
        frame.iloc[2, frame.columns.get_loc("Low")] = 101.0
        with patch.object(analytics_core, "is_entry_tradeable", return_value=(True, "")):
            fill = fill_v96._conditional_fill(
                "000001.SZ",
                frame,
                0,
                100.0,
                102.0,
                is_etf=False,
            )
        self.assertIsNotNone(fill)
        assert fill is not None
        index, price, delay, basis = fill
        self.assertEqual(index, 2)
        self.assertEqual(delay, 2)
        self.assertAlmostEqual(price, 102.0)
        self.assertEqual(basis, "LIMIT_AT_ZONE_HIGH")

    def test_wait_pullback_without_touch_is_no_trade(self) -> None:
        frame = self._frame()
        with patch.object(analytics_core, "is_entry_tradeable", return_value=(True, "")):
            fill = fill_v96._conditional_fill(
                "000001.SZ", frame, 0, 100.0, 102.0, is_etf=False
            )
        self.assertIsNone(fill)

    def test_gap_below_zone_invalidates_instead_of_creating_favorable_fill(self) -> None:
        frame = self._frame()
        frame.iloc[1, frame.columns.get_loc("Open")] = 98.0
        frame.iloc[1, frame.columns.get_loc("Low")] = 97.0
        frame.iloc[1, frame.columns.get_loc("High")] = 103.0
        with patch.object(analytics_core, "is_entry_tradeable", return_value=(True, "")):
            fill = fill_v96._conditional_fill(
                "000001.SZ", frame, 0, 100.0, 102.0, is_etf=False
            )
        self.assertIsNone(fill)

    def test_wait_pullback_remains_outside_immediate_next_open_signal_set(self) -> None:
        self.assertNotIn("WAIT_PULLBACK", analytics_core._BACKTEST_ACTIONABLE_SIGNALS)


if __name__ == "__main__":
    unittest.main()
