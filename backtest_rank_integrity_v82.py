"""v82 backtest ranking semantic-integrity helpers.

The live scan builds ``InstitutionalScore`` without signal-recency decay and the
canonical lifecycle ranker applies recency exactly once.  The historical
post-processing path rebuilds ``InstitutionalScore`` after calibration and, in
legacy code, embeds the same recency multiplier before calling the lifecycle
ranker.  That makes a post-backtest result decay recency twice.

This module normalizes that embedded multiplier only for the single canonical
backtest finalization call.  It does not change component weights, entry
thresholds, lifecycle decisions, or the live-scan score formula.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator

import numpy as np
import pandas as pd

BACKTEST_RECENCY_NORMALIZATION_VERSION = "2026-08-21-v82-single-recency-ranking-v1"


def _number(
    values: pd.Series | Any,
    index: pd.Index,
    default: float,
) -> pd.Series:
    if not isinstance(values, pd.Series):
        values = pd.Series(values, index=index)
    return (
        pd.to_numeric(values, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .fillna(default)
    )


def strip_embedded_backtest_recency(frame: pd.DataFrame) -> pd.DataFrame:
    """Remove the legacy pre-ranking recency multiplier exactly once.

    ``SignalRecencyFactor`` is bounded to the same [0.7, 1.0] domain consumed by
    the lifecycle engine.  Dividing by ``0.8 + 0.2 * factor`` restores the
    recency-neutral score anchor; the lifecycle ranker then applies the one
    intended recency decay.
    """
    result = frame.copy()
    factor = _number(
        result.get("SignalRecencyFactor", pd.Series(1.0, index=result.index)),
        result.index,
        1.0,
    ).clip(0.7, 1.0)
    multiplier = (0.8 + 0.2 * factor).clip(lower=1e-9, upper=1.0)

    applied = pd.Series(False, index=result.index, dtype=bool)
    for column in ("InstitutionalScore", "TechnicalInstitutionalScore"):
        if column not in result.columns:
            continue
        numeric = pd.to_numeric(result[column], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
        valid = numeric.notna()
        result.loc[valid, column] = (
            numeric.loc[valid] / multiplier.loc[valid]
        ).round(4)
        applied |= valid & multiplier.lt(1.0 - 1e-12)

    result["BacktestRecencyNormalizationFactor"] = multiplier.round(4)
    result["BacktestRecencyNormalizationApplied"] = applied
    result["BacktestRecencyNormalizationVersion"] = (
        BACKTEST_RECENCY_NORMALIZATION_VERSION
    )
    return result


@contextmanager
def single_recency_ranking_patch(module: Any) -> Iterator[None]:
    """Patch one analytics module's lifecycle call for a backtest transaction.

    The wrapper is deliberately scoped by a context manager so the public live
    scanner retains its normal lifecycle implementation before and after a
    backtest publication.
    """
    original: Callable[[pd.DataFrame], pd.DataFrame] = module.finalize_signal_ranking

    def finalize_once(frame: pd.DataFrame) -> pd.DataFrame:
        return original(strip_embedded_backtest_recency(frame))

    module.finalize_signal_ranking = finalize_once
    try:
        yield
    finally:
        module.finalize_signal_ranking = original
