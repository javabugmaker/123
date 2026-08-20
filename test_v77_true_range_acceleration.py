from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import indicator_acceleration_v77 as accelerated


class TrueRangeAccelerationTests(unittest.TestCase):
    def test_true_range_matches_legacy_dataframe_formula(self) -> None:
        rng = np.random.default_rng(20260820)
        close = 20.0 + np.cumsum(rng.normal(0.0, 0.2, 800))
        frame = pd.DataFrame(
            {
                "High": close + rng.uniform(0.05, 0.8, 800),
                "Low": close - rng.uniform(0.05, 0.8, 800),
                "Close": close,
            }
        )
        frame.loc[333, "High"] = np.nan
        high = pd.to_numeric(frame["High"], errors="coerce")
        low = pd.to_numeric(frame["Low"], errors="coerce")
        previous = pd.to_numeric(frame["Close"], errors="coerce").shift(1)
        expected = pd.concat(
            [high - low, (high - previous).abs(), (low - previous).abs()],
            axis=1,
        ).max(axis=1, skipna=False)
        expected = expected.where(high.notna() & low.notna() & previous.notna())
        expected = expected.replace([np.inf, -np.inf], np.nan)

        actual = accelerated.true_range(frame)
        np.testing.assert_allclose(
            actual.to_numpy(),
            expected.to_numpy(),
            rtol=0.0,
            atol=0.0,
            equal_nan=True,
        )


if __name__ == "__main__":
    unittest.main()
