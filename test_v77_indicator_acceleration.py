from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import indicator_acceleration_v77 as accelerated
import indicators
import workstation_runtime_v77 as runtime


def _legacy_wilder(series: pd.Series, period: int) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    output = np.full(len(values), np.nan, dtype=np.float64)
    seed_sum = 0.0
    seed_count = 0
    previous = np.nan
    for position, value in enumerate(values.to_numpy(dtype=np.float64)):
        if not np.isfinite(value):
            seed_sum = 0.0
            seed_count = 0
            previous = np.nan
            continue
        if not np.isfinite(previous):
            seed_sum += float(value)
            seed_count += 1
            if seed_count < period:
                continue
            previous = seed_sum / period
        else:
            previous = (previous * (period - 1) + float(value)) / period
        output[position] = previous
    return pd.Series(output, index=values.index, dtype=np.float64)


def _legacy_rolling(series: pd.Series, window: int) -> tuple[pd.Series, pd.Series]:
    values = pd.to_numeric(series, errors="coerce")
    positions = pd.Series(np.arange(len(values), dtype=np.float64), index=values.index)
    valid = values.notna().astype(float)
    count = valid.rolling(window, min_periods=2).sum()
    sum_x = positions.where(valid.astype(bool), 0.0).rolling(window, min_periods=2).sum()
    sum_y = values.fillna(0.0).rolling(window, min_periods=2).sum()
    sum_xx = (
        (positions * positions)
        .where(valid.astype(bool), 0.0)
        .rolling(window, min_periods=2)
        .sum()
    )
    sum_yy = (values * values).fillna(0.0).rolling(window, min_periods=2).sum()
    sum_xy = (
        (positions * values.fillna(0.0))
        .where(valid.astype(bool), 0.0)
        .rolling(window, min_periods=2)
        .sum()
    )
    denominator = sum_xx - sum_x * sum_x / count.replace(0, np.nan)
    slope = (sum_xy - sum_x * sum_y / count.replace(0, np.nan)) / denominator.replace(
        0, np.nan
    )
    slope = slope.where(count >= max(2, window // 2))
    covariance = count * sum_xy - sum_x * sum_y
    variance_x = count * sum_xx - sum_x * sum_x
    variance_y = count * sum_yy - sum_y * sum_y
    r2 = covariance.pow(2) / (variance_x * variance_y).replace(0, np.nan)
    r2 = r2.where(count >= max(2, window // 2)).clip(0.0, 1.0)
    return slope, r2


def _legacy_volume_profile(
    df: pd.DataFrame, bins: int = 50, lookback: int = 252
) -> pd.DataFrame:
    result = df.copy()
    subset = result.iloc[-min(lookback, len(result)) :]
    close, high, low, volume = (
        subset["Close"],
        subset["High"],
        subset["Low"],
        subset["Volume"],
    )
    price_min, price_max = low.min(), high.max()
    if price_min == price_max:
        result["VP_HVN_Center"] = price_min
        result["DistToHVN_Pct"] = 0.0
        return result
    edges = np.linspace(price_min, price_max, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    low_values = low.to_numpy(dtype=float)
    high_values = high.to_numpy(dtype=float)
    volume_values = volume.to_numpy(dtype=float)
    valid = low_values < high_values
    lo_indices = np.clip(np.digitize(low_values, edges) - 1, 0, bins - 1)
    hi_indices = np.clip(np.digitize(high_values, edges) - 1, 0, bins - 1)
    hi_indices = np.where(
        hi_indices <= lo_indices,
        np.minimum(lo_indices + 1, bins - 1),
        hi_indices,
    )
    bin_numbers = np.arange(bins)
    included = (
        valid[:, np.newaxis]
        & (bin_numbers >= lo_indices[:, np.newaxis])
        & (bin_numbers <= hi_indices[:, np.newaxis])
    )
    weights = np.divide(
        volume_values,
        hi_indices - lo_indices + 1,
        out=np.zeros_like(volume_values),
        where=valid,
    )
    profile = (included * weights[:, np.newaxis]).sum(axis=0)
    threshold_hvn = np.percentile(profile[profile > 0], 67)
    threshold_lvn = np.percentile(profile[profile > 0], 33)
    hvn_mask = profile >= threshold_hvn
    lvn_mask = (profile > 0) & (profile <= threshold_lvn)
    current_price = close.iloc[-1]
    weighted_hvn = np.average(centers[hvn_mask], weights=profile[hvn_mask])
    result["VP_HVN_Center"] = weighted_hvn
    result["DistToHVN_Pct"] = ((current_price - weighted_hvn) / weighted_hvn) * 100
    result["Above_HVN"] = current_price > weighted_hvn
    weighted_lvn = np.average(centers[lvn_mask], weights=profile[lvn_mask])
    result["VP_LVN_Center"] = weighted_lvn
    result["DistToLVN_Pct"] = ((current_price - weighted_lvn) / weighted_lvn) * 100
    return result


class IndicatorAccelerationTests(unittest.TestCase):
    def test_wilder_matches_legacy_with_gap_resets(self) -> None:
        rng = np.random.default_rng(20260820)
        values = rng.normal(size=1500)
        values[100:104] = np.nan
        values[820] = np.nan
        series = pd.Series(values)
        for period in (14, 50):
            expected = _legacy_wilder(series, period)
            actual = accelerated.wilder_average(series, period)
            np.testing.assert_allclose(
                actual.to_numpy(),
                expected.to_numpy(),
                rtol=1e-12,
                atol=1e-12,
                equal_nan=True,
            )

    def test_rolling_regression_matches_legacy(self) -> None:
        rng = np.random.default_rng(77)
        values = rng.normal(size=1200)
        values[[15, 16, 310, 811]] = np.nan
        series = pd.Series(values)
        for window in (20, 60):
            expected_slope, expected_r2 = _legacy_rolling(series, window)
            actual_slope, actual_r2 = accelerated._rolling_regression(series, window)
            np.testing.assert_allclose(
                actual_slope.to_numpy(),
                expected_slope.to_numpy(),
                rtol=1e-9,
                atol=1e-10,
                equal_nan=True,
            )
            np.testing.assert_allclose(
                actual_r2.to_numpy(),
                expected_r2.to_numpy(),
                rtol=1e-8,
                atol=1e-10,
                equal_nan=True,
            )

    def test_volume_profile_range_add_matches_legacy_matrix(self) -> None:
        rng = np.random.default_rng(19)
        close = 20.0 + np.cumsum(rng.normal(0.0, 0.2, 252))
        frame = pd.DataFrame(
            {
                "Close": close,
                "High": close + rng.uniform(0.1, 0.8, 252),
                "Low": close - rng.uniform(0.1, 0.8, 252),
                "Volume": rng.integers(100_000, 10_000_000, 252).astype(float),
            },
            index=pd.bdate_range("2025-08-01", periods=252),
        )
        expected = _legacy_volume_profile(frame)
        actual = frame.copy()
        accelerated.compute_volume_profile(actual)
        for column in (
            "VP_HVN_Center",
            "DistToHVN_Pct",
            "VP_LVN_Center",
            "DistToLVN_Pct",
        ):
            self.assertAlmostEqual(
                float(actual[column].iloc[-1]),
                float(expected[column].iloc[-1]),
                places=10,
            )
        self.assertEqual(
            bool(actual["Above_HVN"].iloc[-1]),
            bool(expected["Above_HVN"].iloc[-1]),
        )

    def test_public_indicator_module_is_patched(self) -> None:
        accelerated.install()
        self.assertIs(indicators.wilder_average, accelerated.wilder_average)
        self.assertIs(indicators.compute_regression, accelerated.compute_regression)
        self.assertIs(indicators.compute_volume_profile, accelerated.compute_volume_profile)

    def test_6c12t_runtime_defaults_avoid_oversubscription(self) -> None:
        with patch.object(runtime.os, "cpu_count", return_value=12), patch.dict(
            runtime.os.environ,
            {},
            clear=True,
        ):
            profile = runtime.runtime_profile()
        self.assertEqual(profile.estimated_physical_cores, 6)
        self.assertEqual(profile.scan_threads, 12)
        self.assertEqual(profile.backtest_processes, 6)
        self.assertEqual(profile.backtest_chunk_size, 6)
        self.assertEqual(profile.backtest_fast_chunk_size, 24)
        self.assertEqual(profile.backtest_incremental_tail_bars, 360)


if __name__ == "__main__":
    unittest.main()
