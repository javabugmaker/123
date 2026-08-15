"""
downloader.py — TickFlow Free market-data layer for InstitutionScanner.

TickFlow Free is the sole provider for the A-share/ETF universe and historical
daily OHLCV data. AkShare is intentionally not imported here; it is used only
by fundamental_data.py for low-frequency fundamental refreshes.
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

import numpy as np
import pandas as pd

from trading_calendar import latest_completed_trading_day, market_is_closed

try:
    from tickflow import TickFlow
except ImportError:  # pragma: no cover - runtime error explains installation
    TickFlow = None  # type: ignore[assignment]

from config import (
    CACHE_DIR,
    CACHE_READ_THREADS,
    EXCLUDED_SECURITY_KEYWORDS,
    HISTORY_YEARS,
    LOG_DIR,
    TICKFLOW_ADJUST,
    TICKFLOW_BATCH_SIZE,
    TICKFLOW_MAX_WORKERS,
    TICKFLOW_UNIVERSE_CACHE_TTL_HOURS,
    setup_logging,
)

logger = setup_logging(
    "institution_scanner.downloader",
    level=logging.DEBUG,
    log_to_file=True,
    log_dir=LOG_DIR,
)

_DATA_SOURCE = "tickflow"
_DATA_SOURCE_LABEL = "TickFlow Free"
_LEGACY_SOURCE_NAMES = frozenset({"auto", "akshare", "eastmoney", "sina", "tencent"})
_PRICE_CACHE_SCHEMA_VERSION = "v3-tickflow-forward"
_PRICE_CACHE_DIR = CACHE_DIR / _PRICE_CACHE_SCHEMA_VERSION
_UNIVERSE_CACHE_PATH = CACHE_DIR / "_tickflow_universe.json"
_MARKET_MANIFEST_PATH = _PRICE_CACHE_DIR / "_manifest.json"
_MARKET_MANIFEST_DIRTY: dict[str, dict[str, Any]] = {}
_INCREMENTAL_BARS = 90
_REBASE_TOLERANCE = 1e-4

_TICKFLOW_CLIENT: Any | None = None
_INSTRUMENT_META: dict[str, dict[str, Any]] = {}


class DownloadError(RuntimeError):
    pass


DownloadProgressCallback = Callable[[int, int, int, int], None]


@dataclass
class TickerInfo:
    ticker: str
    name: str = ""
    exchange: str = ""
    sector: str = ""
    industry: str = ""
    is_etf: bool = False
    asset_type: str = "stock"
    market_cap: float | None = None
    total_shares: float | None = None
    float_shares: float | None = None


def _log_download_progress(
    completed: int, total: int, successful: int, skipped: int
) -> None:
    """Stable, throttled GUI/test log format used by the scan progress parser."""
    interval = max(1, total // 100)
    if completed == 1 or completed == total or completed % interval == 0:
        logger.info(
            "DOWNLOAD progress: %d/%d (%d succeeded, %d no-data/failed).",
            completed,
            total,
            successful,
            skipped,
        )


def _notify_download_progress(
    callback: DownloadProgressCallback | None,
    completed: int,
    total: int,
    successful: int,
    skipped: int,
) -> None:
    if callback is not None:
        try:
            callback(int(completed), int(total), int(successful), int(skipped))
        except Exception:
            logger.debug("Download progress callback failed.", exc_info=True)
    _log_download_progress(completed, total, successful, skipped)


def _request_chunks(symbols: list[str]) -> list[list[str]]:
    # TickFlow already parallelises batches internally.  Keeping up to one
    # worker-wave per outer request preserves throughput while allowing the
    # GUI to receive progress between waves instead of waiting for the whole
    # market request to return.
    size = max(1, int(TICKFLOW_BATCH_SIZE) * max(1, int(TICKFLOW_MAX_WORKERS)))
    return [symbols[index : index + size] for index in range(0, len(symbols), size)]


def normalize_data_source(source: str | None = None) -> str:
    """Return the only supported market source.

    Legacy source names are accepted only as migration aliases so old
    checkpoints/CLI tests can be read. They never select another provider.
    """
    value = str(source or _DATA_SOURCE).strip().lower()
    if value in {"", "tickflow", "tickflow-free", "free"} | _LEGACY_SOURCE_NAMES:
        return _DATA_SOURCE
    raise ValueError(f"未知行情数据源 {source!r}；当前仅支持 TickFlow Free")


def get_data_source_label(source: str | None = None) -> str:
    normalize_data_source(source)
    return _DATA_SOURCE_LABEL


def normalize_ticker(ticker: str) -> str:
    normalized = str(ticker).strip().upper()
    if "." in normalized:
        return normalized
    if len(normalized) != 6 or not normalized.isdigit():
        return normalized
    if normalized.startswith(("4", "8", "92")):
        suffix = "BJ"
    elif normalized.startswith(("5", "6")):
        suffix = "SH"
    else:
        suffix = "SZ"
    return f"{normalized}.{suffix}"


def is_etf_ticker(ticker: str) -> bool:
    code = normalize_ticker(ticker).split(".", 1)[0]
    return code.startswith(("15", "16", "50", "51", "56", "58"))


def _is_excluded_security_name(name: str) -> bool:
    normalized = re.sub(r"\s+", "", str(name or "")).upper()
    if "ST" in normalized or "退" in normalized:
        return True
    return any(keyword.upper() in normalized for keyword in EXCLUDED_SECURITY_KEYWORDS)


def _tickflow() -> Any:
    global _TICKFLOW_CLIENT
    if TickFlow is None:
        raise DownloadError(
            '未安装 TickFlow SDK；请运行 pip install "tickflow[all]==0.1.24"'
        )
    if _TICKFLOW_CLIENT is None:
        try:
            _TICKFLOW_CLIENT = TickFlow.free()
        except Exception as exc:
            raise DownloadError(f"TickFlow Free 初始化失败: {exc}") from exc
    return _TICKFLOW_CLIENT


def close_tickflow_client() -> None:
    global _TICKFLOW_CLIENT
    client = _TICKFLOW_CLIENT
    _TICKFLOW_CLIENT = None
    if client is not None and hasattr(client, "close"):
        try:
            client.close()
        except Exception:
            logger.debug("TickFlow client close failed", exc_info=True)


def _safe_cache_stem(ticker: str) -> str:
    value = normalize_ticker(ticker)
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).rstrip(" .")
    return safe or "ticker"


def _cache_path(ticker: str, source: str | None = None) -> Path:
    normalize_data_source(source)
    return _PRICE_CACHE_DIR / f"{_safe_cache_stem(ticker)}.parquet"


def _legacy_cache_path(ticker: str, source: str | None = None) -> Path:
    normalize_data_source(source)
    return _PRICE_CACHE_DIR / f"{_safe_cache_stem(ticker)}.csv"


def _number_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) and number > 0 else None


def _normalize_cn_share_count(value: Any) -> float | None:
    """Return TickFlow CN share-capital metadata in individual shares.

    Historical/free metadata payloads can expose CN share capital at a scale
    that is indistinguishable from 10k-share units.  Treating those small
    values as individual shares makes almost the entire A-share universe look
    smaller than the 100m CNY market-cap floor.  Values already large enough
    to be plausible individual-share counts are preserved; smaller positive
    values are conservatively expanded by 10,000.
    """
    number = _number_or_none(value)
    if number is None:
        return None
    if number < 10_000_000:
        scaled = number * 10_000.0
        if scaled <= 10_000_000_000_000.0:
            return scaled
    return number


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump()
            return dict(dumped) if isinstance(dumped, Mapping) else {}
        except Exception:
            return {}
    if hasattr(value, "__dict__"):
        try:
            return dict(vars(value))
        except TypeError:
            return {}
    return {}


def _extract_universe_symbols(value: Any) -> list[str]:
    data = _as_mapping(value)
    raw = data.get("symbols")
    if raw is None and hasattr(value, "symbols"):
        raw = getattr(value, "symbols")
    if not isinstance(raw, (list, tuple, set)):
        return []
    return list(
        dict.fromkeys(normalize_ticker(symbol) for symbol in raw if str(symbol).strip())
    )


def _validate_ohlcv(df: pd.DataFrame | None) -> pd.DataFrame | None:
    required = ["Open", "High", "Low", "Close", "Volume"]
    if df is None or df.empty or any(column not in df.columns for column in required):
        return None
    cleaned = df.copy()
    cleaned.index = pd.to_datetime(cleaned.index, errors="coerce")
    cleaned = cleaned.loc[cleaned.index.notna()]
    for column in required:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
    if "Amount" in cleaned.columns:
        cleaned["Amount"] = pd.to_numeric(cleaned["Amount"], errors="coerce")
    cleaned = cleaned.sort_index()
    cleaned = cleaned.loc[~cleaned.index.duplicated(keep="last")]
    finite = pd.Series(
        np.isfinite(cleaned[required].to_numpy(dtype=float)).all(axis=1),
        index=cleaned.index,
    )
    valid = (
        cleaned[required].notna().all(axis=1)
        & finite
        & (cleaned["Open"] > 0)
        & (cleaned["High"] > 0)
        & (cleaned["Low"] > 0)
        & (cleaned["Close"] > 0)
        & (cleaned["Volume"] >= 0)
        & (cleaned["High"] >= cleaned[["Open", "Close"]].max(axis=1))
        & (cleaned["Low"] <= cleaned[["Open", "Close"]].min(axis=1))
    )
    cleaned = cleaned.loc[valid]
    if cleaned.empty:
        return None
    keep = required + (["Amount"] if "Amount" in cleaned.columns else [])
    result = cast(pd.DataFrame, cleaned.loc[:, keep])
    result.attrs.update(getattr(df, "attrs", {}))
    result.attrs.setdefault("price_adjustment_mode", TICKFLOW_ADJUST)
    result.attrs.setdefault(
        "adjustment_base_date",
        pd.Timestamp(result.index.max()).strftime("%Y-%m-%d"),
    )
    result.attrs.setdefault("corporate_action_rebase_detected", False)
    return result


def _normalize_tickflow_frame(frame: Any) -> pd.DataFrame | None:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    renamed = frame.rename(
        columns={
            "trade_date": "Date",
            "date": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
            "amount": "Amount",
        }
    ).copy()
    if "Date" in renamed.columns:
        renamed["Date"] = pd.to_datetime(renamed["Date"], errors="coerce")
        renamed = renamed.set_index("Date")
    elif "trade_time" in renamed.columns:
        renamed.index = pd.to_datetime(renamed["trade_time"], errors="coerce")
    return _validate_ohlcv(renamed)


def _load_cache(ticker: str, source: str | None = None) -> pd.DataFrame | None:
    readers = (
        (_cache_path(ticker, source), pd.read_parquet),
        (
            _legacy_cache_path(ticker, source),
            lambda path: pd.read_csv(path, index_col=0, parse_dates=True),
        ),
    )
    for path, reader in readers:
        if not path.exists():
            continue
        try:
            validated = _validate_ohlcv(reader(path))
            if validated is not None:
                # This marker describes the current refresh, not a permanent
                # property of the security.  Parquet may preserve DataFrame
                # attrs, so explicitly clear an earlier run's marker on load.
                validated.attrs["corporate_action_rebase_detected"] = False
            return validated
        except (
            OSError,
            UnicodeDecodeError,
            ImportError,
            ValueError,
            pd.errors.EmptyDataError,
            pd.errors.ParserError,
        ):
            logger.warning("行情缓存损坏，忽略: %s", path)
    return None


def _save_cache(ticker: str, df: pd.DataFrame, source: str | None = None) -> None:
    validated = _validate_ohlcv(df)
    if validated is None:
        return
    path = _cache_path(ticker, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        suffix=".parquet", dir=path.parent, delete=False
    ) as fh:
        temporary = Path(fh.name)
    try:
        validated.to_parquet(temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)



def _record_market_manifest(ticker: str, df: pd.DataFrame) -> None:
    path = _cache_path(ticker)
    try:
        stat = path.stat()
    except OSError:
        return
    index = pd.DatetimeIndex(pd.to_datetime(df.index, errors="coerce")).dropna()
    latest = pd.Timestamp(index.max()).strftime("%Y-%m-%d") if len(index) else ""
    _MARKET_MANIFEST_DIRTY[normalize_ticker(ticker)] = {
        "path": path.name,
        "rows": len(df),
        "last_date": latest,
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "adjust": TICKFLOW_ADJUST,
        "schema": _PRICE_CACHE_SCHEMA_VERSION,
    }


def _flush_market_manifest() -> None:
    if not _MARKET_MANIFEST_DIRTY:
        return
    payload: dict[str, Any] = {}
    try:
        if _MARKET_MANIFEST_PATH.exists():
            loaded = json.loads(_MARKET_MANIFEST_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    payload.update(_MARKET_MANIFEST_DIRTY)
    _MARKET_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = _MARKET_MANIFEST_PATH.with_name(f".{_MARKET_MANIFEST_PATH.name}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(_MARKET_MANIFEST_PATH)
        _MARKET_MANIFEST_DIRTY.clear()
    except OSError:
        logger.debug("Unable to flush market cache manifest", exc_info=True)
    finally:
        temporary.unlink(missing_ok=True)


def _load_caches_parallel(symbols: list[str]) -> dict[str, pd.DataFrame]:
    if not symbols:
        return {}
    workers = min(max(1, int(CACHE_READ_THREADS)), len(symbols))
    if workers <= 1 or len(symbols) < 32:
        return {
            symbol: frame
            for symbol in symbols
            if (frame := _load_cache(symbol)) is not None
        }
    frames: dict[str, pd.DataFrame] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_load_cache, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                frame = future.result()
            except (OSError, ValueError, TypeError, ImportError):
                frame = None
            if frame is not None:
                frames[symbol] = frame
    return frames


def _latest_completed_trading_day(now: datetime | None = None) -> date:
    return latest_completed_trading_day(now)


def _cache_has_completed_daily_bar(
    df: pd.DataFrame | None, now: datetime | None = None
) -> bool:
    if df is None or df.empty:
        return False
    index = pd.DatetimeIndex(df.index).dropna()
    if index.empty:
        return False
    latest = pd.Timestamp(index.max())
    if latest.tzinfo is not None:
        latest = latest.tz_localize(None)
    return latest.date() >= _latest_completed_trading_day(now)


def _is_a_share_market_closed(now: datetime | None = None) -> bool:
    return market_is_closed(now)


def _load_universe_cache() -> dict[str, Any] | None:
    try:
        if not _UNIVERSE_CACHE_PATH.exists():
            return None
        age = datetime.now().timestamp() - _UNIVERSE_CACHE_PATH.stat().st_mtime
        if age > TICKFLOW_UNIVERSE_CACHE_TTL_HOURS * 3600:
            return None
        payload = json.loads(_UNIVERSE_CACHE_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        if "stocks" not in payload or "etfs" not in payload or "metadata" not in payload:
            return None
        return payload
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _save_universe_cache(payload: dict[str, Any]) -> None:
    _UNIVERSE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = _UNIVERSE_CACHE_PATH.with_name(f".{_UNIVERSE_CACHE_PATH.name}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(_UNIVERSE_CACHE_PATH)
    finally:
        temporary.unlink(missing_ok=True)


def _instrument_batches(symbols: list[str]) -> list[dict[str, Any]]:
    client = _tickflow()
    result: list[dict[str, Any]] = []
    for start in range(0, len(symbols), 1000):
        chunk = symbols[start : start + 1000]
        try:
            rows = client.instruments.batch(symbols=chunk)
        except TypeError:
            rows = client.instruments.batch(chunk)
        except Exception as exc:
            logger.warning(
                "TickFlow 标的元数据获取失败 (%d-%d): %s",
                start + 1,
                min(start + len(chunk), len(symbols)),
                exc,
            )
            continue
        if isinstance(rows, (list, tuple)):
            result.extend(mapped for row in rows if (mapped := _as_mapping(row)))
    return result


def _fetch_complete_universe() -> dict[str, Any]:
    client = _tickflow()
    try:
        stocks = _extract_universe_symbols(client.universes.get("CN_Equity_A"))
        etfs = _extract_universe_symbols(client.universes.get("CN_ETF"))
    except Exception as exc:
        raise DownloadError(f"TickFlow 标的池获取失败: {exc}") from exc
    if not stocks:
        raise DownloadError("TickFlow CN_Equity_A 标的池为空")
    if not etfs:
        logger.warning("TickFlow CN_ETF 标的池为空；ETF 扫描将暂时不可用")
    symbols = list(dict.fromkeys(stocks + etfs))
    metadata_rows = _instrument_batches(symbols)
    metadata = {
        normalize_ticker(row.get("symbol", "")): row
        for row in metadata_rows
        if row.get("symbol")
    }
    payload = {"stocks": stocks, "etfs": etfs, "metadata": metadata}
    _save_universe_cache(payload)
    return payload


def _ticker_info_from_meta(
    symbol: str, meta: dict[str, Any], is_etf: bool
) -> TickerInfo:
    ext = meta.get("ext") if isinstance(meta.get("ext"), Mapping) else {}
    total_shares = _normalize_cn_share_count(ext.get("total_shares"))
    float_shares = _normalize_cn_share_count(ext.get("float_shares"))
    name = str(meta.get("name") or "")
    exchange = str(meta.get("exchange") or symbol.rsplit(".", 1)[-1])
    return TickerInfo(
        ticker=symbol,
        name=name,
        exchange=exchange,
        is_etf=is_etf,
        asset_type="etf" if is_etf else "stock",
        total_shares=total_shares,
        float_shares=float_shares,
    )


def build_ticker_universe(
    include_stocks: bool = True,
    include_etfs: bool = True,
) -> tuple[list[TickerInfo], list[TickerInfo]]:
    cached = _load_universe_cache()
    if cached is None:
        cached = _fetch_complete_universe()

    metadata = cached.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    stock_symbols = (
        [normalize_ticker(symbol) for symbol in cached.get("stocks", [])]
        if include_stocks
        else []
    )
    etf_symbols = (
        [normalize_ticker(symbol) for symbol in cached.get("etfs", [])]
        if include_etfs
        else []
    )

    stocks: list[TickerInfo] = []
    etfs: list[TickerInfo] = []
    for symbol in stock_symbols:
        raw_meta = metadata.get(symbol, {})
        meta = raw_meta if isinstance(raw_meta, dict) else {}
        _INSTRUMENT_META[symbol] = meta
        item = _ticker_info_from_meta(symbol, meta, False)
        if not _is_excluded_security_name(item.name):
            stocks.append(item)
    for symbol in etf_symbols:
        raw_meta = metadata.get(symbol, {})
        meta = raw_meta if isinstance(raw_meta, dict) else {}
        _INSTRUMENT_META[symbol] = meta
        item = _ticker_info_from_meta(symbol, meta, True)
        if not _is_excluded_security_name(item.name):
            etfs.append(item)

    stocks.sort(key=lambda item: item.ticker)
    etfs.sort(key=lambda item: item.ticker)
    logger.info(
        "TickFlow universe built: %d stocks, %d ETFs", len(stocks), len(etfs)
    )
    return stocks, etfs


def _history_count() -> int:
    return min(10000, max(320, int(HISTORY_YEARS * 260 + 80)))


def _batch_fetch(
    symbols: list[str],
    count: int | None = None,
) -> dict[str, pd.DataFrame]:
    if not symbols:
        return {}
    client = _tickflow()
    request_count = _history_count() if count is None else max(2, int(count))
    kwargs = {
        "period": "1d",
        "count": request_count,
        "adjust": TICKFLOW_ADJUST,
        "as_dataframe": True,
        "show_progress": False,
        "max_workers": TICKFLOW_MAX_WORKERS,
        "batch_size": TICKFLOW_BATCH_SIZE,
    }
    try:
        raw = client.klines.batch(symbols, **kwargs)
    except TypeError:
        kwargs.pop("batch_size", None)
        raw = client.klines.batch(symbols, **kwargs)
    except Exception as exc:
        raise DownloadError(f"TickFlow 批量 K 线请求失败: {exc}") from exc

    results: dict[str, pd.DataFrame] = {}
    if not isinstance(raw, Mapping):
        return results
    for ticker, frame in raw.items():
        symbol = normalize_ticker(str(ticker))
        normalized = _normalize_tickflow_frame(frame)
        if normalized is not None and not normalized.empty:
            results[symbol] = normalized
    return results


def _fetch_one(ticker: str, count: int | None = None) -> pd.DataFrame | None:
    client = _tickflow()
    request_count = _history_count() if count is None else max(2, int(count))
    try:
        frame = client.klines.get(
            normalize_ticker(ticker),
            period="1d",
            count=request_count,
            adjust=TICKFLOW_ADJUST,
            as_dataframe=True,
        )
    except Exception as exc:
        logger.warning("TickFlow 获取 %s 失败: %s", ticker, exc)
        return None
    return _normalize_tickflow_frame(frame)


def _requires_full_rebase(
    cached: pd.DataFrame,
    recent: pd.DataFrame,
) -> bool:
    """Detect when forward-adjustment history was rebased by a corporate action."""
    common = cached.index.intersection(recent.index)
    if len(common) < 2:
        return True
    sample = common[-min(10, len(common)) :]
    old = pd.to_numeric(cached.loc[sample, "Close"], errors="coerce")
    new = pd.to_numeric(recent.loc[sample, "Close"], errors="coerce")
    valid = old.notna() & new.notna() & old.gt(0)
    if not valid.any():
        return True
    relative = ((new[valid] / old[valid]) - 1.0).abs()
    return bool(relative.max() > _REBASE_TOLERANCE)


def _merge_cached(cached: pd.DataFrame, recent: pd.DataFrame) -> pd.DataFrame:
    combined = cast(pd.DataFrame, pd.concat([cached, recent], axis=0))
    combined = combined.loc[~combined.index.duplicated(keep="last")].sort_index()
    validated = _validate_ohlcv(combined)
    return validated if validated is not None else cached


def download_ticker(
    ticker: str,
    force: bool = False,
    source: str | None = None,
    cache_first: bool = False,
) -> pd.DataFrame | None:
    normalize_data_source(source)
    ticker = normalize_ticker(ticker)
    cached = None if force else _load_cache(ticker)
    if cached is not None and (cache_first or _cache_has_completed_daily_bar(cached)):
        return cached

    if cached is not None and not force:
        recent = _fetch_one(ticker, _INCREMENTAL_BARS)
        if recent is not None and not recent.empty:
            if _requires_full_rebase(cached, recent):
                logger.info("TickFlow 检测到 %s 复权基准变化，重建完整历史。", ticker)
                cached.attrs["corporate_action_rebase_detected"] = True
            else:
                merged = _merge_cached(cached, recent)
                _save_cache(ticker, merged)
                return merged
        else:
            return cached

    full = _fetch_one(ticker)
    if full is not None and not full.empty:
        full.attrs["corporate_action_rebase_detected"] = bool(
            cached is not None
            and cached.attrs.get("corporate_action_rebase_detected", False)
        )
        _save_cache(ticker, full)
        return full
    return cached


def download_batch(
    tickers: list[TickerInfo],
    desc: str = "Downloading",
    force: bool = False,
    source: str | None = None,
    cache_first: bool = False,
    skip_tickers: set[str] | None = None,
    progress_callback: DownloadProgressCallback | None = None,
) -> dict[str, pd.DataFrame]:
    del desc
    normalize_data_source(source)
    skip = {normalize_ticker(ticker) for ticker in (skip_tickers or set())}
    symbols = list(
        dict.fromkeys(
            normalize_ticker(item.ticker)
            for item in tickers
            if item.ticker and normalize_ticker(item.ticker) not in skip
        )
    )
    total = len(symbols)
    results: dict[str, pd.DataFrame] = {}
    stale_cache: dict[str, pd.DataFrame] = {}
    missing: list[str] = []

    cached_frames = {} if force else _load_caches_parallel(symbols)
    for symbol in symbols:
        cached = cached_frames.get(symbol)
        if cached is None:
            missing.append(symbol)
        elif cache_first or _cache_has_completed_daily_bar(cached):
            results[symbol] = cached
        else:
            stale_cache[symbol] = cached

    logger.info(
        "DOWNLOAD start: %d tickers via TickFlow Free; %d fresh cache, "
        "%d incremental, %d full.",
        total,
        len(results),
        len(stale_cache),
        len(missing),
    )
    completed = len(results)
    failed = 0
    _notify_download_progress(
        progress_callback, completed, total, len(results), failed
    )

    rebase: list[str] = []
    if stale_cache:
        stale_symbols = list(stale_cache)
        for batch in _request_chunks(stale_symbols):
            try:
                recent_frames = _batch_fetch(batch, _INCREMENTAL_BARS)
            except DownloadError as exc:
                logger.warning("%s", exc)
                recent_frames = {}

            for symbol in batch:
                cached = stale_cache[symbol]
                recent = recent_frames.get(symbol)
                if recent is None or recent.empty:
                    results[symbol] = cached
                    completed += 1
                    continue
                if _requires_full_rebase(cached, recent):
                    rebase.append(symbol)
                    continue
                merged = _merge_cached(cached, recent)
                _save_cache(symbol, merged)
                results[symbol] = merged
                completed += 1
            _notify_download_progress(
                progress_callback, completed, total, len(results), failed
            )

    full_symbols = list(dict.fromkeys(missing + rebase))
    for batch in _request_chunks(full_symbols):
        try:
            full_frames = _batch_fetch(batch)
        except DownloadError as exc:
            logger.error("%s", exc)
            full_frames = {}

        for symbol in batch:
            frame = full_frames.get(symbol)
            if frame is not None and not frame.empty:
                frame.attrs["corporate_action_rebase_detected"] = symbol in rebase
                _save_cache(symbol, frame)
                results[symbol] = frame
            else:
                old = stale_cache.get(symbol)
                if old is not None and not force:
                    results[symbol] = old
                    logger.warning("TickFlow 无法重建 %s，暂时沿用旧缓存。", symbol)
                else:
                    failed += 1
            completed += 1
        _notify_download_progress(
            progress_callback, completed, total, len(results), failed
        )

    for symbol, frame in results.items():
        _record_market_manifest(symbol, frame)
    _flush_market_manifest()
    if completed != total or total == 0:
        completed = total
        _notify_download_progress(
            progress_callback, completed, total, len(results), failed
        )
    logger.info(
        "Download batch complete (TickFlow Free): %d/%d tickers available.",
        len(results),
        total,
    )
    return results


def _load_or_fetch_meta(symbol: str) -> dict[str, Any]:
    symbol = normalize_ticker(symbol)
    cached = _INSTRUMENT_META.get(symbol)
    if cached:
        return cached
    rows = _instrument_batches([symbol])
    if not rows:
        return {}
    meta = rows[0]
    _INSTRUMENT_META[symbol] = meta
    return meta


def get_market_cap(ticker: str) -> float | None:
    symbol = normalize_ticker(ticker)
    meta = _load_or_fetch_meta(symbol)
    ext = meta.get("ext") if isinstance(meta.get("ext"), Mapping) else {}
    shares = _normalize_cn_share_count(ext.get("total_shares"))
    if shares is None:
        return None
    frame = _load_cache(symbol)
    if frame is None or frame.empty:
        return None
    close = _number_or_none(frame["Close"].iloc[-1])
    return shares * close if close is not None else None


def get_etf_fund_flows(ticker: str) -> float | None:
    del ticker
    return None
