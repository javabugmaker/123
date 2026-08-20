"""v78 TickFlow universe/price-limit metadata acceleration.

Spawned backtest workers start with an almost-empty ``_INSTRUMENT_META`` map.
The v52 metadata helper therefore used to read and JSON-decode the complete
multi-thousand-symbol ``_tickflow_universe.json`` for every newly encountered
ticker.  Entry/exit tradeability checks then rebuilt the same price-limit
evidence repeatedly for every historical sample.

Cache the universe payload by (mtime_ns, size), and cache immutable price-limit
evidence by (ticker, is_etf). A real universe-file replacement invalidates both
layers automatically; historical date-specific board rules remain in
``tradeability.resolve_daily_limit_pct`` and are unchanged.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from typing import Any

import downloader as _downloader

_LEGACY_LOAD_UNIVERSE_CACHE = _downloader._load_universe_cache
_LEGACY_GET_PRICE_LIMIT_EVIDENCE = _downloader.get_price_limit_evidence
_LOCK = threading.RLock()
_LAST_FILE_STATE: tuple[int, int] | None = None
_INSTALLED = False


def _file_state() -> tuple[int, int] | None:
    try:
        stat = _downloader._UNIVERSE_CACHE_PATH.stat()
    except OSError:
        return None
    return int(stat.st_mtime_ns), int(stat.st_size)


@lru_cache(maxsize=4)
def _load_for_state(state: tuple[int, int] | None) -> dict[str, Any] | None:
    _ = state
    payload = _LEGACY_LOAD_UNIVERSE_CACHE()
    return payload if isinstance(payload, dict) else None


@lru_cache(maxsize=8192)
def _price_limit_evidence_tuple(
    ticker: str,
    is_etf: bool,
) -> tuple[str, float | None, str]:
    raw = _LEGACY_GET_PRICE_LIMIT_EVIDENCE(ticker, is_etf=is_etf)
    symbol = str(raw.get("ticker", ticker) or ticker)
    value = raw.get("pct")
    try:
        pct = float(value) if value is not None else None
    except (TypeError, ValueError):
        pct = None
    source = str(raw.get("source", "exchange_fallback") or "exchange_fallback")
    return symbol, pct, source


def get_price_limit_evidence(ticker: str, is_etf: bool = False) -> dict[str, Any]:
    symbol, pct, source = _price_limit_evidence_tuple(str(ticker), bool(is_etf))
    return {"ticker": symbol, "pct": pct, "source": source}


def load_universe_cache() -> dict[str, Any] | None:
    global _LAST_FILE_STATE
    state = _file_state()
    with _LOCK:
        if state != _LAST_FILE_STATE:
            if _LAST_FILE_STATE is not None:
                _downloader._INSTRUMENT_META.clear()
                _price_limit_evidence_tuple.cache_clear()
            _LAST_FILE_STATE = state
        payload = _load_for_state(state)
    return payload


def clear_universe_cache_acceleration() -> None:
    global _LAST_FILE_STATE
    with _LOCK:
        _load_for_state.cache_clear()
        _price_limit_evidence_tuple.cache_clear()
        _LAST_FILE_STATE = _file_state()


def install() -> None:
    global _INSTALLED, _LAST_FILE_STATE
    if _INSTALLED:
        return
    _LAST_FILE_STATE = _file_state()
    _downloader._load_universe_cache = load_universe_cache
    _downloader.get_price_limit_evidence = get_price_limit_evidence
    _downloader.clear_universe_cache_acceleration = clear_universe_cache_acceleration
    _INSTALLED = True


install()
