"""v51 report facade exposing market-data provenance columns.

The stable report implementation remains in ``report_core``. This wrapper
extends the flat public table with liquidity basis, market-cap normalization
provenance and the resolved per-security price-limit ratio without changing
candidate ranking semantics.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import numpy as np
import pandas as pd

import report_core as _core
from downloader import _MARKET_MANIFEST_PATH, normalize_ticker
from report_core import *  # noqa: F403
from tradeability import fallback_daily_limit_pct

_legacy_results_to_dataframe = _core._results_to_dataframe


def _detail(result: Any, key: str, default: Any = None) -> Any:
    details = getattr(result, "filter_details", {})
    return details.get(key, default) if isinstance(details, dict) else default


def _market_manifest() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(_MARKET_MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): value
        for key, value in raw.items()
        if isinstance(value, dict)
    }


def _manifest_value(
    manifest: dict[str, dict[str, Any]],
    ticker: str,
    key: str,
    default: Any = None,
) -> Any:
    row = manifest.get(normalize_ticker(ticker), {})
    return row.get(key, default) if isinstance(row, dict) else default


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

    manifest = _market_manifest()
    by_ticker = {
        str(getattr(result, "ticker", "") or ""): result
        for result in results
        if str(getattr(result, "ticker", "") or "")
    }
    mapped = [by_ticker.get(str(ticker)) for ticker in frame["Ticker"].astype(str)]
    tickers = frame["Ticker"].astype(str).tolist()

    frame["MarketCap"] = [
        _detail(result, "market_cap") if result is not None else None
        for result in mapped
    ]
    frame["MarketCapAvailable"] = [
        bool(_detail(result, "market_cap_available", False)) if result is not None else False
        for result in mapped
    ]
    frame["MarketCapUnitInferred"] = [
        bool(
            _detail(
                result,
                "market_cap_unit_inferred",
                _manifest_value(manifest, ticker, "share_unit_inference_used", False),
            )
        )
        if result is not None
        else bool(_manifest_value(manifest, ticker, "share_unit_inference_used", False))
        for ticker, result in zip(tickers, mapped)
    ]
    frame["MarketCapUnitAssumption"] = [
        str(
            _detail(
                result,
                "market_cap_unit_assumption",
                _manifest_value(manifest, ticker, "share_unit_assumption", "unavailable"),
            )
        )
        if result is not None
        else str(_manifest_value(manifest, ticker, "share_unit_assumption", "unavailable"))
        for ticker, result in zip(tickers, mapped)
    ]
    frame["MarketCapRawTotalShares"] = [
        _detail(
            result,
            "market_cap_raw_total_shares",
            _manifest_value(manifest, ticker, "total_shares_raw"),
        )
        if result is not None
        else _manifest_value(manifest, ticker, "total_shares_raw")
        for ticker, result in zip(tickers, mapped)
    ]
    frame["MarketCapNormalizedTotalShares"] = [
        _detail(
            result,
            "market_cap_normalized_total_shares",
            _manifest_value(manifest, ticker, "total_shares_normalized"),
        )
        if result is not None
        else _manifest_value(manifest, ticker, "total_shares_normalized")
        for ticker, result in zip(tickers, mapped)
    ]
    frame["MarketCapSanityPassed"] = [
        bool(
            _detail(
                result,
                "market_cap_sanity_passed",
                _manifest_value(manifest, ticker, "market_cap_sanity_passed", False),
            )
        )
        if result is not None
        else bool(_manifest_value(manifest, ticker, "market_cap_sanity_passed", False))
        for ticker, result in zip(tickers, mapped)
    ]
    frame["LiquidityBasis"] = [
        str(
            _detail(
                result,
                "liquidity_basis",
                _manifest_value(manifest, ticker, "liquidity_basis", "shares_fallback"),
            )
        )
        if result is not None
        else str(_manifest_value(manifest, ticker, "liquidity_basis", "shares_fallback"))
        for ticker, result in zip(tickers, mapped)
    ]
    frame["MedianTurnover60"] = [
        _detail(
            result,
            "median_turnover_60",
            _manifest_value(manifest, ticker, "median_turnover_60"),
        )
        if result is not None
        else _manifest_value(manifest, ticker, "median_turnover_60")
        for ticker, result in zip(tickers, mapped)
    ]
    frame["TurnoverObservations"] = [
        _detail(
            result,
            "turnover_observations",
            _manifest_value(manifest, ticker, "turnover_observations", 0),
        )
        if result is not None
        else _manifest_value(manifest, ticker, "turnover_observations", 0)
        for ticker, result in zip(tickers, mapped)
    ]

    limit_values: list[float] = []
    for ticker, result in zip(tickers, mapped):
        explicit = (
            _detail(
                result,
                "price_limit_pct",
                _manifest_value(manifest, ticker, "price_limit_pct"),
            )
            if result is not None
            else _manifest_value(manifest, ticker, "price_limit_pct")
        )
        try:
            explicit_value = float(explicit)
        except (TypeError, ValueError):
            explicit_value = np.nan
        if np.isfinite(explicit_value) and 0.02 <= explicit_value <= 0.40:
            limit_values.append(explicit_value)
            continue
        limit_values.append(
            fallback_daily_limit_pct(
                ticker,
                is_etf=bool(getattr(result, "is_etf", False)) if result is not None else False,
            )
        )
    frame["PriceLimitPct"] = limit_values
    return frame


_core._results_to_dataframe = _results_to_dataframe
sys.modules[__name__] = _core
