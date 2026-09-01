"""Canonical low-frequency fundamental refresh service.

TickFlow remains the sole market-data and universe provider.  This service
owns only BaoStock financial reports, point-in-time report records, and the
compatibility summary consumed by the existing scanner.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import pandas as pd

from config import (
    CACHE_DIR,
    FUNDAMENTAL_DATA_PATH,
    FUNDAMENTAL_DOWNLOAD_TIMEOUT,
    FUNDAMENTAL_PROGRESS_HEARTBEAT_SECONDS,
)

from .baostock_fundamentals import (
    BAOSTOCK_PROVIDER_VERSION,
    BaoStockFundamentalProvider,
    BaoStockUnavailable,
    FundamentalFetchOutcome,
    FundamentalFetchPlan,
)
from .fundamental_schema import (
    FUNDAMENTAL_COLUMNS,
    FUNDAMENTAL_SCHEMA_VERSION,
    REPORT_COLUMNS,
    annual_candidate_periods,
    build_fundamental_summary,
    empty_report_frame,
    empty_summary_frame,
    latest_completed_period,
    latest_probe_periods,
    merge_report_records,
    normalize_report_frame,
    normalize_summary_frame,
    normalize_ticker,
    summary_hard_financial_coverage,
    summary_latest_period_coverage,
)

logger = logging.getLogger("institution_scanner.fundamentals")

FUNDAMENTAL_PROVIDER_VERSION: Final = (
    f"{FUNDAMENTAL_SCHEMA_VERSION}+{BAOSTOCK_PROVIDER_VERSION}"
)
_CACHE_PATH = CACHE_DIR / "fundamental_data.csv"
_REPORT_CACHE_PATH = CACHE_DIR / "fundamental_reports.csv"
_META_PATH = CACHE_DIR / "fundamental_data_meta.json"
_REFRESH_LOCK = threading.Lock()
_CACHE_MAX_AGE_DAYS = 1
_MIN_PROVIDER_CHECK_RATIO = 0.80
_MIN_HARD_COVERAGE = 0.80
_CHECKPOINT_EVERY = 100
_ETF_PREFIXES = ("15", "16", "50", "51", "56", "58")


def _china_today() -> date:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date()


def _read_csv(path: Path, *, summary: bool) -> pd.DataFrame:
    if not path.is_file():
        return empty_summary_frame() if summary else empty_report_frame()
    try:
        frame = pd.read_csv(path, dtype={"Ticker": str})
    except (OSError, UnicodeError, pd.errors.ParserError, ValueError):
        return empty_summary_frame() if summary else empty_report_frame()
    return normalize_summary_frame(frame) if summary else normalize_report_frame(frame)


def _configured_summary() -> pd.DataFrame:
    if not FUNDAMENTAL_DATA_PATH:
        return empty_summary_frame()
    return _read_csv(Path(FUNDAMENTAL_DATA_PATH), summary=True)


def _merge_summary_sources(base: pd.DataFrame, preferred: pd.DataFrame) -> pd.DataFrame:
    if base.empty:
        return normalize_summary_frame(preferred)
    if preferred.empty:
        return normalize_summary_frame(base)
    combined = pd.concat([base, preferred], ignore_index=True)
    return normalize_summary_frame(combined.drop_duplicates("Ticker", keep="last"))


def _read_metadata() -> dict[str, Any]:
    try:
        payload = json.loads(_META_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f"{path.stem}_", suffix=".csv", dir=path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        frame.to_csv(temporary, index=False, encoding="utf-8-sig")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(payload: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f"{path.stem}_", suffix=".json", dir=path.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(
            json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _symbols_fingerprint(symbols: Sequence[str]) -> str:
    payload = "\n".join(sorted(set(symbols))).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _stock_symbols(tickers: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in tickers:
        ticker = normalize_ticker(value)
        if not ticker or ticker.split(".", 1)[0].startswith(_ETF_PREFIXES):
            continue
        if ticker not in result:
            result.append(ticker)
    return result


def _normalized_industries(values: Mapping[str, str] | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_ticker, raw_industry in (values or {}).items():
        ticker = normalize_ticker(raw_ticker)
        try:
            if pd.isna(raw_industry):
                continue
        except (TypeError, ValueError):
            continue
        industry = str(raw_industry or "").strip()
        if ticker and industry:
            result[ticker] = industry
    return result


def _cache_is_current(
    metadata: Mapping[str, Any],
    symbols: Sequence[str],
    *,
    as_of: date,
) -> bool:
    if metadata.get("schema_version") != FUNDAMENTAL_SCHEMA_VERSION:
        return False
    if metadata.get("provider") != "baostock":
        return False
    if metadata.get("provider_check_status") not in {"SUCCESS", "DEGRADED"}:
        return False
    if metadata.get("target_report_period") != latest_completed_period(as_of).iso_date:
        return False
    if metadata.get("symbols_fingerprint") != _symbols_fingerprint(symbols):
        return False
    try:
        checked = date.fromisoformat(str(metadata.get("last_successful_check", "")))
    except ValueError:
        return False
    return 0 <= (as_of - checked).days < _CACHE_MAX_AGE_DAYS


def _cache_attempted_today(
    metadata: Mapping[str, Any],
    symbols: Sequence[str],
    *,
    as_of: date,
) -> bool:
    return bool(
        metadata.get("schema_version") == FUNDAMENTAL_SCHEMA_VERSION
        and metadata.get("target_report_period")
        == latest_completed_period(as_of).iso_date
        and metadata.get("symbols_fingerprint") == _symbols_fingerprint(symbols)
        and metadata.get("last_attempt") == as_of.isoformat()
    )


def _announced_records(frame: pd.DataFrame, ticker: str, *, as_of: date) -> pd.DataFrame:
    rows = frame.loc[frame["Ticker"].eq(ticker)].copy()
    if rows.empty:
        return rows
    report_dates = pd.to_datetime(rows["ReportPeriod"], errors="coerce").dt.date
    announcement_dates = pd.to_datetime(rows["AnnouncementDate"], errors="coerce").dt.date
    usable = report_dates.notna() & report_dates.le(as_of)
    usable &= announcement_dates.notna() & announcement_dates.le(as_of)
    return rows.loc[usable]


def _build_fetch_plans(
    symbols: Sequence[str],
    records: pd.DataFrame,
    *,
    as_of: date,
    force: bool,
) -> list[FundamentalFetchPlan]:
    probes = latest_probe_periods(as_of, limit=5)
    target = probes[0]
    annual_candidates = annual_candidate_periods(as_of, desired=3)
    plans: list[FundamentalFetchPlan] = []
    for ticker in symbols:
        cached = _announced_records(records, ticker, as_of=as_of)
        cached_periods = set(cached["ReportPeriod"].astype(str))
        cached_annuals = cached.loc[
            pd.to_numeric(cached["ReportQuarter"], errors="coerce").eq(4)
            & pd.to_numeric(cached["NetProfit"], errors="coerce").notna()
        ]
        cached_annual_years = set(
            pd.to_numeric(cached_annuals["ReportYear"], errors="coerce").dropna().astype(int)
        )
        target_rows = cached.loc[cached["ReportPeriod"].eq(target.iso_date)]
        target_core_complete = (
            pd.to_numeric(target_rows["ROE"], errors="coerce").notna()
            & pd.to_numeric(target_rows["NetProfit"], errors="coerce").notna()
        )
        target_cached = bool(
            target.iso_date in cached_periods
            and target_core_complete.any()
        )
        if force:
            latest = probes
            annual = annual_candidates
        else:
            latest = () if target_cached else (probes if cached.empty else (target,))
            annual = tuple(
                period
                for period in annual_candidates
                if period.year not in cached_annual_years
            )
            if len(cached_annual_years) >= 3:
                annual = ()
        if latest or annual:
            plans.append(
                FundamentalFetchPlan(
                    ticker=ticker,
                    latest_periods=tuple(latest),
                    annual_periods=tuple(annual),
                )
            )
    return plans


def _progress_log(
    completed: int,
    total: int,
    checked: int,
    failed: int,
    *,
    force: bool = False,
) -> None:
    interval = max(1, total // 100)
    if force or completed == total or completed % interval == 0:
        logger.info(
            "FUNDAMENTAL progress: %d/%d（已检查 %d，失败 %d）",
            completed,
            total,
            checked,
            failed,
        )


def _clear_quality_reader_cache() -> None:
    try:
        import fundamental_quality

        clear = getattr(fundamental_quality, "clear_fundamental_quality_cache", None)
        if callable(clear):
            clear()
    except ImportError:
        return


def _provider_identity(provider: Any) -> tuple[str, str]:
    name = str(getattr(provider, "provider_name", "baostock") or "baostock")
    version = str(getattr(provider, "provider_version", "unknown") or "unknown")
    return name, version


def _consume_outcomes(
    outcomes: Any,
    existing_records: pd.DataFrame,
    *,
    total: int,
) -> tuple[pd.DataFrame, int, int, int]:
    records = existing_records
    completed = 0
    checked = 0
    failed = 0
    last_heartbeat = time.monotonic()
    heartbeat = max(1.0, float(FUNDAMENTAL_PROGRESS_HEARTBEAT_SECONDS))
    for outcome in outcomes:
        if not isinstance(outcome, FundamentalFetchOutcome):
            raise TypeError("fundamental provider returned an invalid outcome")
        completed += 1
        checked += int(outcome.checked and not outcome.error)
        failed += int(bool(outcome.error))
        if not outcome.records.empty:
            records = merge_report_records(records, outcome.records)
        if completed % _CHECKPOINT_EVERY == 0:
            _atomic_write_csv(records.loc[:, REPORT_COLUMNS], _REPORT_CACHE_PATH)
        if time.monotonic() - last_heartbeat >= heartbeat:
            _progress_log(completed, total, checked, failed, force=True)
            last_heartbeat = time.monotonic()
        else:
            _progress_log(completed, total, checked, failed)
        if outcome.error:
            logger.debug("BaoStock 财报获取失败 %s: %s", outcome.ticker, outcome.error)
    return records, completed, checked, failed


def refresh_fundamental_data(
    tickers: list[str],
    force: bool = False,
    request: Callable[..., Any] | None = None,
    industry_by_ticker: Mapping[str, str] | None = None,
    workers: int | None = None,
    *,
    provider: Any | None = None,
    as_of: date | None = None,
) -> Path:
    """Refresh BaoStock reports while preserving a usable prior snapshot.

    ``request`` and ``workers`` remain accepted for source-compatible callers.
    BaoStock owns one process-global socket, therefore the canonical adapter is
    intentionally single-session and does not use thread workers.
    """
    del request, workers
    effective_date = as_of or _china_today()
    symbols = _stock_symbols(tickers)
    industries = _normalized_industries(industry_by_ticker)
    if not symbols:
        return _CACHE_PATH

    with _REFRESH_LOCK:
        existing = _merge_summary_sources(
            _configured_summary(),
            _read_csv(_CACHE_PATH, summary=True),
        )
        records = _read_csv(_REPORT_CACHE_PATH, summary=False)
        metadata_before = _read_metadata()
        if not force and not existing.empty:
            cache_current = _cache_is_current(
                metadata_before,
                symbols,
                as_of=effective_date,
            )
            attempted_today = _cache_attempted_today(
                metadata_before,
                symbols,
                as_of=effective_date,
            )
            if cache_current or attempted_today:
                logger.info("BaoStock 财报缓存今日已检查，跳过重复刷新。")
                return _CACHE_PATH

        plans = _build_fetch_plans(
            symbols,
            records,
            as_of=effective_date,
            force=force,
        )
        active_provider = provider or BaoStockFundamentalProvider(
            timeout_seconds=float(FUNDAMENTAL_DOWNLOAD_TIMEOUT),
            logger=logger,
        )
        provider_name, provider_version = _provider_identity(active_provider)
        logger.info(
            "开始刷新 BaoStock 财报：股票 %d，只需查询 %d，目标报告期 %s。",
            len(symbols),
            len(plans),
            latest_completed_period(effective_date).iso_date,
        )

        completed = checked = failed = 0
        provider_unavailable = ""
        if plans:
            try:
                outcomes = active_provider.fetch(plans)
                records, completed, checked, failed = _consume_outcomes(
                    outcomes,
                    records,
                    total=len(plans),
                )
            except BaoStockUnavailable as exc:
                provider_unavailable = str(exc)
                logger.warning("BaoStock 财报刷新不可用，保留现有缓存：%s", exc)
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                provider_unavailable = str(exc)
                logger.warning("BaoStock 财报刷新异常，保留现有缓存：%s", exc)
        else:
            checked = completed = 1

        if provider_unavailable and _REPORT_CACHE_PATH.is_file():
            records = merge_report_records(
                records,
                _read_csv(_REPORT_CACHE_PATH, summary=False),
            )
        if not records.empty:
            _atomic_write_csv(records.loc[:, REPORT_COLUMNS], _REPORT_CACHE_PATH)
        summary = build_fundamental_summary(
            records,
            existing,
            symbols,
            industries,
            as_of=effective_date,
        )
        if summary.empty:
            logger.warning("本轮没有可发布的财报数据，继续使用现有缓存。")
            return _CACHE_PATH
        _atomic_write_csv(summary.loc[:, FUNDAMENTAL_COLUMNS], _CACHE_PATH)
        _clear_quality_reader_cache()

        attempted = max(1, len(plans))
        check_ratio = checked / attempted if plans else 1.0
        hard_coverage = summary_hard_financial_coverage(summary, symbols)
        latest_coverage = summary_latest_period_coverage(
            summary,
            symbols,
            as_of=effective_date,
        )
        provider_check_ok = not provider_unavailable and check_ratio >= _MIN_PROVIDER_CHECK_RATIO
        if provider_check_ok and hard_coverage >= _MIN_HARD_COVERAGE:
            check_status = "SUCCESS"
        elif provider_check_ok:
            check_status = "DEGRADED"
        else:
            check_status = "PARTIAL"
        metadata: dict[str, Any] = {
            **metadata_before,
            "schema_version": FUNDAMENTAL_SCHEMA_VERSION,
            "provider_adapter_version": FUNDAMENTAL_PROVIDER_VERSION,
            "provider": provider_name,
            "provider_version": provider_version,
            "provider_check_status": check_status,
            "target_report_period": latest_completed_period(effective_date).iso_date,
            "last_attempt": effective_date.isoformat(),
            "symbols_fingerprint": _symbols_fingerprint(symbols),
            "symbols_requested": len(symbols),
            "symbols_planned": len(plans),
            "symbols_completed": completed,
            "symbols_checked": checked,
            "symbols_failed": failed,
            "provider_check_ratio": round(check_ratio, 6),
            "hard_financial_coverage": round(hard_coverage, 6),
            "latest_report_period_coverage": round(latest_coverage, 6),
            "summary_rows": len(summary),
            "report_rows": len(records),
            "last_error": provider_unavailable,
        }
        if provider_check_ok:
            metadata["last_successful_check"] = effective_date.isoformat()
        _atomic_write_json(metadata, _META_PATH)
        logger.info(
            "BaoStock 财报刷新完成：状态=%s，硬财务覆盖 %.1f%%，最新报告期覆盖 %.1f%%。",
            check_status,
            hard_coverage * 100.0,
            latest_coverage * 100.0,
        )
        return _CACHE_PATH


def fundamental_data_path() -> Path | None:
    if _CACHE_PATH.is_file():
        return _CACHE_PATH
    configured = Path(FUNDAMENTAL_DATA_PATH) if FUNDAMENTAL_DATA_PATH else None
    return configured if configured is not None and configured.is_file() else None


__all__ = [
    "FUNDAMENTAL_COLUMNS",
    "FUNDAMENTAL_PROVIDER_VERSION",
    "FUNDAMENTAL_SCHEMA_VERSION",
    "REPORT_COLUMNS",
    "_CACHE_PATH",
    "_META_PATH",
    "_REPORT_CACHE_PATH",
    "fundamental_data_path",
    "refresh_fundamental_data",
]
