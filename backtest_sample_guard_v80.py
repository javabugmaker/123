"""Numerical edge guard for the v80 sample-execution fast path.

The vectorised drawdown helper must preserve the stable engine's NaN semantics
when a delayed exit window contains an invalid low/close value. Normal TickFlow
data are finite, but old/corrupt caches must never be silently converted to a
zero drawdown merely because the optimized reducer encountered NaN.
"""

from __future__ import annotations

import numpy as np

import backtest_sample_acceleration_v80 as _sample

_INSTALLED = False


def drawdown_percent(
    entry_price: float,
    closes: np.ndarray,
    lows: np.ndarray,
    start: int,
    end: int,
) -> float:
    close_slice = closes[start : end + 1]
    low_slice = lows[start : end + 1]
    if close_slice.size == 0:
        return 0.0
    running_peak = np.maximum.accumulate(close_slice)
    running_peak = np.maximum(running_peak, float(entry_price))
    ratios = low_slice / running_peak - 1.0
    minimum = float(np.min(ratios)) if ratios.size else 0.0
    if not np.isfinite(minimum):
        return np.nan
    return float(min(0.0, minimum) * 100.0)


def install() -> None:
    global _INSTALLED
    _sample._drawdown_percent = drawdown_percent
    _INSTALLED = True


install()
