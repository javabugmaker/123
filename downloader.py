"""
downloader.py — A 股与 ETF 多数据源行情及历史数据管理。

负责构建标的池、下载历史 OHLCV 数据、维护增量缓存，并提供限速并行下载与错误恢复。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sys
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm

try:
    import akshare as ak
except ImportError:
    ak = None

from config import (
    CACHE_DIR,
    DOWNLOAD_RATE_LIMIT_PAUSE,
    DOWNLOAD_PROGRESS_HEARTBEAT_SECONDS,
    DOWNLOAD_RETRIES,
    DOWNLOAD_THREADS,
    DOWNLOAD_TIMEOUT,
    EXCLUDED_SECURITY_KEYWORDS,
    HISTORY_YEARS,
    LOG_DIR,
    MARKET_CAP_CACHE_TTL_DAYS,
    UNIVERSE_CACHE_TTL_HOURS,
    setup_logging,
)

logger = setup_logging(
    "institution_scanner.downloader",
    level=logging.DEBUG,
    log_to_file=True,
    log_dir=LOG_DIR,
)


class DownloadError(RuntimeError):
    pass


_DOWNLOAD_ERRORS = (
    DownloadError,
    RuntimeError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    json.JSONDecodeError,
    requests.RequestException,
    pd.errors.EmptyDataError,
    pd.errors.ParserError,
)


def _log_download_progress(
    completed: int, total: int, successful: int, skipped: int
) -> None:
    interval = max(1, total // 100)
    if completed == 1 or completed == total or completed % interval == 0:
        logger.info(
            "DOWNLOAD progress: %d/%d (%d succeeded, %d no-data/failed).",
            completed,
            total,
            successful,
            skipped,
        )


_HTTP = requests.Session()
_HTTP.trust_env = False
_HTTP.headers.update({"User-Agent": "Mozilla/5.0"})

_retry_adapter = HTTPAdapter(
    pool_connections=max(16, DOWNLOAD_THREADS * 2),
    pool_maxsize=max(16, DOWNLOAD_THREADS * 2),
    max_retries=requests.urllib3.Retry(
        total=DOWNLOAD_RETRIES,
        connect=DOWNLOAD_RETRIES,
        read=DOWNLOAD_RETRIES,
        status=1,
        backoff_factor=0.5,
        status_forcelist={429, 500, 502, 503, 504},
        allowed_methods={"GET", "POST"},
    ),
)
_HTTP.mount("https://", _retry_adapter)
_HTTP.mount("http://", _retry_adapter)
_EASTMONEY_HOSTS = ("push2delay.eastmoney.com", "push2.eastmoney.com")
_EASTMONEY_HISTORY_HOSTS = ("push2delay.eastmoney.com", "push2his.eastmoney.com")
_UNIVERSE_CACHE_PATH = CACHE_DIR / "_a_share_universe.json"
_ETF_UNIVERSE_CACHE_PATH = CACHE_DIR / "_a_share_etf_universe.json"
_DOWNLOAD_RATE_LOCK = threading.Lock()
_LAST_DOWNLOAD_AT = 0.0
_PRICE_CACHE_SCHEMA_VERSION = "v2-provider-consistent"

_HOST_FAILURE_COUNTER: dict[str, int] = {}
_HOST_FAILURE_LOCK = threading.Lock()
_HOST_CIRCUIT_BREAKER_THRESHOLD = 5
_HOST_CIRCUIT_BREAKER_RESET_SECONDS = 60
_HOST_CIRCUIT_OPEN_UNTIL: dict[str, float] = {}


def _is_host_available(host: str) -> bool:
    with _HOST_FAILURE_LOCK:
        open_until = _HOST_CIRCUIT_OPEN_UNTIL.get(host, 0.0)
        if open_until > time.monotonic():
            return False
        if open_until > 0:
            _HOST_FAILURE_COUNTER.pop(host, None)
            _HOST_CIRCUIT_OPEN_UNTIL.pop(host, None)
        return True


def _record_host_failure(host: str) -> None:
    with _HOST_FAILURE_LOCK:
        count = _HOST_FAILURE_COUNTER.get(host, 0) + 1
        _HOST_FAILURE_COUNTER[host] = count
        if count >= _HOST_CIRCUIT_BREAKER_THRESHOLD:
            _HOST_CIRCUIT_OPEN_UNTIL[host] = (
                time.monotonic() + _HOST_CIRCUIT_BREAKER_RESET_SECONDS
            )
            logger.warning(
                "主机 %s 已触发断路器，暂停 %ds",
                host,
                _HOST_CIRCUIT_BREAKER_RESET_SECONDS,
            )


def _record_host_success(host: str) -> None:
    with _HOST_FAILURE_LOCK:
        _HOST_FAILURE_COUNTER.pop(host, None)


def _wait_for_download_slot() -> None:
    global _LAST_DOWNLOAD_AT
    with _DOWNLOAD_RATE_LOCK:
        now = time.monotonic()
        delay = DOWNLOAD_RATE_LIMIT_PAUSE - (now - _LAST_DOWNLOAD_AT)
        if delay > 0:
            time.sleep(delay)
        _LAST_DOWNLOAD_AT = time.monotonic()


def _eastmoney_get(
    path: str, params: dict[str, Any], history: bool = False
) -> requests.Response:
    hosts = _EASTMONEY_HISTORY_HOSTS if history else _EASTMONEY_HOSTS
    last_error: Exception | None = None
    headers = {"Referer": "https://quote.eastmoney.com/"}
    for attempt in range(DOWNLOAD_RETRIES + 1):
        for host in hosts:
            if not _is_host_available(host):
                logger.debug("主机 %s 已被断路器熔断，跳过", host)
                continue
            try:
                response = _HTTP.get(
                    f"https://{host}{path}",
                    params=params,
                    headers=headers,
                    timeout=(
                        DOWNLOAD_TIMEOUT * 1.5 if attempt > 0 else DOWNLOAD_TIMEOUT
                    ),
                )
                response.raise_for_status()
                _record_host_success(host)
                return response
            except requests.ConnectionError as exc:
                last_error = exc
                _record_host_failure(host)
                logger.debug(
                    "连接失败 %s (attempt %d/%d): %s",
                    host,
                    attempt + 1,
                    DOWNLOAD_RETRIES + 1,
                    exc,
                )
            except requests.Timeout as exc:
                last_error = exc
                _record_host_failure(host)
                delay = 3 * (attempt + 1)
                logger.debug(
                    "超时 %s (attempt %d/%d): 等待 %ds 后重试...",
                    host,
                    attempt + 1,
                    DOWNLOAD_RETRIES + 1,
                    delay,
                )
                time.sleep(delay)
            except requests.RequestException as exc:
                last_error = exc
                _record_host_failure(host)
                logger.debug(
                    "HTTP 错误 %s (attempt %d/%d): %s",
                    host,
                    attempt + 1,
                    DOWNLOAD_RETRIES + 1,
                    exc,
                )
        if attempt < DOWNLOAD_RETRIES:
            wait_time = 2 ** (attempt + 1)
            logger.debug(
                "所有 host 尝试失败，等待 %ds 后进入第 %d 轮重试...",
                wait_time,
                attempt + 2,
            )
            time.sleep(wait_time)
    raise DownloadError(
        f"东方财富接口连接失败 (已重试 {DOWNLOAD_RETRIES + 1} 轮): {last_error}"
    ) from last_error


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
    return any(keyword.upper() in normalized for keyword in EXCLUDED_SECURITY_KEYWORDS)


_STATIC_A_STOCKS: list[tuple[str, str, str, str]] = [
    ("000001.SZ", "平安银行", "金融", "银行"),
    ("000002.SZ", "万科A", "地产", "房地产"),
    ("000333.SZ", "美的集团", "消费", "家电"),
    ("000538.SZ", "云南白药", "医药", "中药"),
    ("000858.SZ", "五粮液", "消费", "白酒"),
    ("002594.SZ", "比亚迪", "消费", "汽车"),
    ("002352.SZ", "顺丰控股", "物流", "物流"),
    ("300750.SZ", "宁德时代", "消费", "新能源"),
    ("300014.SZ", "亿纬锂能", "消费", "新能源"),
    ("300059.SZ", "东方财富", "金融", "互联网金融"),
    ("600000.SH", "浦发银行", "金融", "银行"),
    ("600036.SH", "招商银行", "金融", "银行"),
    ("600519.SH", "贵州茅台", "消费", "白酒"),
    ("601318.SH", "中国平安", "金融", "保险"),
    ("601166.SH", "兴业银行", "金融", "银行"),
    ("601857.SH", "中国石油", "能源", "石油"),
    ("601988.SH", "中国银行", "金融", "银行"),
    ("603259.SH", "药明康德", "医药", "生物医药"),
    ("603501.SH", "韦尔股份", "消费", "电子"),
    ("688981.SH", "中芯国际", "消费", "半导体"),
    ("688599.SH", "天合光能", "消费", "光伏"),
    ("600104.SH", "上汽集团", "消费", "汽车"),
    ("600028.SH", "中国石化", "能源", "石化"),
    ("600900.SH", "长江电力", "公用事业", "电力"),
    ("601899.SH", "紫金矿业", "采矿", "有色金属"),
    ("601989.SH", "中国重工", "工业", "装备"),
    ("603799.SH", "华友钴业", "消费", "新能源"),
]

_STATIC_A_ETFS: list[tuple[str, str]] = [
    ("510300.SH", "沪深300ETF"),
    ("510500.SH", "中证500ETF"),
    ("159901.SZ", "深证100ETF"),
    ("159915.SZ", "创业板ETF"),
    ("515000.SH", "上证红利ETF"),
    ("512690.SH", "科创50ETF"),
    ("159952.SZ", "医药ETF"),
    ("518880.SH", "黄金ETF"),
    ("159996.SZ", "新能源车ETF"),
    ("512980.SH", "证券ETF"),
    ("510880.SH", "红利ETF"),
    ("159997.SZ", "芯片ETF"),
]

_INVALID_SUFFIXES: set[str] = {"W", "R", "P", "Z"}
_INVALID_CHARS: set[str] = {"=", "$", "^", ".", "+", "-"}
_REJECTED_EXCHANGES: set[str] = {"OTC", "OTC BB", "OTCQB", "PINX", "GREY"}


def _is_viable_ticker(symbol: str, exchange: str = "") -> bool:
    if not symbol or len(symbol) > 5:
        return False
    if any(ch in _INVALID_CHARS for ch in symbol):
        return False
    if len(symbol) >= 4 and symbol[-1].upper() in _INVALID_SUFFIXES:
        return False
    return not (exchange and exchange.upper() in _REJECTED_EXCHANGES)


def _is_rejected_stock_name(name: str) -> bool:
    normalized = str(name or "").upper().replace(" ", "")
    return "ST" in normalized or "退" in normalized or "退市" in normalized


def _load_universe_cache(
    path: Path, require_fresh: bool = True
) -> list[dict[str, Any]]:
    try:
        if not path.exists():
            return []
        age_seconds = max(0.0, time.time() - path.stat().st_mtime)
        if require_fresh and age_seconds > UNIVERSE_CACHE_TTL_HOURS * 3600:
            return []
        rows = json.loads(path.read_text(encoding="utf-8"))
        return rows if isinstance(rows, list) else []
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []


def _save_universe_cache(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as file:
        temporary = Path(file.name)
        json.dump(rows, file, ensure_ascii=False)
    try:
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _fetch_a_share_stocks() -> list[TickerInfo]:
    params = {
        "pn": 1,
        "pz": 100,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        "fields": "f12,f13,f14,f20,f100,f102",
    }
    rows = _load_universe_cache(_UNIVERSE_CACHE_PATH)
    if rows:
        logger.info("Loaded %d A-share stocks from local universe cache.", len(rows))
    else:
        try:
            response = _eastmoney_get("/api/qt/clist/get", params)
            data = response.json().get("data") or {}
            rows = list(data.get("diff") or [])
            if not rows:
                raise RuntimeError("东方财富未返回A股证券列表")
            total = int(data.get("total") or len(rows))
            for page in range(2, math.ceil(total / params["pz"]) + 1):
                params["pn"] = page
                response = _eastmoney_get("/api/qt/clist/get", params)
                rows.extend((response.json().get("data") or {}).get("diff") or [])
            _save_universe_cache(_UNIVERSE_CACHE_PATH, rows)
        except _DOWNLOAD_ERRORS:
            rows = _load_universe_cache(_UNIVERSE_CACHE_PATH, require_fresh=False)
        if rows:
            logger.warning("证券池接口不可用，使用本地缓存的 %d 只A股。", len(rows))
        else:
            logger.warning("证券池接口不可用，使用内置的 %d 只A股。", len(_STATIC_A_STOCKS))
            return [
                TickerInfo(
                    ticker=ticker,
                    name=name,
                    sector=sector,
                    industry=industry,
                    asset_type="stock",
                )
                for ticker, name, sector, industry in _STATIC_A_STOCKS
            ]

    tickers: list[TickerInfo] = []
    for row in rows:
        code = str(row.get("f12") or "").zfill(6)
        market = int(row.get("f13") or 0)
        if not code.isdigit() or len(code) != 6:
            continue
        name = str(row.get("f14") or "")
        if _is_rejected_stock_name(name):
            continue
        suffix = (
            "SH"
            if market == 1
            else "BJ"
            if code.startswith(("4", "8", "92"))
            else "SZ"
        )
        market_cap = row.get("f20")
        tickers.append(
            TickerInfo(
                ticker=f"{code}.{suffix}",
                name=name,
                exchange={"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}[suffix],
                sector=str(row.get("f102") or ""),
                industry=str(row.get("f100") or ""),
                asset_type="stock",
                market_cap=(
                    float(market_cap)
                    if isinstance(market_cap, (int, float)) and market_cap > 0
                    else None
                ),
            )
        )
    if len(tickers) < 4000:
        raise DownloadError(f"A股证券列表数量异常，仅获取到 {len(tickers)} 只")
    logger.info("Loaded %d A-share stocks.", len(tickers))
    return tickers


def _fetch_a_share_etfs() -> list[TickerInfo]:
    params = {
        "pn": 1,
        "pz": 100,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f3",
        "fs": "m:0+t:9,m:1+t:9",
        "fields": "f12,f13,f14,f20,f100,f102",
    }
    rows = _load_universe_cache(_ETF_UNIVERSE_CACHE_PATH)
    if rows:
        logger.info("Loaded %d A-share ETFs from local universe cache.", len(rows))
    else:
        try:
            response = _eastmoney_get("/api/qt/clist/get", params)
            data = response.json().get("data") or {}
            rows = list(data.get("diff") or [])
            if not rows:
                raise RuntimeError("东方财富未返回ETF列表")
            total = int(data.get("total") or len(rows))
            for page in range(2, math.ceil(total / params["pz"]) + 1):
                params["pn"] = page
                response = _eastmoney_get("/api/qt/clist/get", params)
                rows.extend((response.json().get("data") or {}).get("diff") or [])
            _save_universe_cache(_ETF_UNIVERSE_CACHE_PATH, rows)
        except _DOWNLOAD_ERRORS:
            rows = _load_universe_cache(_ETF_UNIVERSE_CACHE_PATH, require_fresh=False)
            if rows:
                logger.warning("ETF证券池接口不可用，使用本地缓存的 %d 只ETF。", len(rows))
            else:
                logger.exception("获取全量ETF失败")
                return [
                    TickerInfo(
                        ticker=symbol,
                        name=name,
                        exchange="SSE/SZSE",
                        is_etf=True,
                        asset_type="etf",
                    )
                    for symbol, name in _STATIC_A_ETFS
                    if not _is_excluded_security_name(name)
                ]

    etfs: list[TickerInfo] = []
    allowed_prefixes = ("15", "16", "50", "51", "56", "58")
    for row in rows:
        code = str(row.get("f12") or "").zfill(6)
        market = int(row.get("f13") or 0)
        name = str(row.get("f14") or "")
        if (
            not code.isdigit()
            or len(code) != 6
            or not code.startswith(allowed_prefixes)
        ):
            continue
        if _is_excluded_security_name(name):
            continue
        if name.endswith(("R", "A")) or "分级" in name or "退市" in name:
            continue
        suffix = "SH" if market == 1 else "SZ"
        etfs.append(
            TickerInfo(
                ticker=f"{code}.{suffix}",
                name=name,
                exchange={"SH": "SSE", "SZ": "SZSE"}[suffix],
                is_etf=True,
                asset_type="etf",
                market_cap=(
                    float(row["f20"])
                    if isinstance(row.get("f20"), (int, float)) and row["f20"] > 0
                    else None
                ),
            )
        )
    unique = {item.ticker: item for item in etfs}
    result = sorted(unique.values(), key=lambda item: item.ticker)
    logger.info("Loaded %d A-share ETFs.", len(result))
    return result


def build_ticker_universe(
    include_stocks: bool = True,
    include_etfs: bool = True,
) -> tuple[list[TickerInfo], list[TickerInfo]]:
    stocks: dict[str, TickerInfo] = {}
    etfs: dict[str, TickerInfo] = {}
    if include_stocks:
        for ti in _fetch_a_share_stocks():
            if not ti.is_etf:
                stocks.setdefault(ti.ticker.upper(), ti)
    if include_etfs:
        for ti in _fetch_a_share_etfs():
            etfs.setdefault(ti.ticker.upper(), ti)
    stock_list = sorted(stocks.values(), key=lambda x: x.ticker)
    etf_list = sorted(etfs.values(), key=lambda x: x.ticker)
    logger.info("Universe built: %d stocks, %d ETFs", len(stock_list), len(etf_list))
    return stock_list, etf_list


def _safe_cache_stem(ticker: str, source: str | None = None) -> str:
    value = str(ticker).strip()
    if source:
        value = f"{value}__{normalize_data_source(source)}"
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).rstrip(" .")
    if not safe:
        safe = "ticker"
    if len(safe) > 100:
        safe = f"ticker_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"
    return safe


def _price_cache_root(source: str | None = None) -> Path:
    # Source-bound histories live in a schema directory. This invalidates old
    # mixed-provider caches while preserving the public cache filename shape.
    return CACHE_DIR / _PRICE_CACHE_SCHEMA_VERSION if source else CACHE_DIR


def _cache_path(ticker: str, source: str | None = None) -> Path:
    return _price_cache_root(source) / f"{_safe_cache_stem(ticker, source)}.parquet"


def _legacy_cache_path(ticker: str, source: str | None = None) -> Path:
    return _price_cache_root(source) / f"{_safe_cache_stem(ticker, source)}.csv"


def _to_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if np.isfinite(parsed) else None


def _drop_missing_close(df: pd.DataFrame) -> pd.DataFrame:
    return cast(pd.DataFrame, df.loc[df["Close"].notna()])


def _is_a_share_market_closed(now: datetime | None = None) -> bool:
    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    if current.weekday() >= 5:
        return True
    return current.hour * 60 + current.minute >= 15 * 60


def _latest_completed_trading_day(now: datetime | None = None) -> date:
    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    candidate = current.date()
    if current.weekday() < 5 and current.hour * 60 + current.minute >= 15 * 60:
        return candidate
    while candidate.weekday() >= 5 or candidate == current.date():
        candidate -= timedelta(days=1)
    return candidate


def _cache_has_completed_daily_bar(
    df: pd.DataFrame, now: datetime | None = None
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


def _validate_ohlcv(df: pd.DataFrame) -> pd.DataFrame | None:
    required = ["Open", "High", "Low", "Close", "Volume"]
    if df is None or df.empty or any(column not in df.columns for column in required):
        return None
    cleaned = df[required].copy()
    cleaned.index = pd.to_datetime(cleaned.index, errors="coerce")
    for column in required:
        cleaned[column] = pd.to_numeric(cleaned[column], errors="coerce")
    cleaned = cleaned.loc[cleaned.index.notna()].sort_index()
    cleaned = cleaned.loc[~cleaned.index.duplicated(keep="last")]
    if cleaned.empty:
        return None
    latest_index = pd.Timestamp(cast(Any, cleaned.index.max()))
    if latest_index > pd.Timestamp.now(tz="UTC").tz_localize(None):
        return None
    valid_ohlc = (
        cleaned[["Open", "High", "Low", "Close"]].notna().all(axis=1)
        & (cleaned["Open"] > 0)
        & (cleaned["High"] > 0)
        & (cleaned["Low"] > 0)
        & (cleaned["Close"] > 0)
        & (cleaned["High"] >= cleaned[["Open", "Close"]].max(axis=1))
        & (cleaned["Low"] <= cleaned[["Open", "Close"]].min(axis=1))
    )
    valid_close_volume = (
        cleaned["Close"].notna()
        & np.isfinite(cleaned["Close"])
        & (cleaned["Close"] > 0)
        & cleaned["Volume"].notna()
        & np.isfinite(cleaned["Volume"])
        & (cleaned["Volume"] >= 0)
    )
    if valid_ohlc.mean() < 0.95 or valid_close_volume.mean() < 0.95:
        return None
    cleaned = cleaned.loc[valid_ohlc & valid_close_volume]
    return cleaned if not cleaned.empty else None


def _load_cache(ticker: str, source: str | None = None) -> pd.DataFrame | None:
    parquet_path = _cache_path(ticker, source)
    csv_path = _legacy_cache_path(ticker, source)
    readers = (
        (parquet_path, pd.read_parquet),
        (csv_path, lambda path: pd.read_csv(path, index_col=0, parse_dates=True)),
    )
    for path, reader in readers:
        if not path.exists():
            continue
        try:
            return _validate_ohlcv(reader(path))
        except (
            OSError,
            UnicodeDecodeError,
            ImportError,
            ValueError,
            pd.errors.EmptyDataError,
            pd.errors.ParserError,
        ):
            logger.warning(
                "Corrupted cache for %s at %s — trying next format.", ticker, path.name
            )
    return None


def _save_cache(ticker: str, df: pd.DataFrame, source: str | None = None) -> None:
    path = _cache_path(ticker, source)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        suffix=path.suffix, dir=path.parent, delete=False
    ) as file:
        temporary = Path(file.name)
    try:
        df.to_parquet(temporary)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _meta_path(ticker: str) -> Path:
    return CACHE_DIR / f"{_safe_cache_stem(ticker)}.json"


def _save_meta(ticker: str, data: dict) -> None:
    _meta_path(ticker).write_text(json.dumps(data, default=str))


def _load_meta(ticker: str) -> dict | None:
    path = _meta_path(ticker)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _fetch_market_cap(ticker: str) -> float | None:
    ticker = normalize_ticker(ticker)
    try:
        code, suffix = ticker.upper().split(".", 1)
        market = "1" if suffix == "SH" else "0"
        response = _eastmoney_get(
            "/api/qt/stock/get", {"secid": f"{market}.{code}", "fields": "f20"}
        )
        response.raise_for_status()
        mc = (response.json().get("data") or {}).get("f20")
        if mc is not None and isinstance(mc, (int, float)) and mc > 0:
            return float(mc)
    except (DownloadError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return None


def get_market_cap(ticker: str) -> float | None:
    meta = _load_meta(ticker)
    if meta and "marketCap" in meta:
        try:
            fetched_at = datetime.fromisoformat(str(meta.get("fetchedAt", "")))
            if fetched_at.tzinfo is None:
                fetched_at = fetched_at.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - fetched_at.astimezone(timezone.utc)
            if age <= timedelta(days=MARKET_CAP_CACHE_TTL_DAYS):
                return float(meta["marketCap"])
        except (TypeError, ValueError):
            pass
    mc = _fetch_market_cap(ticker)
    if mc is not None:
        _save_meta(
            ticker,
            {"marketCap": mc, "fetchedAt": datetime.now(timezone.utc).isoformat()},
        )
    return mc


def _download_from_sina(
    ticker: str, start_date: datetime | None = None
) -> pd.DataFrame | None:
    code, suffix = ticker.upper().split(".", 1)
    if suffix == "BJ":
        return None
    symbol = ("sh" if suffix == "SH" else "sz") + code
    response = _HTTP.get(
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20_data=/CN_MarketDataService.getKLineData",
        params={"symbol": symbol, "scale": 240, "ma": "no", "datalen": 1023},
        headers={"Referer": "https://finance.sina.com.cn/"},
        timeout=DOWNLOAD_TIMEOUT,
    )
    response.raise_for_status()
    match = re.search(r"var _data=\((\[.*?\])\);", response.text, re.DOTALL)
    if not match:
        return None
    rows = json.loads(match.group(1))
    if not rows:
        return None
    df = pd.DataFrame(rows).rename(
        columns={
            "day": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }
    )
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for column in ("Open", "High", "Low", "Close", "Volume"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = cast(
        pd.DataFrame,
        df.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]],
    )
    df = _drop_missing_close(df).sort_index()
    return df.loc[df.index >= pd.Timestamp(start_date)] if start_date is not None else df


def _download_from_tencent(
    ticker: str, start_date: datetime | None = None
) -> pd.DataFrame | None:
    code, suffix = ticker.upper().split(".", 1)
    if suffix == "BJ":
        return None
    symbol = ("sh" if suffix == "SH" else "sz") + code
    end_date = datetime.now(timezone.utc).replace(tzinfo=None)
    start_limit = start_date or end_date - timedelta(days=HISTORY_YEARS * 365 + 30)
    rows: list[list[str]] = []
    while end_date > start_limit:
        response = _HTTP.get(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            params={
                "param": f"{symbol},day,{start_limit:%Y-%m-%d},{end_date:%Y-%m-%d},640,qfq"
            },
            headers={"Referer": "https://gu.qq.com/"},
            timeout=DOWNLOAD_TIMEOUT,
        )
        response.raise_for_status()
        data = (response.json().get("data") or {}).get(symbol) or {}
        batch = data.get("qfqday") or data.get("day") or []
        if not batch:
            break
        rows.extend(batch)
        oldest_date = pd.Timestamp(str(batch[0][0])).date()
        if oldest_date <= start_limit.date() or oldest_date >= end_date.date():
            break
        end_date = datetime.combine(oldest_date, datetime.min.time()) - timedelta(days=1)
        if len(batch) < 640:
            break
    if not rows:
        return None
    df = pd.DataFrame(
        [row[:6] for row in rows],
        columns=cast(Any, ["Date", "Open", "Close", "High", "Low", "Volume"]),
    )
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    for column in ("Open", "High", "Low", "Close", "Volume"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df = cast(
        pd.DataFrame,
        df.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]],
    )
    return cast(
        pd.DataFrame,
        _drop_missing_close(df.loc[~df.index.duplicated(keep="last")]).sort_index(),
    )


def _download_from_akshare(
    ticker: str, start_date: datetime | None = None
) -> pd.DataFrame | None:
    if ak is None:
        logger.debug("AkShare is not installed; skipping %s.", ticker)
        return None
    ticker = normalize_ticker(ticker)
    code, _suffix = ticker.split(".", 1)
    end_date = datetime.now(timezone.utc).replace(tzinfo=None)
    request_start = start_date or end_date - timedelta(days=HISTORY_YEARS * 365 + 30)
    try:
        if is_etf_ticker(ticker):
            frame = ak.fund_etf_hist_em(
                symbol=code,
                period="daily",
                start_date=request_start.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust="qfq",
            )
        else:
            frame = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=request_start.strftime("%Y%m%d"),
                end_date=end_date.strftime("%Y%m%d"),
                adjust="qfq",
                timeout=DOWNLOAD_TIMEOUT,
            )
    except Exception as exc:
        logger.debug("AkShare failed for %s: %s", ticker, exc)
        return None
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    normalized = frame.rename(
        columns={
            "日期": "Date",
            "date": "Date",
            "开盘": "Open",
            "open": "Open",
            "最高": "High",
            "high": "High",
            "最低": "Low",
            "low": "Low",
            "收盘": "Close",
            "close": "Close",
            "成交量": "Volume",
            "volume": "Volume",
        }
    )
    required = ["Date", "Open", "High", "Low", "Close", "Volume"]
    if any(column not in normalized for column in required):
        return None
    normalized = normalized.loc[:, required].copy()
    normalized["Date"] = pd.to_datetime(normalized["Date"], errors="coerce")
    normalized = normalized.set_index("Date")
    validated = _validate_ohlcv(normalized)
    if validated is None:
        return None
    return validated.loc[validated.index >= pd.Timestamp(start_date)] if start_date is not None else validated


def _fetch_eastmoney_realtime_price(ticker: str) -> float | None:
    code, suffix = ticker.upper().split(".", 1)
    market = "1" if suffix == "SH" else "0"
    response = _eastmoney_get(
        "/api/qt/stock/get",
        {"secid": f"{market}.{code}", "fields": "f43,f60"},
    )
    data = response.json().get("data") or {}
    for field_name in ("f43", "f60"):
        value = _to_float(data.get(field_name))
        if value is not None and value > 0:
            return value / 100
    return None


def _fetch_eastmoney_realtime_prices(
    tickers: list[str] | set[str],
) -> dict[str, float]:
    requested = {normalize_ticker(ticker) for ticker in tickers if ticker}
    if not requested:
        return {}
    prices: dict[str, float] = {}
    groups = (
        (
            {ticker for ticker in requested if not is_etf_ticker(ticker)},
            "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048",
        ),
        (
            {ticker for ticker in requested if is_etf_ticker(ticker)},
            "m:0+t:9,m:1+t:9",
        ),
    )
    for group_tickers, market_filter in groups:
        if not group_tickers:
            continue
        params = {
            "pn": 1,
            "pz": 5000,
            "po": 1,
            "np": 1,
            "fltt": 2,
            "invt": 2,
            "fid": "f3",
            "fs": market_filter,
            "fields": "f12,f13,f43,f60",
        }
        try:
            response = _eastmoney_get("/api/qt/clist/get", params)
            data = response.json().get("data") or {}
            rows = list(data.get("diff") or [])
            total = int(data.get("total") or len(rows))
            for page in range(2, math.ceil(total / params["pz"]) + 1):
                params["pn"] = page
                response = _eastmoney_get("/api/qt/clist/get", params)
                rows.extend((response.json().get("data") or {}).get("diff") or [])
        except _DOWNLOAD_ERRORS as exc:
            logger.debug("批量实时行情获取失败：%s", exc)
            continue
        for row in rows:
            code = str(row.get("f12") or "").zfill(6)
            try:
                market = int(row.get("f13") or 0)
            except (TypeError, ValueError):
                continue
            suffix = (
                "SH"
                if market == 1
                else "BJ"
                if code.startswith(("4", "8", "92"))
                else "SZ"
            )
            ticker = f"{code}.{suffix}"
            if ticker not in group_tickers:
                continue
            for field_name in ("f43", "f60"):
                value = _to_float(row.get(field_name))
                if value is not None and value > 0:
                    prices[ticker] = value / 100
                    break
    return prices


def _download_from_eastmoney(
    ticker: str, start_date: datetime | None = None
) -> pd.DataFrame | None:
    attempts = DOWNLOAD_RETRIES + 1
    for attempt in range(1, attempts + 1):
        try:
            end_date = datetime.now(timezone.utc).replace(tzinfo=None)
            request_start = start_date or end_date - timedelta(days=HISTORY_YEARS * 365 + 30)
            code, suffix = ticker.upper().split(".", 1)
            market = "1" if suffix == "SH" else "0"
            response = _eastmoney_get(
                "/api/qt/stock/kline/get",
                {
                    "secid": f"{market}.{code}",
                    "klt": 101,
                    "fqt": 1,
                    "beg": request_start.strftime("%Y%m%d"),
                    "end": end_date.strftime("%Y%m%d"),
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56",
                },
                history=True,
            )
            klines = (response.json().get("data") or {}).get("klines") or []
            if not klines:
                return None
            df = pd.DataFrame(
                [line.split(",")[:6] for line in klines],
                columns=cast(Any, ["Date", "Open", "Close", "High", "Low", "Volume"]),
            )
            df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
            for column in ("Open", "High", "Low", "Close", "Volume"):
                df[column] = pd.to_numeric(df[column], errors="coerce")
            df = cast(
                pd.DataFrame,
                df.set_index("Date")[["Open", "High", "Low", "Close", "Volume"]],
            )
            df = _drop_missing_close(df)
            return df if not df.empty else None
        except (DownloadError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            msg = str(exc).lower()
            if any(
                keyword in msg
                for keyword in (
                    "404",
                    "not found",
                    "delisted",
                    "no timezone",
                    "timeout",
                    "timed out",
                    "no data found",
                    "failed to perform",
                    "curl",
                )
            ):
                return None
            if "401" in msg or "429" in msg or "rate limit" in msg:
                time.sleep(5 + attempt * 5)
                continue
            if attempt < attempts:
                time.sleep(2**attempt)
    return None


_DATA_SOURCE_LABELS = {
    "auto": "自动优选",
    "akshare": "AkShare",
    "eastmoney": "东方财富",
    "sina": "新浪",
    "tencent": "腾讯",
}

_DATA_SOURCE_CANDIDATES = {
    "auto": ("tencent", "akshare", "eastmoney"),
    "akshare": ("akshare",),
    "eastmoney": ("eastmoney",),
    "sina": ("sina",),
    "tencent": ("tencent",),
}

_SOURCE_DOWNLOAD_WORKER_CAPS = {
    "auto": 4,
    "akshare": 4,
    "sina": 8,
    "tencent": 8,
}


def normalize_data_source(source: str) -> str:
    normalized = source.strip().lower()
    if normalized not in _DATA_SOURCE_LABELS:
        raise ValueError(f"不支持的数据源：{source}")
    return normalized


def get_data_source_label(source: str) -> str:
    return _DATA_SOURCE_LABELS[normalize_data_source(source)]


def _download_worker_count(source: str, total: int) -> int:
    if total <= 0:
        return 0
    selected = normalize_data_source(source)
    configured = max(1, int(DOWNLOAD_THREADS))
    source_cap = _SOURCE_DOWNLOAD_WORKER_CAPS.get(selected, configured)
    return min(total, configured, source_cap)


def _download_single_with_source(
    ticker: str,
    source: str = "eastmoney",
    start_date: datetime | None = None,
) -> tuple[pd.DataFrame | None, str | None]:
    ticker = normalize_ticker(ticker)
    selected = normalize_data_source(source)
    loaders = {
        "akshare": _download_from_akshare,
        "eastmoney": _download_from_eastmoney,
        "sina": _download_from_sina,
        "tencent": _download_from_tencent,
    }
    for candidate in _DATA_SOURCE_CANDIDATES[selected]:
        try:
            _wait_for_download_slot()
            frame = loaders[candidate](ticker, start_date=start_date)
        except _DOWNLOAD_ERRORS as exc:
            logger.debug(
                "数据源 %s 获取 %s 失败：%s",
                get_data_source_label(candidate),
                ticker,
                exc,
            )
            continue
        if frame is not None and not frame.empty:
            if selected == "auto":
                logger.debug(
                    "自动优选已使用%s获取 %s 的数据。",
                    get_data_source_label(candidate),
                    ticker,
                )
            return frame, candidate
    return None, None


def _download_single(
    ticker: str,
    source: str = "eastmoney",
    start_date: datetime | None = None,
) -> pd.DataFrame | None:
    frame, _actual_source = _download_single_with_source(ticker, source, start_date)
    return frame


def _price_source_meta_path(ticker: str, selected_source: str) -> Path:
    return _price_cache_root(selected_source) / (
        f"{_safe_cache_stem(ticker, selected_source)}.source.json"
    )


def _save_price_source_meta(
    ticker: str, selected_source: str, actual_source: str
) -> None:
    path = _price_source_meta_path(ticker, selected_source)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "selected_source": normalize_data_source(selected_source),
        "actual_source": normalize_data_source(actual_source),
        "cache_schema": _PRICE_CACHE_SCHEMA_VERSION,
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_price_source_meta(ticker: str, selected_source: str) -> dict[str, str]:
    path = _price_source_meta_path(ticker, selected_source)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return {}
    if payload.get("cache_schema") != _PRICE_CACHE_SCHEMA_VERSION:
        return {}
    actual = str(payload.get("actual_source", "")).strip().lower()
    if actual not in _DATA_SOURCE_LABELS or actual == "auto":
        return {}
    return {"actual_source": actual}


def download_ticker(
    ticker: str,
    force: bool = False,
    source: str = "eastmoney",
    cache_first: bool = False,
) -> pd.DataFrame | None:
    """Return provider-consistent OHLCV history for one ticker."""
    ticker = normalize_ticker(ticker)
    selected = normalize_data_source(source)

    def persist(
        frame: pd.DataFrame | None, actual_source: str | None
    ) -> pd.DataFrame | None:
        if frame is None or frame.empty or actual_source is None:
            return frame
        validated = _validate_ohlcv(frame)
        if validated is None:
            return None
        _save_cache(ticker, validated, selected)
        _save_price_source_meta(ticker, selected, actual_source)
        return validated

    if force:
        frame, actual_source = _download_single_with_source(ticker, selected)
        return persist(frame, actual_source)

    cached = _load_cache(ticker, selected)
    if cached is None:
        frame, actual_source = _download_single_with_source(ticker, selected)
        return persist(frame, actual_source)
    if cache_first or _cache_has_completed_daily_bar(cached):
        return cached

    cached_index = pd.DatetimeIndex(cached.index).dropna()
    if cached_index.empty:
        return cached
    last_timestamp = pd.Timestamp(cast(Any, cached_index[-1]))
    last_date = cast(datetime, last_timestamp.to_pydatetime())
    if last_date.tzinfo is not None:
        last_date = last_date.replace(tzinfo=None)

    metadata = _load_price_source_meta(ticker, selected)
    actual_source = metadata.get("actual_source")
    if selected != "auto":
        actual_source = selected
    request_start = last_date - timedelta(days=7)
    if actual_source:
        try:
            incremental, provider = _download_single_with_source(
                ticker, actual_source, start_date=request_start
            )
        except _DOWNLOAD_ERRORS as exc:
            logger.debug("Incremental update failed for %s: %s", ticker, exc)
            incremental, provider = None, None
        if incremental is not None and not incremental.empty and provider == actual_source:
            new_df = incremental.loc[incremental.index >= pd.Timestamp(last_date)]
            if not new_df.empty:
                combined = cast(pd.DataFrame, pd.concat([cached, new_df]))
                combined = combined.loc[~combined.index.duplicated(keep="last")].sort_index()
                validated = _validate_ohlcv(combined)
                if validated is not None and not validated.equals(cached):
                    return persist(validated, actual_source)
            return cached

    if selected == "auto":
        rebuilt, provider = _download_single_with_source(ticker, selected)
        if rebuilt is not None and not rebuilt.empty and provider is not None:
            return persist(rebuilt, provider)
    return cached


def download_batch(
    tickers: list[TickerInfo],
    desc: str = "Downloading",
    force: bool = False,
    source: str = "eastmoney",
    cache_first: bool = False,
    skip_tickers: set[str] | None = None,
) -> dict[str, pd.DataFrame]:
    results: dict[str, pd.DataFrame] = {}
    skip_tickers = {normalize_ticker(ticker) for ticker in (skip_tickers or set())}
    symbols = list(
        dict.fromkeys(
            normalize_ticker(t.ticker)
            for t in tickers
            if t.ticker
            and t.ticker.strip()
            and normalize_ticker(t.ticker) not in skip_tickers
        )
    )
    total = len(symbols)
    skipped_delisted = 0
    selected_source = normalize_data_source(source)
    worker_count = _download_worker_count(selected_source, total)
    logger.info(
        "DOWNLOAD start: %d tickers via %s with %d workers (force=%s).",
        total,
        get_data_source_label(selected_source),
        worker_count,
        force,
    )

    if not total:
        _log_download_progress(0, 0, 0, 0)
    elif worker_count <= 1:
        _log_download_progress(0, total, 0, 0)
        for completed, sym in enumerate(
            tqdm(symbols, desc=desc, unit="ticker", disable=not sys.stderr.isatty()),
            start=1,
        ):
            try:
                df = download_ticker(
                    sym, force=force, source=selected_source, cache_first=cache_first
                )
                if df is not None and not df.empty:
                    results[sym] = df
                else:
                    skipped_delisted += 1
            except _DOWNLOAD_ERRORS:
                skipped_delisted += 1
            _log_download_progress(completed, total, len(results), skipped_delisted)
    else:
        _log_download_progress(0, total, 0, 0)
        max_pending = max(worker_count * 2, worker_count)
        symbol_iter = iter(symbols)
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures: dict[Any, str] = {}

            def download_scheduled(sym: str) -> pd.DataFrame | None:
                return download_ticker(sym, force, selected_source, cache_first)

            def submit_next() -> bool:
                try:
                    sym = next(symbol_iter)
                except StopIteration:
                    return False
                futures[pool.submit(download_scheduled, sym)] = sym
                return True

            for _ in range(min(max_pending, total)):
                submit_next()
            completed = 0
            with tqdm(
                total=total,
                desc=desc,
                unit="ticker",
                disable=not sys.stderr.isatty(),
            ) as progress:
                while futures:
                    done, _ = wait(
                        futures,
                        timeout=DOWNLOAD_PROGRESS_HEARTBEAT_SECONDS,
                        return_when=FIRST_COMPLETED,
                    )
                    if not done:
                        logger.info(
                            "DOWNLOAD waiting: %d/%d complete, %d requests still active.",
                            completed,
                            total,
                            len(futures),
                        )
                        continue
                    for future in done:
                        sym = futures.pop(future)
                        completed += 1
                        try:
                            df = future.result(timeout=DOWNLOAD_TIMEOUT + 10)
                            if df is not None and not df.empty:
                                results[sym] = df
                            else:
                                skipped_delisted += 1
                        except _DOWNLOAD_ERRORS as exc:
                            logger.debug("Download exception for %s: %s", sym, exc)
                            skipped_delisted += 1
                        progress.update(1)
                        _log_download_progress(
                            completed, total, len(results), skipped_delisted
                        )
                        submit_next()

    logger.info(
        "Download batch complete (%s, %d workers): %d/%d tickers succeeded, %d delisted/no-data skipped.",
        get_data_source_label(selected_source),
        worker_count,
        len(results),
        total,
        skipped_delisted,
    )
    return results


def get_etf_fund_flows(ticker: str) -> float | None:
    return None
