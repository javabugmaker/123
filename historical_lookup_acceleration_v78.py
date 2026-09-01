"""v78 historical-universe lookup acceleration.

Backtest samples can call ``point_in_time_eligibility`` many times per ticker.
The stable implementation correctly caches the parsed snapshot index, but it
still glob/stat-scans the snapshot directory on every sample merely to rebuild
its cache key, then linearly walks that ticker's date entries.

Keep refresh detection with a short worker-local TTL, reuse both the parsed
index and per-ticker sorted date tuples, and use bisect for each sample lookup.
A file change is observed within a few seconds; eligibility semantics are
identical.
"""

from __future__ import annotations

import bisect
import sys
import threading
import time
from pathlib import Path

import pandas as pd

import historical_universe as _history

_STATE_TTL_SECONDS = 5.0
_LOCK = threading.RLock()
_STATE_DEADLINE = 0.0
_STATE_KEY: tuple[str, tuple[tuple[str, int, int], ...]] | None = None
_STATE_INDEX: dict[str, tuple[tuple[pd.Timestamp, bool, str], ...]] = {}
_STATE_DATES: dict[str, tuple[pd.Timestamp, ...]] = {}
_INSTALLED = False


def _refresh_state(
    snapshot_dir: Path | None = None,
) -> dict[str, tuple[tuple[pd.Timestamp, bool, str], ...]]:
    global _STATE_DEADLINE, _STATE_KEY, _STATE_INDEX, _STATE_DATES
    now = time.monotonic()
    if snapshot_dir is None and now < _STATE_DEADLINE:
        return _STATE_INDEX

    with _LOCK:
        now = time.monotonic()
        if snapshot_dir is None and now < _STATE_DEADLINE:
            return _STATE_INDEX
        directory_text, signature = _history._snapshot_cache_key(snapshot_dir)
        key = (directory_text, signature)
        if key != _STATE_KEY:
            _STATE_INDEX = _history._load_snapshot_index(directory_text, signature)
            _STATE_DATES = {
                ticker: tuple(pd.Timestamp(entry[0]).normalize() for entry in entries)
                for ticker, entries in _STATE_INDEX.items()
            }
            _STATE_KEY = key
        if snapshot_dir is None:
            _STATE_DEADLINE = now + _STATE_TTL_SECONDS
        return _STATE_INDEX


def point_in_time_eligibility(
    ticker: str,
    at_date: str | pd.Timestamp,
    snapshot_dir: Path | None = None,
) -> tuple[bool | None, str]:
    index = _refresh_state(snapshot_dir)
    symbol = _history.normalize_ticker(ticker)
    entries = index.get(symbol, ())
    if not entries:
        return None, "no_point_in_time_snapshot"

    cutoff = pd.Timestamp(at_date).normalize()
    dates = _STATE_DATES.get(symbol)
    if dates is None:
        dates = tuple(pd.Timestamp(entry[0]).normalize() for entry in entries)
    position = bisect.bisect_right(dates, cutoff) - 1
    if position < 0:
        return None, "snapshot_starts_after_signal"
    selected = entries[position]
    observed = pd.Timestamp(selected[0]).normalize()
    age_days = int((cutoff - observed).days)
    if age_days < 0:
        return None, "snapshot_starts_after_signal"
    if age_days > _history.PIT_UNIVERSE_MAX_SNAPSHOT_AGE_DAYS:
        return None, f"snapshot_too_old:{age_days}d"
    return bool(selected[1]), selected[2] or (
        "eligible" if selected[1] else "snapshot_excluded"
    )


def clear_historical_lookup_acceleration() -> None:
    global _STATE_DEADLINE, _STATE_KEY, _STATE_INDEX, _STATE_DATES
    with _LOCK:
        _STATE_DEADLINE = 0.0
        _STATE_KEY = None
        _STATE_INDEX = {}
        _STATE_DATES = {}


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _history.point_in_time_eligibility = point_in_time_eligibility

    analytics_core = sys.modules.get("analytics_core")
    if analytics_core is not None:
        setattr(analytics_core, "point_in_time_eligibility", point_in_time_eligibility)
    _INSTALLED = True


install()
