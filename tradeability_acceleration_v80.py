"""v80 vectorised A-share/ETF tradeability matrices for historical backtests.

The stable execution model calls entry/exit tradeability helpers for every
historical sample. Those helpers repeatedly select pandas rows and resolve the
same security price-limit rule. v80 computes the complete entry/exit state once
per ticker DataFrame, then serves O(1) lookups. Historical ChiNext rule changes,
metadata overrides, suspensions and locked-limit semantics are unchanged.
"""

from __future__ import annotations

import threading
import weakref
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

import tradeability as _trade

_INSTALLED = False
_TLS = threading.local()
_REQUIRED = ("Open", "High", "Low", "Close", "Volume")


@dataclass
class _TradeState:
    frame_ref: weakref.ReferenceType[pd.DataFrame]
    ticker: str
    is_etf: bool
    entry_tradeable: np.ndarray
    entry_reason: np.ndarray
    exit_tradeable: np.ndarray
    exit_reason: np.ndarray
    exit_resolution: dict[int, tuple[np.ndarray, np.ndarray]] = field(
        default_factory=dict
    )


def _numeric(frame: pd.DataFrame, column: str) -> np.ndarray:
    return pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=np.float64)


def _date_array(frame: pd.DataFrame) -> np.ndarray:
    result = np.empty(len(frame), dtype="datetime64[ns]")
    result[:] = np.datetime64("NaT", "ns")
    for position, value in enumerate(frame.index):
        if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(
            value, (bool, np.bool_)
        ):
            continue
        timestamp = _trade._trade_timestamp(value)
        if timestamp is not None:
            result[position] = np.datetime64(timestamp.to_datetime64())
    return result


def _limit_vector(ticker: str, frame: pd.DataFrame, is_etf: bool) -> np.ndarray:
    symbol = str(ticker or "").strip().upper()
    code = symbol.split(".", 1)[0]
    suffix = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
    evidence: dict[str, Any] = {}
    try:
        from downloader import get_price_limit_evidence

        raw = get_price_limit_evidence(symbol, is_etf=is_etf)
        if isinstance(raw, dict):
            evidence = raw
    except (ImportError, OSError, RuntimeError, TypeError, ValueError):
        evidence = {}

    try:
        metadata_limit = float(evidence.get("pct"))
    except (TypeError, ValueError):
        metadata_limit = np.nan
    source = str(evidence.get("source", "") or "")
    dates = _date_array(frame)
    pre_chinext = (~np.isnat(dates)) & (
        dates < np.datetime64("2020-08-24", "ns")
    )

    if np.isfinite(metadata_limit) and 0.02 <= metadata_limit <= 0.40:
        limits = np.full(len(frame), metadata_limit, dtype=np.float64)
        chinext_related = (
            (not is_etf and code.startswith(("300", "301")))
            or source == "chinext_related_etf_20pct_rule"
        )
        if chinext_related:
            limits[pre_chinext] = 0.10
        return limits

    if suffix == "BJ":
        return np.full(len(frame), 0.30, dtype=np.float64)
    if not is_etf and code.startswith(("300", "301")):
        limits = np.full(len(frame), 0.20, dtype=np.float64)
        limits[pre_chinext] = 0.10
        return limits
    if not is_etf and code.startswith(("688", "689")):
        return np.full(len(frame), 0.20, dtype=np.float64)
    if is_etf and code.startswith(("588", "589")):
        return np.full(len(frame), 0.20, dtype=np.float64)
    return np.full(len(frame), 0.10, dtype=np.float64)


def _build_state(
    ticker: str,
    frame: pd.DataFrame,
    is_etf: bool,
) -> _TradeState | None:
    if frame is None or not all(column in frame.columns for column in _REQUIRED):
        return None
    current = getattr(_TLS, "trade_state", None)
    symbol = str(ticker or "").strip().upper()
    if (
        isinstance(current, _TradeState)
        and current.frame_ref() is frame
        and current.ticker == symbol
        and current.is_etf == bool(is_etf)
    ):
        return current

    n = len(frame)
    open_price = _numeric(frame, "Open")
    high = _numeric(frame, "High")
    low = _numeric(frame, "Low")
    close = _numeric(frame, "Close")
    volume = _numeric(frame, "Volume")
    previous_close = np.empty(n, dtype=np.float64)
    previous_close[:] = np.nan
    if n > 1:
        previous_close[1:] = close[:-1]
    limits = _limit_vector(symbol, frame, bool(is_etf))

    entry_price_valid = (
        np.isfinite(open_price)
        & np.isfinite(high)
        & np.isfinite(low)
        & np.isfinite(previous_close)
        & (open_price > 0.0)
        & (high > 0.0)
        & (low > 0.0)
        & (previous_close > 0.0)
    )
    volume_valid = np.isfinite(volume) & (volume > 0.0)
    limit_up_threshold = previous_close * (1.0 + limits) * (1.0 - _trade._LIMIT_TOLERANCE)
    entry_locked = (
        entry_price_valid
        & volume_valid
        & (open_price >= limit_up_threshold)
        & (low >= limit_up_threshold)
    )
    entry_tradeable = entry_price_valid & volume_valid & ~entry_locked
    entry_reason = np.full(n, "tradeable", dtype=object)
    entry_reason[~entry_price_valid] = "invalid_price"
    entry_reason[entry_price_valid & ~volume_valid] = "suspended_or_zero_volume"
    entry_reason[entry_locked] = "locked_limit_up"
    if n:
        entry_tradeable[0] = False
        entry_reason[0] = "invalid_entry_index"

    exit_price_valid = entry_price_valid & np.isfinite(close) & (close > 0.0)
    limit_down_threshold = previous_close * (1.0 - limits) * (1.0 + _trade._LIMIT_TOLERANCE)
    exit_locked = (
        exit_price_valid
        & volume_valid
        & (open_price <= limit_down_threshold)
        & (high <= limit_down_threshold)
    )
    exit_tradeable = exit_price_valid & volume_valid & ~exit_locked
    exit_reason = np.full(n, "tradeable", dtype=object)
    exit_reason[~exit_price_valid] = "invalid_price"
    exit_reason[exit_price_valid & ~volume_valid] = "suspended_or_zero_volume"
    exit_reason[exit_locked] = "locked_limit_down"
    if n:
        exit_tradeable[0] = False
        exit_reason[0] = "invalid_exit_index"

    result = _TradeState(
        frame_ref=weakref.ref(frame),
        ticker=symbol,
        is_etf=bool(is_etf),
        entry_tradeable=entry_tradeable,
        entry_reason=entry_reason,
        exit_tradeable=exit_tradeable,
        exit_reason=exit_reason,
    )
    _TLS.trade_state = result
    return result


def is_entry_tradeable(
    ticker: str,
    frame: pd.DataFrame,
    entry_index: int,
    *,
    is_etf: bool = False,
) -> tuple[bool, str]:
    try:
        index = int(entry_index)
    except (TypeError, ValueError):
        return False, "invalid_entry_index"
    if frame is None or index <= 0 or index >= len(frame):
        return False, "invalid_entry_index"
    state = _build_state(ticker, frame, is_etf)
    if state is None:
        return _trade._LEGACY_IS_ENTRY_TRADEABLE(
            ticker, frame, index, is_etf=is_etf
        )
    return bool(state.entry_tradeable[index]), str(state.entry_reason[index])


def is_exit_tradeable(
    ticker: str,
    frame: pd.DataFrame,
    exit_index: int,
    *,
    is_etf: bool = False,
) -> tuple[bool, str]:
    try:
        index = int(exit_index)
    except (TypeError, ValueError):
        return False, "invalid_exit_index"
    if frame is None or index <= 0 or index >= len(frame):
        return False, "invalid_exit_index"
    state = _build_state(ticker, frame, is_etf)
    if state is None:
        return _trade._LEGACY_IS_EXIT_TRADEABLE(
            ticker, frame, index, is_etf=is_etf
        )
    return bool(state.exit_tradeable[index]), str(state.exit_reason[index])


def _exit_resolution(state: _TradeState, max_delay_days: int) -> tuple[np.ndarray, np.ndarray]:
    delay_limit = max(0, int(max_delay_days))
    cached = state.exit_resolution.get(delay_limit)
    if cached is not None:
        return cached
    n = len(state.exit_tradeable)
    resolved = np.full(n, -1, dtype=np.int32)
    delays = np.full(n, -1, dtype=np.int16)
    unresolved = np.ones(n, dtype=bool)
    for delay in range(delay_limit + 1):
        length = n - delay
        if length <= 0:
            break
        candidate = np.zeros(n, dtype=bool)
        candidate[:length] = state.exit_tradeable[delay:]
        choose = unresolved & candidate
        if choose.any():
            positions = np.flatnonzero(choose)
            resolved[positions] = positions + delay
            delays[positions] = delay
            unresolved[positions] = False
    state.exit_resolution[delay_limit] = (resolved, delays)
    return resolved, delays


def resolve_exit_index(
    ticker: str,
    frame: pd.DataFrame,
    intended_index: int,
    *,
    is_etf: bool = False,
    max_delay_days: int = 10,
) -> tuple[int | None, int, str]:
    try:
        intended = int(intended_index)
    except (TypeError, ValueError):
        intended = 0
    start = max(1, intended)
    if frame is None:
        return None, 0, "out_of_range"
    state = _build_state(ticker, frame, is_etf)
    if state is None:
        return _trade._LEGACY_RESOLVE_EXIT_INDEX(
            ticker,
            frame,
            intended,
            is_etf=is_etf,
            max_delay_days=max_delay_days,
        )
    n = len(frame)
    if start >= n:
        return None, 0, "out_of_range"
    resolved, delays = _exit_resolution(state, max_delay_days)
    target = int(resolved[start])
    if target >= 0:
        delay = int(delays[start])
        if delay == 0:
            return target, 0, "tradeable"
        return target, delay, str(state.exit_reason[target - 1])

    stop = min(n, start + max(0, int(max_delay_days)) + 1)
    last_reason = (
        str(state.exit_reason[stop - 1]) if stop > start else "out_of_range"
    )
    return None, max(0, stop - start), last_reason


def clear_tradeability_cache() -> None:
    if hasattr(_TLS, "trade_state"):
        delattr(_TLS, "trade_state")


def install() -> None:
    global _INSTALLED
    if not hasattr(_trade, "_LEGACY_IS_ENTRY_TRADEABLE"):
        _trade._LEGACY_IS_ENTRY_TRADEABLE = _trade.is_entry_tradeable
        _trade._LEGACY_IS_EXIT_TRADEABLE = _trade.is_exit_tradeable
        _trade._LEGACY_RESOLVE_EXIT_INDEX = _trade.resolve_exit_index
    _trade.is_entry_tradeable = is_entry_tradeable
    _trade.is_exit_tradeable = is_exit_tradeable
    _trade.resolve_exit_index = resolve_exit_index
    _trade.clear_tradeability_cache = clear_tradeability_cache

    import sys

    analytics_core = sys.modules.get("analytics_core")
    if analytics_core is not None:
        setattr(analytics_core, "is_entry_tradeable", is_entry_tradeable)
        setattr(analytics_core, "resolve_exit_index", resolve_exit_index)
    _INSTALLED = True


install()
