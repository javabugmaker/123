"""v77 backtest cache-validation acceleration.

Each ProcessPool worker reuses one benchmark DataFrame for hundreds/thousands of
tickers. The stable cache path nevertheless recomputes that benchmark's full
market fingerprint and the same historical-prefix validation for every ticker.
Memoise only the worker-context benchmark object; ticker market histories still
run the full v69 integrity checks independently.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

import analytics_core as _core

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
        cached = context.get("_v77_benchmark_market_state")
        if isinstance(cached, dict) and cached:
            return dict(cached)
        state = dict(_LEGACY_MARKET_CACHE_STATE(frame))
        context["_v77_benchmark_market_state"] = dict(state)
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
        cache = context.setdefault("_v77_benchmark_prefix_matches", {})
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
    _core.market_cache_state = market_cache_state
    _core.market_prefix_matches = market_prefix_matches
    _INSTALLED = True


install()
