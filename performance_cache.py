"""v36 compute-cache namespace facade.

The stable cache implementation remains in ``performance_cache_core``.  Market
volume normalization changed in v36, so indicator/backtest files are isolated in
new directories even though their algorithmic cache-version contracts remain
v5/v8 for backward-compatible diagnostics.
"""

from __future__ import annotations

import sys

import performance_cache_core as _core
from performance_cache_core import *  # noqa: F403

MARKET_DATA_CACHE_NAMESPACE = "volume-shares-v1"
INDICATOR_CACHE_DIR = _core.CACHE_DIR / (
    f"_indicators_{_core.INDICATOR_CACHE_VERSION}_{MARKET_DATA_CACHE_NAMESPACE}"
)
BACKTEST_CACHE_DIR = _core.CACHE_DIR / (
    f"_backtest_{_core.BACKTEST_CACHE_VERSION}_{MARKET_DATA_CACHE_NAMESPACE}"
)

_core.MARKET_DATA_CACHE_NAMESPACE = MARKET_DATA_CACHE_NAMESPACE
_core.INDICATOR_CACHE_DIR = INDICATOR_CACHE_DIR
_core.BACKTEST_CACHE_DIR = BACKTEST_CACHE_DIR

sys.modules[__name__] = _core
