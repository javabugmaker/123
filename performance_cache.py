from __future__ import annotations

"""Persistent compute caches used by scanner and historical backtests.

The cache keys deliberately include the raw market-cache file signature and the
scoring version.  A TickFlow refresh/rebase or a scoring change therefore
invalidates stale derived data automatically instead of mixing generations.
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

from config import CACHE_DIR, SCORING_VERSION

INDICATOR_CACHE_VERSION = "v2"
BACKTEST_CACHE_VERSION = "v4"
INDICATOR_CACHE_DIR = CACHE_DIR / f"_indicators_{INDICATOR_CACHE_VERSION}"
BACKTEST_CACHE_DIR = CACHE_DIR / f"_backtest_{BACKTEST_CACHE_VERSION}"


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


def _frame_identity(df: pd.DataFrame) -> dict[str, Any]:
    if df is None or df.empty:
        return {"rows": 0, "first": "", "last": ""}
    index = pd.DatetimeIndex(pd.to_datetime(df.index, errors="coerce")).dropna()
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


def load_or_compute_indicators(
    ticker: str,
    frame: pd.DataFrame,
    compute_fn: Callable[[pd.DataFrame], pd.DataFrame],
    *,
    source_path: Path | None = None,
    enabled: bool = True,
) -> tuple[pd.DataFrame, bool]:
    """Return enriched indicators and whether a persistent cache was reused."""
    if frame is None or frame.empty:
        return frame, False
    if not enabled or source_path is None or not source_path.exists():
        return compute_fn(frame.copy()), False

    signature = data_signature(frame, source_path)
    stem = _safe_stem(ticker)
    data_path = INDICATOR_CACHE_DIR / f"{stem}.parquet"
    meta_path = INDICATOR_CACHE_DIR / f"{stem}.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        if (
            data_path.exists()
            and meta.get("version") == INDICATOR_CACHE_VERSION
            and meta.get("scoring_version") == SCORING_VERSION
            and meta.get("signature") == signature
        ):
            cached = pd.read_parquet(data_path)
            cached.index = pd.to_datetime(cached.index, errors="coerce")
            if len(cached) == len(frame) and not cached.empty:
                source_last = pd.Timestamp(pd.to_datetime(frame.index, errors="coerce").max())
                cache_last = pd.Timestamp(cached.index.max())
                if source_last == cache_last:
                    return cached, True
    except (OSError, ValueError, TypeError, json.JSONDecodeError, ImportError):
        pass

    enriched = compute_fn(frame.copy())
    try:
        _atomic_parquet(data_path, enriched)
        _atomic_json(
            meta_path,
            {
                "version": INDICATOR_CACHE_VERSION,
                "scoring_version": SCORING_VERSION,
                "signature": signature,
                **_frame_identity(frame),
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


def load_backtest_cache(ticker: str, key: str) -> list[dict[str, Any]] | None:
    path = BACKTEST_CACHE_DIR / f"{_safe_stem(ticker)}.json.gz"
    if not key or not path.exists():
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("key") != key or not isinstance(payload.get("samples"), list):
            return None
        return list(payload["samples"])
    except (OSError, EOFError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _json_default(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value)!r}")


def save_backtest_cache(ticker: str, key: str, samples: list[dict[str, Any]]) -> None:
    if not key:
        return
    BACKTEST_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKTEST_CACHE_DIR / f"{_safe_stem(ticker)}.json.gz"
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".json.gz", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=5) as stream:
            json.dump({"key": key, "samples": samples}, stream, ensure_ascii=False, default=_json_default, separators=(",", ":"))
        temporary.replace(path)
    except (OSError, TypeError, ValueError):
        temporary.unlink(missing_ok=True)
        return
    finally:
        temporary.unlink(missing_ok=True)
