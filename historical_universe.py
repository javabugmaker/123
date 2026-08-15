from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

from config import CACHE_DIR
from downloader import normalize_ticker

_CACHE_DIR = CACHE_DIR / "v3-tickflow-forward"
_BENCHMARKS = {"000300.SH", "000905.SH", "399006.SZ"}
_SECURITY = re.compile(r"^\d{6}\.(?:SH|SZ|BJ)$")
_SNAPSHOT_DIR = CACHE_DIR / "historical_universe"


def cached_security_tickers(cache_dir: Path | None = None) -> list[str]:
    directory = cache_dir or _CACHE_DIR
    if not directory.exists():
        return []
    result: set[str] = set()
    for path in directory.glob("*.parquet"):
        ticker = normalize_ticker(path.stem)
        if ticker in _BENCHMARKS or not _SECURITY.fullmatch(ticker):
            continue
        result.add(ticker)
    return sorted(result)


def merge_with_cached_universe(
    current_tickers: list[str],
    cache_dir: Path | None = None,
) -> list[str]:
    combined = [normalize_ticker(ticker) for ticker in current_tickers if str(ticker).strip()]
    combined.extend(cached_security_tickers(cache_dir))
    return list(dict.fromkeys(combined))


def _snapshot_files(snapshot_dir: Path | None = None) -> tuple[Path, ...]:
    directory = snapshot_dir or _SNAPSHOT_DIR
    if not directory.exists():
        return ()
    return tuple(sorted((*directory.glob("*.csv"), *directory.glob("*.parquet"))))


@lru_cache(maxsize=4)
def _load_snapshot_index(
    directory_text: str = "",
    file_signature: tuple[tuple[str, int, int], ...] = (),
) -> dict[str, tuple[tuple[pd.Timestamp, bool, str], ...]]:
    # ``file_signature`` is part of the cache key.  Snapshot files can be
    # refreshed while the long-running GUI process remains open, so caching by
    # directory path alone would otherwise keep serving stale eligibility.
    del file_signature
    directory = Path(directory_text) if directory_text else _SNAPSHOT_DIR
    frames: list[pd.DataFrame] = []
    for path in _snapshot_files(directory):
        try:
            frame = (
                pd.read_parquet(path)
                if path.suffix.lower() == ".parquet"
                else pd.read_csv(path, encoding="utf-8-sig")
            )
        except (OSError, ImportError, UnicodeError, ValueError):
            continue
        if "Ticker" not in frame:
            continue
        date_column = next(
            (column for column in ("AsOf", "Date", "TradeDate") if column in frame),
            None,
        )
        if date_column is None:
            continue
        working = frame.copy()
        working["_date"] = pd.to_datetime(working[date_column], errors="coerce")
        working["_ticker"] = working["Ticker"].map(normalize_ticker)
        if "Eligible" in working:
            eligible = working["Eligible"].astype(str).str.lower().isin(
                {"true", "1", "yes", "y", "是"}
            )
        else:
            listed = (
                working["Listed"].astype(str).str.lower().isin(
                    {"true", "1", "yes", "y", "是"}
                )
                if "Listed" in working
                else pd.Series(True, index=working.index)
            )
            is_st = (
                working["IsST"].astype(str).str.lower().isin(
                    {"true", "1", "yes", "y", "是"}
                )
                if "IsST" in working
                else pd.Series(False, index=working.index)
            )
            eligible = listed & ~is_st
        working["_eligible"] = eligible
        working["_reason"] = working.get(
            "ExclusionReason", pd.Series("", index=working.index)
        ).fillna("").astype(str)
        frames.append(working[["_ticker", "_date", "_eligible", "_reason"]])
    if not frames:
        return {}
    combined = pd.concat(frames, ignore_index=True).dropna(subset=["_date"])
    combined = combined.loc[combined["_ticker"].map(lambda value: bool(_SECURITY.fullmatch(value)))]
    result: dict[str, tuple[tuple[pd.Timestamp, bool, str], ...]] = {}
    for ticker, group in combined.sort_values("_date").groupby("_ticker", sort=False):
        result[str(ticker)] = tuple(
            (pd.Timestamp(date), bool(eligible), str(reason))
            for date, eligible, reason in group[["_date", "_eligible", "_reason"]].itertuples(index=False, name=None)
        )
    return result


def _snapshot_cache_key(
    snapshot_dir: Path | None = None,
) -> tuple[str, tuple[tuple[str, int, int], ...]]:
    directory = snapshot_dir or _SNAPSHOT_DIR
    try:
        directory_text = str(directory.resolve())
    except OSError:
        directory_text = str(directory)
    signature: list[tuple[str, int, int]] = []
    for path in _snapshot_files(directory):
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((path.name, int(stat.st_mtime_ns), int(stat.st_size)))
    return directory_text, tuple(signature)


def point_in_time_eligibility(
    ticker: str,
    at_date: str | pd.Timestamp,
    snapshot_dir: Path | None = None,
) -> tuple[bool | None, str]:
    """Resolve the latest known universe/ST state at or before ``at_date``."""
    directory_text, signature = _snapshot_cache_key(snapshot_dir)
    entries = _load_snapshot_index(directory_text, signature).get(
        normalize_ticker(ticker), ()
    )
    if not entries:
        return None, "no_point_in_time_snapshot"
    cutoff = pd.Timestamp(at_date)
    selected: tuple[pd.Timestamp, bool, str] | None = None
    for entry in entries:
        if entry[0] > cutoff:
            break
        selected = entry
    if selected is None:
        return None, "snapshot_starts_after_signal"
    return bool(selected[1]), selected[2] or ("eligible" if selected[1] else "snapshot_excluded")


def historical_universe_status(snapshot_dir: Path | None = None) -> dict[str, object]:
    directory_text, signature = _snapshot_cache_key(snapshot_dir)
    index = _load_snapshot_index(directory_text, signature)
    observations = sum(len(entries) for entries in index.values())
    dates = [entry[0] for entries in index.values() for entry in entries]
    return {
        "available": bool(index),
        "ticker_count": len(index),
        "observations": observations,
        "start_date": min(dates).strftime("%Y-%m-%d") if dates else "",
        "end_date": max(dates).strftime("%Y-%m-%d") if dates else "",
        "directory": str(snapshot_dir or _SNAPSHOT_DIR),
    }


def merge_with_historical_universe(
    current_tickers: list[str],
    cache_dir: Path | None = None,
    snapshot_dir: Path | None = None,
) -> list[str]:
    combined = merge_with_cached_universe(current_tickers, cache_dir)
    directory_text, signature = _snapshot_cache_key(snapshot_dir)
    combined.extend(_load_snapshot_index(directory_text, signature))
    return list(dict.fromkeys(combined))
