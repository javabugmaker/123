"""v78 TickFlow universe-cache acceleration.

Backtest tradeability checks resolve one price-limit rule per ticker.  The v52
metadata helper falls back to ``_load_universe_cache`` when that ticker is not
already present in ``_INSTRUMENT_META``.  In spawned backtest workers the map
starts nearly empty, so the old path could read and JSON-decode the entire
multi-thousand-symbol ``_tickflow_universe.json`` once for every new ticker.

Cache that immutable payload by file (mtime_ns, size).  Repeated ticker lookups
become dictionary access; a real universe-file replacement changes the key,
clears stale instrument metadata and reloads once.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from typing import Any

import downloader as _downloader

_LEGACY_LOAD_UNIVERSE_CACHE = _downloader._load_universe_cache
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
    # ``state`` is intentionally only the cache key. The stable loader retains
    # TTL/schema/error handling and performs the actual read/JSON validation.
    _ = state
    payload = _LEGACY_LOAD_UNIVERSE_CACHE()
    return payload if isinstance(payload, dict) else None


def load_universe_cache() -> dict[str, Any] | None:
    global _LAST_FILE_STATE
    state = _file_state()
    with _LOCK:
        if state != _LAST_FILE_STATE:
            if _LAST_FILE_STATE is not None:
                _downloader._INSTRUMENT_META.clear()
            _LAST_FILE_STATE = state
        payload = _load_for_state(state)
    return payload


def clear_universe_cache_acceleration() -> None:
    global _LAST_FILE_STATE
    with _LOCK:
        _load_for_state.cache_clear()
        _LAST_FILE_STATE = _file_state()


def install() -> None:
    global _INSTALLED, _LAST_FILE_STATE
    if _INSTALLED:
        return
    _LAST_FILE_STATE = _file_state()
    _downloader._load_universe_cache = load_universe_cache
    _downloader.clear_universe_cache_acceleration = clear_universe_cache_acceleration
    _INSTALLED = True


install()
