from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

from institution_scanner.price_limit_policy import (
    CHINEXT_20_START,
    LIMIT_TOLERANCE,
    fallback_resolution,
    resolve_price_limit,
    trade_timestamp,
)

# Compatibility aliases consumed by the vectorized v80 acceleration facade.
_LIMIT_TOLERANCE = LIMIT_TOLERANCE
_CHINEXT_20_START = CHINEXT_20_START
_trade_timestamp = trade_timestamp


def _fallback_limit_and_source(
    ticker: str,
    *,
    is_etf: bool = False,
    trade_date: str | date | datetime | pd.Timestamp | None = None,
) -> tuple[float, str]:
    resolved = fallback_resolution(
        ticker,
        is_etf=is_etf,
        trade_date=trade_date,
    )
    return resolved.pct, resolved.source


def fallback_daily_limit_pct(
    ticker: str,
    *,
    is_etf: bool = False,
    trade_date: str | date | datetime | pd.Timestamp | None = None,
) -> float:
    return _fallback_limit_and_source(
        ticker,
        is_etf=is_etf,
        trade_date=trade_date,
    )[0]


def _price_limit_evidence(ticker: str, is_etf: bool) -> tuple[float | None, str]:
    evidence: dict[str, Any] = {}
    try:
        from downloader import get_price_limit_evidence

        raw = get_price_limit_evidence(ticker, is_etf=is_etf)
        if isinstance(raw, dict):
            evidence = raw
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        evidence = {}
    try:
        pct = float(evidence.get("pct"))
    except (TypeError, ValueError):
        pct = np.nan
    source = str(evidence.get("source", "") or "")
    return (float(pct) if np.isfinite(pct) else None), source


def resolve_daily_limit_pct(
    ticker: str,
    *,
    is_etf: bool = False,
    trade_date: str | date | datetime | pd.Timestamp | None = None,
) -> tuple[float, str]:
    """Resolve one price-limit ratio with auditable provenance."""
    metadata_pct, metadata_source = _price_limit_evidence(ticker, is_etf)
    resolved = resolve_price_limit(
        ticker,
        is_etf=is_etf,
        trade_date=trade_date,
        metadata_pct=metadata_pct,
        metadata_source=metadata_source,
    )
    return resolved.pct, resolved.source


def daily_limit_pct(
    ticker: str,
    *,
    is_etf: bool = False,
    trade_date: str | date | datetime | pd.Timestamp | None = None,
) -> float:
    return resolve_daily_limit_pct(
        ticker,
        is_etf=is_etf,
        trade_date=trade_date,
    )[0]


def price_limit_source(
    ticker: str,
    *,
    is_etf: bool = False,
    trade_date: str | date | datetime | pd.Timestamp | None = None,
) -> str:
    return resolve_daily_limit_pct(
        ticker,
        is_etf=is_etf,
        trade_date=trade_date,
    )[1]


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return np.nan
    return result if np.isfinite(result) else np.nan


def _row_trade_date(frame: pd.DataFrame, index: int) -> pd.Timestamp | None:
    try:
        value = frame.index[int(index)]
    except (IndexError, TypeError, ValueError):
        return None
    if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(
        value, (bool, np.bool_)
    ):
        return None
    return trade_timestamp(value)


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
    if not all(
        np.isfinite(value) and value > 0
        for value in (open_price, high, low, previous_close)
    ):
        return False, "invalid_price"
    if not np.isfinite(volume) or volume <= 0:
        return False, "suspended_or_zero_volume"

    limit_pct = daily_limit_pct(
        ticker,
        is_etf=is_etf,
        trade_date=_row_trade_date(frame, entry_index),
    )
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

    limit_pct = daily_limit_pct(
        ticker,
        is_etf=is_etf,
        trade_date=_row_trade_date(frame, exit_index),
    )
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
    start = max(1, int(intended_index))
    stop = min(len(frame), start + max(0, int(max_delay_days)) + 1)
    last_reason = "out_of_range"
    for index in range(start, stop):
        tradeable, reason = is_exit_tradeable(
            ticker,
            frame,
            index,
            is_etf=is_etf,
        )
        if tradeable:
            return index, index - start, "tradeable" if index == start else last_reason
        last_reason = reason
    return None, max(0, stop - start), last_reason
