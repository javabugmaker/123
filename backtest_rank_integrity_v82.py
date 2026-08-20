"""v82 ranking semantic-integrity helpers.

The live scan builds ``InstitutionalScore`` without signal-recency decay and the
canonical lifecycle ranker applies recency exactly once. The historical
post-processing path rebuilds ``InstitutionalScore`` after calibration and, in
legacy code, embeds the same recency multiplier before calling the lifecycle
ranker. That makes a post-backtest result decay recency twice.

A permanently installed guard keeps the analytics lifecycle entry point stable.
A ``ContextVar`` activates recency normalization only inside the current
backtest publication context, so unrelated threads/tasks retain ordinary live-
scan semantics. The same stable guard stamps observational ranking-time decision
provenance after the one canonical ranking pass. No model weight, threshold,
decision rule, or public scanner formula is changed.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Callable, Iterator

import numpy as np
import pandas as pd

from ranking_provenance_v82 import stamp_ranking_decision_provenance

BACKTEST_RECENCY_NORMALIZATION_VERSION = "2026-08-21-v82-single-recency-ranking-v1"
_RECENCY_NORMALIZATION_ACTIVE: ContextVar[bool] = ContextVar(
    "institution_scanner_v82_backtest_recency_normalization",
    default=False,
)
_GUARD_INSTALLED_ATTR = "_v82_single_recency_guard_installed"
_GUARD_ORIGINAL_ATTR = "_v82_single_recency_guard_original"


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
    the lifecycle engine. Dividing by ``0.8 + 0.2 * factor`` restores the
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


def install_single_recency_ranking_guard(module: Any) -> None:
    """Install one stable context-aware integrity guard on an analytics module.

    Installation is idempotent. Recency normalization is active only inside
    ``single_recency_ranking_context``. Ranking-time decision provenance is
    always stamped on the returned canonical result and is observational only.
    """
    if bool(getattr(module, _GUARD_INSTALLED_ATTR, False)):
        return
    original: Callable[[pd.DataFrame], pd.DataFrame] = module.finalize_signal_ranking

    def guarded_finalize(frame: pd.DataFrame) -> pd.DataFrame:
        candidate = (
            strip_embedded_backtest_recency(frame)
            if _RECENCY_NORMALIZATION_ACTIVE.get()
            else frame
        )
        result = original(candidate)
        return stamp_ranking_decision_provenance(result)

    setattr(module, _GUARD_ORIGINAL_ATTR, original)
    module.finalize_signal_ranking = guarded_finalize
    setattr(module, _GUARD_INSTALLED_ATTR, True)


@contextmanager
def single_recency_ranking_context() -> Iterator[None]:
    """Activate one-recency semantics only for the current execution context."""
    token = _RECENCY_NORMALIZATION_ACTIVE.set(True)
    try:
        yield
    finally:
        _RECENCY_NORMALIZATION_ACTIVE.reset(token)
