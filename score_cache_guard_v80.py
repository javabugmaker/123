"""v80 mutation guard for v79 thread-local endpoint caches.

v79 intentionally keys normalized Series/entry caches by DataFrame identity.
That is correct for the immutable scanner/backtest frames, but an in-place edit
keeps the same object id and could therefore reuse stale endpoint inputs.  The
entry decision only depends on a small recent window, so use a lightweight raw
endpoint signature and clear the thread cache only when those inputs actually
change.  Unchanged frames keep the v79 hit path.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import score_acceleration_v79 as _v79
import score_core as _score

_ORIGINAL_ENTRY_POINT = _v79.entry_point
_INSTALLED = False


def _finite_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return np.nan
    return number if np.isfinite(number) else np.nan


def _endpoint_signature(df: pd.DataFrame) -> tuple[object, ...]:
    if df is None or df.empty:
        return (0,)

    def latest(column: str) -> float:
        if column not in df.columns:
            return np.nan
        return _finite_float(df[column].iloc[-1])

    def recent_max(column: str, start: int, stop: int | None = None) -> float:
        if column not in df.columns:
            return np.nan
        values = pd.to_numeric(df[column].iloc[start:stop], errors="coerce").to_numpy(
            dtype=np.float64
        )
        finite = values[np.isfinite(values)]
        return float(np.max(finite)) if finite.size else np.nan

    def recent_min(column: str, start: int, stop: int | None = None) -> float:
        if column not in df.columns:
            return np.nan
        values = pd.to_numeric(df[column].iloc[start:stop], errors="coerce").to_numpy(
            dtype=np.float64
        )
        finite = values[np.isfinite(values)]
        return float(np.min(finite)) if finite.size else np.nan

    def recent_mean(column: str, start: int, stop: int | None = None) -> float:
        if column not in df.columns:
            return np.nan
        values = pd.to_numeric(df[column].iloc[start:stop], errors="coerce").to_numpy(
            dtype=np.float64
        )
        finite = values[np.isfinite(values)]
        return float(np.mean(finite)) if finite.size else np.nan

    last_index = df.index[-1]
    return (
        len(df),
        last_index,
        latest("Close"),
        latest("Volume"),
        latest("ATR14"),
        latest("RSI14"),
        latest("MA20"),
        latest("MA50"),
        recent_max("High", -21, -1),
        recent_min("Low", -20, None),
        recent_mean("Volume", -21, -1),
    )


def entry_point(
    df: pd.DataFrame,
    breakout: float | None = None,
    volume_score: float | None = None,
    value_trap_risk_value: float | None = None,
    price_decimals: int | None = None,
) -> dict[str, Any]:
    signature = _endpoint_signature(df)
    state = _v79._state(df)
    previous = getattr(state, "v80_endpoint_signature", None)
    if previous is not None and previous != signature:
        _v79.clear_thread_score_cache()
        state = _v79._state(df)
    setattr(state, "v80_endpoint_signature", signature)
    return _ORIGINAL_ENTRY_POINT(
        df,
        breakout=breakout,
        volume_score=volume_score,
        value_trap_risk_value=value_trap_risk_value,
        price_decimals=price_decimals,
    )


def install() -> None:
    global _INSTALLED
    _v79.entry_point = entry_point
    _score.entry_point = entry_point
    _INSTALLED = True


install()
