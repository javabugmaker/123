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
    FUNDAMENTAL_CHECKPOINT_EVERY,
    FUNDAMENTAL_DATA_PATH,
    FUNDAMENTAL_DOWNLOAD_THREADS,
    FUNDAMENTAL_DOWNLOAD_TIMEOUT,
    FUNDAMENTAL_MAX_IN_FLIGHT_FACTOR,
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
_CHECKPOINT_EVERY = max(1, int(FUNDAMENTAL_CHECKPOINT_EVERY))
_ETF_PREFIXES = ("15", "16", "50", "51", "56", "58")
_REFRESH_JOURNAL_VERSION: Final = "2026-09-01-v111-fundamental-journal-v1"


class FundamentalRefreshCancelled(RuntimeError):
    """Raised after the durable journal captures a cancelled refresh batch."""


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


def _refresh_journal_path() -> Path:
    return _META_PATH.with_name("fundamental_refresh_journal.jsonl")


def _journal_identity(symbols: Sequence[str], *, as_of: date) -> dict[str, str]:
    return {
        "version": _REFRESH_JOURNAL_VERSION,
        "attempt_date": as_of.isoformat(),
        "target_report_period": latest_completed_period(as_of).iso_date,
        "symbols_fingerprint": _symbols_fingerprint(symbols),
        "provider_adapter_version": FUNDAMENTAL_PROVIDER_VERSION,
    }


def _empty_journal_state(identity: Mapping[str, str]) -> dict[str, Any]:
    return {
        "identity": dict(identity),
        "journal_valid": False,
        "phases": {
            "latest": {"completed": [], "failed": []},
            "annual": {"completed": [], "failed": []},
        },
    }


def _phase_values(state: Mapping[str, Any], phase: str, key: str) -> set[str]:
    phases = state.get("phases", {})
    phase_state = phases.get(phase, {}) if isinstance(phases, Mapping) else {}
    values = phase_state.get(key, []) if isinstance(phase_state, Mapping) else []
    if not isinstance(values, list):
        return set()
    return {normalize_ticker(value) for value in values if normalize_ticker(value)}


def _set_phase_values(
    state: dict[str, Any],
    phase: str,
    *,
    completed: set[str],
    failed: set[str],
) -> None:
    phases = state.setdefault("phases", {})
    phase_state = phases.setdefault(phase, {})
    phase_state["completed"] = sorted(completed)
    phase_state["failed"] = sorted(failed)


def _apply_journal_entry(state: dict[str, Any], entry: Mapping[str, Any]) -> None:
    phase = str(entry.get("phase", "")).strip().lower()
    ticker = normalize_ticker(entry.get("ticker", ""))
    if phase not in {"latest", "annual"} or not ticker:
        return
    completed = _phase_values(state, phase, "completed")
    failed = _phase_values(state, phase, "failed")
    if bool(entry.get("success", False)):
        completed.add(ticker)
        failed.discard(ticker)
    else:
        failed.add(ticker)
    _set_phase_values(
        state,
        phase,
        completed=completed,
        failed=failed,
    )


def _read_refresh_journal(
    symbols: Sequence[str],
    *,
    as_of: date,
) -> tuple[dict[str, Any], pd.DataFrame]:
    expected = _journal_identity(symbols, as_of=as_of)
    state = _empty_journal_state(expected)
    path = _refresh_journal_path()
    if not path.is_file():
        return state, empty_report_frame()
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            header_line = handle.readline()
            header = json.loads(header_line) if header_line.strip() else {}
            if not isinstance(header, dict) or header.get("identity") != expected:
                return state, empty_report_frame()
            state["journal_valid"] = True
            for line in handle:
                try:
                    entry = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "outcome":
                    continue
                _apply_journal_entry(state, entry)
                raw_records = entry.get("records", [])
                if isinstance(raw_records, list):
                    rows.extend(value for value in raw_records if isinstance(value, dict))
    except (OSError, UnicodeError, TypeError, ValueError):
        return _empty_journal_state(expected), empty_report_frame()
    frame = normalize_report_frame(pd.DataFrame(rows)) if rows else empty_report_frame()
    return state, frame


def _initialize_refresh_journal(state: dict[str, Any]) -> None:
    path = _refresh_journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f"{path.stem}_",
        suffix=".jsonl",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(
                    {"type": "header", "identity": state["identity"]},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        state["journal_valid"] = True
    finally:
        temporary.unlink(missing_ok=True)


def _outcome_journal_entry(
    outcome: FundamentalFetchOutcome,
    *,
    phase: str,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    if not outcome.records.empty:
        serializable = outcome.records.astype(object).where(outcome.records.notna(), None)
        records = serializable.to_dict(orient="records")
    return {
        "type": "outcome",
        "phase": phase,
        "ticker": normalize_ticker(outcome.ticker),
        "success": bool(outcome.checked and not outcome.error),
        "error": str(outcome.error or ""),
        "records": records,
    }


def _append_refresh_journal(
    state: dict[str, Any],
    entries: Sequence[Mapping[str, Any]],
) -> None:
    if not entries:
        return
    if not bool(state.get("journal_valid", False)):
        _initialize_refresh_journal(state)
    path = _refresh_journal_path()
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for entry in entries:
            handle.write(json.dumps(dict(entry), ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    for entry in entries:
        _apply_journal_entry(state, entry)


def _remove_refresh_journal() -> None:
    _refresh_journal_path().unlink(missing_ok=True)


def _journal_has_progress(state: Mapping[str, Any]) -> bool:
    return any(
        _phase_values(state, phase, key)
        for phase in ("latest", "annual")
        for key in ("completed", "failed")
    )


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


def _phase_fetch_plans(
    plans: Sequence[FundamentalFetchPlan],
    state: Mapping[str, Any],
    *,
    phase: str,
) -> list[FundamentalFetchPlan]:
    completed = _phase_values(state, phase, "completed")
    result: list[FundamentalFetchPlan] = []
    for plan in plans:
        ticker = normalize_ticker(plan.ticker)
        if not ticker or ticker in completed:
            continue
        if phase == "latest" and plan.latest_periods:
            result.append(
                FundamentalFetchPlan(
                    ticker=ticker,
                    latest_periods=plan.latest_periods,
                    annual_periods=(),
                    enrich_latest=True,
                )
            )
        elif phase == "annual" and plan.annual_periods:
            result.append(
                FundamentalFetchPlan(
                    ticker=ticker,
                    latest_periods=(),
                    annual_periods=plan.annual_periods,
                    enrich_latest=False,
                )
            )
    return result


def _plan_completion(
    plans: Sequence[FundamentalFetchPlan],
    state: Mapping[str, Any],
) -> tuple[set[str], set[str]]:
    latest_completed = _phase_values(state, "latest", "completed")
    annual_completed = _phase_values(state, "annual", "completed")
    latest_failed = _phase_values(state, "latest", "failed")
    annual_failed = _phase_values(state, "annual", "failed")
    completed: set[str] = set()
    failed: set[str] = set()
    for plan in plans:
        ticker = normalize_ticker(plan.ticker)
        latest_ok = not plan.latest_periods or ticker in latest_completed
        annual_ok = not plan.annual_periods or ticker in annual_completed
        if latest_ok and annual_ok:
            completed.add(ticker)
        if (
            (plan.latest_periods and ticker in latest_failed)
            or (plan.annual_periods and ticker in annual_failed)
        ):
            failed.add(ticker)
    return completed, failed


def _provider_outcomes(
    provider: Any,
    plans: Sequence[FundamentalFetchPlan],
    *,
    workers: int,
    cancel_event: threading.Event | None = None,
) -> Any:
    parallel_fetch = getattr(provider, "fetch_parallel", None)
    if workers > 1 and callable(parallel_fetch):
        kwargs: dict[str, Any] = {
            "workers": workers,
            "max_in_flight": workers
            * max(1, int(FUNDAMENTAL_MAX_IN_FLIGHT_FACTOR)),
        }
        if isinstance(provider, BaoStockFundamentalProvider):
            kwargs["cancel_event"] = cancel_event
        return parallel_fetch(plans, **kwargs)
    return provider.fetch(plans)


def _progress_log(
    completed: int,
    total: int,
    checked: int,
    failed: int,
    *,
    phase: str,
    started_at: float,
    workers: int,
    force: bool = False,
) -> None:
    interval = max(1, total // 100)
    if force or completed == total or completed % interval == 0:
        elapsed = max(0.001, time.monotonic() - started_at)
        rate = completed / elapsed
        success_rate = checked / completed * 100.0 if completed else 0.0
        remaining_seconds = (total - completed) / rate if rate > 0 else 0.0
        logger.info(
            "FUNDAMENTAL progress: %d/%d (%d checked, %d failed). "
            "phase=%s | workers=%d | rate=%.2f stocks/s | success=%.1f%% | ETA=%s",
            completed,
            total,
            checked,
            failed,
            phase.upper(),
            workers,
            rate,
            success_rate,
            _format_duration(remaining_seconds),
        )


def _format_duration(seconds: float) -> str:
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


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
    phase: str,
    journal_state: dict[str, Any],
    workers: int,
    cancel_event: threading.Event | None = None,
) -> tuple[pd.DataFrame, int, int, int]:
    records = existing_records
    completed = 0
    checked = 0
    failed = 0
    pending_entries: list[dict[str, Any]] = []
    pending_frames: list[pd.DataFrame] = []
    started_at = time.monotonic()
    last_heartbeat = time.monotonic()
    heartbeat = max(1.0, float(FUNDAMENTAL_PROGRESS_HEARTBEAT_SECONDS))

    def checkpoint() -> None:
        nonlocal records
        if not pending_entries:
            return
        _append_refresh_journal(journal_state, pending_entries)
        if pending_frames:
            batch = normalize_report_frame(
                pd.concat(pending_frames, ignore_index=True, sort=False)
            )
            records = merge_report_records(records, batch)
        pending_entries.clear()
        pending_frames.clear()

    try:
        for outcome in outcomes:
            if not isinstance(outcome, FundamentalFetchOutcome):
                raise TypeError("fundamental provider returned an invalid outcome")
            completed += 1
            checked += int(outcome.checked and not outcome.error)
            failed += int(bool(outcome.error))
            pending_entries.append(_outcome_journal_entry(outcome, phase=phase))
            if not outcome.records.empty:
                pending_frames.append(outcome.records)
            if completed % _CHECKPOINT_EVERY == 0:
                checkpoint()
            if time.monotonic() - last_heartbeat >= heartbeat:
                _progress_log(
                    completed,
                    total,
                    checked,
                    failed,
                    phase=phase,
                    started_at=started_at,
                    workers=workers,
                    force=True,
                )
                last_heartbeat = time.monotonic()
            else:
                _progress_log(
                    completed,
                    total,
                    checked,
                    failed,
                    phase=phase,
                    started_at=started_at,
                    workers=workers,
                )
            if outcome.error:
                logger.debug("BaoStock 财报获取失败 %s: %s", outcome.ticker, outcome.error)
            if cancel_event is not None and cancel_event.is_set():
                raise FundamentalRefreshCancelled("BaoStock 财报刷新已取消")
    finally:
        # Preserve even a short final batch when the provider raises or the GUI
        # requests cancellation.  A hard process termination can lose at most
        # the configured checkpoint interval instead of the whole refresh.
        try:
            checkpoint()
        finally:
            close = getattr(outcomes, "close", None)
            if callable(close):
                close()
    if completed:
        if not records.empty:
            _atomic_write_csv(records.loc[:, REPORT_COLUMNS], _REPORT_CACHE_PATH)
        _progress_log(
            completed,
            total,
            checked,
            failed,
            phase=phase,
            started_at=started_at,
            workers=workers,
            force=True,
        )
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
    cancel_event: threading.Event | None = None,
) -> Path:
    """Refresh BaoStock reports with process isolation and resumable journals.

    BaoStock accepts one stock code per request and stores its active socket in
    module globals.  The canonical provider therefore parallelises by process,
    not thread.  Each worker keeps one independent session, while the parent
    appends outcome batches to an fsynced journal before updating in-memory
    records.  A terminated GUI run can resume both rows and no-data checks.
    """
    del request
    effective_date = as_of or _china_today()
    symbols = _stock_symbols(tickers)
    industries = _normalized_industries(industry_by_ticker)
    requested_workers = max(
        1,
        min(12, int(workers if workers is not None else FUNDAMENTAL_DOWNLOAD_THREADS)),
    )
    if not symbols:
        return _CACHE_PATH
    if cancel_event is not None and cancel_event.is_set():
        raise FundamentalRefreshCancelled("BaoStock 财报刷新已取消")

    with _REFRESH_LOCK:
        existing = _merge_summary_sources(
            _configured_summary(),
            _read_csv(_CACHE_PATH, summary=True),
        )
        records = _read_csv(_REPORT_CACHE_PATH, summary=False)
        journal_state, journal_records = _read_refresh_journal(
            symbols,
            as_of=effective_date,
        )
        if not journal_records.empty:
            records = merge_report_records(records, journal_records)
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
            if (cache_current or attempted_today) and not _journal_has_progress(
                journal_state
            ):
                logger.info("BaoStock 财报缓存今日已检查，跳过重复刷新。")
                return _CACHE_PATH

        plans = _build_fetch_plans(
            symbols,
            records,
            as_of=effective_date,
            force=force,
        )
        latest_plans = _phase_fetch_plans(plans, journal_state, phase="latest")
        annual_plans = _phase_fetch_plans(plans, journal_state, phase="annual")
        active_provider = provider or BaoStockFundamentalProvider(
            timeout_seconds=float(FUNDAMENTAL_DOWNLOAD_TIMEOUT),
            logger=logger,
        )
        provider_name, provider_version = _provider_identity(active_provider)
        logger.info(
            "开始刷新 BaoStock 财报：股票 %d，最新季度 %d，历史回填 %d，"
            "进程 %d，断点间隔 %d，目标报告期 %s。",
            len(symbols),
            len(latest_plans),
            len(annual_plans),
            requested_workers,
            _CHECKPOINT_EVERY,
            latest_completed_period(effective_date).iso_date,
        )

        tasks_completed = tasks_checked = tasks_failed = 0
        provider_unavailable = ""
        phases = (("latest", latest_plans), ("annual", annual_plans))
        for phase, phase_plans in phases:
            if not phase_plans or provider_unavailable:
                continue
            if cancel_event is not None and cancel_event.is_set():
                raise FundamentalRefreshCancelled("BaoStock 财报刷新已取消")
            phase_workers = min(requested_workers, len(phase_plans))
            phase_label = "最新季度" if phase == "latest" else "历史年报回填"
            logger.info(
                "BaoStock %s阶段启动：任务 %d，独立会话 %d。",
                phase_label,
                len(phase_plans),
                phase_workers,
            )
            try:
                outcomes = _provider_outcomes(
                    active_provider,
                    phase_plans,
                    workers=phase_workers,
                    cancel_event=cancel_event,
                )
                records, completed, checked, failed = _consume_outcomes(
                    outcomes,
                    records,
                    total=len(phase_plans),
                    phase=phase,
                    journal_state=journal_state,
                    workers=phase_workers,
                    cancel_event=cancel_event,
                )
                if cancel_event is not None and cancel_event.is_set():
                    raise FundamentalRefreshCancelled("BaoStock 财报刷新已取消")
                tasks_completed += completed
                tasks_checked += checked
                tasks_failed += failed
            except FundamentalRefreshCancelled:
                raise
            except BaoStockUnavailable as exc:
                provider_unavailable = str(exc)
                logger.warning("BaoStock 财报刷新不可用，保留现有缓存：%s", exc)
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                provider_unavailable = str(exc)
                logger.warning("BaoStock 财报刷新异常，保留现有缓存：%s", exc)
            if phase == "latest" and not records.empty:
                interim = build_fundamental_summary(
                    records,
                    existing,
                    symbols,
                    industries,
                    as_of=effective_date,
                )
                if not interim.empty:
                    _atomic_write_csv(
                        interim.loc[:, FUNDAMENTAL_COLUMNS],
                        _CACHE_PATH,
                    )
                    _clear_quality_reader_cache()
                    logger.info(
                        "BaoStock 最新季度阶段已发布中间缓存；历史年报继续后台回填。"
                    )

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
        completed_symbols, failed_symbols = _plan_completion(plans, journal_state)
        check_ratio = len(completed_symbols) / attempted if plans else 1.0
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
            "symbols_completed": len(completed_symbols),
            "symbols_checked": len(completed_symbols),
            "symbols_failed": len(failed_symbols),
            "symbols_remaining": max(0, len(plans) - len(completed_symbols)),
            "latest_tasks_planned": len(latest_plans),
            "annual_tasks_planned": len(annual_plans),
            "tasks_completed_this_run": tasks_completed,
            "tasks_checked_this_run": tasks_checked,
            "tasks_failed_this_run": tasks_failed,
            "refresh_workers": requested_workers,
            "checkpoint_every": _CHECKPOINT_EVERY,
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
        all_required_complete = not plans or len(completed_symbols) == len(plans)
        if all_required_complete and not provider_unavailable:
            _remove_refresh_journal()
        logger.info(
            "BaoStock 财报刷新完成：状态=%s，进程=%d，完成 %d/%d，"
            "硬财务覆盖 %.1f%%，最新报告期覆盖 %.1f%%。",
            check_status,
            requested_workers,
            len(completed_symbols),
            len(plans),
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
    "FundamentalRefreshCancelled",
    "REPORT_COLUMNS",
    "_CACHE_PATH",
    "_META_PATH",
    "_REPORT_CACHE_PATH",
    "fundamental_data_path",
    "refresh_fundamental_data",
]
