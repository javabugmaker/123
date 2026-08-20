"""v79 calibration/score-weight cache transaction bridge.

The historical backtest publishes ScoreCalibration.json before returning.  The
v79 score hot path intentionally avoids stat'ing that file on every score call,
so invalidate the short-lived worker cache immediately after each calibration
run.  The next consumer therefore re-enters the stable accepted/guard-rail
loader with no stale-weight window at the backtest/ranking boundary.
"""

from __future__ import annotations

from typing import Any

import analytics_core as _core
import score_core as _score

_LEGACY_RUN_HISTORICAL_BACKTEST = _core.run_historical_backtest
_INSTALLED = False


def run_historical_backtest(*args: Any, **kwargs: Any):
    try:
        return _LEGACY_RUN_HISTORICAL_BACKTEST(*args, **kwargs)
    finally:
        _score.invalidate_model_weight_cache()


def install() -> None:
    global _INSTALLED
    _core.run_historical_backtest = run_historical_backtest
    _INSTALLED = True


install()
