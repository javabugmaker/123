from __future__ import annotations

import numpy as np
import pandas as pd

from technical_resonance_v90 import (
    RESONANCE_VERSION,
    attach_resonance_to_samples,
    compute_five_factor_resonance,
    summarize_resonance_samples,
)


def _trend_frame(direction: float, rows: int = 160) -> pd.DataFrame:
    index = pd.bdate_range("2025-01-02", periods=rows)
    x = np.arange(rows, dtype=float)
    # Add a small acceleration/wave so KDJ has meaningful K-vs-D variation
    # instead of a perfectly constant RSV in a linear trend.
    close = 20.0 + direction * (0.035 * x + 0.0008 * x * x) + 0.18 * np.sin(x / 4.0)
    high = close + 0.35
    low = close - 0.35
    open_ = close - direction * 0.05
    volume = 1_000_000.0 + np.maximum(direction, 0.0) * x * 5_000.0
    return pd.DataFrame(
        {
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        },
        index=index,
    )


def test_strong_uptrend_reaches_four_or_five_votes() -> None:
    frame = _trend_frame(1.0)
    resonance = compute_five_factor_resonance(frame)
    last = resonance.iloc[-1]
    assert last["ResonanceAvailable"] == 5
    assert 4 <= last["ResonanceCount"] <= 5
    assert bool(last["ResonanceStrongBull"])
    assert bool(last["ResonanceMACDBull"])
    assert bool(last["ResonanceRSIBull"])
    assert bool(last["ResonanceOBVBull"])
    assert bool(last["ResonanceBOLLBull"])


def test_strong_downtrend_stays_low_resonance() -> None:
    frame = _trend_frame(-1.0)
    resonance = compute_five_factor_resonance(frame)
    last = resonance.iloc[-1]
    assert last["ResonanceAvailable"] == 5
    assert last["ResonanceCount"] <= 1
    assert bool(last["ResonanceStrongBear"])


def test_sample_attachment_uses_signal_date_not_entry_date() -> None:
    frame = _trend_frame(-1.0, rows=120)
    signal_position = 90
    # A large reversal occurs *after* the signal. If attachment accidentally
    # reads entry/future bars, the saved count will differ from the signal bar.
    frame.iloc[signal_position + 1 :, frame.columns.get_loc("Close")] += 12.0
    frame.iloc[signal_position + 1 :, frame.columns.get_loc("High")] += 12.0
    frame.iloc[signal_position + 1 :, frame.columns.get_loc("Low")] += 12.0
    frame.iloc[signal_position + 1 :, frame.columns.get_loc("Open")] += 12.0

    expected = compute_five_factor_resonance(frame).iloc[signal_position]
    samples = [
        {
            "ticker": "000001.SZ",
            "signal_date": frame.index[signal_position].strftime("%Y-%m-%d"),
            "entry_date": frame.index[signal_position + 1].strftime("%Y-%m-%d"),
        }
    ]
    attached = attach_resonance_to_samples(samples, frame)
    assert attached[0]["resonance_version"] == RESONANCE_VERSION
    assert attached[0]["resonance_count"] == expected["ResonanceCount"]
    assert attached[0]["resonance_count"] != compute_five_factor_resonance(frame).iloc[
        signal_position + 1
    ]["ResonanceCount"]


def test_summary_keeps_resonance_as_diagnostic_groups() -> None:
    frame = pd.DataFrame(
        {
            "resonance_count": [1, 2, 4, 5],
            "resonance_delta_3d": [-1, 1, 1, 0],
            "sample_weight": [1.0, 1.0, 1.0, 1.0],
            "net_return20": [-4.0, 1.0, 5.0, 7.0],
            "net_return60": [-8.0, 2.0, 9.0, 11.0],
            "benchmark_return20": [0.0, 0.0, 0.0, 0.0],
            "benchmark_return60": [0.0, 0.0, 0.0, 0.0],
            "drawdown60": [-10.0, -6.0, -4.0, -3.0],
        }
    )
    analysis = summarize_resonance_samples(frame)
    assert analysis["status"] == "EXPERIMENTAL_DIAGNOSTIC_ONLY"
    bands = {row["group"]: row for row in analysis["by_band"]}
    assert bands["4-5/5"]["samples"] == 2
    assert bands["4-5/5"]["average_net_excess_20d"] == 6.0
    transitions = {row["group"]: row for row in analysis["by_transition"]}
    assert transitions["RISING_TO_4PLUS"]["samples"] == 1
