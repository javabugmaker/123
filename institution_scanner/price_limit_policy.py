"""Canonical A-share/ETF daily price-limit policy.

Scalar live checks and vectorized historical backtests must consume the same
exchange-rule implementation. Provider metadata may override the fallback only
when it is an explicit ratio with auditable provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

import numpy as np
import pandas as pd

CHINEXT_20_START: Final[pd.Timestamp] = pd.Timestamp("2020-08-24")
LIMIT_TOLERANCE: Final[float] = 0.0025


@dataclass(frozen=True)
class PriceLimitResolution:
    pct: float
    source: str


def trade_timestamp(value: Any) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(timestamp):
        return None
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_localize(None)
    return timestamp.normalize()


def fallback_resolution(
    ticker: str,
    *,
    is_etf: bool = False,
    trade_date: str | date | datetime | pd.Timestamp | None = None,
) -> PriceLimitResolution:
    symbol = str(ticker or "").strip().upper()
    code = symbol.split(".", 1)[0]
    suffix = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
    when = trade_timestamp(trade_date)

    if suffix == "BJ":
        return PriceLimitResolution(0.30, "beijing_30pct_rule")
    if not is_etf and code.startswith(("300", "301")):
        if when is not None and when < CHINEXT_20_START:
            return PriceLimitResolution(0.10, "chinext_pre_2020_10pct_rule")
        return PriceLimitResolution(0.20, "chinext_20pct_rule")
    if not is_etf and code.startswith(("688", "689")):
        return PriceLimitResolution(0.20, "star_20pct_rule")
    if is_etf and code.startswith(("588", "589")):
        return PriceLimitResolution(0.20, "star_etf_20pct_rule")
    return PriceLimitResolution(0.10, "standard_10pct_rule")


def resolve_price_limit(
    ticker: str,
    *,
    is_etf: bool = False,
    trade_date: str | date | datetime | pd.Timestamp | None = None,
    metadata_pct: float | None = None,
    metadata_source: str = "",
) -> PriceLimitResolution:
    try:
        override = float(metadata_pct) if metadata_pct is not None else np.nan
    except (TypeError, ValueError):
        override = np.nan
    source = str(metadata_source or "")
    when = trade_timestamp(trade_date)

    if np.isfinite(override) and 0.02 <= override <= 0.40:
        symbol = str(ticker or "").strip().upper()
        code = symbol.split(".", 1)[0]
        chinext_related = (
            (not is_etf and code.startswith(("300", "301")))
            or source == "chinext_related_etf_20pct_rule"
        )
        if chinext_related and when is not None and when < CHINEXT_20_START:
            return PriceLimitResolution(0.10, "chinext_pre_2020_10pct_rule")
        return PriceLimitResolution(
            float(override), source or "security_metadata_rule"
        )
    return fallback_resolution(
        ticker,
        is_etf=is_etf,
        trade_date=trade_date,
    )


def price_limit_vector(
    ticker: str,
    dates: np.ndarray,
    *,
    is_etf: bool = False,
    metadata_pct: float | None = None,
    metadata_source: str = "",
) -> np.ndarray:
    """Vectorized limits with identical historical semantics to scalar resolution."""
    values = np.asarray(dates, dtype="datetime64[ns]")
    n = len(values)
    symbol = str(ticker or "").strip().upper()
    code = symbol.split(".", 1)[0]
    suffix = symbol.rsplit(".", 1)[-1] if "." in symbol else ""
    try:
        override = float(metadata_pct) if metadata_pct is not None else np.nan
    except (TypeError, ValueError):
        override = np.nan
    source = str(metadata_source or "")
    pre_chinext = (~np.isnat(values)) & (
        values < np.datetime64("2020-08-24", "ns")
    )

    if np.isfinite(override) and 0.02 <= override <= 0.40:
        limits = np.full(n, override, dtype=np.float64)
        chinext_related = (
            (not is_etf and code.startswith(("300", "301")))
            or source == "chinext_related_etf_20pct_rule"
        )
        if chinext_related:
            limits[pre_chinext] = 0.10
        return limits
    if suffix == "BJ":
        return np.full(n, 0.30, dtype=np.float64)
    if not is_etf and code.startswith(("300", "301")):
        limits = np.full(n, 0.20, dtype=np.float64)
        limits[pre_chinext] = 0.10
        return limits
    if not is_etf and code.startswith(("688", "689")):
        return np.full(n, 0.20, dtype=np.float64)
    if is_etf and code.startswith(("588", "589")):
        return np.full(n, 0.20, dtype=np.float64)
    return np.full(n, 0.10, dtype=np.float64)
