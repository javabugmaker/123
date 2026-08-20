"""v80 backtest worker acceleration bundle.

Each ProcessPool worker reuses one benchmark DataFrame for hundreds/thousands of
tickers. v77/v78 keep exact market-history validation, incremental maturity
rewind and historical-universe memoization. v80 adds O(1) benchmark alignment,
vectorised tradeability/exit resolution, precomputed sample execution state,
one-hash warm cache validation and vectorised-workload process/chunk tuning.
All layers are installed through ``import analytics`` so Windows spawned workers
receive the same runtime as the parent process.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

import analytics_core as _core
import backtest_alignment_acceleration_v80 as _alignment_v80
import backtest_cache_acceleration_v80 as _backtest_cache_v80
import backtest_incremental_v78 as _incremental
import backtest_sample_acceleration_v80 as _sample_v80
import backtest_worker_tuning_v78 as _worker_tuning_v78
import backtest_worker_tuning_v80 as _worker_tuning_v80
import cache_acceleration_v77 as _cache_acceleration
import historical_lookup_acceleration_v78 as _historical_lookup
import tradeability_acceleration_v80 as _tradeability_v80

_cache_acceleration.install()
_incremental.install()
_historical_lookup.install()
_worker_tuning_v78.install()
_worker_tuning_v80.install()
_tradeability_v80.install()
_sample_v80.install()
_alignment_v80.install()

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
        cached = context.get("_v80_benchmark_market_state")
        if isinstance(cached, dict) and cached:
            return dict(cached)
        state = dict(_LEGACY_MARKET_CACHE_STATE(frame))
        context["_v80_benchmark_market_state"] = dict(state)
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
        cache = context.setdefault("_v80_benchmark_prefix_matches", {})
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
    _worker_tuning_v78.install()
    _worker_tuning_v80.install()
    _tradeability_v80.install()
    _sample_v80.install()
    _alignment_v80.install()
    _core.market_cache_state = market_cache_state
    _core.market_prefix_matches = market_prefix_matches
    # v78 incremental installs before the v80 cache layer. Re-assert v80 last
    # so warm-cache hits use the one-fingerprint path in every spawned worker.
    _backtest_cache_v80.install()
    _INSTALLED = True


install()
