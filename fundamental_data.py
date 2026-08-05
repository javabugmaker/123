from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

try:
    import akshare as ak
except ImportError:
    ak = None

from config import (
    CACHE_DIR,
    DOWNLOAD_RATE_LIMIT_PAUSE,
    FUNDAMENTAL_DATA_PATH,
    FUNDAMENTAL_DOWNLOAD_THREADS,
    FUNDAMENTAL_PROGRESS_HEARTBEAT_SECONDS,
)
from downloader import normalize_ticker

logger = logging.getLogger("institution_scanner.fundamental_data")

FUNDAMENTAL_COLUMNS = (
    "Ticker",
    "Industry",
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

# Module-level batch data cache — populated once per refresh run
_batch_finance_cache: dict[str, dict[str, Any]] = {}
_batch_holders_cache: dict[str, dict[str, Any]] = {}
_batch_cache_lock = threading.Lock()


def _wait_for_slot() -> None:
    """Rate-limit helper — preserved for compatibility."""
    time.sleep(DOWNLOAD_RATE_LIMIT_PAUSE)


def _log_fundamental_progress(
    completed: int,
    total: int,
    updated: int,
    unavailable: int,
    force: bool = False,
) -> None:
    interval = max(1, total // 100)
    if (
        force
        or completed == 0
        or completed == total
        or completed % interval == 0
    ):
        logger.info(
            "FUNDAMENTAL progress: %d/%d (%d updated, %d unavailable).",
            completed,
            total,
            updated,
            unavailable,
        )


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return np.nan
    return number if np.isfinite(number) else np.nan


def _code_to_ticker(code: str) -> str:
    """Convert a 6-digit stock code to a normalized ticker with exchange suffix."""
    code = str(code).strip().zfill(6)
    if not code.isdigit() or len(code) != 6:
        return ""
    if code.startswith(("5", "6", "688")):
        return f"{code}.SH"
    elif code.startswith(("4", "8", "92")):
        return f"{code}.BJ"
    else:
        return f"{code}.SZ"


# ---------------------------------------------------------------------------
# Batch data fetching via AKShare
# ---------------------------------------------------------------------------

def _batch_fetch_financial_data() -> dict[str, dict[str, Any]]:
    """Fetch financial indicators for all A-shares in one batch via AKShare.

    Returns a dict keyed by normalized ticker (e.g. '000001.SZ') with:
        ROE, GrossMargin, NetProfit, Industry
    """
    if ak is None:
        logger.warning("AKShare 未安装，无法批量获取财务数据。")
        return {}

    # Try the latest reporting period: 2026-Q1 (20260331)
    for report_date in ("20260331", "20251231", "20250930"):
        try:
            logger.info("正在通过 AKShare 批量获取 %s 财务数据...", report_date)
            df = ak.stock_yjbb_em(date=report_date)
            if df is None or df.empty:
                continue
            result: dict[str, dict[str, Any]] = {}
            for _, row in df.iterrows():
                code = str(row.get("股票代码", "")).strip()
                ticker = _code_to_ticker(code)
                if not ticker:
                    continue
                industry = row.get("所处行业")
                result[ticker] = {
                    "ROE": _number(row.get("净资产收益率")),
                    "GrossMargin": _number(row.get("销售毛利率")),
                    "NetProfit": _number(row.get("净利润-净利润")),
                    "Industry": str(industry).strip() if pd.notna(industry) and str(industry).strip() != "None" else "",
                }
            logger.info("AKShare 财务数据批量获取完成：%d 只股票。", len(result))
            return result
        except Exception as exc:
            logger.warning("AKShare 批量获取 %s 财务数据失败：%s", report_date, exc)
            continue
    return {}


def _batch_fetch_institutional_data() -> dict[str, dict[str, Any]]:
    """Fetch institutional holdings for all A-shares in one batch via AKShare.

    Returns a dict keyed by normalized ticker with:
        OrgNum, OrgNumChange
    """
    if ak is None:
        return {}

    try:
        logger.info("正在通过 AKShare 批量获取机构持股数据...")
        df = ak.stock_institute_hold()
        if df is None or df.empty:
            logger.warning("AKShare 机构持股数据为空。")
            return {}
        result: dict[str, dict[str, Any]] = {}
        for _, row in df.iterrows():
            code = str(row.get("证券代码", "")).strip()
            ticker = _code_to_ticker(code)
            if not ticker:
                continue
            result[ticker] = {
                "OrgNum": _number(row.get("机构数")),
                "OrgNumChange": _number(row.get("机构数变化")),
            }
        logger.info("AKShare 机构持股数据批量获取完成：%d 只股票。", len(result))
        return result
    except Exception as exc:
        logger.warning("AKShare 批量获取机构持股数据失败：%s", exc)
        return {}


def _prefetch_batch_data() -> None:
    """Populate the module-level batch data caches."""
    global _batch_finance_cache, _batch_holders_cache
    with _batch_cache_lock:
        if not _batch_finance_cache:
            _batch_finance_cache = _batch_fetch_financial_data()
        if not _batch_holders_cache:
            _batch_holders_cache = _batch_fetch_institutional_data()


def _clear_batch_cache() -> None:
    """Clear the module-level batch data caches."""
    global _batch_finance_cache, _batch_holders_cache
    with _batch_cache_lock:
        _batch_finance_cache = {}
        _batch_holders_cache = {}


# ---------------------------------------------------------------------------
# Per-ticker data lookup (from batch cache)
# ---------------------------------------------------------------------------

def _fetch_ticker_from_batch(ticker: str) -> dict[str, Any] | None:
    """Look up fundamental data for a single ticker from the batch cache.

    This replaces the old _fetch_ticker which made individual API calls to
    Eastmoney's deprecated RPT_F10_FINANCE_MAINFIN_INDEX and RPT_F10_EH_HOLDERS.
    """
    normalized = normalize_ticker(ticker)
    finance = _batch_finance_cache.get(normalized, {})
    holders = _batch_holders_cache.get(normalized, {})

    if not finance and not holders:
        return None

    org_change = holders.get("OrgNumChange", np.nan)
    org_num = holders.get("OrgNum", np.nan)

    # Determine institutional holding trend
    if np.isfinite(org_change) and org_change > 0:
        trend = "increasing"
        periods = float(org_change)
    else:
        trend = "not_increasing"
        periods = 0.0

    return {
        "Ticker": normalized,
        "Industry": finance.get("Industry", ""),
        "ROE": finance.get("ROE", np.nan),
        "GrossMargin": finance.get("GrossMargin", np.nan),
        "InstitutionHoldingTrend": trend,
        "InstitutionHoldingPeriods": periods,
        "NetProfitY1": finance.get("NetProfit", np.nan),
        "NetProfitY2": np.nan,
        "NetProfitY3": np.nan,
        "IndustryGrossMarginPercentile": np.nan,
    }


def _read_frame(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path, dtype={"Ticker": str})
    except (OSError, UnicodeError, pd.errors.ParserError, ValueError):
        return pd.DataFrame(columns=FUNDAMENTAL_COLUMNS)
    for column in FUNDAMENTAL_COLUMNS:
        if column not in frame:
            frame[column] = "" if column == "Industry" else np.nan
    frame = frame.loc[:, FUNDAMENTAL_COLUMNS].copy()
    frame["Ticker"] = frame["Ticker"].map(normalize_ticker)
    frame["Industry"] = frame["Industry"].fillna("").astype(str).str.strip()
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


def _industry_margin_percentiles(frame: pd.DataFrame) -> pd.Series:
    margins = pd.to_numeric(
        frame.get("GrossMargin", pd.Series(np.nan, index=frame.index)),
        errors="coerce",
    )
    industries = (
        frame.get("Industry", pd.Series("", index=frame.index))
        .fillna("")
        .astype(str)
        .str.strip()
    )
    global_rank = margins.rank(method="min", ascending=False)
    global_count = max(1, int(margins.notna().sum()) - 1)
    global_percentile = (global_rank - 1.0) / global_count
    peer_rank = margins.groupby(industries, dropna=False).rank(
        method="min", ascending=False
    )
    peer_count = margins.groupby(industries, dropna=False).transform("count")
    peer_percentile = (peer_rank - 1.0) / (peer_count - 1.0).clip(lower=1.0)
    return peer_percentile.where(
        industries.ne("") & peer_count.ge(3), global_percentile
    ).where(margins.notna())


def _fetch_fundamental_row(
    ticker: str,
    request: Callable[..., Any] | None,
    industries: Mapping[str, str],
) -> dict[str, Any] | None:
    """Fetch fundamental data for a single ticker from the batch cache."""
    try:
        row = _fetch_ticker_from_batch(ticker)
    except Exception as exc:
        logger.debug("基本面获取失败 %s: %s", ticker, exc)
        return None
    if row is not None:
        # Industry from the AKShare batch data takes priority;
        # fall back to the industry_by_ticker mapping provided by downloader.
        industry_from_batch = row.get("Industry", "")
        if not industry_from_batch or str(industry_from_batch).strip() == "":
            row["Industry"] = industries.get(ticker, "")
    return row


def refresh_fundamental_data(
    tickers: list[str],
    force: bool = False,
    request: Callable[..., Any] | None = None,
    industry_by_ticker: Mapping[str, str] | None = None,
    workers: int | None = None,
) -> Path:
    """Refresh fundamental data for all tickers.

    Uses AKShare batch APIs (stock_yjbb_em + stock_institute_hold) to fetch
    financial indicators and institutional holdings in just 2 API calls instead
    of making individual requests per ticker.
    """
    existing = _read_frame(_CACHE_PATH)
    fallback = _configured_frame()
    industries = {
        normalize_ticker(ticker): str(industry).strip()
        for ticker, industry in (industry_by_ticker or {}).items()
        if str(industry).strip()
    }
    known_industries = set(
        existing.loc[existing["Industry"].ne(""), "Ticker"].tolist()
    )
    if (
        not force
        and _is_current_quarter()
        and not existing.empty
        and (not industries or set(industries).issubset(known_industries))
    ):
        logger.info("基本面缓存已是本季度版本，跳过刷新。")
        return _CACHE_PATH

    symbols = [
        ticker
        for ticker in dict.fromkeys(normalize_ticker(value) for value in tickers)
        if not ticker.split(".", 1)[0].startswith(("15", "16", "50", "51", "56", "58"))
    ]
    total = len(symbols)

    # Prefetch batch data via AKShare (2 API calls total, regardless of ticker count)
    _clear_batch_cache()
    _prefetch_batch_data()

    worker_count = min(
        total,
        max(1, int(workers if workers is not None else FUNDAMENTAL_DOWNLOAD_THREADS)),
    ) if total else 0
    logger.info(
        "开始刷新基本面数据：%d 只股票，%d 个并发请求。",
        total,
        worker_count,
    )
    rows: list[dict[str, Any]] = []
    unavailable = 0
    completed = 0
    _log_fundamental_progress(completed, total, len(rows), unavailable)

    if worker_count <= 1:
        for ticker in symbols:
            row = _fetch_fundamental_row(ticker, request, industries)
            completed += 1
            if row is None:
                unavailable += 1
            else:
                rows.append(row)
            _log_fundamental_progress(completed, total, len(rows), unavailable)
    elif symbols:
        max_pending = max(worker_count * 2, worker_count)
        ticker_iter = iter(symbols)
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures: dict[Any, str] = {}

            def submit_next() -> bool:
                try:
                    ticker = next(ticker_iter)
                except StopIteration:
                    return False
                futures[pool.submit(_fetch_fundamental_row, ticker, request, industries)] = ticker
                return True

            for _ in range(min(max_pending, total)):
                submit_next()

            while futures:
                done, _ = wait(
                    futures,
                    timeout=FUNDAMENTAL_PROGRESS_HEARTBEAT_SECONDS,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    _log_fundamental_progress(
                        completed,
                        total,
                        len(rows),
                        unavailable,
                        force=True,
                    )
                    continue
                for future in done:
                    futures.pop(future)
                    completed += 1
                    try:
                        row = future.result()
                    except Exception as exc:
                        logger.debug("基本面工作线程失败：%s", exc)
                        row = None
                    if row is None:
                        unavailable += 1
                    else:
                        rows.append(row)
                    _log_fundamental_progress(
                        completed, total, len(rows), unavailable
                    )
                    submit_next()

    _clear_batch_cache()

    downloaded = pd.DataFrame(rows, columns=FUNDAMENTAL_COLUMNS)
    combined = pd.concat([fallback, existing], ignore_index=True)
    if not downloaded.empty:
        combined = pd.concat([combined.set_index("Ticker"), downloaded.set_index("Ticker")], axis=0)
        combined = combined.groupby(level=0, sort=False).last()
        combined = downloaded.set_index("Ticker").combine_first(combined).reset_index()
    if combined.empty:
        return _CACHE_PATH
    combined = combined.drop_duplicates("Ticker", keep="last")
    if industries:
        combined["Industry"] = combined["Ticker"].map(industries).fillna(
            combined["Industry"]
        )
    combined["Industry"] = combined["Industry"].fillna("").astype(str).str.strip()
    combined["IndustryGrossMarginPercentile"] = _industry_margin_percentiles(
        combined
    )
    _write_frame(combined)
    logger.info(
        "基本面刷新完成：%d/%d 只股票已更新，%d 只暂不可用。",
        len(rows),
        total,
        unavailable,
    )
    return _CACHE_PATH


def fundamental_data_path() -> Path | None:
    if _CACHE_PATH.is_file():
        return _CACHE_PATH
    if FUNDAMENTAL_DATA_PATH and Path(FUNDAMENTAL_DATA_PATH).is_file():
        return Path(FUNDAMENTAL_DATA_PATH)
    return None