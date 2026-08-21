from __future__ import annotations

import unittest

import pandas as pd

import model_calibration


class ModelCalibrationCopyOnWriteTests(unittest.TestCase):
    def test_weighted_spearman_accepts_copy_on_write_rank_views(self) -> None:
        score = pd.Series([1.0, 2.0, 3.0, 4.0])
        increasing = pd.Series([10.0, 20.0, 30.0, 40.0])
        decreasing = pd.Series([40.0, 30.0, 20.0, 10.0])
        weights = pd.Series([1.0, 0.5, 1.0, 0.25])

        with pd.option_context("mode.copy_on_write", True):
            positive = model_calibration._spearman(score, increasing, weights)
            negative = model_calibration._spearman(score, decreasing, weights)

        self.assertAlmostEqual(positive, 1.0, places=12)
        self.assertAlmostEqual(negative, -1.0, places=12)


if __name__ == "__main__":
    unittest.main()
