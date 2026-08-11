"""v36 TickFlow market-data normalization facade.

The stable v35 downloader implementation lives in ``downloader_core``.  This
module normalizes TickFlow CN daily ``Volume`` into individual shares before
market data enters the cache/indicator pipeline.  TickFlow payloads historically
behave like board-lot volume for CN securities, while the scanner's absolute
liquidity thresholds are expressed in shares.
"""

from __future__ import annotations

import sys
from typing import Any

import numpy as np
import pandas as pd

import downloader_core as _core
from downloader_core import *  # noqa: F403

_TICKFLOW_CN_LOT_SIZE = 100.0
_VOLUME_INFERENCE_ROWS = 60
_PRICE_CACHE_SCHEMA_VERSION = "v4-tickflow-forward-volume-shares"
_PRICE_CACHE_DIR = _core.CACHE_DIR / _PRICE_CACHE_SCHEMA_VERSION
_MARKET_MANIFEST_PATH = _PRICE_CACHE_DIR / "_manifest.json"

_legacy_normalize_tickflow_frame = _core._normalize_tickflow_frame
_legacy_record_market_manifest = _core._record_market_manifest


def _infer_tickflow_volume_scale(frame: pd.DataFrame) -> float:
    """Infer whether TickFlow volume is already shares or still board lots.

    ``Amount / (Close * Volume)`` is approximately 1 when volume is shares and
    approximately 100 when volume is CN board lots.  Use recent observations so
    forward-adjusted historical prices do not dominate the inference.  If
    turnover is unavailable, prefer the documented/provider-compatible CN lot
    convention and convert by 100.
    """
    required = {"Close", "Volume", "Amount"}
    if not required.issubset(frame.columns):
        return _TICKFLOW_CN_LOT_SIZE

    close = pd.to_numeric(frame["Close"], errors="coerce")
    volume = pd.to_numeric(frame["Volume"], errors="coerce")
    amount = pd.to_numeric(frame["Amount"], errors="coerce")
    denominator = close * volume
    ratio = (amount / denominator.replace(0.0, np.nan)).replace(
        [np.inf, -np.inf], np.nan
    )
    ratio = ratio.loc[(ratio > 0.0) & ratio.notna()].tail(_VOLUME_INFERENCE_ROWS)
    if ratio.empty:
        return _TICKFLOW_CN_LOT_SIZE

    median_ratio = float(ratio.median())
    # The geometric midpoint between 1 and 100 is 10.  This is intentionally
    # tolerant of corporate-action adjustment factors and daily VWAP/close gaps.
    scale = _TICKFLOW_CN_LOT_SIZE if median_ratio >= 10.0 else 1.0
    if not (0.25 <= median_ratio <= 4.0 or 20.0 <= median_ratio <= 400.0):
        _core.logger.debug(
            "TickFlow volume-unit inference is ambiguous: median turnover ratio %.4f; scale=%s",
            median_ratio,
            scale,
        )
    return scale


def _normalize_tickflow_frame(frame: Any) -> pd.DataFrame | None:
    """Return canonical TickFlow OHLCV with ``Volume`` in individual shares."""
    normalized = _legacy_normalize_tickflow_frame(frame)
    if normalized is None or normalized.empty:
        return normalized

    scale = _infer_tickflow_volume_scale(normalized)
    canonical = normalized.copy()
    canonical["Volume"] = pd.to_numeric(canonical["Volume"], errors="coerce") * scale
    return _core._validate_ohlcv(canonical)


def _record_market_manifest(ticker: str, df: pd.DataFrame) -> None:
    _legacy_record_market_manifest(ticker, df)
    key = _core.normalize_ticker(ticker)
    metadata = _core._MARKET_MANIFEST_DIRTY.get(key)
    if isinstance(metadata, dict):
        metadata["volume_unit"] = "shares"
        metadata["volume_schema"] = _PRICE_CACHE_SCHEMA_VERSION


_core._PRICE_CACHE_SCHEMA_VERSION = _PRICE_CACHE_SCHEMA_VERSION
_core._PRICE_CACHE_DIR = _PRICE_CACHE_DIR
_core._MARKET_MANIFEST_PATH = _MARKET_MANIFEST_PATH
_core._TICKFLOW_CN_LOT_SIZE = _TICKFLOW_CN_LOT_SIZE
_core._infer_tickflow_volume_scale = _infer_tickflow_volume_scale
_core._normalize_tickflow_frame = _normalize_tickflow_frame
_core._record_market_manifest = _record_market_manifest
_core.TICKFLOW_CANONICAL_VOLUME_UNIT = "shares"

sys.modules[__name__] = _core
