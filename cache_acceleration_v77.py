"""v77 market-cache hashing acceleration with identical fingerprint semantics."""

from __future__ import annotations

import hashlib
import sys
from typing import Any

import numpy as np
import pandas as pd
from pandas.api.types import is_numeric_dtype

import performance_cache as _cache

# performance_cache replaces its public module object with performance_cache_core.
# Capture the already-installed v69 prefix verifier directly; private facade
# locals are intentionally not present on the aliased core module.
_LEGACY_MARKET_PREFIX_MATCHES = _cache.market_prefix_matches
_INSTALLED = False


def market_history_fingerprint(
    df: pd.DataFrame,
    *,
    end_date: str | pd.Timestamp | None = None,
) -> str:
    if df is None or df.empty:
        return ""
    frame = df.copy(deep=False)
    frame.index = _cache._normalized_index(frame)
    frame = frame.loc[~frame.index.isna()].sort_index()
    if end_date is not None:
        try:
            cutoff = pd.Timestamp(end_date)
        except (TypeError, ValueError):
            return ""
        frame = frame.loc[frame.index <= cutoff]
    columns = [column for column in _cache._MARKET_COLUMNS if column in frame.columns]
    if frame.empty or not columns:
        return ""

    selected = frame.loc[:, columns]
    if all(is_numeric_dtype(selected[column].dtype) for column in columns):
        numeric = selected
    else:
        numeric = selected.apply(pd.to_numeric, errors="coerce")
    row_hashes = pd.util.hash_pandas_object(numeric, index=True).to_numpy(
        dtype=np.uint64,
        copy=False,
    )
    payload = b"|".join(
        [",".join(columns).encode("utf-8"), row_hashes.tobytes()]
    )
    return hashlib.sha256(payload).hexdigest()[:32]


def market_cache_state(df: pd.DataFrame) -> dict[str, Any]:
    identity = _cache._frame_identity(df)
    return {
        "rows": int(identity.get("rows", 0)),
        "first": str(identity.get("first", "")),
        "last": str(identity.get("last", "")),
        "tail_fingerprint": _cache.market_tail_fingerprint(df),
        "history_fingerprint": market_history_fingerprint(df),
    }


def market_prefix_matches(df: pd.DataFrame, state: dict[str, Any] | None) -> bool:
    if df is None or df.empty or not state:
        return False
    expected_history = str(state.get("history_fingerprint", "") or "").strip()
    if not expected_history:
        return bool(_LEGACY_MARKET_PREFIX_MATCHES(df, state))
    last_text = str(state.get("last", "") or "").strip()
    if not last_text:
        return False
    try:
        last_date = pd.Timestamp(last_text)
    except (TypeError, ValueError):
        return False
    index = _cache._normalized_index(df)
    if last_date not in index:
        return False
    current_history = market_history_fingerprint(df, end_date=last_date)
    return bool(current_history and current_history == expected_history)


def install() -> None:
    global _INSTALLED
    # Re-asserting is cheap and makes the runtime robust to tests/older facades
    # rebinding these hooks after initial module import.
    _cache.market_history_fingerprint = market_history_fingerprint
    _cache.market_cache_state = market_cache_state
    _cache.market_prefix_matches = market_prefix_matches

    analytics_core = sys.modules.get("analytics_core")
    if analytics_core is not None:
        setattr(analytics_core, "market_cache_state", market_cache_state)
        setattr(analytics_core, "market_prefix_matches", market_prefix_matches)
    _INSTALLED = True


install()
