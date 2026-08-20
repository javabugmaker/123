"""v78 backtest worker acceleration bundle.

Each ProcessPool worker reuses one benchmark DataFrame for hundreds/thousands of
tickers. Install the v77 numeric market-hash fast path before capturing the
benchmark delegate, then layer benchmark memoization on top. v78 also installs
cache-aware incremental recomputation, historical-universe lookup memoization
and profile-aware process tuning in every spawned worker through the normal
``import analytics`` initializer path.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

import analytics_core as _core
import backtest_incremental_v78 as _incremental
import backtest_worker_tuning_v78 as _worker_tuning
import cache_acceleration_v77 as _cache_acceleration
import historical_lookup_acceleration_v78 as _historical_lookup

_cache_acceleration.install()
_incremental.install()
_historical_lookup.install()
_worker_tuning.install()

_LEGACY_MARKET_CACHE_STATE = _core.market_cache_state
_LEGACY_MARKET_PREFIX_MATCHES = _core.market_prefix_matches
_INSTALLED = False


def _worker_benchmark() -> pd.DataFrame | None:
    context = getattr(_core, "_BACKTEST_WORKER_CONTEXT", {})
    if not isinstance(context, dict):
        return None
    frame = context.get("benchmark_frame")
    return frame if isinstance(frame, pd.DataFrame) else None


def market_cache_state(frame: pd.DataFrame) -> dict[str, Any]:
    benchmark = _worker_benchmark()
    if benchmark is not None and frame is benchmark:
        context = _core._BACKTEST_WORKER_CONTEXT
        cached = context.get("_v78_benchmark_market_state")
        if isinstance(cached, dict) and cached:
            return dict(cached)
        state = dict(_LEGACY_MARKET_CACHE_STATE(frame))
        context["_v78_benchmark_market_state"] = dict(state)
        return state
    return _LEGACY_MARKET_CACHE_STATE(frame)


def _state_key(state: dict[str, Any] | None) -> tuple[object, ...]:
    if not isinstance(state, dict):
        return ()
    return (
        state.get("rows"),
        state.get("first"),
        state.get("last"),
        state.get("tail_fingerprint"),
        state.get("history_fingerprint"),
    )


def market_prefix_matches(
    frame: pd.DataFrame,
    state: dict[str, Any] | None,
) -> bool:
    benchmark = _worker_benchmark()
    if benchmark is not None and frame is benchmark:
        context = _core._BACKTEST_WORKER_CONTEXT
        cache = context.setdefault("_v78_benchmark_prefix_matches", {})
        key = _state_key(state)
        if key in cache:
            return bool(cache[key])
        matched = bool(_LEGACY_MARKET_PREFIX_MATCHES(frame, state))
        cache[key] = matched
        return matched
    return bool(_LEGACY_MARKET_PREFIX_MATCHES(frame, state))


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _cache_acceleration.install()
    _incremental.install()
    _historical_lookup.install()
    _worker_tuning.install()
    _core.market_cache_state = market_cache_state
    _core.market_prefix_matches = market_prefix_matches
    _INSTALLED = True


install()
