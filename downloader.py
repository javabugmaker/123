"""v51 downloader entrypoint with transparent v4 cache migration.

``downloader_v51`` adds turnover/metadata provenance and writes the new v5
schema. Existing v4 canonical-share Parquet caches are read and copied forward
locally on first access, so this release does not force a full-market network
redownload just because the provenance schema changed.
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

import pandas as pd

import downloader_v51 as _core
from downloader_v51 import *  # noqa: F403

_v51_load_cache = _core._load_cache
_v51_save_cache = _core._save_cache
_v51_price_limit = _core.get_price_limit_pct
_LEGACY_V4_DIR = _core.CACHE_DIR / "v4-tickflow-forward-volume-shares"


def _legacy_v4_paths(ticker: str) -> tuple[Path, Path]:
    stem = _core._safe_cache_stem(ticker)
    return _LEGACY_V4_DIR / f"{stem}.parquet", _LEGACY_V4_DIR / f"{stem}.csv"


def _load_cache(ticker: str, source: str | None = None) -> pd.DataFrame | None:
    current = _v51_load_cache(ticker, source)
    if current is not None:
        return current

    _core.normalize_data_source(source)
    parquet_path, csv_path = _legacy_v4_paths(ticker)
    readers = (
        (parquet_path, pd.read_parquet),
        (
            csv_path,
            lambda path: pd.read_csv(path, index_col=0, parse_dates=True),
        ),
    )
    for path, reader in readers:
        if not path.exists():
            continue
        try:
            validated = _core._validate_ohlcv(reader(path))
        except (
            OSError,
            UnicodeDecodeError,
            ImportError,
            ValueError,
            pd.errors.EmptyDataError,
            pd.errors.ParserError,
        ):
            _core.logger.warning("旧版行情缓存损坏，忽略: %s", path)
            continue
        if validated is None:
            continue
        validated.attrs["volume_unit"] = "shares"
        validated.attrs["amount_unit"] = "CNY"
        validated.attrs["cache_migrated_from_schema"] = "v4-tickflow-forward-volume-shares"
        validated.attrs["corporate_action_rebase_detected"] = False
        try:
            _v51_save_cache(ticker, validated, source)
        except OSError:
            _core.logger.debug("Unable to migrate v4 cache for %s", ticker, exc_info=True)
        return validated
    return None


def _cached_metadata_available(ticker: str) -> bool:
    symbol = _core.normalize_ticker(ticker)
    metadata = _core._INSTRUMENT_META.get(symbol)
    if isinstance(metadata, dict) and metadata:
        return True
    cached = _core._load_universe_cache()
    if not isinstance(cached, dict):
        return False
    metadata_map = cached.get("metadata", {})
    if not isinstance(metadata_map, dict):
        return False
    raw = metadata_map.get(symbol)
    if not isinstance(raw, dict) or not raw:
        return False
    _core._INSTRUMENT_META[symbol] = raw
    return True


@lru_cache(maxsize=8192)
def get_price_limit_pct(ticker: str, is_etf: bool = False) -> float | None:
    """Resolve price limits from already-cached metadata without hidden I/O."""
    if not _cached_metadata_available(ticker):
        return None
    return _v51_price_limit(ticker, is_etf=is_etf)


_core._load_cache = _load_cache
_core.get_price_limit_pct = get_price_limit_pct
_core.PRICE_CACHE_MIGRATION_SOURCE = "v4-tickflow-forward-volume-shares"
sys.modules[__name__] = _core
