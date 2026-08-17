"""v51 scanner facade adding data-evidence provenance to ScanResult.

The orchestration implementation remains in ``scanner_core``. This wrapper
keeps the existing dataclass/output contract while attaching liquidity,
market-cap normalization and price-limit evidence to ``filter_details`` for
downstream export.
"""

from __future__ import annotations

import sys
from typing import Any

import numpy as np
import pandas as pd

import scanner_core as _core
from downloader import get_market_cap_evidence
from filters import filter_min_volume
from scanner_core import *  # noqa: F403
from tradeability import daily_limit_pct

_legacy_scan_single_from_df = _core.scan_single_from_df


def _attach_data_evidence(
    result: Any,
    ticker_info: Any,
    frame: pd.DataFrame | None,
) -> None:
    if frame is None or frame.empty or getattr(result, "error", ""):
        return
    details = getattr(result, "filter_details", None)
    if not isinstance(details, dict):
        details = {}
        result.filter_details = details

    if "Volume" in frame.columns:
        try:
            liquidity = filter_min_volume(frame)
        except (KeyError, TypeError, ValueError):
            liquidity = None
        if liquidity is not None:
            for key in (
                "liquidity_basis",
                "median_turnover_60",
                "turnover_observations",
                "avg_volume_60",
            ):
                if key in liquidity.details:
                    details[key] = liquidity.details[key]

    details["price_limit_pct"] = daily_limit_pct(
        str(getattr(ticker_info, "ticker", "")),
        is_etf=bool(getattr(ticker_info, "is_etf", False)),
    )

    if bool(getattr(ticker_info, "is_etf", False)):
        details["market_cap_unit_assumption"] = "not_applicable_etf"
        details["market_cap_unit_inferred"] = False
        return

    evidence = get_market_cap_evidence(
        str(getattr(ticker_info, "ticker", "")),
        frame=frame,
        fetch=False,
    )
    if evidence.get("normalized_total_shares") is None:
        normalized = getattr(ticker_info, "total_shares", None)
        try:
            normalized_value = float(normalized)
        except (TypeError, ValueError):
            normalized_value = np.nan
        if np.isfinite(normalized_value) and normalized_value > 0:
            evidence["normalized_total_shares"] = normalized_value
            evidence["unit_assumption"] = "ticker_info_normalized"
            evidence["sanity_passed"] = True

    details["market_cap_unit_inferred"] = bool(
        evidence.get("unit_inference_used", False)
    )
    details["market_cap_unit_assumption"] = str(
        evidence.get("unit_assumption", "unavailable")
    )
    details["market_cap_raw_total_shares"] = evidence.get("raw_total_shares")
    details["market_cap_normalized_total_shares"] = evidence.get(
        "normalized_total_shares"
    )
    details["market_cap_sanity_passed"] = bool(
        evidence.get("market_cap_sanity_passed", evidence.get("sanity_passed", False))
    )


def scan_single_from_df(
    ticker_info: _core.TickerInfo,
    df: pd.DataFrame | None,
    indicators_computed: bool = False,
) -> _core.ScanResult:
    result = _legacy_scan_single_from_df(
        ticker_info,
        df,
        indicators_computed=indicators_computed,
    )
    _attach_data_evidence(result, ticker_info, df)
    return result


_core.scan_single_from_df = scan_single_from_df
sys.modules[__name__] = _core
