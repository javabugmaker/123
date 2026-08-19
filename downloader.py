"""v56 TickFlow public facade with GUI-local authenticated API support.

``downloader_v56`` resolves credentials from the gitignored GUI-local settings
first and then the process environment.  Authenticated mode uses TickFlow's
full SDK client and post-close quote fallback; Free remains the no-key fallback.
The cache-only execution metadata lookup is preserved so historical calculations
never trigger hidden metadata network I/O.
"""

from __future__ import annotations

import sys
from functools import lru_cache

import downloader_v56 as _core
from downloader_v56 import *  # noqa: F403

_v51_price_limit = _core.get_price_limit_pct


def _cached_metadata_available(ticker: str) -> bool:
    symbol = _core.normalize_ticker(ticker)
    metadata = _core._INSTRUMENT_META.get(symbol)
    if isinstance(metadata, dict) and metadata:
        return True
    cached = _core._load_universe_cache()
    if not isinstance(cached, dict):
        return False
    metadata_map = cached.get("metadata", {})
    if not isinstance(metadata_map, dict):
        return False
    raw = metadata_map.get(symbol)
    if not isinstance(raw, dict) or not raw:
        return False
    _core._INSTRUMENT_META[symbol] = raw
    return True


@lru_cache(maxsize=8192)
def get_price_limit_pct(ticker: str, is_etf: bool = False) -> float | None:
    """Resolve a security limit from cached TickFlow metadata only."""
    if not _cached_metadata_available(ticker):
        return None
    return _v51_price_limit(ticker, is_etf=is_etf)


_core.get_price_limit_pct = get_price_limit_pct
sys.modules[__name__] = _core
