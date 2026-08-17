from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
import pandas as pd

_LIMIT_TOLERANCE = 0.0025
_CHINEXT_20_START = pd.Timestamp("2020-08-24")


def _trade_timestamp(value: Any) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(timestamp):
        return None
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def _fallback_limit_and_source(
    ticker: str,
    *,
    is_etf: bool = False,
    trade_date: str | date | datetime | pd.Timestamp | None = None,
) -> tuple[float, str]:
    symbol = str(ticker or "").strip().upper()
    code = symbol.split(".", 1)[0]
    suffix = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
    when = _trade_timestamp(trade_date)

    if suffix == "BJ":
        return 0.30, "beijing_30pct_rule"

    if not is_etf and code.startswith(("300", "301")):
        if when is not None and when < _CHINEXT_20_START:
            return 0.10, "chinext_pre_2020_10pct_rule"
        return 0.20, "chinext_20pct_rule"

    if not is_etf and code.startswith(("688", "689")):
        return 0.20, "star_20pct_rule"

    if is_etf and code.startswith(("588", "589")):
        return 0.20, "star_etf_20pct_rule"

    return 0.10, "standard_10pct_rule"


def fallback_daily_limit_pct(
    ticker: str,
    *,
    is_etf: bool = False,
    trade_date: str | date | datetime | pd.Timestamp | None = None,
) -> float:
    """Offline exchange-rule fallback when explicit metadata is unavailable."""
    return _fallback_limit_and_source(
        ticker,
        is_etf=is_etf,
        trade_date=trade_date,
    )[0]


def resolve_daily_limit_pct(
    ticker: str,
    *,
    is_etf: bool = False,
    trade_date: str | date | datetime | pd.Timestamp | None = None,
) -> tuple[float, str]:
    """Resolve one price-limit ratio with auditable provenance.

    ``limit_up``/``limit_down`` price levels are intentionally not consumed as
    ratios.  Only downloader evidence explicitly identified as a ratio or an
    ETF rule may override the exchange fallback.
    """
    evidence: dict[str, Any] = {}
    try:
        from downloader import get_price_limit_evidence

        raw = get_price_limit_evidence(ticker, is_etf=is_etf)
        if isinstance(raw, dict):
            evidence = raw
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        evidence = {}

    try:
        metadata_limit = float(evidence.get("pct"))
    except (TypeError, ValueError):
        metadata_limit = np.nan
    source = str(evidence.get("source", "") or "")
    when = _trade_timestamp(trade_date)

    if np.isfinite(metadata_limit) and 0.02 <= metadata_limit <= 0.40:
        # ChiNext stock/related-fund limit widened from 10% to 20% on
        # 2020-08-24.  Preserve the historical execution regime in backtests.
        symbol = str(ticker or "").strip().upper()
        code = symbol.split(".", 1)[0]
        chinext_related = (
            (not is_etf and code.startswith(("300", "301")))
            or source == "chinext_related_etf_20pct_rule"
        )
        if chinext_related and when is not None and when < _CHINEXT_20_START:
            return 0.10, "chinext_pre_2020_10pct_rule"
        return float(metadata_limit), source or "security_metadata_rule"

    return _fallback_limit_and_source(
        ticker,
        is_etf=is_etf,
        trade_date=trade_date,
    )


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
    # A RangeIndex/Int64Index is a row locator, not a Unix-nanosecond trading
    # timestamp.  Treating integer 1 as 1970-01-01 silently selects historical
    # exchange rules for undated frames.  With no date provenance, use the
    # current board rule instead.
    if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(
        value, (bool, np.bool_)
    ):
        return None
    return _trade_timestamp(value)


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
    """Delay an exit through suspension/locked limit-down sessions."""
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
