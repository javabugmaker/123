"""AKShare batch adapter for low-frequency, point-in-time financial reports."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Final

import numpy as np
import pandas as pd

from network_proxy import configure_akshare_proxy_from_system

from .fundamental_schema import (
    ReportPeriod,
    empty_report_frame,
    normalize_report_frame,
    normalize_ticker,
)

try:
    import akshare as _akshare
except ImportError:  # pragma: no cover - dependency-free callers
    _akshare = None

AKSHARE_PROVIDER_VERSION: Final = "2026-09-01-akshare-batch-pit-adapter-v1"
_CALL_LOCK = threading.Lock()


class AkShareUnavailable(RuntimeError):
    """Raised when AKShare cannot provide a usable batch response."""


class AkShareQueryError(RuntimeError):
    """Raised when one AKShare report-period request fails."""


@dataclass(frozen=True)
class FundamentalFetchPlan:
    ticker: str
    latest_periods: tuple[ReportPeriod, ...]
    annual_periods: tuple[ReportPeriod, ...]
    enrich_latest: bool = True


@dataclass(frozen=True)
class FundamentalFetchOutcome:
    ticker: str
    records: pd.DataFrame
    checked: bool
    error: str = ""


def _number_series(frame: pd.DataFrame, *names: str) -> pd.Series:
    for name in names:
        if name in frame:
            values = frame[name]
            if values.dtype == object:
                values = values.astype(str).str.replace(",", "", regex=False)
            return pd.to_numeric(values, errors="coerce").replace(
                [np.inf, -np.inf], np.nan
            )
    return pd.Series(np.nan, index=frame.index, dtype=float)


def _text_series(frame: pd.DataFrame, *names: str) -> pd.Series:
    for name in names:
        if name in frame:
            return (
                frame[name]
                .fillna("")
                .astype(str)
                .str.strip()
                .replace({"nan": "", "None": "", "<NA>": ""})
            )
    return pd.Series("", index=frame.index, dtype=object)


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return normalize_ticker(text)


def normalize_earnings_report(
    frame: pd.DataFrame | None,
    period: ReportPeriod,
    *,
    fetched_at: str,
) -> pd.DataFrame:
    """Map one Eastmoney earnings-report cross section into the PIT schema."""
    if frame is None or frame.empty:
        return empty_report_frame()
    working = frame.copy()
    ticker = _text_series(working, "股票代码", "证券代码", "代码").map(
        _normalize_code
    )
    announcement = pd.to_datetime(
        _text_series(working, "最新公告日期", "公告日期"),
        errors="coerce",
        format="mixed",
    ).dt.strftime("%Y-%m-%d").fillna("")
    revenue = _number_series(
        working,
        "营业总收入-营业总收入",
        "营业收入-营业收入",
        "营业总收入",
        "营业收入",
    )
    net_profit = _number_series(
        working,
        "净利润-净利润",
        "归母净利润",
        "净利润",
    )
    eps = _number_series(working, "每股收益", "基本每股收益")
    operating_cash_flow_per_share = _number_series(
        working,
        "每股经营现金流量",
        "每股经营现金流",
    )
    cash_to_profit = operating_cash_flow_per_share.div(eps.where(eps.abs().gt(1e-12)))
    net_margin = net_profit.div(revenue.where(revenue.abs().gt(1e-12))).mul(100.0)

    normalized = pd.DataFrame(
        {
            "Ticker": ticker,
            "Industry": _text_series(working, "所处行业", "行业", "行业名称"),
            "ReportPeriod": period.iso_date,
            "AnnouncementDate": announcement,
            "ReportYear": period.year,
            "ReportQuarter": period.quarter,
            "ROE": _number_series(
                working,
                "净资产收益率",
                "加权净资产收益率",
                "ROE",
            ),
            "GrossMargin": _number_series(working, "销售毛利率", "毛利率"),
            "NetMargin": net_margin,
            "NetProfit": net_profit,
            "Revenue": revenue,
            "EPSTTM": eps,
            "NetProfitYoY": _number_series(
                working,
                "净利润-同比增长",
                "净利润同比增长",
            ),
            "EquityYoY": np.nan,
            "AssetYoY": np.nan,
            "DebtToAssets": np.nan,
            "CurrentRatio": np.nan,
            "QuickRatio": np.nan,
            "OperatingCashFlowToRevenue": np.nan,
            "OperatingCashFlowToNetProfit": cash_to_profit,
            "Provider": "akshare",
            "FetchedAt": fetched_at,
        }
    )
    return normalize_report_frame(normalized.loc[ticker.ne("")])


class AkShareFundamentalProvider:
    """Fetch whole-market quarterly cross sections and reuse them per ticker.

    ``stock_yjbb_em`` returns one report period for the whole market, so a full
    refresh needs only a handful of provider calls instead of one request per
    stock.  Calls remain serial because the public upstream is not a concurrency
    contract; local ticker extraction is vectorized and in-memory.
    """

    provider_name: Final = "akshare"

    def __init__(
        self,
        *,
        module: Any | None = None,
        timeout_seconds: float = 300.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._module = _akshare if module is None else module
        self._timeout_seconds = max(15.0, float(timeout_seconds))
        self._logger = logger or logging.getLogger(
            "institution_scanner.fundamentals"
        )
        self._period_cache: dict[ReportPeriod, pd.DataFrame] = {}
        self._period_errors: dict[ReportPeriod, str] = {}

    @property
    def provider_version(self) -> str:
        if self._module is None:
            return "unavailable"
        value = str(getattr(self._module, "__version__", "unknown") or "unknown")
        return value.strip() or "unknown"

    def _bounded_call(self, period: ReportPeriod) -> pd.DataFrame:
        if self._module is None:
            raise AkShareUnavailable("AKShare 未安装，无法刷新财报数据")
        operation = getattr(self._module, "stock_yjbb_em", None)
        if not callable(operation):
            raise AkShareUnavailable(
                "当前 AKShare 缺少 stock_yjbb_em，请升级依赖后重试"
            )
        outcome: dict[str, Any] = {}

        def run() -> None:
            if not _CALL_LOCK.acquire(blocking=False):
                outcome["error"] = RuntimeError("上一笔 AKShare 请求仍在运行")
                return
            try:
                configure_akshare_proxy_from_system()
                outcome["frame"] = operation(date=period.key)
            except Exception as exc:  # provider exception surface is unstable
                outcome["error"] = exc
            finally:
                _CALL_LOCK.release()

        worker = threading.Thread(
            target=run,
            name=f"akshare-financial-{period.key}",
            daemon=True,
        )
        worker.start()
        started_at = time.monotonic()
        heartbeat = min(10.0, max(1.0, self._timeout_seconds / 6.0))
        while worker.is_alive():
            remaining = self._timeout_seconds - (time.monotonic() - started_at)
            if remaining <= 0:
                raise AkShareQueryError(
                    f"AKShare 业绩报表 {period.key} 请求超时"
                )
            worker.join(timeout=min(heartbeat, remaining))
            if worker.is_alive():
                self._logger.info(
                    "AKShare 财报批次 %s 仍在下载，已等待 %d 秒。",
                    period.key,
                    round(time.monotonic() - started_at),
                )
        error = outcome.get("error")
        if error is not None:
            raise AkShareQueryError(
                f"AKShare 业绩报表 {period.key} 获取失败：{error}"
            ) from error
        frame = outcome.get("frame")
        if not isinstance(frame, pd.DataFrame):
            raise AkShareQueryError(
                f"AKShare 业绩报表 {period.key} 返回类型无效"
            )
        return frame

    def _load_period(self, period: ReportPeriod) -> pd.DataFrame:
        cached = self._period_cache.get(period)
        if cached is not None:
            return cached
        if period in self._period_errors:
            raise AkShareQueryError(self._period_errors[period])
        try:
            raw = self._bounded_call(period)
            fetched_at = datetime.now(timezone.utc).isoformat()
            normalized = normalize_earnings_report(
                raw,
                period,
                fetched_at=fetched_at,
            )
        except (AkShareUnavailable, AkShareQueryError) as exc:
            self._period_errors[period] = str(exc)
            raise
        self._period_cache[period] = normalized
        self._logger.info(
            "AKShare 财报批次 %s 已载入：%d 只股票。",
            period.key,
            len(normalized),
        )
        return normalized

    def fetch(
        self,
        plans: Sequence[FundamentalFetchPlan],
        *,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[FundamentalFetchOutcome]:
        if self._module is None:
            raise AkShareUnavailable("AKShare 未安装，无法刷新财报数据")
        requested_periods = tuple(
            dict.fromkeys(
                period
                for plan in plans
                for period in (*plan.latest_periods, *plan.annual_periods)
            )
        )
        failures: dict[ReportPeriod, str] = {}
        for period in requested_periods:
            if cancel_event is not None and cancel_event.is_set():
                return
            try:
                self._load_period(period)
            except (AkShareUnavailable, AkShareQueryError) as exc:
                failures[period] = str(exc)
                self._logger.warning("%s", exc)
        if requested_periods and len(failures) == len(requested_periods):
            details = next(iter(failures.values()), "全部财报批次不可用")
            raise AkShareUnavailable(details)

        for plan in plans:
            if cancel_event is not None and cancel_event.is_set():
                return
            periods = tuple(dict.fromkeys((*plan.latest_periods, *plan.annual_periods)))
            frames: list[pd.DataFrame] = []
            for period in periods:
                frame = self._period_cache.get(period)
                if frame is None or frame.empty:
                    continue
                rows = frame.loc[frame["Ticker"].eq(normalize_ticker(plan.ticker))]
                if not rows.empty:
                    frames.append(rows)
            records = (
                normalize_report_frame(pd.concat(frames, ignore_index=True))
                if frames
                else empty_report_frame()
            )
            failed_periods = [period for period in periods if period in failures]
            error = ""
            if failed_periods:
                error = "；".join(
                    f"{period.key}: {failures[period]}" for period in failed_periods
                )
            yield FundamentalFetchOutcome(
                ticker=normalize_ticker(plan.ticker),
                records=records,
                checked=not failed_periods,
                error=error,
            )


__all__ = [
    "AKSHARE_PROVIDER_VERSION",
    "AkShareFundamentalProvider",
    "AkShareQueryError",
    "AkShareUnavailable",
    "FundamentalFetchOutcome",
    "FundamentalFetchPlan",
    "normalize_earnings_report",
]
