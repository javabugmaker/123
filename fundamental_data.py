from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import requests

from config import (
    CACHE_DIR,
    DOWNLOAD_RATE_LIMIT_PAUSE,
    DOWNLOAD_RETRIES,
    DOWNLOAD_TIMEOUT,
    FUNDAMENTAL_DATA_PATH,
)
from downloader import normalize_ticker

logger = logging.getLogger("institution_scanner.fundamental_data")

FUNDAMENTAL_COLUMNS = (
    "Ticker",
    "ROE",
    "GrossMargin",
    "InstitutionHoldingTrend",
    "InstitutionHoldingPeriods",
    "NetProfitY1",
    "NetProfitY2",
    "NetProfitY3",
    "IndustryGrossMarginPercentile",
)

_CACHE_PATH = CACHE_DIR / "fundamental_data.csv"
_META_PATH = CACHE_DIR / "fundamental_data_meta.json"
_EASTMONEY_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_HTTP = requests.Session()
_HTTP.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
_LAST_REQUEST_AT = 0.0


def _wait_for_slot() -> None:
    global _LAST_REQUEST_AT
    now = time.monotonic()
    delay = DOWNLOAD_RATE_LIMIT_PAUSE - (now - _LAST_REQUEST_AT)
    if delay > 0:
        time.sleep(delay)
    _LAST_REQUEST_AT = time.monotonic()


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return np.nan
    return number if np.isfinite(number) else np.nan


def _value(row: dict[str, Any], *names: str) -> Any:
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        if name in row and pd.notna(row[name]):
            return row[name]
        value = lowered.get(name.lower())
        if value is not None and pd.notna(value):
            return value
    return None


def _periods_from_trend(value: Any) -> float:
    text = str(value or "").strip()
    for token in ("连续", "季度", "期"):
        text = text.replace(token, "")
    try:
        return max(0.0, float(text))
    except ValueError:
        return np.nan


def _request_report(report_name: str, secucode: str, columns: str, request: Callable[..., Any] | None = None) -> list[dict[str, Any]]:
    requester = request or _HTTP.get
    params = {
        "reportName": report_name,
        "columns": columns,
        "filter": f'(SECUCODE="{secucode}")',
        "pageNumber": 1,
        "pageSize": 20,
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
        "source": "HSF10",
        "client": "WEB",
    }
    last_error: Exception | None = None
    for attempt in range(DOWNLOAD_RETRIES + 1):
        try:
            _wait_for_slot()
            response = requester(_EASTMONEY_URL, params=params, timeout=DOWNLOAD_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            data = payload.get("result", {}).get("data", []) if isinstance(payload, dict) else []
            return data if isinstance(data, list) else []
        except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError) as exc:
            last_error = exc
            if attempt < DOWNLOAD_RETRIES:
                time.sleep(2**attempt)
    if last_error is not None:
        raise last_error
    return []


def _fetch_ticker(ticker: str, request: Callable[..., Any] | None = None) -> dict[str, Any] | None:
    normalized = normalize_ticker(ticker)
    finance = _request_report(
        "RPT_F10_FINANCE_MAINFIN_INDEX",
        normalized,
        "SECUCODE,REPORT_DATE,ROEJQ,XSMLL,NETPROFIT,NETPROFIT_YOY",
        request,
    )
    holders = _request_report(
        "RPT_F10_EH_HOLDERS",
        normalized,
        "SECUCODE,REPORT_DATE,ORG_NUM,ORG_NUM_CHANGE",
        request,
    )
    if not finance and not holders:
        return None
    latest = finance[0] if finance else {}
    recent_finance = finance[:3]
    profits = [_number(_value(row, "NETPROFIT", "NET_PROFIT", "netprofit")) for row in recent_finance]
    profits += [np.nan] * (3 - len(profits))
    holding_numbers = [_number(_value(row, "ORG_NUM", "ORGNUM", "INSTITUTION_NUM")) for row in holders[:4]]
    finite_holdings = [value for value in holding_numbers if np.isfinite(value)]
    increasing = len(finite_holdings) >= 2 and finite_holdings[0] > finite_holdings[-1]
    periods = 0.0
    if increasing:
        periods = float(sum(current > following for current, following in zip(finite_holdings, finite_holdings[1:])))
    trend = "increasing" if increasing else "not_increasing"
    return {
        "Ticker": normalized,
        "ROE": _number(_value(latest, "ROEJQ", "ROE", "ROE_AVG")),
        "GrossMargin": _number(_value(latest, "XSMLL", "GROSS_MARGIN", "GROSSPROFIT_MARGIN")),
        "InstitutionHoldingTrend": trend,
        "InstitutionHoldingPeriods": periods if periods else _periods_from_trend(_value(holders[0], "ORG_NUM_CHANGE") if holders else None),
        "NetProfitY1": profits[0],
        "NetProfitY2": profits[1],
        "NetProfitY3": profits[2],
        "IndustryGrossMarginPercentile": np.nan,
    }


def _read_frame(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path, dtype={"Ticker": str})
    except (OSError, UnicodeError, pd.errors.ParserError, ValueError):
        return pd.DataFrame(columns=FUNDAMENTAL_COLUMNS)
    for column in FUNDAMENTAL_COLUMNS:
        if column not in frame:
            frame[column] = np.nan
    frame = frame.loc[:, FUNDAMENTAL_COLUMNS].copy()
    frame["Ticker"] = frame["Ticker"].map(normalize_ticker)
    return frame.drop_duplicates("Ticker", keep="last")


def _configured_frame() -> pd.DataFrame:
    if not FUNDAMENTAL_DATA_PATH:
        return pd.DataFrame(columns=FUNDAMENTAL_COLUMNS)
    path = Path(FUNDAMENTAL_DATA_PATH)
    return _read_frame(path) if path.is_file() else pd.DataFrame(columns=FUNDAMENTAL_COLUMNS)


def _is_current_quarter() -> bool:
    try:
        metadata = json.loads(_META_PATH.read_text(encoding="utf-8"))
        return metadata.get("quarter") == f"{date.today().year}-Q{(date.today().month - 1) // 3 + 1}"
    except (OSError, ValueError, TypeError):
        return False


def _write_frame(frame: pd.DataFrame) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="fundamental_", suffix=".csv", dir=CACHE_DIR)
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        frame.to_csv(temp_path, index=False, encoding="utf-8-sig")
        temp_path.replace(_CACHE_PATH)
        _META_PATH.write_text(json.dumps({"quarter": f"{date.today().year}-Q{(date.today().month - 1) // 3 + 1}", "updated": date.today().isoformat()}, ensure_ascii=False), encoding="utf-8")
    finally:
        temp_path.unlink(missing_ok=True)


def refresh_fundamental_data(tickers: list[str], force: bool = False, request: Callable[..., Any] | None = None) -> Path:
    existing = _read_frame(_CACHE_PATH)
    fallback = _configured_frame()
    if not force and _is_current_quarter() and not existing.empty:
        return _CACHE_PATH
    rows: list[dict[str, Any]] = []
    for ticker in dict.fromkeys(normalize_ticker(value) for value in tickers):
        if ticker.split(".", 1)[0].startswith(("15", "16", "50", "51", "56", "58")):
            continue
        try:
            row = _fetch_ticker(ticker, request)
        except (OSError, ValueError, TypeError, KeyError, requests.RequestException) as exc:
            logger.warning("基本面获取失败 %s: %s", ticker, exc)
            row = None
        if row is not None:
            rows.append(row)
    downloaded = pd.DataFrame(rows, columns=FUNDAMENTAL_COLUMNS)
    combined = pd.concat([fallback, existing], ignore_index=True)
    if not downloaded.empty:
        combined = pd.concat([combined.set_index("Ticker"), downloaded.set_index("Ticker")], axis=0)
        combined = combined.groupby(level=0, sort=False).last()
        combined = downloaded.set_index("Ticker").combine_first(combined).reset_index()
    if combined.empty:
        return _CACHE_PATH
    combined = combined.drop_duplicates("Ticker", keep="last")
    margins = pd.to_numeric(combined["GrossMargin"], errors="coerce")
    combined["IndustryGrossMarginPercentile"] = margins.rank(pct=True, ascending=False, method="average")
    _write_frame(combined)
    return _CACHE_PATH


def fundamental_data_path() -> Path | None:
    if _CACHE_PATH.is_file():
        return _CACHE_PATH
    if FUNDAMENTAL_DATA_PATH and Path(FUNDAMENTAL_DATA_PATH).is_file():
        return Path(FUNDAMENTAL_DATA_PATH)
    return None
