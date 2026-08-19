from __future__ import annotations

import unittest

import pandas as pd

import analytics
from evidence import enrich_evidence_fields


class CalibrationGovernanceTests(unittest.TestCase):
    def test_unstable_walk_forward_confidence_is_shrunk_twice(self) -> None:
        rows = [
            {"rank_ic": 0.10, "top_bottom_spread20": 1.0},
            {"rank_ic": 0.08, "top_bottom_spread20": 0.8},
            {"rank_ic": -0.03, "top_bottom_spread20": 0.4},
            {"rank_ic": -0.02, "top_bottom_spread20": -0.2},
        ]
        result = analytics.calibration_stability_stats(rows, minimum_folds=3)

        self.assertEqual(result["status"], "UNSTABLE")
        self.assertAlmostEqual(float(result["stable_fold_ratio"]), 0.5, places=4)
        self.assertAlmostEqual(float(result["raw_confidence_multiplier"]), 0.5, places=4)
        self.assertAlmostEqual(float(result["confidence_multiplier"]), 0.25, places=4)
        self.assertEqual(
            result["confidence_governance"],
            "unstable-stable-ratio-shrink-v1",
        )

    def test_stable_walk_forward_keeps_legacy_multiplier(self) -> None:
        rows = [
            {"rank_ic": 0.10, "top_bottom_spread20": 1.0},
            {"rank_ic": 0.08, "top_bottom_spread20": 0.8},
            {"rank_ic": 0.05, "top_bottom_spread20": 0.4},
            {"rank_ic": -0.02, "top_bottom_spread20": -0.2},
        ]
        result = analytics.calibration_stability_stats(rows, minimum_folds=3)

        self.assertEqual(result["status"], "STABLE")
        self.assertAlmostEqual(float(result["stable_fold_ratio"]), 0.75, places=4)
        self.assertAlmostEqual(float(result["confidence_multiplier"]), 0.75, places=4)
        self.assertEqual(result["confidence_governance"], "legacy-v1")

    def test_insufficient_folds_keep_legacy_neutral_treatment(self) -> None:
        rows = [
            {"rank_ic": 0.10, "top_bottom_spread20": 1.0},
            {"rank_ic": -0.02, "top_bottom_spread20": -0.2},
        ]
        result = analytics.calibration_stability_stats(rows, minimum_folds=3)

        self.assertEqual(result["status"], "INSUFFICIENT_FOLDS")
        self.assertAlmostEqual(float(result["confidence_multiplier"]), 1.0, places=4)

    def test_evidence_text_exposes_stability_and_survivorship_warning(self) -> None:
        frame = pd.DataFrame(
            {
                "BacktestSamples": [2],
                "BacktestEffectiveSamples": [1.5],
                "BacktestMode": ["FAST"],
                "BacktestConfidenceTier": ["样本不足"],
                "GlobalCalibrationSamples": [100],
                "GlobalCalibrationEffectiveSamples": [80.0],
                "GlobalCalibrationConfidence": [0.25],
                "GlobalCalibrationLevel": ["asset_signal"],
                "GlobalCalibrationStability": ["UNSTABLE"],
                "GlobalCalibrationStableFoldRatio": [0.5],
                "SurvivorshipBiasWarning": [True],
            }
        )

        result = enrich_evidence_fields(frame)
        peer = str(result.loc[0, "PeerCalibrationEvidence"])
        reason = str(result.loc[0, "EvidenceReason"])

        self.assertIn("UNSTABLE(50%)", peer)
        self.assertIn("幸存者偏差提示", peer)
        self.assertIn("稳定折占比 50%", reason)
        self.assertIn("幸存者偏差", reason)


if __name__ == "__main__":
    unittest.main()
