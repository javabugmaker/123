"""Calibration/score-weight cache transaction boundary.

The historical backtest publishes ScoreCalibration.json before returning. The
score hot path intentionally avoids stat'ing that file on every score call, so
the short-lived weight cache is invalidated immediately after every calibration
run.

This module is also the final owner of ``run_historical_backtest`` in the
canonical runtime. Legacy research/test integrations may patch
``_backtest_one_ticker`` with a positional-only callable; modern production
executors accept the profile-aware keyword contract. Detect that difference at
this final transaction boundary and temporarily route cached execution through a
minimal positional adapter. This removes the need for another analytics facade
and makes compatibility independent of import order.
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


def _legacy_cached_adapter(
    ticker: str,
    source: str,
    benchmark_frame: Any,
    commission: float,
    stamp_duty: float,
    slippage: float,
    split_dates: Any,
    benchmark_signature: str = "",
    **_kwargs: Any,
) -> tuple[list[dict[str, Any]], bool]:
    del benchmark_signature
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


def run_historical_backtest(*args: Any, **kwargs: Any):
    executor = _core._backtest_one_ticker
    try:
        if _supports_profile_contract(executor):
            return _LEGACY_RUN_HISTORICAL_BACKTEST(*args, **kwargs)

        with _LEGACY_EXECUTOR_LOCK:
            previous_cached = _core._backtest_one_ticker_cached
            _core._backtest_one_ticker_cached = _legacy_cached_adapter
            try:
                return _LEGACY_RUN_HISTORICAL_BACKTEST(*args, **kwargs)
            finally:
                _core._backtest_one_ticker_cached = previous_cached
    finally:
        _score.invalidate_model_weight_cache()


def install() -> None:
    global _INSTALLED
    _core.run_historical_backtest = run_historical_backtest
    _INSTALLED = True


install()
