from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

_LIMIT_TOLERANCE = 0.0025


def daily_limit_pct(ticker: str, *, is_etf: bool = False) -> float:
    symbol = str(ticker or "").strip().upper()
    code = symbol.split(".", 1)[0]
    suffix = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
    if suffix == "BJ":
        return 0.30
    if not is_etf and code.startswith(("300", "301", "688", "689")):
        return 0.20
    return 0.10


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return np.nan
    return result if np.isfinite(result) else np.nan


def is_entry_tradeable(
    ticker: str,
    frame: pd.DataFrame,
    entry_index: int,
    *,
    is_etf: bool = False,
) -> tuple[bool, str]:
    required = {"Open", "High", "Low", "Close", "Volume"}
    if frame is None or entry_index <= 0 or entry_index >= len(frame):
        return False, "invalid_entry_index"
    if not required.issubset(frame.columns):
        return False, "missing_ohlcv"

    row = frame.iloc[int(entry_index)]
    previous = frame.iloc[int(entry_index) - 1]
    open_price = _number(row["Open"])
    high = _number(row["High"])
    low = _number(row["Low"])
    volume = _number(row["Volume"])
    previous_close = _number(previous["Close"])
    if not all(np.isfinite(value) and value > 0 for value in (open_price, high, low, previous_close)):
        return False, "invalid_price"
    if not np.isfinite(volume) or volume <= 0:
        return False, "suspended_or_zero_volume"

    limit_pct = daily_limit_pct(ticker, is_etf=is_etf)
    theoretical_limit_up = previous_close * (1.0 + limit_pct)
    threshold = theoretical_limit_up * (1.0 - _LIMIT_TOLERANCE)
    if open_price >= threshold and low >= threshold:
        return False, "locked_limit_up"
    return True, "tradeable"
