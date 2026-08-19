"""v69 compute-cache integrity facade.

v52 isolated the canonical share-volume namespace. v62 added deterministic full
OHLCV fingerprints so older provider revisions invalidate derived caches. v69
closes the final same-length edge: content fingerprints are checked even when
the source file size/mtime signature did not change. Pure appended histories
still use the existing prefix-verified incremental path.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

import performance_cache_core as _core
from performance_cache_core import *  # noqa: F403

_LEGACY_LOAD_OR_COMPUTE_INDICATORS = _core.load_or_compute_indicators
_LEGACY_MARKET_CACHE_STATE = _core.market_cache_state
_LEGACY_MARKET_PREFIX_MATCHES = _core.market_prefix_matches

MARKET_DATA_CACHE_NAMESPACE = "volume-shares-history-fingerprint-v2"
INDICATOR_CACHE_VERSION = "v7"
BACKTEST_CACHE_VERSION = "v10"
INDICATOR_CACHE_DIR = _core.CACHE_DIR / (
    f"_indicators_{INDICATOR_CACHE_VERSION}_{MARKET_DATA_CACHE_NAMESPACE}"
)
BACKTEST_CACHE_DIR = _core.CACHE_DIR / (
    f"_backtest_{BACKTEST_CACHE_VERSION}_{MARKET_DATA_CACHE_NAMESPACE}"
)


def market_history_fingerprint(
    df: pd.DataFrame,
    *,
    end_date: str | pd.Timestamp | None = None,
) -> str:
    """Hash every available market row, not only the recent tail."""
    if df is None or df.empty:
        return ""
    frame = df.copy(deep=False)
    frame.index = _core._normalized_index(frame)
    frame = frame.loc[~frame.index.isna()].sort_index()
    if end_date is not None:
        try:
            cutoff = pd.Timestamp(end_date)
        except (TypeError, ValueError):
            return ""
        frame = frame.loc[frame.index <= cutoff]
    columns = [column for column in _core._MARKET_COLUMNS if column in frame.columns]
    if frame.empty or not columns:
        return ""
    numeric = frame.loc[:, columns].apply(pd.to_numeric, errors="coerce")
    row_hashes = pd.util.hash_pandas_object(numeric, index=True).to_numpy(
        dtype=np.uint64,
        copy=False,
    )
    payload = b"|".join(
        [",".join(columns).encode("utf-8"), row_hashes.tobytes()]
    )
    return hashlib.sha256(payload).hexdigest()[:32]


def market_cache_state(df: pd.DataFrame) -> dict[str, Any]:
    state = dict(_LEGACY_MARKET_CACHE_STATE(df))
    state["history_fingerprint"] = market_history_fingerprint(df)
    return state


def market_prefix_matches(df: pd.DataFrame, state: dict[str, Any] | None) -> bool:
    """Require the full cached market prefix to be unchanged before appending."""
    if df is None or df.empty or not state:
        return False
    expected_history = str(state.get("history_fingerprint", "") or "").strip()
    if not expected_history:
        return _LEGACY_MARKET_PREFIX_MATCHES(df, state)
    last_text = str(state.get("last", "") or "").strip()
    if not last_text:
        return False
    try:
        last_date = pd.Timestamp(last_text)
    except (TypeError, ValueError):
        return False
    index = _core._normalized_index(df)
    if last_date not in index:
        return False
    current_history = market_history_fingerprint(df, end_date=last_date)
    return bool(current_history and current_history == expected_history)


def _write_indicator_cache(
    data_path: Path,
    meta_path: Path,
    enriched: pd.DataFrame,
    source: pd.DataFrame,
    signature: str,
) -> None:
    try:
        _core._atomic_parquet(data_path, enriched)
        _core._atomic_json(
            meta_path,
            {
                "version": _core.INDICATOR_CACHE_VERSION,
                "scoring_version": _core.SCORING_VERSION,
                "signature": signature,
                **market_cache_state(source),
            },
        )
    except (OSError, ValueError, TypeError, ImportError):
        pass


def load_or_compute_indicators(
    ticker: str,
    frame: pd.DataFrame,
    compute_fn: Callable[[pd.DataFrame], pd.DataFrame],
    *,
    source_path: Path | None = None,
    enabled: bool = True,
) -> tuple[pd.DataFrame, bool]:
    """Reuse exact data only when its full OHLCV content still matches."""
    if frame is None or frame.empty:
        return frame, False
    if not enabled or source_path is None or not source_path.exists():
        return compute_fn(frame.copy()), False

    source = frame.copy(deep=False)
    source.index = _core._normalized_index(source)
    source = source.loc[~source.index.isna()].sort_index()
    signature = _core.data_signature(source, source_path)
    stem = _core._safe_stem(ticker)
    data_path = _core.INDICATOR_CACHE_DIR / f"{stem}.parquet"
    meta_path = _core.INDICATOR_CACHE_DIR / f"{stem}.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        meta = {}

    if (
        data_path.exists()
        and meta.get("version") == _core.INDICATOR_CACHE_VERSION
        and meta.get("scoring_version") == _core.SCORING_VERSION
    ):
        cached = _core._read_indicator_cache(data_path)
        if cached is not None and len(cached) == len(source):
            expected_history = str(meta.get("history_fingerprint", "") or "")
            current_history = market_history_fingerprint(source)
            cached_history = market_history_fingerprint(cached)
            content_mismatch = (
                not expected_history
                or not current_history
                or current_history != expected_history
                or cached_history != current_history
            )
            if content_mismatch:
                enriched = compute_fn(source.copy())
                _write_indicator_cache(
                    data_path,
                    meta_path,
                    enriched,
                    source,
                    signature,
                )
                return enriched, False

    # The core path handles exact signature hits and appended histories. Its
    # market_prefix_matches hook now performs a full-prefix content check before
    # any incremental indicator/backtest reuse.
    return _LEGACY_LOAD_OR_COMPUTE_INDICATORS(
        ticker,
        frame,
        compute_fn,
        source_path=source_path,
        enabled=enabled,
    )


_core.MARKET_DATA_CACHE_NAMESPACE = MARKET_DATA_CACHE_NAMESPACE
_core.INDICATOR_CACHE_VERSION = INDICATOR_CACHE_VERSION
_core.BACKTEST_CACHE_VERSION = BACKTEST_CACHE_VERSION
_core.INDICATOR_CACHE_DIR = INDICATOR_CACHE_DIR
_core.BACKTEST_CACHE_DIR = BACKTEST_CACHE_DIR
_core.market_history_fingerprint = market_history_fingerprint
_core.market_cache_state = market_cache_state
_core.market_prefix_matches = market_prefix_matches
_core.load_or_compute_indicators = load_or_compute_indicators

sys.modules[__name__] = _core
