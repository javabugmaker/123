from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import backtest_math_integrity_v94 as math_v94
import backtest_production_activation_v93 as pit_v93
import scoring_consistency_v94 as scoring_v94
from execution_integrity_v87 import smooth_breakout_price_component


class V94BacktestMathIntegrityTests(unittest.TestCase):
    def test_provisional_weight_survives_date_balancing(self) -> None:
        frame = pd.DataFrame(
            {
                "entry_date": ["2025-01-02"] * 4,
                "sample_weight": [0.25] * 4,
                "universe_evidence_weight": [0.25] * 4,
                "universe_snapshot_status": ["PROVISIONAL"] * 4,
            }
        )
        weights = math_v94.date_balanced_evidence_weights(frame)
        self.assertTrue(np.allclose(weights.to_numpy(), np.full(4, 0.0625)))
        self.assertAlmostEqual(float(weights.sum()), 0.25, places=8)

    def test_verified_date_keeps_one_unit_budget(self) -> None:
        frame = pd.DataFrame(
            {
                "entry_date": ["2025-01-02"] * 4,
                "sample_weight": [1.0] * 4,
                "universe_evidence_weight": [1.0] * 4,
                "universe_snapshot_status": ["ELIGIBLE"] * 4,
            }
        )
        weights = math_v94.date_balanced_evidence_weights(frame)
        self.assertAlmostEqual(float(weights.sum()), 1.0, places=8)

    def test_explicit_exclusion_never_becomes_provisional(self) -> None:
        frame = pd.DataFrame(
            {
                "universe_snapshot_status": ["INELIGIBLE", "UNAVAILABLE"],
                "universe_snapshot_reason": ["", "no_point_in_time_snapshot"],
                "sample_weight": [1.0, 1.0],
                "split": ["test", "test"],
            },
            index=[10, 20],
        )
        result = pit_v93._production_point_in_time_frame(frame)
        self.assertNotIn(10, result.index)
        self.assertIn(20, result.index)
        self.assertEqual(str(result.loc[20, "universe_snapshot_status"]), "PROVISIONAL")
        self.assertAlmostEqual(float(result.loc[20, "sample_weight"]), 0.25)
        self.assertAlmostEqual(float(result.loc[20, "universe_evidence_weight"]), 0.25)

    def test_peer_calibration_preserves_entry_signal_semantics(self) -> None:
        rows = [
            {"level": "asset_signal", "entry_signal": "BREAKOUT_CONFIRM", "confidence": 0.7},
            {"level": "signal", "entry_signal": "BUY_NOW", "confidence": 0.6},
            {"level": "asset", "asset_type": "stock", "confidence": 0.9},
            {"level": "global", "confidence": 1.0},
        ]
        filtered = math_v94.signal_semantic_calibration_rows(rows)
        self.assertEqual(len(filtered), 2)
        self.assertEqual(
            {str(row["entry_signal"]) for row in filtered},
            {"BREAKOUT_CONFIRM", "BUY_NOW"},
        )

    def test_fast_trigger_uses_same_smooth_breakout_price_math(self) -> None:
        dates = pd.date_range("2025-01-01", periods=40, freq="B")
        frame = pd.DataFrame(index=dates)
        frame["Close"] = 100.0
        frame["High"] = 100.0
        frame["Low"] = 99.0
        frame["Volume"] = 1_000_000.0
        frame["CMF"] = 0.0
        frame["AD_Slope"] = 0.0
        frame["OBV"] = 0.0
        frame["MA200"] = 100.0
        frame["VolMA20"] = 1_000_000.0
        frame["VolMA120"] = np.nan
        frame["VolZScore"] = 0.0
        frame["AD"] = 0.0
        frame["MFI"] = 50.0
        frame["ATR14"] = 1.0
        frame["ATR50"] = 1.0
        frame["BB_Width"] = 0.1
        frame["HV20"] = 0.2
        frame["HV60"] = 0.2

        frame.iloc[-1, frame.columns.get_loc("Close")] = 100.01
        raw, _adjusted = scoring_v94.canonical_trigger_score_matrix(frame)
        expected, _ = smooth_breakout_price_component(np.array([0.01]))
        self.assertAlmostEqual(float(raw[-1]), float(expected[0]), places=6)

        frame.iloc[-1, frame.columns.get_loc("Close")] = 99.99
        raw_below, _adjusted_below = scoring_v94.canonical_trigger_score_matrix(frame)
        self.assertLess(abs(float(raw[-1] - raw_below[-1])), 2.0)


if __name__ == "__main__":
    unittest.main()
