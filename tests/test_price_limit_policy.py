from __future__ import annotations

import numpy as np
import pandas as pd

from institution_scanner.price_limit_policy import (
    price_limit_vector,
    resolve_price_limit,
)


def test_chinext_historical_rule_matches_scalar_and_vector_paths() -> None:
    dates = np.array(
        [np.datetime64("2020-08-21"), np.datetime64("2020-08-24")],
        dtype="datetime64[ns]",
    )
    vector = price_limit_vector("300001.SZ", dates, is_etf=False)

    assert resolve_price_limit(
        "300001.SZ", is_etf=False, trade_date=pd.Timestamp("2020-08-21")
    ).pct == 0.10
    assert resolve_price_limit(
        "300001.SZ", is_etf=False, trade_date=pd.Timestamp("2020-08-24")
    ).pct == 0.20
    assert vector.tolist() == [0.10, 0.20]


def test_exchange_fallbacks_are_centralized() -> None:
    assert resolve_price_limit("688001.SH").pct == 0.20
    assert resolve_price_limit("920001.BJ").pct == 0.30
    assert resolve_price_limit("600000.SH").pct == 0.10
    assert resolve_price_limit("588000.SH", is_etf=True).pct == 0.20


def test_metadata_override_keeps_historical_chinext_guard() -> None:
    resolution = resolve_price_limit(
        "300001.SZ",
        trade_date="2020-08-21",
        metadata_pct=0.20,
        metadata_source="security_metadata_rule",
    )
    assert resolution.pct == 0.10
    assert resolution.source == "chinext_pre_2020_10pct_rule"
