"""v77 vectorised indicator kernels.

These replacements preserve the public indicator formulas while removing the
largest Python/pandas overheads in the daily scan and exact backtest:

* True Range uses NumPy maximums instead of a temporary 3-column DataFrame;
* Wilder smoothing uses SciPy's compiled one-pole filter per finite segment;
* rolling linear-regression slope/R² share NumPy prefix sums instead of many
  pandas rolling objects;
* Volume Profile uses a difference-array range add instead of allocating a
  rows-by-bins boolean matrix for every ticker/candidate.
"""

from __future__ import annotations

import sys
from typing import Any

import numpy as np
import pandas as pd
from scipy.signal import lfilter

import indicators as _ind

_INSTALLED = False


def true_range(df: pd.DataFrame) -> pd.Series:
    high = pd.to_numeric(df["High"], errors="coerce").to_numpy(dtype=np.float64)
    low = pd.to_numeric(df["Low"], errors="coerce").to_numpy(dtype=np.float64)
    close = pd.to_numeric(df["Close"], errors="coerce").to_numpy(dtype=np.float64)
    previous = np.empty_like(close)
    if previous.size:
        previous[0] = np.nan
        previous[1:] = close[:-1]
    valid = np.isfinite(high) & np.isfinite(low) & np.isfinite(previous)
    result = np.maximum(
        np.maximum(high - low, np.abs(high - previous)),
        np.abs(low - previous),
    )
    result[~valid] = np.nan
    return pd.Series(result, index=df.index, dtype=np.float64)


def wilder_average(series: pd.Series, period: int) -> pd.Series:
    """Exact Wilder recurrence with compiled filtering and NaN-gap reset semantics."""
    if period <= 0:
        raise ValueError("period must be positive")
    values = (
        pd.to_numeric(series, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .to_numpy(dtype=np.float64)
    )
    output = np.full(values.shape, np.nan, dtype=np.float64)
    if values.size == 0:
        return pd.Series(output, index=series.index, dtype=np.float64)

    finite = np.isfinite(values)
    padded = np.concatenate(([False], finite, [False]))
    transitions = np.flatnonzero(padded[1:] != padded[:-1])
    alpha = 1.0 / float(period)
    beta = 1.0 - alpha

    for start, end in transitions.reshape(-1, 2):
        length = int(end - start)
        if length < period:
            continue
        seed_position = int(start + period - 1)
        seed = float(values[start : start + period].mean())
        output[seed_position] = seed
        tail = values[seed_position + 1 : end]
        if tail.size:
            filtered, _ = lfilter(
                [alpha],
                [1.0, -beta],
                tail,
                zi=np.asarray([beta * seed], dtype=np.float64),
            )
            output[seed_position + 1 : end] = filtered

    return pd.Series(output, index=series.index, dtype=np.float64)


def _window_sums(values: np.ndarray, window: int) -> tuple[np.ndarray, ...]:
    if window <= 0:
        raise ValueError("window must be positive")
    count = len(values)
    positions = np.arange(count, dtype=np.float64)
    valid = ~np.isnan(values)
    weights = valid.astype(np.float64)
    clean = np.where(valid, values, 0.0)

    def rolling_sum(array: np.ndarray) -> np.ndarray:
        prefix = np.empty(count + 1, dtype=np.float64)
        prefix[0] = 0.0
        np.cumsum(array, out=prefix[1:])
        ends = np.arange(1, count + 1, dtype=np.int64)
        starts = np.maximum(0, ends - int(window))
        return prefix[ends] - prefix[starts]

    sum_count = rolling_sum(weights)
    sum_x = rolling_sum(positions * weights)
    sum_y = rolling_sum(clean)
    sum_xx = rolling_sum(positions * positions * weights)
    sum_yy = rolling_sum(clean * clean)
    sum_xy = rolling_sum(positions * clean)
    return sum_count, sum_x, sum_y, sum_xx, sum_yy, sum_xy


def _rolling_regression(series: pd.Series, window: int) -> tuple[pd.Series, pd.Series]:
    numeric = pd.to_numeric(series, errors="coerce")
    values = numeric.to_numpy(dtype=np.float64)
    if values.size == 0:
        empty = pd.Series(np.nan, index=numeric.index, dtype=np.float64)
        return empty.copy(), empty

    count, sum_x, sum_y, sum_xx, sum_yy, sum_xy = _window_sums(values, window)
    minimum = max(2, int(window) // 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        denominator = sum_xx - (sum_x * sum_x) / count
        numerator = sum_xy - (sum_x * sum_y) / count
        slope = numerator / denominator
        covariance = count * sum_xy - sum_x * sum_y
        variance_x = count * sum_xx - sum_x * sum_x
        variance_y = count * sum_yy - sum_y * sum_y
        r2 = (covariance * covariance) / (variance_x * variance_y)

    valid_slope = (count >= minimum) & np.isfinite(denominator) & (denominator != 0.0)
    slope = np.where(valid_slope, slope, np.nan)
    valid_r2 = (
        (count >= minimum)
        & np.isfinite(variance_x)
        & np.isfinite(variance_y)
        & (variance_x != 0.0)
        & (variance_y != 0.0)
    )
    r2 = np.where(valid_r2, np.clip(r2, 0.0, 1.0), np.nan)
    return (
        pd.Series(slope, index=numeric.index, dtype=np.float64),
        pd.Series(r2, index=numeric.index, dtype=np.float64),
    )


def _rolling_slope(series: pd.Series, window: int) -> pd.Series:
    slope, _ = _rolling_regression(series, window)
    return slope


def _rolling_r2(series: pd.Series, window: int) -> pd.Series:
    _, r2 = _rolling_regression(series, window)
    return r2


def compute_regression(df: pd.DataFrame, period: int = _ind.REGRESSION_PERIOD) -> None:
    slope, r2 = _rolling_regression(df["Close"], period)
    df["RegSlope"] = slope
    df["RegR2"] = r2


def compute_volume_profile(
    df: pd.DataFrame,
    bins: int = _ind.VOLUME_PROFILE_BINS,
    lookback: int = _ind.VOLUME_PROFILE_LOOKBACK,
) -> None:
    if df is None or df.empty or bins <= 0:
        return
    lookback = min(int(lookback), len(df))
    subset = df.iloc[-lookback:]
    close = pd.to_numeric(subset["Close"], errors="coerce")
    high = pd.to_numeric(subset["High"], errors="coerce")
    low = pd.to_numeric(subset["Low"], errors="coerce")
    volume = pd.to_numeric(subset["Volume"], errors="coerce")
    price_min = float(low.min())
    price_max = float(high.max())
    if not np.isfinite(price_min) or not np.isfinite(price_max):
        return
    if price_min == price_max:
        df["VP_HVN_Center"] = price_min
        df["DistToHVN_Pct"] = 0.0
        return

    bin_edges = np.linspace(price_min, price_max, int(bins) + 1)
    bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2.0
    low_values = low.to_numpy(dtype=np.float64)
    high_values = high.to_numpy(dtype=np.float64)
    volume_values = volume.to_numpy(dtype=np.float64)
    valid = (
        np.isfinite(low_values)
        & np.isfinite(high_values)
        & np.isfinite(volume_values)
        & (low_values < high_values)
    )
    if not valid.any():
        return

    lo_indices = np.clip(np.digitize(low_values, bin_edges) - 1, 0, bins - 1)
    hi_indices = np.clip(np.digitize(high_values, bin_edges) - 1, 0, bins - 1)
    hi_indices = np.where(
        hi_indices <= lo_indices,
        np.minimum(lo_indices + 1, bins - 1),
        hi_indices,
    )
    widths = hi_indices - lo_indices + 1
    weights = np.divide(
        volume_values,
        widths,
        out=np.zeros_like(volume_values),
        where=valid,
    )

    difference = np.zeros(int(bins) + 1, dtype=np.float64)
    np.add.at(difference, lo_indices[valid], weights[valid])
    np.add.at(difference, hi_indices[valid] + 1, -weights[valid])
    profile = np.cumsum(difference[:-1])
    if not np.isfinite(profile).all() or float(profile.sum()) == 0.0:
        return

    positive = profile > 0
    positive_values = profile[positive]
    threshold_hvn = float(np.percentile(positive_values, 67)) if positive.any() else 0.0
    threshold_lvn = float(np.percentile(positive_values, 33)) if positive.any() else 0.0
    hvn_mask = profile >= threshold_hvn
    lvn_mask = positive & (profile <= threshold_lvn)
    current_price = float(close.iloc[-1])

    if hvn_mask.any():
        weighted_hvn = float(np.average(bin_centers[hvn_mask], weights=profile[hvn_mask]))
        df["VP_HVN_Center"] = weighted_hvn
        df["DistToHVN_Pct"] = ((current_price - weighted_hvn) / weighted_hvn) * 100.0
        df["Above_HVN"] = current_price > weighted_hvn
    else:
        df["VP_HVN_Center"] = np.nan
        df["DistToHVN_Pct"] = np.nan
        df["Above_HVN"] = np.nan

    if lvn_mask.any():
        weighted_lvn = float(np.average(bin_centers[lvn_mask], weights=profile[lvn_mask]))
        df["VP_LVN_Center"] = weighted_lvn
        df["DistToLVN_Pct"] = ((current_price - weighted_lvn) / weighted_lvn) * 100.0
    else:
        df["VP_LVN_Center"] = np.nan
        df["DistToLVN_Pct"] = np.nan


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _ind.true_range = true_range
    _ind.wilder_average = wilder_average
    _ind._rolling_slope = _rolling_slope
    _ind._rolling_r2 = _rolling_r2
    _ind.compute_regression = compute_regression
    _ind.compute_volume_profile = compute_volume_profile

    analytics_core = sys.modules.get("analytics_core")
    if analytics_core is not None:
        setattr(analytics_core, "compute_volume_profile", compute_volume_profile)

    scanner_module = sys.modules.get("scanner")
    if scanner_module is not None:
        setattr(scanner_module, "true_range", true_range)
        setattr(scanner_module, "wilder_average", wilder_average)
    _INSTALLED = True


install()


def acceleration_status() -> dict[str, Any]:
    return {
        "installed": bool(_INSTALLED),
        "true_range": _ind.true_range is true_range,
        "wilder": _ind.wilder_average is wilder_average,
        "rolling_regression": _ind.compute_regression is compute_regression,
        "volume_profile": _ind.compute_volume_profile is compute_volume_profile,
    }
