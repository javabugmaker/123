from __future__ import annotations

"""Persistent compute caches used by scanner and historical backtests.

The cache layer distinguishes immutable strategy parameters from market state.
Appending a new TickFlow daily bar therefore reuses the existing indicator and
backtest history instead of invalidating ten years of derived work.  Historical
price changes (for example a forward-adjustment rebase) are detected by a tail
fingerprint and still force a safe full rebuild.
"""

import gzip
import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from config import CACHE_DIR, INDICATOR_INCREMENTAL_LOOKBACK_BARS, SCORING_VERSION

INDICATOR_CACHE_VERSION = "v4"
BACKTEST_CACHE_VERSION = "v7"
INDICATOR_CACHE_DIR = CACHE_DIR / f"_indicators_{INDICATOR_CACHE_VERSION}"
BACKTEST_CACHE_DIR = CACHE_DIR / f"_backtest_{BACKTEST_CACHE_VERSION}"
_MARKET_COLUMNS = ("Open", "High", "Low", "Close", "Volume", "Amount")
_CUMULATIVE_COLUMNS = ("OBV", "AD")
_REQUIRED_INDICATOR_COLUMNS = {"MA20", "MA50", "MA200", "ATR14", "ATR50", "RSI14", "CMF", "OBV", "AD"}


def _safe_stem(ticker: str) -> str:
    value = str(ticker).strip().upper()
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).rstrip(" .")
    return safe or "ticker"


def file_signature(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        stat = path.stat()
    except OSError:
        return ""
    payload = f"{path.name}|{stat.st_size}|{stat.st_mtime_ns}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _normalized_index(df: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(df.index, errors="coerce"))


def _frame_identity(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or df.empty:
        return {"rows": 0, "first": "", "last": ""}
    index = _normalized_index(df).dropna()
    first = str(index.min()) if len(index) else ""
    last = str(index.max()) if len(index) else ""
    close = pd.to_numeric(df.get("Close", pd.Series(dtype=float)), errors="coerce")
    sample_values: list[float] = []
    if len(close):
        positions = sorted({0, len(close) // 4, len(close) // 2, len(close) * 3 // 4, len(close) - 1})
        for position in positions:
            try:
                value = float(close.iloc[position])
            except (IndexError, TypeError, ValueError):
                value = float("nan")
            sample_values.append(round(value, 8) if np.isfinite(value) else 0.0)
    return {
        "rows": int(len(df)),
        "first": first,
        "last": last,
        "sample_close": sample_values,
    }


def data_signature(df: pd.DataFrame, source_path: Path | None = None) -> str:
    payload = {
        "file": file_signature(source_path),
        "frame": _frame_identity(df),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def market_tail_fingerprint(
    df: pd.DataFrame,
    *,
    end_date: str | pd.Timestamp | None = None,
    rows: int = 12,
) -> str:
    """Hash a small OHLCV tail ending at ``end_date`` for append safety."""
    if df is None or df.empty:
        return ""
    frame = df.copy(deep=False)
    frame.index = _normalized_index(frame)
    frame = frame[~frame.index.isna()].sort_index()
    if end_date is not None:
        cutoff = pd.Timestamp(end_date)
        frame = frame.loc[frame.index <= cutoff]
    columns = [column for column in _MARKET_COLUMNS if column in frame.columns]
    if frame.empty or not columns:
        return ""
    tail = frame[columns].tail(max(1, int(rows))).apply(pd.to_numeric, errors="coerce")
    payload = {
        "dates": [value.isoformat() for value in pd.DatetimeIndex(tail.index)],
        "columns": columns,
        "values": [
            [None if not np.isfinite(value) else round(float(value), 8) for value in row]
            for row in tail.to_numpy(dtype=float)
        ],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def market_cache_state(df: pd.DataFrame) -> dict[str, Any]:
    identity = _frame_identity(df)
    return {
        "rows": int(identity.get("rows", 0)),
        "first": str(identity.get("first", "")),
        "last": str(identity.get("last", "")),
        "tail_fingerprint": market_tail_fingerprint(df),
    }


def market_prefix_matches(df: pd.DataFrame, state: dict[str, Any] | None) -> bool:
    """Return True when current market data safely extends a cached prefix."""
    if df is None or df.empty or not state:
        return False
    last_text = str(state.get("last", "")).strip()
    expected = str(state.get("tail_fingerprint", "")).strip()
    if not last_text or not expected:
        return False
    try:
        last_date = pd.Timestamp(last_text)
    except (TypeError, ValueError):
        return False
    index = _normalized_index(df)
    if last_date not in index:
        return False
    current = market_tail_fingerprint(df, end_date=last_date)
    return bool(current and current == expected)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".json", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".parquet", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_indicator_cache(data_path: Path) -> pd.DataFrame | None:
    try:
        cached = pd.read_parquet(data_path)
        cached.index = pd.to_datetime(cached.index, errors="coerce")
        cached = cached[~cached.index.isna()].sort_index()
        return cached if not cached.empty else None
    except (OSError, ValueError, TypeError, ImportError):
        return None


def _align_cumulative_tail(cached: pd.DataFrame, tail: pd.DataFrame, anchor: pd.Timestamp) -> None:
    """Align cumulative indicators (OBV/A-D) to the cached historical level."""
    if anchor not in cached.index or anchor not in tail.index:
        return
    for column in _CUMULATIVE_COLUMNS:
        if column not in cached.columns or column not in tail.columns:
            continue
        old_value = pd.to_numeric(pd.Series([cached.at[anchor, column]]), errors="coerce").iloc[0]
        new_value = pd.to_numeric(pd.Series([tail.at[anchor, column]]), errors="coerce").iloc[0]
        if pd.notna(old_value) and pd.notna(new_value):
            tail[column] = pd.to_numeric(tail[column], errors="coerce") + float(old_value - new_value)


def load_or_compute_indicators(
    ticker: str,
    frame: pd.DataFrame,
    compute_fn: Callable[[pd.DataFrame], pd.DataFrame],
    *,
    source_path: Path | None = None,
    enabled: bool = True,
) -> tuple[pd.DataFrame, bool]:
    """Return indicators, incrementally extending a valid persistent cache."""
    if frame is None or frame.empty:
        return frame, False
    if not enabled or source_path is None or not source_path.exists():
        return compute_fn(frame.copy()), False

    source = frame.copy(deep=False)
    source.index = _normalized_index(source)
    source = source[~source.index.isna()].sort_index()
    signature = data_signature(source, source_path)
    stem = _safe_stem(ticker)
    data_path = INDICATOR_CACHE_DIR / f"{stem}.parquet"
    meta_path = INDICATOR_CACHE_DIR / f"{stem}.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        meta = {}

    cached = None
    if (
        data_path.exists()
        and meta.get("version") == INDICATOR_CACHE_VERSION
        and meta.get("scoring_version") == SCORING_VERSION
    ):
        cached = _read_indicator_cache(data_path)

    if cached is not None:
        source_last = pd.Timestamp(source.index.max())
        cache_last = pd.Timestamp(cached.index.max())
        if (
            len(cached) == len(source)
            and source_last == cache_last
            and market_tail_fingerprint(source) == market_tail_fingerprint(cached)
        ):
            if meta.get("signature") != signature:
                try:
                    _atomic_json(
                        meta_path,
                        {
                            "version": INDICATOR_CACHE_VERSION,
                            "scoring_version": SCORING_VERSION,
                            "signature": signature,
                            **market_cache_state(source),
                        },
                    )
                except (OSError, ValueError, TypeError):
                    pass
            return cached, True

        cached_state = {
            "rows": len(cached),
            "first": str(cached.index.min()),
            "last": str(cache_last),
            "tail_fingerprint": market_tail_fingerprint(cached),
        }
        if source_last > cache_last and market_prefix_matches(source, cached_state):
            cache_last_pos = int(source.index.get_indexer([cache_last])[0])
            start = max(0, cache_last_pos - max(252, int(INDICATOR_INCREMENTAL_LOOKBACK_BARS)))
            tail_raw = source.iloc[start:].copy()
            tail = compute_fn(tail_raw)
            tail.index = pd.to_datetime(tail.index, errors="coerce")
            _align_cumulative_tail(cached, tail, cache_last)
            appended = tail.loc[tail.index > cache_last]
            if not appended.empty:
                merged = pd.concat([cached, appended], axis=0)
                merged = merged[~merged.index.duplicated(keep="last")].sort_index()
                if len(merged) == len(source) and pd.Timestamp(merged.index.max()) == source_last:
                    try:
                        _atomic_parquet(data_path, merged)
                        _atomic_json(
                            meta_path,
                            {
                                "version": INDICATOR_CACHE_VERSION,
                                "scoring_version": SCORING_VERSION,
                                "signature": signature,
                                **market_cache_state(source),
                            },
                        )
                    except (OSError, ValueError, TypeError, ImportError):
                        pass
                    return merged, True

    enriched = compute_fn(source.copy())
    try:
        _atomic_parquet(data_path, enriched)
        _atomic_json(
            meta_path,
            {
                "version": INDICATOR_CACHE_VERSION,
                "scoring_version": SCORING_VERSION,
                "signature": signature,
                **market_cache_state(source),
            },
        )
    except (OSError, ValueError, TypeError, ImportError):
        pass
    return enriched, False


def backtest_cache_key(payload: dict[str, Any]) -> str:
    normalized = {
        "version": BACKTEST_CACHE_VERSION,
        "scoring_version": SCORING_VERSION,
        **payload,
    }
    raw = json.dumps(normalized, sort_keys=True, ensure_ascii=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _backtest_path(ticker: str) -> Path:
    return BACKTEST_CACHE_DIR / f"{_safe_stem(ticker)}.json.gz"


def load_backtest_cache_state(ticker: str, key: str) -> dict[str, Any] | None:
    path = _backtest_path(ticker)
    if not key or not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("key") != key or not isinstance(payload.get("samples"), list):
            return None
        return payload
    except (OSError, EOFError, TypeError, ValueError, json.JSONDecodeError):
        return None


def load_backtest_cache(ticker: str, key: str) -> list[dict[str, Any]] | None:
    payload = load_backtest_cache_state(ticker, key)
    return list(payload["samples"]) if payload is not None else None


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def save_backtest_cache(
    ticker: str,
    key: str,
    samples: list[dict[str, Any]],
    *,
    state: dict[str, Any] | None = None,
) -> None:
    if not key:
        return
    BACKTEST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _backtest_path(ticker)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".json.gz", delete=False) as handle:
        temporary = Path(handle.name)
    payload: dict[str, Any] = {"key": key, "samples": samples}
    if state:
        payload["state"] = state
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=3) as stream:
            json.dump(payload, stream, ensure_ascii=False, default=_json_default, separators=(",", ":"))
        temporary.replace(path)
    except (OSError, TypeError, ValueError):
        temporary.unlink(missing_ok=True)
        return
    finally:
        temporary.unlink(missing_ok=True)
