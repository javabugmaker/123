from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

_LIMIT_TOLERANCE = 0.0025


def fallback_daily_limit_pct(ticker: str, *, is_etf: bool = False) -> float:
    """Offline fallback when security-specific exchange metadata is unavailable."""
    symbol = str(ticker or "").strip().upper()
    code = symbol.split(".", 1)[0]
    suffix = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
    if suffix == "BJ":
        return 0.30
    if not is_etf and code.startswith(("300", "301", "688", "689")):
        return 0.20
    if is_etf and code.startswith(("588", "589")):
        return 0.20
    return 0.10


def daily_limit_pct(ticker: str, *, is_etf: bool = False) -> float:
    """Return the security-specific daily price-limit ratio without hidden I/O."""
    try:
        from downloader import get_price_limit_pct

        metadata_limit = get_price_limit_pct(ticker, is_etf=is_etf)
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        metadata_limit = None
    if metadata_limit is not None:
        try:
            value = float(metadata_limit)
        except (TypeError, ValueError):
            value = np.nan
        if np.isfinite(value) and 0.02 <= value <= 0.40:
            return value
    return fallback_daily_limit_pct(ticker, is_etf=is_etf)


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


def is_exit_tradeable(
    ticker: str,
    frame: pd.DataFrame,
    exit_index: int,
    *,
    is_etf: bool = False,
) -> tuple[bool, str]:
    """Return whether a close exit can reasonably fill on the requested day."""
    required = {"Open", "High", "Low", "Close", "Volume"}
    if frame is None or exit_index <= 0 or exit_index >= len(frame):
        return False, "invalid_exit_index"
    if not required.issubset(frame.columns):
        return False, "missing_ohlcv"

    row = frame.iloc[int(exit_index)]
    previous = frame.iloc[int(exit_index) - 1]
    open_price = _number(row["Open"])
    high = _number(row["High"])
    low = _number(row["Low"])
    close = _number(row["Close"])
    volume = _number(row["Volume"])
    previous_close = _number(previous["Close"])
    if not all(
        np.isfinite(value) and value > 0
        for value in (open_price, high, low, close, previous_close)
    ):
        return False, "invalid_price"
    if not np.isfinite(volume) or volume <= 0:
        return False, "suspended_or_zero_volume"

    limit_pct = daily_limit_pct(ticker, is_etf=is_etf)
    theoretical_limit_down = previous_close * (1.0 - limit_pct)
    threshold = theoretical_limit_down * (1.0 + _LIMIT_TOLERANCE)
    if open_price <= threshold and high <= threshold:
        return False, "locked_limit_down"
    return True, "tradeable"


def resolve_exit_index(
    ticker: str,
    frame: pd.DataFrame,
    intended_index: int,
    *,
    is_etf: bool = False,
    max_delay_days: int = 10,
) -> tuple[int | None, int, str]:
    """Delay an exit through suspension/locked limit-down sessions."""
    start = max(1, int(intended_index))
    stop = min(len(frame), start + max(0, int(max_delay_days)) + 1)
    last_reason = "out_of_range"
    for index in range(start, stop):
        tradeable, reason = is_exit_tradeable(
            ticker, frame, index, is_etf=is_etf
        )
        if tradeable:
            return index, index - start, "tradeable" if index == start else last_reason
        last_reason = reason
    return None, max(0, stop - start), last_reason
