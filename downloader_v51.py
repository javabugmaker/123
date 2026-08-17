"""v52 TickFlow metadata integrity facade.

The v51 normalisation/market-cap implementation is retained in
``downloader_v51_base``.  v52 deliberately stops interpreting TickFlow
``limit_up``/``limit_down`` price levels as percentage ratios.  Only explicitly
named ratio fields are accepted; otherwise the execution layer applies audited
exchange/security rules.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

import numpy as np

import downloader_v51_base as _core
from downloader_v51_base import *  # noqa: F403

_LIMIT_CANDIDATES = (0.05, 0.10, 0.20, 0.30)
_EXPLICIT_RATIO_KEYS = (
    "price_limit_pct",
    "price_limit_ratio",
    "daily_limit_pct",
    "daily_limit_ratio",
    "limit_pct",
    "limit_ratio",
)
_STAR_ETF_KEYWORDS = ("科创", "双创")
_CHINEXT_ETF_KEYWORDS = ("创业板", "创业")
_legacy_record_market_manifest = _core._record_market_manifest


def _cached_metadata(symbol: str) -> dict[str, Any]:
    symbol = _core.normalize_ticker(symbol)
    existing = _core._INSTRUMENT_META.get(symbol)
    if isinstance(existing, dict) and existing:
        return existing
    cached = _core._load_universe_cache()
    if not isinstance(cached, dict):
        return {}
    metadata = cached.get("metadata", {})
    if not isinstance(metadata, dict):
        return {}
    raw = metadata.get(symbol)
    if not isinstance(raw, dict) or not raw:
        return {}
    _core._INSTRUMENT_META[symbol] = raw
    return raw


def _canonical_ratio(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric) or numeric <= 0:
        return None
    # Some providers express percentage fields as 10/20/30 rather than
    # 0.10/0.20/0.30.  Accept that only for fields whose *name* says ratio/pct.
    if 2.0 <= numeric <= 40.0:
        numeric /= 100.0
    if not 0.02 <= numeric <= 0.40:
        return None
    candidate = min(_LIMIT_CANDIDATES, key=lambda item: abs(item - numeric))
    return float(candidate) if abs(candidate - numeric) <= 0.005 else None


def _explicit_ratio(metadata: dict[str, Any]) -> float | None:
    ext = metadata.get("ext") if isinstance(metadata.get("ext"), Mapping) else {}
    for container in (metadata, ext):
        if not isinstance(container, Mapping):
            continue
        for key in _EXPLICIT_RATIO_KEYS:
            if key not in container:
                continue
            ratio = _canonical_ratio(container.get(key))
            if ratio is not None:
                return ratio
    return None


def _instrument_name(metadata: dict[str, Any]) -> str:
    ext = metadata.get("ext") if isinstance(metadata.get("ext"), Mapping) else {}
    values = []
    for container in (metadata, ext):
        if not isinstance(container, Mapping):
            continue
        for key in ("name", "display_name", "short_name", "name_cn", "cn_name"):
            value = str(container.get(key, "") or "").strip()
            if value:
                values.append(value)
    return " ".join(values)


def get_price_limit_evidence(ticker: str, is_etf: bool = False) -> dict[str, Any]:
    """Return a safe current limit ratio and provenance without hidden I/O."""
    symbol = _core.normalize_ticker(ticker)
    metadata = _cached_metadata(symbol)
    explicit = _explicit_ratio(metadata)
    if explicit is not None:
        return {
            "ticker": symbol,
            "pct": explicit,
            "source": "explicit_ratio_metadata",
        }

    if is_etf:
        code = symbol.split(".", 1)[0]
        name = _instrument_name(metadata)
        if code.startswith(("588", "589")) or any(
            keyword in name for keyword in _STAR_ETF_KEYWORDS
        ):
            return {
                "ticker": symbol,
                "pct": 0.20,
                "source": "star_etf_20pct_rule",
            }
        if any(keyword in name for keyword in _CHINEXT_ETF_KEYWORDS):
            return {
                "ticker": symbol,
                "pct": 0.20,
                "source": "chinext_related_etf_20pct_rule",
            }

    return {"ticker": symbol, "pct": None, "source": "exchange_fallback"}


@lru_cache(maxsize=8192)
def get_price_limit_pct(ticker: str, is_etf: bool = False) -> float | None:
    evidence = get_price_limit_evidence(ticker, is_etf=is_etf)
    value = evidence.get("pct")
    return float(value) if value is not None else None


def _record_market_manifest(ticker: str, df) -> None:
    # Preserve v51 liquidity/share-capital provenance, then overwrite the
    # ambiguous price-limit fields with the v52 evidence contract.
    _legacy_record_market_manifest(ticker, df)
    key = _core.normalize_ticker(ticker)
    row = _core._MARKET_MANIFEST_DIRTY.get(key)
    if not isinstance(row, dict):
        return
    is_etf = bool(_core.is_etf_ticker(key))
    evidence = get_price_limit_evidence(key, is_etf=is_etf)
    row["price_limit_pct"] = evidence.get("pct")
    row["price_limit_source"] = evidence.get("source", "exchange_fallback")


_core.get_price_limit_evidence = get_price_limit_evidence
_core.get_price_limit_pct = get_price_limit_pct
_core._record_market_manifest = _record_market_manifest
sys.modules[__name__] = _core
