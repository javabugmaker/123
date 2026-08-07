from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
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
    FUNDAMENTAL_PROGRESS_HEARTBEAT_SECONDS,
)
from downloader import normalize_ticker
from network_proxy import configure_akshare_proxy_from_system

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
_batch_finance_cache: dict[str, dict[str, Any]] = {}
_batch_holders_cache: dict[str, dict[str, Any]] = {}
_batch_cache_lock = threading.Lock()
_NETWORK_ENV_LOCK = threading.Lock()
_AKSHARE_CALL_LOCK = threading.Lock()
_CACHE_COMPLETENESS_THRESHOLD = 0.80
_FUNDAMENTAL_CACHE_MAX_AGE_DAYS = 14
_BATCH_FETCH_TIMEOUT_SECONDS = max(
    30.0, float(FUNDAMENTAL_PROGRESS_HEARTBEAT_SECONDS) * 3.0
)
_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
)


def _wait_for_slot() -> None:
    time.sleep(DOWNLOAD_RATE_LIMIT_PAUSE)


def _log_fundamental_progress(
    completed: int,
    total: int,
    updated: int,
    unavailable: int,
    force: bool = False,
) -> None:
    interval = max(1, total // 100)
    if force or completed == 0 or completed == total or completed % interval == 0:
        logger.info(
            "FUNDAMENTAL progress: %d/%d (%d updated, %d unavailable).",
            completed,
            total,
            updated,
            unavailable,
        )


def _number(value: Any) -> float:
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text or text.lower() in {"--", "-", "nan", "none", "null", "n/a"}:
            return np.nan
        match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
        if match is None:
            return np.nan
        value = match.group(0)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return np.nan
    return number if np.isfinite(number) else np.nan


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null", "<na>"} else text


def _row_value(row: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = row.get(name)
        if _text(value):
            return value
    normalized = {str(key).strip().lower(): value for key, value in row.items()}
    for name in names:
        value = normalized.get(name.strip().lower())
        if _text(value):
            return value
    return None


def _code_to_ticker(code: Any) -> str:
    value = _text(code).upper()
    if value.endswith(".0") and value[:-2].isdigit():
        value = value[:-2]
    if "." in value:
        number, suffix = value.rsplit(".", 1)
        if suffix in {"SH", "SZ", "BJ"} and number.isdigit():
            return f"{number.zfill(6)}.{suffix}"
    if not value.isdigit():
        return ""
    value = value.zfill(6)
    if len(value) != 6:
        return ""
    if value.startswith(("4", "8", "92")):
        return f"{value}.BJ"
    suffix = "SH" if value.startswith(("5", "6")) else "SZ"
    return f"{value}.{suffix}"


def _annual_report_dates() -> tuple[str, ...]:
    latest_year = date.today().year - 1
    return tuple(f"{year}1231" for year in range(latest_year, latest_year - 4, -1))


def _institution_report_symbols(limit: int = 8) -> tuple[str, ...]:
    """Return recent completed AKShare report periods in YYYYQ format."""
    if limit <= 0:
        return ()
    today = date.today()
    quarter = (today.month - 1) // 3
    year = today.year
    if quarter == 0:
        year -= 1
        quarter = 4
    result: list[str] = []
    for _ in range(limit):
        result.append(f"{year}{quarter}")
        quarter -= 1
        if quarter == 0:
            year -= 1
            quarter = 4
    return tuple(result)


@contextmanager
def _direct_network_environment():
    """Compatibility wrapper that preserves proxy state for AkShare calls.

    Older code cleared HTTP(S)_PROXY and set NO_PROXY=* here. Because os.environ
    is process-global, a timed-out daemon request could leave every later AkShare
    download forced to direct-connect. Keep the context manager API, but never
    mutate proxy variables; instead mirror the active Windows/Clash system proxy.
    """
    configure_akshare_proxy_from_system()
    yield


def _run_akshare_dataframe(
    label: str, operation: Callable[[], Any]
) -> pd.DataFrame | None:
    """Bound waiting time for provider calls without blocking the scan forever."""
    outcome: dict[str, Any] = {}

    def run() -> None:
        # Do not stack provider threads when a previous AKShare call timed out
        # but is still alive. This also prevents concurrent code from repeatedly
        # reconfiguring process proxy state.
        if not _AKSHARE_CALL_LOCK.acquire(blocking=False):
            outcome["error"] = RuntimeError("previous AKShare request is still active")
            return
        try:
            with _direct_network_environment():
                outcome["frame"] = operation()
        except Exception as exc:
            outcome["error"] = exc
        finally:
            _AKSHARE_CALL_LOCK.release()

    worker = threading.Thread(target=run, name=f"akshare-{label}", daemon=True)
    worker.start()
    started_at = time.monotonic()
    heartbeat = max(1.0, float(FUNDAMENTAL_PROGRESS_HEARTBEAT_SECONDS))
    while worker.is_alive():
        elapsed = time.monotonic() - started_at
        remaining = _BATCH_FETCH_TIMEOUT_SECONDS - elapsed
        if remaining <= 0:
            logger.warning(
                "AKShare %s 请求超时（%d 秒），本轮跳过并继续扫描。",
                label,
                round(_BATCH_FETCH_TIMEOUT_SECONDS),
            )
            return None
        worker.join(timeout=min(heartbeat, remaining))
        if worker.is_alive():
            logger.info(
                "AKShare %s 仍在下载，已等待 %d 秒。",
                label,
                round(time.monotonic() - started_at),
            )
    error = outcome.get("error")
    if error is not None:
        logger.warning("AKShare %s 获取失败：%s", label, error)
        return None
    frame = outcome.get("frame")
    return frame if isinstance(frame, pd.DataFrame) else None


def _batch_fetch_financial_data() -> dict[str, dict[str, Any]]:
    if ak is None:
        logger.warning("AKShare 未安装，无法批量获取财务数据。")
        return {}
    if not hasattr(ak, "stock_yjbb_em"):
        logger.warning("当前 AKShare 缺少 stock_yjbb_em，请升级 AKShare 后重试。")
        return {}

    logger.info("AKShare 版本：%s", getattr(ak, "__version__", "unknown"))
    result: dict[str, dict[str, Any]] = {}
    reports_loaded = 0
    for report_date in _annual_report_dates():
        report_number = reports_loaded + 1
        logger.info(
            "正在通过 AKShare 批量获取年度财报 %s（第 %d/3 份）...",
            report_date,
            report_number,
        )
        frame = _run_akshare_dataframe(
            f"年度财报 {report_date}",
            lambda report_date=report_date: ak.stock_yjbb_em(date=report_date),
        )
        if frame is None or frame.empty:
            continue
        profit_column = f"NetProfitY{report_number}"
        for _, row in frame.iterrows():
            ticker = _code_to_ticker(
                _row_value(row, "股票代码", "证券代码", "代码")
            )
            if not ticker:
                continue
            values = result.setdefault(
                ticker,
                {
                    "ROE": np.nan,
                    "GrossMargin": np.nan,
                    "Industry": "",
                    "NetProfitY1": np.nan,
                    "NetProfitY2": np.nan,
                    "NetProfitY3": np.nan,
                },
            )
            values[profit_column] = _number(
                _row_value(row, "净利润-净利润", "净利润", "归母净利润")
            )
            if reports_loaded == 0:
                values["ROE"] = _number(
                    _row_value(row, "净资产收益率", "加权净资产收益率", "ROE")
                )
                values["GrossMargin"] = _number(
                    _row_value(row, "销售毛利率", "毛利率", "GrossMargin")
                )
                values["Industry"] = _text(
                    _row_value(row, "所处行业", "行业", "行业名称")
                )
        reports_loaded += 1
        logger.info(
            "AKShare 年度财报 %s 已载入：%d 只股票（已取得 %d/3 份年报）。",
            report_date,
            len(result),
            reports_loaded,
        )
        if reports_loaded == 3:
            break
    logger.info(
        "AKShare 年度财报批量获取完成：%d 只股票，%d 份年报。",
        len(result),
        reports_loaded,
    )
    return result


def _batch_fetch_institutional_data() -> dict[str, dict[str, Any]]:
    """Fetch two snapshots of institution-count changes, not aggregate holdings."""
    if ak is None:
        return {}
    if not hasattr(ak, "stock_institute_hold"):
        logger.warning("当前 AKShare 缺少 stock_institute_hold，请升级 AKShare 后重试。")
        return {}

    snapshots: list[tuple[str, pd.DataFrame]] = []
    for report_symbol in _institution_report_symbols():
        logger.info("正在通过 AKShare 获取机构覆盖报告期 %s...", report_symbol)
        frame = _run_akshare_dataframe(
            f"机构覆盖 {report_symbol}",
            lambda report_symbol=report_symbol: ak.stock_institute_hold(
                symbol=report_symbol
            ),
        )
        if frame is None or frame.empty:
            continue
        snapshots.append((report_symbol, frame))
        logger.info("AKShare 机构覆盖 %s 已载入：%d 条。", report_symbol, len(frame))
        if len(snapshots) == 2:
            break

    if not snapshots:
        logger.warning("AKShare 机构覆盖家数数据为空或暂不可用。")
        return {}

    result: dict[str, dict[str, Any]] = {}
    for snapshot_index, (report_symbol, frame) in enumerate(snapshots, start=1):
        change_key = f"OrgNumChange{snapshot_index}"
        for _, row in frame.iterrows():
            ticker = _code_to_ticker(
                _row_value(row, "证券代码", "股票代码", "代码")
            )
            if not ticker:
                continue
            values = result.setdefault(
                ticker,
                {
                    "OrgNum": np.nan,
                    "OrgNumChange": np.nan,
                    "OrgNumChange1": np.nan,
                    "OrgNumChange2": np.nan,
                    "LatestReportSymbol": "",
                    "PreviousReportSymbol": "",
                },
            )
            org_num = _number(
                _row_value(row, "机构数", "持股机构家数", "机构家数")
            )
            org_change = _number(
                _row_value(
                    row,
                    "机构数变化",
                    "机构数变动",
                    "持股机构家数变动",
                    "持股家数变动",
                )
            )
            values[change_key] = org_change
            if snapshot_index == 1:
                values["OrgNum"] = org_num
                values["OrgNumChange"] = org_change
                values["LatestReportSymbol"] = report_symbol
            else:
                values["PreviousReportSymbol"] = report_symbol

    logger.info(
        "AKShare 机构覆盖家数批量获取完成：%d 只股票，使用报告期 %s。",
        len(result),
        ", ".join(symbol for symbol, _ in snapshots),
    )
    return result


def _prefetch_batch_data() -> None:
    global _batch_finance_cache, _batch_holders_cache
    with _batch_cache_lock:
        if not _batch_finance_cache:
            _batch_finance_cache = _batch_fetch_financial_data()
        if not _batch_holders_cache:
            _batch_holders_cache = _batch_fetch_institutional_data()


def _clear_batch_cache() -> None:
    global _batch_finance_cache, _batch_holders_cache
    with _batch_cache_lock:
        _batch_finance_cache = {}
        _batch_holders_cache = {}


def _fetch_ticker_from_batch(ticker: str) -> dict[str, Any] | None:
    normalized = normalize_ticker(ticker)
    finance = _batch_finance_cache.get(normalized, {})
    holders = _batch_holders_cache.get(normalized, {})
    if not finance and not holders:
        return None

    changes = [
        _number(holders.get("OrgNumChange1", np.nan)),
        _number(holders.get("OrgNumChange2", np.nan)),
    ]
    changes = [value for value in changes if np.isfinite(value)]
    periods = float(len(changes))
    if len(changes) >= 2 and all(value > 0 for value in changes[:2]):
        trend = "increasing"
    elif len(changes) >= 2 and all(value <= 0 for value in changes[:2]):
        trend = "not_increasing"
    else:
        trend = "unknown"

    return {
        "Ticker": normalized,
        "Industry": finance.get("Industry", ""),
        "ROE": finance.get("ROE", np.nan),
        "GrossMargin": finance.get("GrossMargin", np.nan),
        "InstitutionHoldingTrend": trend,
        "InstitutionHoldingPeriods": periods,
        "NetProfitY1": finance.get("NetProfitY1", np.nan),
        "NetProfitY2": finance.get("NetProfitY2", np.nan),
        "NetProfitY3": finance.get("NetProfitY3", np.nan),
        "IndustryGrossMarginPercentile": np.nan,
    }


def _fetch_ticker(
    ticker: str, request: Callable[..., Any] | None = None
) -> dict[str, Any] | None:
    del request
    return _fetch_ticker_from_batch(ticker)


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


def _fundamental_index(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.reindex(columns=FUNDAMENTAL_COLUMNS).copy()
    normalized["Ticker"] = normalized["Ticker"].map(normalize_ticker)
    normalized = normalized.loc[normalized["Ticker"].ne("")]
    return normalized.drop_duplicates("Ticker", keep="last").set_index("Ticker")


def _replace_fundamental_rows(
    base: pd.DataFrame, replacement: pd.DataFrame
) -> pd.DataFrame:
    if replacement.empty:
        return base
    merged = base.reindex(base.index.union(replacement.index, sort=False)).copy()
    merged.loc[replacement.index, replacement.columns] = replacement
    return merged


def _is_current_quarter() -> bool:
    """A cache is current only when its quarter and update age are both current."""
    try:
        metadata = json.loads(_META_PATH.read_text(encoding="utf-8"))
        expected_quarter = (
            f"{date.today().year}-Q{(date.today().month - 1) // 3 + 1}"
        )
        if metadata.get("quarter") != expected_quarter:
            return False
        updated = date.fromisoformat(str(metadata.get("updated", "")))
        return (date.today() - updated).days <= _FUNDAMENTAL_CACHE_MAX_AGE_DAYS
    except (OSError, ValueError, TypeError):
        return False


def _write_frame(frame: pd.DataFrame) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix="fundamental_", suffix=".csv", dir=CACHE_DIR
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        frame.to_csv(temp_path, index=False, encoding="utf-8-sig")
        temp_path.replace(_CACHE_PATH)
        _META_PATH.write_text(
            json.dumps(
                {
                    "quarter": (
                        f"{date.today().year}-Q"
                        f"{(date.today().month - 1) // 3 + 1}"
                    ),
                    "updated": date.today().isoformat(),
                    "provider": "akshare-batch",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
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


def _cache_completeness(
    frame: pd.DataFrame,
    symbols: list[str],
    industries: Mapping[str, str],
) -> float:
    """Measure usable factor-group coverage rather than demanding every cell."""
    if frame.empty or not symbols:
        return 0.0
    cached = (
        frame.drop_duplicates("Ticker", keep="last")
        .set_index("Ticker")
        .reindex(symbols)
    )
    roe = pd.to_numeric(cached.get("ROE"), errors="coerce").notna()
    margin = pd.to_numeric(
        cached.get("IndustryGrossMarginPercentile"), errors="coerce"
    ).notna()
    profits = (
        pd.to_numeric(cached.get("NetProfitY1"), errors="coerce").notna()
        & pd.to_numeric(cached.get("NetProfitY2"), errors="coerce").notna()
        & pd.to_numeric(cached.get("NetProfitY3"), errors="coerce").notna()
    )
    periods = pd.to_numeric(
        cached.get("InstitutionHoldingPeriods"), errors="coerce"
    )
    trend = (
        cached.get("InstitutionHoldingTrend", pd.Series("", index=cached.index))
        .fillna("")
        .astype(str)
        .str.strip()
    )
    holder = periods.ge(2) & trend.ne("") & ~trend.str.lower().eq("unknown")
    factor_count = (
        roe.astype(int)
        + margin.astype(int)
        + profits.astype(int)
        + holder.astype(int)
    )
    complete = factor_count.ge(3)
    if industries:
        expected_industry = cached.index.to_series().isin(industries)
        cache_industry = (
            cached.get("Industry", pd.Series("", index=cached.index))
            .fillna("")
            .astype(str)
            .str.strip()
            .ne("")
        )
        complete &= ~expected_industry | cache_industry
    return float(complete.mean()) if len(complete) else 0.0


def _fetch_fundamental_row(
    ticker: str,
    request: Callable[..., Any] | None,
    industries: Mapping[str, str],
) -> dict[str, Any] | None:
    try:
        row = _fetch_ticker(ticker, request)
    except Exception as exc:
        logger.debug("基本面获取失败 %s: %s", ticker, exc)
        return None
    if row is not None:
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
    """Refresh stale/incomplete fundamentals; otherwise reuse the current cache."""
    existing = _read_frame(_CACHE_PATH)
    fallback = _configured_frame()
    industries = {
        normalize_ticker(ticker): str(industry).strip()
        for ticker, industry in (industry_by_ticker or {}).items()
        if str(industry).strip()
    }
    symbols = [
        ticker
        for ticker in dict.fromkeys(normalize_ticker(value) for value in tickers)
        if ticker
        and not ticker.split(".", 1)[0].startswith(
            ("15", "16", "50", "51", "56", "58")
        )
    ]
    total = len(symbols)
    completeness = _cache_completeness(existing, symbols, industries)
    cache_current = _is_current_quarter()
    if (
        not force
        and cache_current
        and completeness >= _CACHE_COMPLETENESS_THRESHOLD
    ):
        logger.info(
            "基本面缓存时效与完整度正常（%.0f%%），跳过刷新。",
            completeness * 100,
        )
        return _CACHE_PATH
    if not force and existing.empty and not symbols:
        return _CACHE_PATH
    if not force and not existing.empty:
        logger.info(
            "基本面缓存需要刷新：时效=%s，完整度=%.0f%%。",
            "正常" if cache_current else "过期",
            completeness * 100,
        )

    logger.info("开始准备 AKShare 基本面批量数据：%d 只股票。", total)
    _log_fundamental_progress(0, total, 0, 0, force=True)
    _clear_batch_cache()
    _prefetch_batch_data()

    del workers
    logger.info("开始整理基本面数据：%d 只股票，批量数据已就绪。", total)
    rows: list[dict[str, Any]] = []
    unavailable = 0
    completed = 0
    _log_fundamental_progress(completed, total, len(rows), unavailable)

    for ticker in symbols:
        row = _fetch_fundamental_row(ticker, request, industries)
        completed += 1
        if row is None:
            unavailable += 1
        else:
            rows.append(row)
        _log_fundamental_progress(completed, total, len(rows), unavailable)

    _clear_batch_cache()
    downloaded = pd.DataFrame(rows, columns=FUNDAMENTAL_COLUMNS)
    combined = _replace_fundamental_rows(
        _fundamental_index(fallback), _fundamental_index(existing)
    )
    if not downloaded.empty:
        combined = _fundamental_index(downloaded).combine_first(combined)
    combined = combined.reset_index()
    if combined.empty:
        logger.warning("本轮未取得任何基本面数据，保留现有缓存。")
        return _CACHE_PATH
    combined = combined.drop_duplicates("Ticker", keep="last")
    if industries:
        combined["Industry"] = combined["Ticker"].map(industries).fillna(
            combined["Industry"]
        )
    combined["Industry"] = combined["Industry"].fillna("").astype(str).str.strip()
    combined["IndustryGrossMarginPercentile"] = _industry_margin_percentiles(combined)
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
