"""v51 TickFlow market-data normalization and metadata provenance facade.

The stable downloader implementation lives in ``downloader_core``.  This
module keeps canonical CN volume in individual shares, exposes audited market-
cap normalization evidence, and derives per-security price-limit ratios from
TickFlow ``limit_up``/``limit_down`` metadata when available.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

import numpy as np
import pandas as pd

import downloader_core as _core
from downloader_core import *  # noqa: F403

_TICKFLOW_CN_LOT_SIZE = 100.0
_VOLUME_INFERENCE_ROWS = 60
# v36 already isolated canonical share-volume caches.  v51 adds manifest-level
# provenance only, so keep that compatible cache directory instead of forcing a
# second full-market cache migration for unchanged OHLCV bytes.
_PRICE_CACHE_SCHEMA_VERSION = "v4-tickflow-forward-volume-shares"
_PRICE_CACHE_DIR = _core.CACHE_DIR / _PRICE_CACHE_SCHEMA_VERSION
_MARKET_MANIFEST_PATH = _PRICE_CACHE_DIR / "_manifest.json"
_SHARE_INFERENCE_THRESHOLD = 10_000_000.0
_SHARE_INFERENCE_SCALE = 10_000.0
_SHARE_SANITY_MIN = 1_000_000.0
_SHARE_SANITY_MAX = 10_000_000_000_000.0
_LIMIT_CANDIDATES = (0.05, 0.10, 0.20, 0.30)

_legacy_normalize_tickflow_frame = _core._normalize_tickflow_frame
_legacy_record_market_manifest = _core._record_market_manifest


def _infer_tickflow_volume_scale(frame: pd.DataFrame) -> float:
    """Infer whether TickFlow volume is already shares or still board lots."""
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
    validated = _core._validate_ohlcv(canonical)
    if validated is not None:
        validated.attrs["volume_unit"] = "shares"
        validated.attrs["amount_unit"] = "CNY"
    return validated


def _share_count_evidence(value: Any) -> dict[str, Any]:
    raw = _core._number_or_none(value)
    if raw is None:
        return {
            "raw_total_shares": None,
            "normalized_total_shares": None,
            "unit_inference_used": False,
            "unit_assumption": "unavailable",
            "sanity_passed": False,
        }
    inferred = bool(raw < _SHARE_INFERENCE_THRESHOLD)
    normalized = float(raw * _SHARE_INFERENCE_SCALE if inferred else raw)
    sanity = bool(_SHARE_SANITY_MIN <= normalized <= _SHARE_SANITY_MAX)
    return {
        "raw_total_shares": float(raw),
        "normalized_total_shares": normalized,
        "unit_inference_used": inferred,
        "unit_assumption": "10k_shares_inferred" if inferred else "individual_shares",
        "sanity_passed": sanity,
    }


def _normalize_cn_share_count(value: Any) -> float | None:
    evidence = _share_count_evidence(value)
    normalized = evidence["normalized_total_shares"]
    return float(normalized) if evidence["sanity_passed"] and normalized is not None else None


def _metadata_from_cache(symbol: str) -> dict[str, Any]:
    symbol = _core.normalize_ticker(symbol)
    existing = _core._INSTRUMENT_META.get(symbol)
    if isinstance(existing, dict) and existing:
        return existing
    cached = _core._load_universe_cache()
    if isinstance(cached, dict):
        metadata = cached.get("metadata", {})
        if isinstance(metadata, dict):
            raw = metadata.get(symbol, {})
            if isinstance(raw, dict) and raw:
                _core._INSTRUMENT_META[symbol] = raw
                return raw
    return {}


def _metadata_for_symbol(symbol: str, *, fetch: bool) -> dict[str, Any]:
    metadata = _metadata_from_cache(symbol)
    if metadata or not fetch:
        return metadata
    rows = _core._instrument_batches([_core.normalize_ticker(symbol)])
    if not rows:
        return {}
    metadata = rows[0]
    _core._INSTRUMENT_META[_core.normalize_ticker(symbol)] = metadata
    return metadata


def get_market_cap_evidence(
    ticker: str,
    *,
    frame: pd.DataFrame | None = None,
    fetch: bool = True,
) -> dict[str, Any]:
    """Return market cap plus raw/normalized share-capital provenance."""
    symbol = _core.normalize_ticker(ticker)
    metadata = _metadata_for_symbol(symbol, fetch=fetch)
    ext = metadata.get("ext") if isinstance(metadata.get("ext"), Mapping) else {}
    evidence = _share_count_evidence(ext.get("total_shares"))
    price_frame = frame if frame is not None else _core._load_cache(symbol)
    close = None
    if price_frame is not None and not price_frame.empty and "Close" in price_frame.columns:
        close = _core._number_or_none(price_frame["Close"].iloc[-1])
    shares = evidence.get("normalized_total_shares")
    market_cap = (
        float(shares) * float(close)
        if evidence.get("sanity_passed") and shares is not None and close is not None
        else None
    )
    cap_sanity = bool(
        market_cap is not None
        and np.isfinite(market_cap)
        and 10_000_000.0 <= market_cap <= 100_000_000_000_000.0
    )
    return {
        "ticker": symbol,
        **evidence,
        "latest_close": close,
        "market_cap": float(market_cap) if cap_sanity else None,
        "market_cap_sanity_passed": cap_sanity,
        "source": "tickflow_ext_total_shares",
    }


def get_market_cap(ticker: str) -> float | None:
    evidence = get_market_cap_evidence(ticker, fetch=True)
    value = evidence.get("market_cap")
    return float(value) if value is not None else None


def _direct_limit_ratio(limit_up: float, limit_down: float) -> float | None:
    values = [value for value in (limit_up, limit_down) if np.isfinite(value) and 0 < value < 1]
    if not values:
        return None
    mean = float(np.mean(values))
    candidate = min(_LIMIT_CANDIDATES, key=lambda item: abs(item - mean))
    return float(candidate) if abs(candidate - mean) <= 0.025 else None


def _limit_ratio_from_prices(
    limit_up: float,
    limit_down: float,
    frame: pd.DataFrame | None,
) -> float | None:
    if frame is None or frame.empty or "Close" not in frame.columns:
        return None
    closes = pd.to_numeric(frame["Close"], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    ).dropna()
    if closes.empty:
        return None
    references = [float(closes.iloc[-1])]
    if len(closes) >= 2:
        references.append(float(closes.iloc[-2]))
    estimates: list[float] = []
    for reference in references:
        if reference <= 0:
            continue
        if np.isfinite(limit_up) and limit_up > reference:
            estimates.append(float(limit_up / reference - 1.0))
        if np.isfinite(limit_down) and 0 < limit_down < reference:
            estimates.append(float(1.0 - limit_down / reference))
    estimates = [value for value in estimates if 0.02 <= value <= 0.40]
    if not estimates:
        return None
    for candidate in _LIMIT_CANDIDATES:
        residuals = sorted(abs(value - candidate) for value in estimates)
        support = residuals[: min(2, len(residuals))]
        if support and float(np.mean(support)) <= 0.025:
            return float(candidate)
    return None


@lru_cache(maxsize=8192)
def get_price_limit_pct(ticker: str, is_etf: bool = False) -> float | None:
    """Infer the current security-specific limit ratio from TickFlow metadata."""
    del is_etf  # metadata is security-specific; the flag is only caller context.
    symbol = _core.normalize_ticker(ticker)
    metadata = _metadata_for_symbol(symbol, fetch=False)
    if not metadata:
        # Normal universe construction populates metadata before scanning.  This
        # fallback helps specified-ticker runs without turning each backtest
        # worker into a metadata request loop.
        metadata = _metadata_for_symbol(symbol, fetch=True)
    ext = metadata.get("ext") if isinstance(metadata.get("ext"), Mapping) else {}
    limit_up = _core._number_or_none(ext.get("limit_up"))
    limit_down = _core._number_or_none(ext.get("limit_down"))
    up = float(limit_up) if limit_up is not None else np.nan
    down = float(limit_down) if limit_down is not None else np.nan
    direct = _direct_limit_ratio(up, down)
    if direct is not None:
        return direct
    return _limit_ratio_from_prices(up, down, _core._load_cache(symbol))


def _record_market_manifest(ticker: str, df: pd.DataFrame) -> None:
    _legacy_record_market_manifest(ticker, df)
    key = _core.normalize_ticker(ticker)
    metadata = _core._MARKET_MANIFEST_DIRTY.get(key)
    if not isinstance(metadata, dict):
        return
    metadata["volume_unit"] = "shares"
    metadata["amount_unit"] = "CNY"
    metadata["volume_schema"] = _PRICE_CACHE_SCHEMA_VERSION

    amount = (
        pd.to_numeric(df["Amount"], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        ).tail(60).dropna()
        if "Amount" in df.columns
        else pd.Series(dtype=float)
    )
    if len(amount) >= 30:
        metadata["liquidity_basis"] = "turnover_cny"
        metadata["median_turnover_60"] = float(amount.median())
        metadata["turnover_observations"] = len(amount)
    else:
        metadata["liquidity_basis"] = "shares_fallback"
        metadata["median_turnover_60"] = None
        metadata["turnover_observations"] = len(amount)

    cap = get_market_cap_evidence(key, frame=df, fetch=False)
    metadata["market_cap_source"] = cap.get("source", "")
    metadata["total_shares_raw"] = cap.get("raw_total_shares")
    metadata["total_shares_normalized"] = cap.get("normalized_total_shares")
    metadata["share_unit_inference_used"] = bool(cap.get("unit_inference_used", False))
    metadata["share_unit_assumption"] = cap.get("unit_assumption", "unavailable")
    metadata["market_cap_sanity_passed"] = bool(cap.get("market_cap_sanity_passed", False))
    limit_pct = get_price_limit_pct(key)
    metadata["price_limit_pct"] = limit_pct
    metadata["price_limit_source"] = "tickflow_ext" if limit_pct is not None else "fallback_rule"


_core._PRICE_CACHE_SCHEMA_VERSION = _PRICE_CACHE_SCHEMA_VERSION
_core._PRICE_CACHE_DIR = _PRICE_CACHE_DIR
_core._MARKET_MANIFEST_PATH = _MARKET_MANIFEST_PATH
_core._TICKFLOW_CN_LOT_SIZE = _TICKFLOW_CN_LOT_SIZE
_core._infer_tickflow_volume_scale = _infer_tickflow_volume_scale
_core._normalize_tickflow_frame = _normalize_tickflow_frame
_core._share_count_evidence = _share_count_evidence
_core._normalize_cn_share_count = _normalize_cn_share_count
_core.get_market_cap_evidence = get_market_cap_evidence
_core.get_market_cap = get_market_cap
_core.get_price_limit_pct = get_price_limit_pct
_core._record_market_manifest = _record_market_manifest
_core.TICKFLOW_CANONICAL_VOLUME_UNIT = "shares"
_core.TICKFLOW_CANONICAL_AMOUNT_UNIT = "CNY"

sys.modules[__name__] = _core
