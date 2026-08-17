"""v51 report facade exposing market-data provenance columns.

The stable report implementation remains in ``report_core``. This wrapper
extends the flat public table with liquidity basis, market-cap normalization
provenance and the resolved per-security price-limit ratio without changing
candidate ranking semantics.
"""

from __future__ import annotations

import sys
from typing import Any

import numpy as np
import pandas as pd

import report_core as _core
from report_core import *  # noqa: F403
from tradeability import fallback_daily_limit_pct

_legacy_results_to_dataframe = _core._results_to_dataframe


def _detail(result: Any, key: str, default: Any = None) -> Any:
    details = getattr(result, "filter_details", {})
    return details.get(key, default) if isinstance(details, dict) else default


def _results_to_dataframe(results: list[Any]) -> pd.DataFrame:
    frame = _legacy_results_to_dataframe(results)
    provenance_columns = (
        "MarketCap",
        "MarketCapAvailable",
        "MarketCapUnitInferred",
        "MarketCapUnitAssumption",
        "MarketCapRawTotalShares",
        "MarketCapNormalizedTotalShares",
        "MarketCapSanityPassed",
        "LiquidityBasis",
        "MedianTurnover60",
        "TurnoverObservations",
        "PriceLimitPct",
    )
    if frame.empty:
        for column in provenance_columns:
            if column not in frame.columns:
                frame[column] = pd.Series(dtype=object)
        return frame

    by_ticker = {
        str(getattr(result, "ticker", "") or ""): result
        for result in results
        if str(getattr(result, "ticker", "") or "")
    }
    mapped = [by_ticker.get(str(ticker)) for ticker in frame["Ticker"].astype(str)]
    frame["MarketCap"] = [
        _detail(result, "market_cap") if result is not None else None
        for result in mapped
    ]
    frame["MarketCapAvailable"] = [
        bool(_detail(result, "market_cap_available", False)) if result is not None else False
        for result in mapped
    ]
    frame["MarketCapUnitInferred"] = [
        bool(_detail(result, "market_cap_unit_inferred", False)) if result is not None else False
        for result in mapped
    ]
    frame["MarketCapUnitAssumption"] = [
        str(_detail(result, "market_cap_unit_assumption", "unavailable"))
        if result is not None
        else "unavailable"
        for result in mapped
    ]
    frame["MarketCapRawTotalShares"] = [
        _detail(result, "market_cap_raw_total_shares") if result is not None else None
        for result in mapped
    ]
    frame["MarketCapNormalizedTotalShares"] = [
        _detail(result, "market_cap_normalized_total_shares") if result is not None else None
        for result in mapped
    ]
    frame["MarketCapSanityPassed"] = [
        bool(_detail(result, "market_cap_sanity_passed", False)) if result is not None else False
        for result in mapped
    ]
    frame["LiquidityBasis"] = [
        str(_detail(result, "liquidity_basis", "shares_fallback"))
        if result is not None
        else "shares_fallback"
        for result in mapped
    ]
    frame["MedianTurnover60"] = [
        _detail(result, "median_turnover_60") if result is not None else None
        for result in mapped
    ]
    frame["TurnoverObservations"] = [
        _detail(result, "turnover_observations", 0) if result is not None else 0
        for result in mapped
    ]
    limit_values: list[float] = []
    for result in mapped:
        if result is None:
            limit_values.append(np.nan)
            continue
        explicit = _detail(result, "price_limit_pct")
        try:
            explicit_value = float(explicit)
        except (TypeError, ValueError):
            explicit_value = np.nan
        if np.isfinite(explicit_value) and 0.02 <= explicit_value <= 0.40:
            limit_values.append(explicit_value)
            continue
        limit_values.append(
            fallback_daily_limit_pct(
                str(getattr(result, "ticker", "")),
                is_etf=bool(getattr(result, "is_etf", False)),
            )
        )
    frame["PriceLimitPct"] = limit_values
    return frame


_core._results_to_dataframe = _results_to_dataframe
sys.modules[__name__] = _core
