"""BaoStock adapter for low-frequency, point-in-time financial reports."""

from __future__ import annotations

import io
import logging
import socket
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

import numpy as np
import pandas as pd

from .fundamental_schema import (
    REPORT_COLUMNS,
    ReportPeriod,
    baostock_code,
    normalize_report_frame,
    normalize_ticker,
    parse_report_period,
)

try:
    import baostock as _baostock
except ImportError:  # pragma: no cover - exercised by dependency-free callers
    _baostock = None

BAOSTOCK_PROVIDER_VERSION: Final = "2026-09-01-baostock-adapter-v1"
_SESSION_LOCK = threading.Lock()


class BaoStockUnavailable(RuntimeError):
    """Raised when the provider cannot establish a usable session."""


class BaoStockQueryError(RuntimeError):
    """Raised when a provider query returns a non-success status."""


@dataclass(frozen=True)
class FundamentalFetchPlan:
    ticker: str
    latest_periods: tuple[ReportPeriod, ...]
    annual_periods: tuple[ReportPeriod, ...]


@dataclass(frozen=True)
class FundamentalFetchOutcome:
    ticker: str
    records: pd.DataFrame
    checked: bool
    error: str = ""


def _number(value: Any) -> float:
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        if not value or value.lower() in {"nan", "none", "null", "--", "-"}:
            return np.nan
    try:
        result = float(value)
    except (TypeError, ValueError):
        return np.nan
    return result if np.isfinite(result) else np.nan


def _text(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    result = str(value).strip()
    return "" if result.lower() in {"nan", "none", "null", "<na>"} else result


def _date_text(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else pd.Timestamp(parsed).date().isoformat()


class BaoStockFundamentalProvider:
    """Single-session BaoStock reader.

    BaoStock keeps its socket in module-global state, so concurrent threads are
    unsafe.  One bounded session is deliberately used for a refresh; the caller
    checkpoints records incrementally to make the slower per-ticker API
    resumable.
    """

    provider_name: Final = "baostock"

    def __init__(
        self,
        *,
        module: Any | None = None,
        timeout_seconds: float = 12.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._module = _baostock if module is None else module
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._logger = logger or logging.getLogger("institution_scanner.fundamentals")

    @property
    def provider_version(self) -> str:
        if self._module is None:
            return "unavailable"
        return _text(getattr(self._module, "__version__", "unknown")) or "unknown"

    @staticmethod
    def _quiet_call(operation: Any, *args: Any, **kwargs: Any) -> Any:
        capture = io.StringIO()
        with redirect_stdout(capture), redirect_stderr(capture):
            return operation(*args, **kwargs)

    @contextmanager
    def _session(self) -> Iterator[None]:
        if self._module is None:
            raise BaoStockUnavailable("BaoStock 未安装，无法刷新财报数据")
        with _SESSION_LOCK:
            previous_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(self._timeout_seconds)
            logged_in = False
            try:
                try:
                    response = self._quiet_call(self._module.login)
                except Exception as exc:
                    raise BaoStockUnavailable(f"BaoStock 登录失败：{exc}") from exc
                code = _text(getattr(response, "error_code", ""))
                message = _text(getattr(response, "error_msg", ""))
                if code != "0":
                    raise BaoStockUnavailable(
                        f"BaoStock 登录失败：{code or 'UNKNOWN'} {message or '未知错误'}"
                    )
                logged_in = True
                yield
            finally:
                if logged_in:
                    try:
                        self._quiet_call(self._module.logout)
                    except Exception:
                        self._logger.debug("BaoStock logout failed", exc_info=True)
                socket.setdefaulttimeout(previous_timeout)

    def _query_frame(
        self,
        method_name: str,
        ticker: str,
        period: ReportPeriod,
    ) -> pd.DataFrame:
        if self._module is None:
            raise BaoStockUnavailable("BaoStock 未安装")
        method = getattr(self._module, method_name, None)
        if method is None:
            raise BaoStockQueryError(f"当前 BaoStock 缺少 {method_name}")
        provider_code = baostock_code(ticker)
        if not provider_code:
            raise BaoStockQueryError(f"无法转换股票代码：{ticker}")
        try:
            result = self._quiet_call(
                method,
                code=provider_code,
                year=period.year,
                quarter=period.quarter,
            )
        except Exception as exc:
            raise BaoStockQueryError(
                f"{method_name} {ticker} {period.key} 请求异常：{exc}"
            ) from exc
        error_code = _text(getattr(result, "error_code", ""))
        error_message = _text(getattr(result, "error_msg", ""))
        if error_code != "0":
            raise BaoStockQueryError(
                f"{method_name} {ticker} {period.key} 失败："
                f"{error_code or 'UNKNOWN'} {error_message or '未知错误'}"
            )
        fields = [str(value) for value in getattr(result, "fields", [])]
        rows: list[list[Any]] = []
        try:
            while result.next():
                rows.append(list(result.get_row_data()))
        except Exception as exc:
            raise BaoStockQueryError(
                f"{method_name} {ticker} {period.key} 读取失败：{exc}"
            ) from exc
        return pd.DataFrame(rows, columns=fields) if fields else pd.DataFrame()

    def _profit_record(
        self,
        ticker: str,
        period: ReportPeriod,
        frame: pd.DataFrame,
        fetched_at: str,
    ) -> dict[str, Any] | None:
        if frame.empty:
            return None
        working = frame.copy()
        if "pubDate" in working:
            working["_pub"] = pd.to_datetime(working["pubDate"], errors="coerce")
            working = working.sort_values("_pub", kind="stable")
        row = working.iloc[-1]
        reported_period = parse_report_period(row.get("statDate")) or period
        return {
            "Ticker": normalize_ticker(ticker),
            "Industry": "",
            "ReportPeriod": reported_period.iso_date,
            "AnnouncementDate": _date_text(row.get("pubDate")),
            "ReportYear": reported_period.year,
            "ReportQuarter": reported_period.quarter,
            "ROE": _number(row.get("roeAvg")),
            "GrossMargin": _number(row.get("gpMargin")),
            "NetMargin": _number(row.get("npMargin")),
            "NetProfit": _number(row.get("netProfit")),
            "Revenue": _number(row.get("MBRevenue")),
            "EPSTTM": _number(row.get("epsTTM")),
            "NetProfitYoY": np.nan,
            "EquityYoY": np.nan,
            "AssetYoY": np.nan,
            "DebtToAssets": np.nan,
            "CurrentRatio": np.nan,
            "QuickRatio": np.nan,
            "OperatingCashFlowToRevenue": np.nan,
            "OperatingCashFlowToNetProfit": np.nan,
            "Provider": self.provider_name,
            "FetchedAt": fetched_at,
        }

    @staticmethod
    def _latest_row(frame: pd.DataFrame, period: ReportPeriod) -> pd.Series | None:
        if frame.empty:
            return None
        working = frame.copy()
        if "statDate" in working:
            stat_date = pd.to_datetime(working["statDate"], errors="coerce").dt.date
            matching = working.loc[stat_date.eq(period.end_date)]
            if not matching.empty:
                working = matching
        if "pubDate" in working:
            working["_pub"] = pd.to_datetime(working["pubDate"], errors="coerce")
            working = working.sort_values("_pub", kind="stable")
        return working.iloc[-1]

    def _enrich_latest_record(
        self,
        ticker: str,
        period: ReportPeriod,
        record: dict[str, Any],
    ) -> None:
        queries: tuple[tuple[str, dict[str, str]], ...] = (
            (
                "query_growth_data",
                {
                    "YOYNI": "NetProfitYoY",
                    "YOYEquity": "EquityYoY",
                    "YOYAsset": "AssetYoY",
                },
            ),
            (
                "query_balance_data",
                {
                    "liabilityToAsset": "DebtToAssets",
                    "currentRatio": "CurrentRatio",
                    "quickRatio": "QuickRatio",
                },
            ),
            (
                "query_cash_flow_data",
                {
                    "CFOToOR": "OperatingCashFlowToRevenue",
                    "CFOToNP": "OperatingCashFlowToNetProfit",
                },
            ),
        )
        for method_name, mapping in queries:
            try:
                frame = self._query_frame(method_name, ticker, period)
            except BaoStockQueryError as exc:
                self._logger.debug("BaoStock optional enrichment skipped: %s", exc)
                continue
            row = self._latest_row(frame, period)
            if row is None:
                continue
            for source, target in mapping.items():
                record[target] = _number(row.get(source))
            announcement = _date_text(row.get("pubDate"))
            if announcement and announcement > _text(record.get("AnnouncementDate")):
                record["AnnouncementDate"] = announcement

    def _fetch_plan(self, plan: FundamentalFetchPlan) -> FundamentalFetchOutcome:
        ticker = normalize_ticker(plan.ticker)
        fetched_at = datetime.now(timezone.utc).isoformat()
        records: dict[str, dict[str, Any]] = {}
        queried: set[str] = set()
        checked = False
        try:
            for period in plan.latest_periods:
                checked = True
                queried.add(period.key)
                frame = self._query_frame("query_profit_data", ticker, period)
                record = self._profit_record(ticker, period, frame, fetched_at)
                if record is not None:
                    records[period.key] = record
                    break
            for period in plan.annual_periods:
                if period.key in queried:
                    continue
                checked = True
                queried.add(period.key)
                frame = self._query_frame("query_profit_data", ticker, period)
                record = self._profit_record(ticker, period, frame, fetched_at)
                if record is not None:
                    records[period.key] = record
            if records:
                latest_key = max(records)
                latest_period = parse_report_period(records[latest_key]["ReportPeriod"])
                if latest_period is not None:
                    self._enrich_latest_record(ticker, latest_period, records[latest_key])
            frame = normalize_report_frame(pd.DataFrame(records.values(), columns=REPORT_COLUMNS))
            return FundamentalFetchOutcome(ticker=ticker, records=frame, checked=checked)
        except BaoStockQueryError as exc:
            return FundamentalFetchOutcome(
                ticker=ticker,
                records=normalize_report_frame(pd.DataFrame(records.values())),
                checked=checked,
                error=str(exc),
            )

    def fetch(self, plans: Sequence[FundamentalFetchPlan]) -> Iterator[FundamentalFetchOutcome]:
        with self._session():
            for plan in plans:
                yield self._fetch_plan(plan)
