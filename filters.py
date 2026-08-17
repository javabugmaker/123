"""v51 screening facade: turnover liquidity + shared volatility state.

The prior implementation is retained in ``filters_core``.  This facade only
changes the two screening semantics audited in v51, then patches the core
module so existing imports and ``run_all_filters`` keep one implementation.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import filters_core as _core
from config import (
    MIN_VOLUME,
    VOLUME_MIN_MEDIAN_TURNOVER_60D,
    VOLUME_TURNOVER_MIN_OBSERVATIONS,
)
from filters_core import *  # noqa: F403
from volatility_state import evaluate_volatility_contraction

_legacy_filter_min_volume = _core.filter_min_volume


def filter_min_volume(df: pd.DataFrame):
    """Use 60d median CNY turnover as the primary liquidity gate.

    Old caches without ``Amount`` retain the existing share-volume rule so the
    migration is backwards compatible and auditable through ``liquidity_basis``.
    """
    if "Amount" in df.columns:
        amount = pd.to_numeric(df["Amount"], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
        recent = amount.iloc[-60:].dropna()
        minimum = max(1, int(VOLUME_TURNOVER_MIN_OBSERVATIONS))
        if len(recent) >= minimum:
            median_turnover = float(recent.median())
            passed = bool(
                np.isfinite(median_turnover)
                and median_turnover >= float(VOLUME_MIN_MEDIAN_TURNOVER_60D)
            )
            return _core.FilterResult(
                passed=passed,
                reason=(
                    f"60日成交额中位数 {median_turnover:,.0f} 元 "
                    f"{'>=' if passed else '<'} 最低 {VOLUME_MIN_MEDIAN_TURNOVER_60D:,.0f} 元"
                ),
                details={
                    "liquidity_basis": "turnover_cny",
                    "median_turnover_60": median_turnover,
                    "turnover_observations": len(recent),
                    "fallback_min_volume_shares": int(MIN_VOLUME),
                },
            )

    legacy = _legacy_filter_min_volume(df)
    legacy.details["liquidity_basis"] = "shares_fallback"
    legacy.details["turnover_observations"] = 0
    return legacy


def filter_volatility_contraction(df: pd.DataFrame):
    """Use the same robust squeeze state consumed by the scoring model."""
    state = evaluate_volatility_contraction(df)
    details = state.details()
    if state.available_components <= 0:
        return _core.FilterResult(
            passed=False,
            reason="波动收缩数据不足",
            details=details,
        )
    return _core.FilterResult(
        passed=state.passed,
        reason=(
            "Volatility contraction: "
            f"ATR={state.atr_contracting}, BB={state.bb_contracting}, "
            f"HV={state.hv_contracting}"
        ),
        details=details,
    )


_core.filter_min_volume = filter_min_volume
_core.filter_volatility_contraction = filter_volatility_contraction

sys.modules[__name__] = _core
