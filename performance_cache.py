"""v52 compute-cache namespace facade.

Indicator semantics are unchanged.  Backtest tradeability semantics changed in
v52 because price-limit rules are now date-aware and no longer inferred from
ambiguous TickFlow price-level metadata, so the backtest cache moves to v9.
"""

from __future__ import annotations

import sys

import performance_cache_core as _core
from performance_cache_core import *  # noqa: F403

MARKET_DATA_CACHE_NAMESPACE = "volume-shares-v1"
INDICATOR_CACHE_VERSION = _core.INDICATOR_CACHE_VERSION
BACKTEST_CACHE_VERSION = "v9"
INDICATOR_CACHE_DIR = _core.CACHE_DIR / (
    f"_indicators_{INDICATOR_CACHE_VERSION}_{MARKET_DATA_CACHE_NAMESPACE}"
)
BACKTEST_CACHE_DIR = _core.CACHE_DIR / (
    f"_backtest_{BACKTEST_CACHE_VERSION}_{MARKET_DATA_CACHE_NAMESPACE}"
)

_core.MARKET_DATA_CACHE_NAMESPACE = MARKET_DATA_CACHE_NAMESPACE
_core.INDICATOR_CACHE_VERSION = INDICATOR_CACHE_VERSION
_core.BACKTEST_CACHE_VERSION = BACKTEST_CACHE_VERSION
_core.INDICATOR_CACHE_DIR = INDICATOR_CACHE_DIR
_core.BACKTEST_CACHE_DIR = BACKTEST_CACHE_DIR

sys.modules[__name__] = _core
