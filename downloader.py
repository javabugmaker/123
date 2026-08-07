"""
downloader.py — TickFlow Free market-data layer for InstitutionScanner.

TickFlow is the only OHLCV/universe provider.  AkShare is intentionally kept
out of this module and is used only by fundamental_data.py for low-frequency
fundamental refreshes.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

try:
    from tickflow import TickFlow
except ImportError:  # pragma: no cover - handled with a clear runtime error
    TickFlow = None  # type: ignore[assignment]

from config import (
    CACHE_DIR,
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
_PRICE_CACHE_SCHEMA_VERSION = "v3-tickflow-forward"
_PRICE_CACHE_DIR = CACHE_DIR / _PRICE_CACHE_SCHEMA_VERSION
_UNIVERSE_CACHE_PATH = CACHE_DIR / "_tickflow_universe.json"
_TICKFLOW_CLIENT: Any | None = None
_INSTRUMENT_META: dict[str, dict[str, Any]] = {}



_AKSHARE_MANAGED_PROXY_ENV: dict[str, str] = {}


def _proxy_url(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    return value if "://" in value else f"http://{value}"


def _windows_system_proxy() -> dict[str, str]:
    """Read WinINET proxy (used by Clash system-proxy mode) for AkShare only."""
    if sys.platform != "win32":
        return {}
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0] or 0)
            if not enabled:
                return {}
            raw = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "").strip()
    except (OSError, ValueError, TypeError):
        return {}
    if not raw:
        return {}
    if ";" not in raw and "=" not in raw:
        proxy = _proxy_url(raw)
        return {"http": proxy, "https": proxy} if proxy else {}
    result: dict[str, str] = {}
    for item in raw.split(";"):
        if "=" not in item:
            continue
        protocol, server = item.split("=", 1)
        protocol = protocol.strip().lower()
        if protocol in {"http", "https"}:
            proxy = _proxy_url(server)
            if proxy:
                result[protocol] = proxy
    if "http" in result and "https" not in result:
        result["https"] = result["http"]
    if "https" in result and "http" not in result:
        result["http"] = result["https"]
    return result


def configure_akshare_proxy_from_system() -> dict[str, str]:
    """Mirror Clash/Windows system proxy into Requests env for AkShare fundamentals."""
    system_proxy = _windows_system_proxy()
    explicit = {
        "http": os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or "",
        "https": os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "",
    }
    if not system_proxy:
        for key, managed in list(_AKSHARE_MANAGED_PROXY_ENV.items()):
            if os.environ.get(key) == managed:
                os.environ.pop(key, None)
            _AKSHARE_MANAGED_PROXY_ENV.pop(key, None)
        return {k: v for k, v in explicit.items() if v}

    resolved = {
        "http": _proxy_url(explicit["http"] or system_proxy.get("http", "")),
        "https": _proxy_url(
            explicit["https"] or system_proxy.get("https", system_proxy.get("http", ""))
        ),
    }
    for protocol, value in resolved.items():
        if not value:
            continue
        upper = f"{protocol.upper()}_PROXY"
        lower = f"{protocol}_proxy"
        if not os.environ.get(upper) and not os.environ.get(lower):
            os.environ[upper] = value
            os.environ[lower] = value
            _AKSHARE_MANAGED_PROXY_ENV[upper] = value
            _AKSHARE_MANAGED_PROXY_ENV[lower] = value
    return {k: v for k, v in resolved.items() if v}


class DownloadError(RuntimeError):
    pass


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


def normalize_data_source(source: str | None = None) -> str:
    value = str(source or _DATA_SOURCE).strip().lower()
    if value in {"", "tickflow", "tickflow-free", "free"}:
        return _DATA_SOURCE
    raise ValueError(f"已移除行情数据源 {source!r}；当前仅支持 TickFlow Free")


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
    valid = (
        cleaned[required].notna().all(axis=1)
        & np.isfinite(cleaned[required]).all(axis=1)
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
    return cast(pd.DataFrame, cleaned.loc[:, keep])


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
    elif "trade_time" in frame.columns:
        renamed.index = pd.to_datetime(frame["trade_time"], errors="coerce")
    return _validate_ohlcv(renamed)


def _load_cache(ticker: str, source: str | None = None) -> pd.DataFrame | None:
    for path, reader in (
        (_cache_path(ticker, source), pd.read_parquet),
        (
            _legacy_cache_path(ticker, source),
            lambda p: pd.read_csv(p, index_col=0, parse_dates=True),
        ),
    ):
        if not path.exists():
            continue
        try:
            return _validate_ohlcv(reader(path))
        except (OSError, ValueError, ImportError, pd.errors.ParserError):
            logger.warning("行情缓存损坏，忽略: %s", path)
    return None


def _save_cache(ticker: str, df: pd.DataFrame, source: str | None = None) -> None:
    validated = _validate_ohlcv(df)
    if validated is None:
        return
    path = _cache_path(ticker, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".parquet", dir=path.parent, delete=False) as fh:
        temporary = Path(fh.name)
    try:
        validated.to_parquet(temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _latest_completed_trading_day(now: datetime | None = None) -> date:
    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    candidate = current.date()
    if current.weekday() < 5 and current.hour * 60 + current.minute >= 15 * 60:
        return candidate
    candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


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
    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    return current.weekday() >= 5 or current.hour * 60 + current.minute >= 15 * 60


def _load_universe_cache() -> dict[str, Any] | None:
    try:
        if not _UNIVERSE_CACHE_PATH.exists():
            return None
        age = datetime.now().timestamp() - _UNIVERSE_CACHE_PATH.stat().st_mtime
        if age > TICKFLOW_UNIVERSE_CACHE_TTL_HOURS * 3600:
            return None
        payload = json.loads(_UNIVERSE_CACHE_PATH.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
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
    # TickFlow HTTP docs cap one instrument metadata batch at 1000 symbols.
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
        if isinstance(rows, list):
            result.extend(row for row in rows if isinstance(row, dict))
    return result


def _ticker_info_from_meta(symbol: str, meta: dict[str, Any], is_etf: bool) -> TickerInfo:
    ext = meta.get("ext") if isinstance(meta.get("ext"), dict) else {}
    total_shares = _number_or_none(ext.get("total_shares"))
    float_shares = _number_or_none(ext.get("float_shares"))
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


def _number_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if np.isfinite(number) and number > 0 else None


def build_ticker_universe(
    include_stocks: bool = True,
    include_etfs: bool = True,
) -> tuple[list[TickerInfo], list[TickerInfo]]:
    cached = _load_universe_cache()
    if cached is None:
        client = _tickflow()
        stock_symbols: list[str] = []
        etf_symbols: list[str] = []
        if include_stocks:
            universe = client.universes.get("CN_Equity_A")
            stock_symbols = [
                normalize_ticker(symbol)
                for symbol in (universe.get("symbols") or [])
                if symbol
            ]
        if include_etfs:
            universe = client.universes.get("CN_ETF")
            etf_symbols = [
                normalize_ticker(symbol)
                for symbol in (universe.get("symbols") or [])
                if symbol
            ]
        all_symbols = list(dict.fromkeys(stock_symbols + etf_symbols))
        metadata = _instrument_batches(all_symbols)
        meta_by_symbol = {
            normalize_ticker(row.get("symbol", "")): row
            for row in metadata
            if row.get("symbol")
        }
        cached = {
            "stocks": stock_symbols,
            "etfs": etf_symbols,
            "metadata": meta_by_symbol,
        }
        _save_universe_cache(cached)

    stock_symbols = [normalize_ticker(s) for s in cached.get("stocks", [])] if include_stocks else []
    etf_symbols = [normalize_ticker(s) for s in cached.get("etfs", [])] if include_etfs else []
    metadata = cached.get("metadata", {})
    if not isinstance(metadata, dict):
        metadata = {}

    stocks: list[TickerInfo] = []
    etfs: list[TickerInfo] = []
    for symbol in stock_symbols:
        meta = metadata.get(symbol, {}) if isinstance(metadata.get(symbol, {}), dict) else {}
        _INSTRUMENT_META[symbol] = meta
        item = _ticker_info_from_meta(symbol, meta, False)
        if not _is_excluded_security_name(item.name):
            stocks.append(item)
    for symbol in etf_symbols:
        meta = metadata.get(symbol, {}) if isinstance(metadata.get(symbol, {}), dict) else {}
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


def _batch_fetch(symbols: list[str]) -> dict[str, pd.DataFrame]:
    if not symbols:
        return {}
    client = _tickflow()
    try:
        raw = client.klines.batch(
            symbols,
            period="1d",
            count=_history_count(),
            adjust=TICKFLOW_ADJUST,
            as_dataframe=True,
            show_progress=False,
            max_workers=TICKFLOW_MAX_WORKERS,
            batch_size=TICKFLOW_BATCH_SIZE,
        )
    except TypeError:
        # Keep compatibility with older SDKs that may not expose batch_size.
        raw = client.klines.batch(
            symbols,
            period="1d",
            count=_history_count(),
            adjust=TICKFLOW_ADJUST,
            as_dataframe=True,
            show_progress=False,
            max_workers=TICKFLOW_MAX_WORKERS,
        )
    except Exception as exc:
        raise DownloadError(f"TickFlow 批量 K 线请求失败: {exc}") from exc

    results: dict[str, pd.DataFrame] = {}
    if not isinstance(raw, dict):
        return results
    for ticker, frame in raw.items():
        symbol = normalize_ticker(ticker)
        normalized = _normalize_tickflow_frame(frame)
        if normalized is not None and not normalized.empty:
            results[symbol] = normalized
    return results


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
    try:
        client = _tickflow()
        frame = client.klines.get(
            ticker,
            period="1d",
            count=_history_count(),
            adjust=TICKFLOW_ADJUST,
            as_dataframe=True,
        )
    except Exception as exc:
        if cached is not None:
            logger.warning("TickFlow 更新 %s 失败，继续使用缓存: %s", ticker, exc)
            return cached
        logger.warning("TickFlow 获取 %s 失败: %s", ticker, exc)
        return None
    normalized = _normalize_tickflow_frame(frame)
    if normalized is not None:
        _save_cache(ticker, normalized)
        return normalized
    return cached


def download_batch(
    tickers: list[TickerInfo],
    desc: str = "Downloading",
    force: bool = False,
    source: str | None = None,
    cache_first: bool = False,
    skip_tickers: set[str] | None = None,
) -> dict[str, pd.DataFrame]:
    del desc  # TickFlow SDK handles batching; GUI progress is logged below.
    normalize_data_source(source)
    skip = {normalize_ticker(t) for t in (skip_tickers or set())}
    symbols = list(
        dict.fromkeys(
            normalize_ticker(item.ticker)
            for item in tickers
            if item.ticker and normalize_ticker(item.ticker) not in skip
        )
    )
    total = len(symbols)
    results: dict[str, pd.DataFrame] = {}
    pending: list[str] = []

    for symbol in symbols:
        cached = None if force else _load_cache(symbol)
        if cached is not None and (
            cache_first or _cache_has_completed_daily_bar(cached)
        ):
            results[symbol] = cached
        else:
            pending.append(symbol)

    logger.info(
        "DOWNLOAD start: %d tickers via TickFlow Free; %d cache hits, %d need refresh.",
        total,
        len(results),
        len(pending),
    )
    logger.info(
        "DOWNLOAD progress: %d/%d (%d succeeded, %d no-data/failed).",
        len(results),
        total,
        len(results),
        0,
    )

    failed = 0
    if pending:
        try:
            fetched = _batch_fetch(pending)
        except DownloadError as exc:
            logger.error("%s", exc)
            fetched = {}
        for symbol in pending:
            frame = fetched.get(symbol)
            if frame is not None and not frame.empty:
                _save_cache(symbol, frame)
                results[symbol] = frame
            else:
                stale = _load_cache(symbol)
                if stale is not None and not force:
                    results[symbol] = stale
                    logger.debug("TickFlow 无新数据，沿用 %s 本地缓存", symbol)
                else:
                    failed += 1

    logger.info(
        "DOWNLOAD progress: %d/%d (%d succeeded, %d no-data/failed).",
        total,
        total,
        len(results),
        failed,
    )
    logger.info(
        "Download batch complete (TickFlow Free): %d/%d tickers available.",
        len(results),
        total,
    )
    return results


def get_market_cap(ticker: str) -> float | None:
    symbol = normalize_ticker(ticker)
    meta = _INSTRUMENT_META.get(symbol, {})
    ext = meta.get("ext") if isinstance(meta.get("ext"), dict) else {}
    shares = _number_or_none(ext.get("total_shares"))
    if shares is None:
        return None
    frame = _load_cache(symbol)
    if frame is None or frame.empty:
        return None
    close = _number_or_none(frame["Close"].iloc[-1])
    return shares * close if close is not None else None


def _fetch_eastmoney_realtime_price(ticker: str) -> float | None:
    """Legacy compatibility: TickFlow Free has no realtime quote endpoint."""
    frame = _load_cache(ticker)
    return float(frame["Close"].iloc[-1]) if frame is not None and not frame.empty else None


def _fetch_eastmoney_realtime_prices(
    tickers: list[str] | set[str],
) -> dict[str, float]:
    """Legacy compatibility: return latest cached TickFlow daily closes."""
    result: dict[str, float] = {}
    for ticker in tickers:
        value = _fetch_eastmoney_realtime_price(ticker)
        if value is not None and np.isfinite(value):
            result[normalize_ticker(ticker)] = float(value)
    return result


def get_etf_fund_flows(ticker: str) -> float | None:
    del ticker
    return None
