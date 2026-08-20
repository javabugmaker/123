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
    if lookup is None:
        # Match v51: missing benchmark prices produce INCOMPLETE / NaN returns.
        open_by_date: dict[str, float] = {}
        close_by_date: dict[str, float] = {}
    else:
        open_by_date = lookup.open_by_date
        close_by_date = lookup.close_by_date

    aligned: list[dict[str, Any]] = []
    for source in samples:
        item = dict(source)
        entry_key = _date_key(item.get("entry_date"))
        entry_open = float(open_by_date.get(entry_key, np.nan)) if entry_key else np.nan
        item["benchmark_entry_basis"] = "OPEN"
        item["benchmark_entry_price"] = entry_open
        valid_entry = np.isfinite(entry_open) and entry_open > 0.0
        complete = True
        for horizon in (20, 60):
            exit_key = _date_key(item.get(f"exit{horizon}_date"))
            exit_close = (
                float(close_by_date.get(exit_key, np.nan)) if exit_key else np.nan
            )
            if valid_entry and np.isfinite(exit_close) and exit_close > 0.0:
                item[f"benchmark_return{horizon}"] = (
                    float(exit_close / entry_open - 1.0) * 100.0
                )
            else:
                item[f"benchmark_return{horizon}"] = np.nan
                complete = False
        item["benchmark_alignment_status"] = "ALIGNED" if complete else "INCOMPLETE"
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
