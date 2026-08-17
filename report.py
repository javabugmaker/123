"""v52 report provenance facade.

The v51 report contract remains in ``report_v51``.  v52 corrects two fields
that real output showed could be misleading: ETF share counts are no longer
presented as stock market-cap provenance, and every exported price-limit ratio
is recomputed from the current audited rule engine rather than trusting stale
manifest values.
"""

from __future__ import annotations

import sys
from typing import Any

import pandas as pd

import report_v51 as _core
from report_v51 import *  # noqa: F403
from tradeability import daily_limit_pct, price_limit_source

_legacy_results_to_dataframe = _core._results_to_dataframe


def _results_to_dataframe(results: list[Any]) -> pd.DataFrame:
    frame = _legacy_results_to_dataframe(results)
    if frame.empty:
        if "MarketCapApplicable" not in frame.columns:
            frame["MarketCapApplicable"] = pd.Series(dtype=bool)
        if "PriceLimitSource" not in frame.columns:
            frame["PriceLimitSource"] = pd.Series(dtype=object)
        return frame

    is_etf = (
        frame.get("IsETF", pd.Series(False, index=frame.index))
        .fillna(False)
        .astype(bool)
    )
    if "AssetType" in frame.columns:
        is_etf |= frame["AssetType"].fillna("").astype(str).str.lower().eq("etf")

    frame["MarketCapApplicable"] = ~is_etf
    etf_index = frame.index[is_etf]
    if len(etf_index):
        # Fund units are useful instrument metadata, but they are not the stock
        # share-capital evidence represented by MarketCap* fields.
        frame.loc[etf_index, "MarketCap"] = None
        if "MarketCapDataAvailable" in frame.columns:
            frame.loc[etf_index, "MarketCapDataAvailable"] = False
        if "MarketCapAvailable" in frame.columns:
            frame.loc[etf_index, "MarketCapAvailable"] = False
        if "MarketCapUnitInferred" in frame.columns:
            frame.loc[etf_index, "MarketCapUnitInferred"] = False
        if "MarketCapUnitAssumption" in frame.columns:
            frame.loc[etf_index, "MarketCapUnitAssumption"] = "not_applicable"
        if "MarketCapRawTotalShares" in frame.columns:
            frame.loc[etf_index, "MarketCapRawTotalShares"] = None
        if "MarketCapNormalizedTotalShares" in frame.columns:
            frame.loc[etf_index, "MarketCapNormalizedTotalShares"] = None
        if "MarketCapSanityPassed" in frame.columns:
            frame.loc[etf_index, "MarketCapSanityPassed"] = False

    limit_values: list[float] = []
    limit_sources: list[str] = []
    for index, ticker in frame["Ticker"].astype(str).items():
        etf = bool(is_etf.loc[index])
        limit_values.append(daily_limit_pct(ticker, is_etf=etf))
        limit_sources.append(price_limit_source(ticker, is_etf=etf))
    frame["PriceLimitPct"] = limit_values
    frame["PriceLimitSource"] = limit_sources
    return frame


_core._results_to_dataframe = _results_to_dataframe
sys.modules[__name__] = _core
