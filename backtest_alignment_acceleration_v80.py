"""v80 benchmark-alignment acceleration with identical open-to-close semantics.

v51 correctly aligns benchmark returns from the entry session OPEN to the
resolved exit session CLOSE.  The original helper performed a full benchmark
index normalization and linear equality scan three times for every historical
sample, including samples loaded from a cache that was already aligned.

v80 builds one immutable date->price lookup per benchmark DataFrame/worker and
returns already-aligned cache samples without touching them.  No benchmark
pricing or cache-integrity rule changes.
"""

from __future__ import annotations

import threading
import weakref
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

import backtest_alignment as _alignment

_INSTALLED = False
_TLS = threading.local()


@dataclass
class _BenchmarkLookup:
    frame_ref: weakref.ReferenceType[pd.DataFrame]
    open_by_date: dict[str, float]
    close_by_date: dict[str, float]
    dates: pd.Index
    open_values: np.ndarray
    close_values: np.ndarray


def _valid_price(value: Any) -> float:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return np.nan
    return price if np.isfinite(price) and price > 0.0 else np.nan


def _date_key(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
            return text[:10]
    timestamp = pd.to_datetime(value, errors="coerce")
    if pd.isna(timestamp):
        return ""
    normalized = pd.Timestamp(timestamp)
    if normalized.tzinfo is not None:
        normalized = normalized.tz_localize(None)
    return normalized.normalize().strftime("%Y-%m-%d")


def _date_keys(values: list[Any]) -> np.ndarray:
    """Normalize a batch of sample dates while preserving scalar semantics."""
    if not values:
        return np.empty(0, dtype=object)

    raw = np.asarray(values, dtype=object)
    keys = np.full(len(raw), "", dtype=object)
    string_mask = np.fromiter(
        (isinstance(value, str) for value in raw),
        dtype=bool,
        count=len(raw),
    )
    if np.any(string_mask):
        positions = np.flatnonzero(string_mask)
        text = pd.Series(raw[positions], copy=False, dtype="string").str.strip()
        iso = (
            text.str.len().ge(10)
            & text.str.slice(4, 5).eq("-")
            & text.str.slice(7, 8).eq("-")
        ).to_numpy(dtype=bool)
        if np.any(iso):
            keys[positions[iso]] = text.iloc[np.flatnonzero(iso)].str.slice(
                0, 10
            ).to_numpy(dtype=object)

    timezone_aware = np.fromiter(
        (
            not isinstance(value, str)
            and getattr(value, "tzinfo", None) is not None
            for value in raw
        ),
        dtype=bool,
        count=len(raw),
    )
    if np.any(timezone_aware):
        positions = np.flatnonzero(timezone_aware)
        keys[positions] = np.fromiter(
            (_date_key(raw[int(position)]) for position in positions),
            dtype=object,
            count=len(positions),
        )

    remaining = np.flatnonzero(~timezone_aware & (keys == ""))
    if not remaining.size:
        return keys
    try:
        parsed = pd.to_datetime(
            pd.Series(raw[remaining], copy=False),
            errors="coerce",
            format="mixed",
        )
        if isinstance(parsed.dtype, pd.DatetimeTZDtype):
            parsed = parsed.dt.tz_localize(None)
        if not pd.api.types.is_datetime64_any_dtype(parsed.dtype):
            raise TypeError("mixed timezone/object dates require scalar fallback")
        formatted = parsed.dt.normalize().dt.strftime("%Y-%m-%d").fillna("")
        keys[remaining] = formatted.to_numpy(dtype=object)
    except (TypeError, ValueError, OverflowError):
        keys[remaining] = np.fromiter(
            (_date_key(raw[int(position)]) for position in remaining),
            dtype=object,
            count=len(remaining),
        )
    return keys


def _build_lookup(frame: pd.DataFrame | None) -> _BenchmarkLookup | None:
    if frame is None or frame.empty:
        return None
    current = getattr(_TLS, "benchmark_lookup", None)
    if isinstance(current, _BenchmarkLookup) and current.frame_ref() is frame:
        return current

    index = _alignment._normalized_index(frame)
    valid_index = ~index.isna()
    positions = np.flatnonzero(valid_index)
    if not positions.size:
        return None

    # _price_on_date historically chooses the last duplicate date. Assigning
    # in source order preserves that exact rule while avoiding groupby overhead.
    open_values = (
        pd.to_numeric(frame["Open"], errors="coerce").to_numpy(dtype=np.float64)
        if "Open" in frame.columns
        else np.full(len(frame), np.nan, dtype=np.float64)
    )
    close_values = (
        pd.to_numeric(frame["Close"], errors="coerce").to_numpy(dtype=np.float64)
        if "Close" in frame.columns
        else np.full(len(frame), np.nan, dtype=np.float64)
    )
    open_by_date: dict[str, float] = {}
    close_by_date: dict[str, float] = {}
    for position in positions:
        key = pd.Timestamp(index[int(position)]).strftime("%Y-%m-%d")
        open_by_date[key] = _valid_price(open_values[int(position)])
        close_by_date[key] = _valid_price(close_values[int(position)])

    result = _BenchmarkLookup(
        frame_ref=weakref.ref(frame),
        open_by_date=open_by_date,
        close_by_date=close_by_date,
        dates=pd.Index(tuple(open_by_date), dtype=object),
        open_values=np.fromiter(open_by_date.values(), dtype=np.float64),
        close_values=np.fromiter(close_by_date.values(), dtype=np.float64),
    )
    _TLS.benchmark_lookup = result
    return result


def _price_on_date(
    frame: pd.DataFrame,
    date_value: Any,
    column: str,
) -> float:
    lookup = _build_lookup(frame)
    if lookup is None:
        return np.nan
    key = _date_key(date_value)
    if not key:
        return np.nan
    if column == "Open":
        return float(lookup.open_by_date.get(key, np.nan))
    if column == "Close":
        return float(lookup.close_by_date.get(key, np.nan))
    # Preserve compatibility for unexpected callers/columns.
    return _alignment._LEGACY_PRICE_ON_DATE(frame, date_value, column)


def _already_aligned(samples: list[dict[str, Any]]) -> bool:
    if not samples:
        return True
    return all(
        str(item.get("benchmark_entry_basis", "") or "").upper() == "OPEN"
        and str(item.get("benchmark_alignment_status", "") or "").upper()
        in {"ALIGNED", "INCOMPLETE"}
        for item in samples
    )


def _lookup_prices(
    lookup: _BenchmarkLookup | None,
    keys: np.ndarray,
    *,
    column: str,
) -> np.ndarray:
    prices = np.full(len(keys), np.nan, dtype=np.float64)
    if lookup is None or not len(keys):
        return prices
    positions = lookup.dates.get_indexer(keys)
    found = positions >= 0
    if not np.any(found):
        return prices
    source = lookup.open_values if column == "Open" else lookup.close_values
    prices[found] = source[positions[found]]
    return prices


def align_benchmark_returns(
    samples: list[dict[str, Any]],
    benchmark_frame: pd.DataFrame | None,
) -> list[dict[str, Any]]:
    """Recompute benchmark legs with O(1) date lookups, or reuse aligned cache."""
    if not samples:
        return samples
    # New v51+ backtest caches are written through aligned_one, so their
    # benchmark fields are already canonical. Re-aligning them is pure waste.
    if _already_aligned(samples):
        return samples

    lookup = _build_lookup(benchmark_frame)
    entry_keys = _date_keys([item.get("entry_date") for item in samples])
    exit20_keys = _date_keys([item.get("exit20_date") for item in samples])
    exit60_keys = _date_keys([item.get("exit60_date") for item in samples])
    entry_open = _lookup_prices(lookup, entry_keys, column="Open")
    exit20_close = _lookup_prices(lookup, exit20_keys, column="Close")
    exit60_close = _lookup_prices(lookup, exit60_keys, column="Close")

    valid_entry = np.isfinite(entry_open) & (entry_open > 0.0)
    valid20 = valid_entry & np.isfinite(exit20_close) & (exit20_close > 0.0)
    valid60 = valid_entry & np.isfinite(exit60_close) & (exit60_close > 0.0)
    return20 = np.full(len(samples), np.nan, dtype=np.float64)
    return60 = np.full(len(samples), np.nan, dtype=np.float64)
    return20[valid20] = (exit20_close[valid20] / entry_open[valid20] - 1.0) * 100.0
    return60[valid60] = (exit60_close[valid60] / entry_open[valid60] - 1.0) * 100.0
    complete = valid20 & valid60

    # Dict materialization is the public return contract; all date parsing,
    # lookup and return arithmetic above stays in bulk arrays.
    aligned: list[dict[str, Any]] = []
    for position, source in enumerate(samples):
        item = dict(source)
        item["benchmark_entry_basis"] = "OPEN"
        item["benchmark_entry_price"] = float(entry_open[position])
        item["benchmark_return20"] = float(return20[position])
        item["benchmark_return60"] = float(return60[position])
        item["benchmark_alignment_status"] = (
            "ALIGNED" if bool(complete[position]) else "INCOMPLETE"
        )
        aligned.append(item)
    return aligned


def clear_benchmark_alignment_cache() -> None:
    if hasattr(_TLS, "benchmark_lookup"):
        delattr(_TLS, "benchmark_lookup")


def install() -> None:
    global _INSTALLED
    if not hasattr(_alignment, "_LEGACY_PRICE_ON_DATE"):
        _alignment._LEGACY_PRICE_ON_DATE = _alignment._price_on_date
    _alignment._price_on_date = _price_on_date
    _alignment.align_benchmark_returns = align_benchmark_returns
    _alignment.clear_benchmark_alignment_cache = clear_benchmark_alignment_cache
    _INSTALLED = True


install()
