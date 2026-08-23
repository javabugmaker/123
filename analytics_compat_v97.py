"""Narrow backwards-compatibility bridge for the canonical analytics runtime.

The v97 runtime keeps modern profile-aware executors internally. A small number
of older research/test integrations patch the public ``analytics`` facade with a
positional-only ``_backtest_one_ticker`` callback. Rather than teaching every
modern cache/worker layer about that obsolete signature, this module detects the
legacy callback at the outer run boundary and temporarily routes the single run
through a positional cache adapter.

Normal production execution does not enter the compatibility branch. The
historical PERFORMANCE_ENGINE_VERSION string is also preserved as an API
compatibility identifier; v97 composition is exposed separately through
ANALYTICS_RUNTIME_COMPOSITION_VERSION.
"""

from __future__ import annotations

import inspect
import sys
import threading
from typing import Any

import pandas as pd

import analytics_core as _core

ANALYTICS_COMPAT_VERSION = "2026-08-23-v97-legacy-executor-boundary-v1"
LEGACY_PERFORMANCE_ENGINE_VERSION = (
    "2026-08-20-v80-vectorized-backtest-workstation-v1"
)

_INSTALLED = False
_ORIGINAL_RUN: Any = None
_LOCK = threading.RLock()


def _supports_profile_contract(callable_obj: Any) -> bool:
    probe = getattr(callable_obj, "side_effect", None)
    if callable(probe):
        callable_obj = probe
    try:
        parameters = inspect.signature(callable_obj).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.name == "profile"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _facade_executor() -> Any:
    facade = sys.modules.get("analytics")
    if facade is not None:
        candidate = getattr(facade, "_backtest_one_ticker", None)
        if callable(candidate) and candidate is not _core._backtest_one_ticker:
            return candidate
    return _core._backtest_one_ticker


def _legacy_cached_adapter(
    ticker: str,
    source: str,
    benchmark_frame: pd.DataFrame | None,
    commission: float,
    stamp_duty: float,
    slippage: float,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None],
    benchmark_signature: str = "",
    *,
    profile: Any = None,
    benchmark_name: str = "沪深300",
) -> tuple[list[dict[str, Any]], bool]:
    del benchmark_signature, profile, benchmark_name
    samples = _core._backtest_one_ticker(
        ticker,
        source,
        benchmark_frame,
        commission,
        stamp_duty,
        slippage,
        split_dates,
    )
    return list(samples or []), False


def install() -> None:
    """Install one outer compatibility boundary without changing hot paths."""
    global _INSTALLED, _ORIGINAL_RUN

    _core.PERFORMANCE_ENGINE_VERSION = LEGACY_PERFORMANCE_ENGINE_VERSION
    _core.ANALYTICS_COMPAT_VERSION = ANALYTICS_COMPAT_VERSION
    facade = sys.modules.get("analytics")
    if facade is not None:
        setattr(facade, "PERFORMANCE_ENGINE_VERSION", LEGACY_PERFORMANCE_ENGINE_VERSION)
        setattr(facade, "ANALYTICS_COMPAT_VERSION", ANALYTICS_COMPAT_VERSION)

    if _INSTALLED:
        return
    _ORIGINAL_RUN = _core.run_historical_backtest

    def run_historical_backtest(*args: Any, **kwargs: Any) -> Any:
        executor = _facade_executor()
        if _supports_profile_contract(executor):
            return _ORIGINAL_RUN(*args, **kwargs)

        # Legacy callback is a compatibility-only lane. Serialize the temporary
        # global swap so unrelated threads can never observe the positional shim.
        with _LOCK:
            previous_executor = _core._backtest_one_ticker
            previous_cached = _core._backtest_one_ticker_cached
            _core._backtest_one_ticker = executor
            _core._backtest_one_ticker_cached = _legacy_cached_adapter
            try:
                return _ORIGINAL_RUN(*args, **kwargs)
            finally:
                _core._backtest_one_ticker = previous_executor
                _core._backtest_one_ticker_cached = previous_cached

    run_historical_backtest.__name__ = "run_historical_backtest"
    run_historical_backtest.__module__ = "analytics"
    _core.run_historical_backtest = run_historical_backtest
    if facade is not None:
        setattr(facade, "run_historical_backtest", run_historical_backtest)
    _INSTALLED = True
