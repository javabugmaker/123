"""Calibration/score-weight cache transaction boundary.

The historical backtest publishes ScoreCalibration.json before returning. The
score hot path intentionally avoids stat'ing that file on every score call, so
the short-lived weight cache is invalidated immediately after every calibration
run.

This module is the final owner of ``run_historical_backtest`` in the canonical
runtime. Legacy research/test callbacks may still accept only the historical
seven positional arguments. The compatibility lane adapts both the raw callback
and the cached callback boundary so modern profile keywords never reach that old
signature.
"""

from __future__ import annotations

import inspect
import threading
from typing import Any

import analytics_core as _core
import score_core as _score

_LEGACY_RUN_HISTORICAL_BACKTEST = _core.run_historical_backtest
_INSTALLED = False
_LEGACY_EXECUTOR_LOCK = threading.RLock()


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


def _modern_executor_adapter(legacy_executor: Any):
    def adapted(
        ticker: str,
        source: str,
        benchmark_frame: Any,
        commission: float,
        stamp_duty: float,
        slippage: float,
        split_dates: Any,
        **_kwargs: Any,
    ):
        return legacy_executor(
            ticker,
            source,
            benchmark_frame,
            commission,
            stamp_duty,
            slippage,
            split_dates,
        )

    return adapted


def _modern_cached_adapter(legacy_executor: Any):
    def adapted(
        ticker: str,
        source: str,
        benchmark_frame: Any,
        commission: float,
        stamp_duty: float,
        slippage: float,
        split_dates: Any,
        benchmark_signature: str = "",
        **_kwargs: Any,
    ):
        del benchmark_signature, _kwargs
        samples = legacy_executor(
            ticker,
            source,
            benchmark_frame,
            commission,
            stamp_duty,
            slippage,
            split_dates,
        )
        return list(samples or []), False

    return adapted


def run_historical_backtest(*args: Any, **kwargs: Any):
    executor = _core._backtest_one_ticker
    try:
        if _supports_profile_contract(executor):
            return _LEGACY_RUN_HISTORICAL_BACKTEST(*args, **kwargs)

        with _LEGACY_EXECUTOR_LOCK:
            previous_executor = _core._backtest_one_ticker
            previous_cached = _core._backtest_one_ticker_cached
            _core._backtest_one_ticker = _modern_executor_adapter(executor)
            _core._backtest_one_ticker_cached = _modern_cached_adapter(executor)
            try:
                return _LEGACY_RUN_HISTORICAL_BACKTEST(*args, **kwargs)
            finally:
                _core._backtest_one_ticker = previous_executor
                _core._backtest_one_ticker_cached = previous_cached
    finally:
        _score.invalidate_model_weight_cache()


def install() -> None:
    global _INSTALLED
    _core.run_historical_backtest = run_historical_backtest
    _INSTALLED = True


install()
