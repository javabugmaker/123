"""Point-in-time schemas and pure transformations for A-share fundamentals.

The market-data boundary is intentionally absent from this module.  TickFlow
owns the universe and OHLCV; this module only normalises low-frequency financial
reports and materialises the compatibility summary consumed by the scanner.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

import numpy as np
import pandas as pd

FUNDAMENTAL_SCHEMA_VERSION: Final = "2026-09-01-baostock-pit-v1"

REPORT_COLUMNS: Final[tuple[str, ...]] = (
    "Ticker",
    "Industry",
    "ReportPeriod",
    "AnnouncementDate",
    "ReportYear",
    "ReportQuarter",
    "ROE",
    "GrossMargin",
    "NetMargin",
    "NetProfit",
    "Revenue",
    "EPSTTM",
    "NetProfitYoY",
    "EquityYoY",
    "AssetYoY",
    "DebtToAssets",
    "CurrentRatio",
    "QuickRatio",
    "OperatingCashFlowToRevenue",
    "OperatingCashFlowToNetProfit",
    "Provider",
    "FetchedAt",
)

# The legacy institution fields remain readable so old result files and caches
# do not become unparseable.  BaoStock does not supply this evidence and new
# rows leave it unknown; current quality decisions do not depend on it.
FUNDAMENTAL_COLUMNS: Final[tuple[str, ...]] = (
    "Ticker",
    "Industry",
    "LatestReportPeriod",
    "LatestAnnouncementDate",
    "LatestReportType",
    "FundamentalProvider",
    "FundamentalFetchedAt",
    "FundamentalDataStatus",
    "ROE",
    "GrossMargin",
    "NetProfitLatest",
    "RevenueLatest",
    "NetProfitYoY",
    "DebtToAssets",
    "OperatingCashFlowToNetProfit",
    "NetProfitY1",
    "NetProfitY2",
    "NetProfitY3",
    "IndustryGrossMarginPercentile",
    "InstitutionHoldingTrend",
    "InstitutionHoldingPeriods",
)

FUNDAMENTAL_REQUIRED_COLUMNS: Final[frozenset[str]] = frozenset(
    {
        "Ticker",
        "ROE",
        "GrossMargin",
        "NetProfitY1",
        "NetProfitY2",
        "NetProfitY3",
        "IndustryGrossMarginPercentile",
    }
)

_REPORT_TEXT_COLUMNS = frozenset(
    {"Ticker", "Industry", "ReportPeriod", "AnnouncementDate", "Provider", "FetchedAt"}
)
_SUMMARY_TEXT_COLUMNS = frozenset(
    {
        "Ticker",
        "Industry",
        "LatestReportPeriod",
        "LatestAnnouncementDate",
        "LatestReportType",
        "FundamentalProvider",
        "FundamentalFetchedAt",
        "FundamentalDataStatus",
        "InstitutionHoldingTrend",
    }
)
_QUARTER_ENDS: Final[dict[int, tuple[int, int]]] = {
    1: (3, 31),
    2: (6, 30),
    3: (9, 30),
    4: (12, 31),
}
_REPORT_TYPE_NAMES: Final[dict[int, str]] = {
    1: "一季报",
    2: "半年报",
    3: "三季报",
    4: "年报",
}


@dataclass(frozen=True, order=True)
class ReportPeriod:
    year: int
    quarter: int

    def __post_init__(self) -> None:
        if self.quarter not in _QUARTER_ENDS:
            raise ValueError(f"invalid report quarter: {self.quarter}")

    @property
    def end_date(self) -> date:
        month, day = _QUARTER_ENDS[self.quarter]
        return date(self.year, month, day)

    @property
    def key(self) -> str:
        return self.end_date.strftime("%Y%m%d")

    @property
    def iso_date(self) -> str:
        return self.end_date.isoformat()

    @property
    def report_type(self) -> str:
        return _REPORT_TYPE_NAMES[self.quarter]

    @property
    def filing_deadline(self) -> date:
        if self.quarter == 1:
            return date(self.year, 4, 30)
        if self.quarter == 2:
            return date(self.year, 8, 31)
        if self.quarter == 3:
            return date(self.year, 10, 31)
        return date(self.year + 1, 4, 30)


def previous_period(period: ReportPeriod) -> ReportPeriod:
    if period.quarter == 1:
        return ReportPeriod(period.year - 1, 4)
    return ReportPeriod(period.year, period.quarter - 1)


def latest_completed_period(as_of: date) -> ReportPeriod:
    quarter = (as_of.month - 1) // 3 + 1
    candidate = ReportPeriod(as_of.year, quarter)
    return candidate if candidate.end_date <= as_of else previous_period(candidate)


def latest_probe_periods(as_of: date, limit: int = 5) -> tuple[ReportPeriod, ...]:
    if limit <= 0:
        return ()
    current = latest_completed_period(as_of)
    result: list[ReportPeriod] = []
    for _ in range(limit):
        result.append(current)
        current = previous_period(current)
    return tuple(result)


def annual_candidate_periods(as_of: date, desired: int = 3) -> tuple[ReportPeriod, ...]:
    """Return enough annual candidates to find three already-published years."""
    if desired <= 0:
        return ()
    latest = latest_completed_period(as_of)
    start_year = latest.year if latest.quarter == 4 else latest.year - 1
    latest_annual = ReportPeriod(start_year, 4)
    candidate_count = desired + int(as_of <= latest_annual.filing_deadline)
    return tuple(
        ReportPeriod(year, 4)
        for year in range(start_year, start_year - candidate_count, -1)
    )


def normalize_ticker(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    text = str(value).strip().upper()
    if not text or text in {"NAN", "NONE", "<NA>"}:
        return ""
    if text.startswith(("SH.", "SZ.", "BJ.")):
        exchange, number = text.split(".", 1)
        text = f"{number}.{exchange}"
    if "." in text:
        number, exchange = text.rsplit(".", 1)
        if exchange in {"SH", "SZ", "BJ"} and number.isdigit():
            return f"{number.zfill(6)}.{exchange}"
        return ""
    if not text.isdigit():
        return ""
    number = text.zfill(6)
    if number.startswith(("4", "8", "92")):
        exchange = "BJ"
    elif number.startswith(("5", "6")):
        exchange = "SH"
    else:
        exchange = "SZ"
    return f"{number}.{exchange}"


def baostock_code(ticker: str) -> str:
    normalized = normalize_ticker(ticker)
    if not normalized:
        return ""
    number, exchange = normalized.split(".", 1)
    return f"{exchange.lower()}.{number}"


def parse_report_period(value: Any) -> ReportPeriod | None:
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    timestamp = pd.Timestamp(parsed)
    quarter = {3: 1, 6: 2, 9: 3, 12: 4}.get(timestamp.month)
    if quarter is None or timestamp.day != _QUARTER_ENDS[quarter][1]:
        return None
    return ReportPeriod(timestamp.year, quarter)


def _date_text(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else pd.Timestamp(parsed).date().isoformat()


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


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return np.nan
    return result if np.isfinite(result) else np.nan


def empty_report_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=REPORT_COLUMNS)


def empty_summary_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=FUNDAMENTAL_COLUMNS)


def normalize_report_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return empty_report_frame()
    normalized = frame.copy()
    for column in REPORT_COLUMNS:
        if column not in normalized:
            normalized[column] = "" if column in _REPORT_TEXT_COLUMNS else np.nan
    normalized = normalized.loc[:, REPORT_COLUMNS]
    normalized["Ticker"] = normalized["Ticker"].map(normalize_ticker)
    normalized["Industry"] = normalized["Industry"].map(_text)
    normalized["ReportPeriod"] = normalized["ReportPeriod"].map(_date_text)
    normalized["AnnouncementDate"] = normalized["AnnouncementDate"].map(_date_text)
    normalized["Provider"] = normalized["Provider"].map(_text)
    normalized["FetchedAt"] = normalized["FetchedAt"].map(_text)
    for column in REPORT_COLUMNS:
        if column not in _REPORT_TEXT_COLUMNS:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    valid = normalized["Ticker"].ne("") & normalized["ReportPeriod"].ne("")
    normalized = normalized.loc[valid].copy()
    normalized = normalized.sort_values(
        ["Ticker", "ReportPeriod", "AnnouncementDate", "FetchedAt"],
        kind="stable",
    )
    return normalized.drop_duplicates(
        ["Ticker", "ReportPeriod", "AnnouncementDate", "Provider"],
        keep="last",
    ).reset_index(drop=True)


def normalize_summary_frame(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return empty_summary_frame()
    normalized = frame.copy()
    for column in FUNDAMENTAL_COLUMNS:
        if column not in normalized:
            normalized[column] = "" if column in _SUMMARY_TEXT_COLUMNS else np.nan
    normalized = normalized.loc[:, FUNDAMENTAL_COLUMNS]
    normalized["Ticker"] = normalized["Ticker"].map(normalize_ticker)
    for column in _SUMMARY_TEXT_COLUMNS - {"Ticker"}:
        normalized[column] = normalized[column].map(_text)
    for column in FUNDAMENTAL_COLUMNS:
        if column not in _SUMMARY_TEXT_COLUMNS:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    return normalized.loc[normalized["Ticker"].ne("")].drop_duplicates("Ticker", keep="last")


def merge_report_records(existing: pd.DataFrame, downloaded: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        return normalize_report_frame(downloaded)
    if downloaded.empty:
        return normalize_report_frame(existing)
    return normalize_report_frame(pd.concat([existing, downloaded], ignore_index=True))


def _record_value(row: pd.Series, column: str) -> float:
    return _number(row.get(column, np.nan))


def _existing_row(existing: pd.DataFrame, ticker: str) -> pd.Series | None:
    matches = existing.loc[existing["Ticker"].eq(ticker)]
    return None if matches.empty else matches.iloc[-1]


def _latest_announced_records(records: pd.DataFrame, as_of: date) -> pd.DataFrame:
    if records.empty:
        return records
    report_date = pd.to_datetime(records["ReportPeriod"], errors="coerce").dt.date
    announcement = pd.to_datetime(records["AnnouncementDate"], errors="coerce").dt.date
    known_on_date = announcement.notna() & announcement.le(as_of)
    usable = report_date.notna() & report_date.le(as_of) & known_on_date
    filtered = records.loc[usable].copy()
    if filtered.empty:
        return filtered
    return filtered.sort_values(
        ["ReportPeriod", "AnnouncementDate", "FetchedAt"], kind="stable"
    ).drop_duplicates(["ReportPeriod"], keep="last")


def _summary_from_records(
    ticker: str,
    records: pd.DataFrame,
    existing: pd.Series | None,
    industry: str,
    target: ReportPeriod,
    as_of: date,
) -> dict[str, Any] | None:
    usable = _latest_announced_records(records, as_of)
    if usable.empty:
        return None
    latest = usable.iloc[-1]
    latest_period = parse_report_period(latest["ReportPeriod"])
    annual = usable.loc[pd.to_numeric(usable["ReportQuarter"], errors="coerce").eq(4)].copy()
    annual = annual.sort_values(["ReportYear", "AnnouncementDate"], ascending=[False, False])
    annual = annual.drop_duplicates("ReportYear", keep="first")
    annual_profits = [
        _record_value(row, "NetProfit") for _, row in annual.head(3).iterrows()
    ]
    if not annual_profits and existing is not None:
        legacy = [_number(existing.get(f"NetProfitY{offset}")) for offset in range(1, 4)]
        if all(np.isfinite(value) for value in legacy):
            annual_profits = legacy
    annual_profits.extend([np.nan] * (3 - len(annual_profits)))

    roe = _record_value(latest, "ROE")
    net_profit = _record_value(latest, "NetProfit")
    report_complete = np.isfinite(roe) and np.isfinite(net_profit)
    if latest_period is None:
        status = "INVALID"
    elif not report_complete:
        status = "PARTIAL"
    elif latest_period == target:
        status = "CURRENT"
    elif as_of <= target.filing_deadline:
        status = "AWAITING_RELEASE"
    else:
        status = "STALE"

    resolved_industry = industry or _text(latest.get("Industry"))
    if not resolved_industry and existing is not None:
        resolved_industry = _text(existing.get("Industry"))
    return {
        "Ticker": ticker,
        "Industry": resolved_industry,
        "LatestReportPeriod": _text(latest.get("ReportPeriod")),
        "LatestAnnouncementDate": _text(latest.get("AnnouncementDate")),
        "LatestReportType": latest_period.report_type if latest_period is not None else "",
        "FundamentalProvider": _text(latest.get("Provider")),
        "FundamentalFetchedAt": _text(latest.get("FetchedAt")),
        "FundamentalDataStatus": status,
        "ROE": roe,
        "GrossMargin": _record_value(latest, "GrossMargin"),
        "NetProfitLatest": net_profit,
        "RevenueLatest": _record_value(latest, "Revenue"),
        "NetProfitYoY": _record_value(latest, "NetProfitYoY"),
        "DebtToAssets": _record_value(latest, "DebtToAssets"),
        "OperatingCashFlowToNetProfit": _record_value(latest, "OperatingCashFlowToNetProfit"),
        "NetProfitY1": annual_profits[0],
        "NetProfitY2": annual_profits[1],
        "NetProfitY3": annual_profits[2],
        "IndustryGrossMarginPercentile": np.nan,
        "InstitutionHoldingTrend": "",
        "InstitutionHoldingPeriods": np.nan,
    }


def _legacy_summary(existing: pd.Series, industry: str) -> dict[str, Any]:
    row = {column: existing.get(column, np.nan) for column in FUNDAMENTAL_COLUMNS}
    row["Ticker"] = normalize_ticker(existing.get("Ticker"))
    row["Industry"] = industry or _text(existing.get("Industry"))
    if not _text(row.get("FundamentalProvider")):
        row["FundamentalProvider"] = "legacy-cache"
    if not _text(row.get("FundamentalDataStatus")):
        row["FundamentalDataStatus"] = "LEGACY"
    return row


def _industry_margin_percentiles(frame: pd.DataFrame) -> pd.Series:
    margins = pd.to_numeric(frame["GrossMargin"], errors="coerce")
    industries = frame["Industry"].fillna("").astype(str).str.strip()
    global_rank = margins.rank(method="min", ascending=False)
    global_count = max(1, int(margins.notna().sum()) - 1)
    global_percentile = (global_rank - 1.0) / global_count
    peer_rank = margins.groupby(industries, dropna=False).rank(method="min", ascending=False)
    peer_count = margins.groupby(industries, dropna=False).transform("count")
    peer_percentile = (peer_rank - 1.0) / (peer_count - 1.0).clip(lower=1.0)
    return peer_percentile.where(
        industries.ne("") & peer_count.ge(3), global_percentile
    ).where(margins.notna())


def build_fundamental_summary(
    records: pd.DataFrame,
    existing_summary: pd.DataFrame,
    symbols: Sequence[str],
    industries: Mapping[str, str],
    *,
    as_of: date,
) -> pd.DataFrame:
    normalized_records = normalize_report_frame(records)
    existing = normalize_summary_frame(existing_summary)
    target = latest_completed_period(as_of)
    normalized_symbols = tuple(
        ticker for ticker in dict.fromkeys(normalize_ticker(value) for value in symbols) if ticker
    )
    rows: list[dict[str, Any]] = []
    for ticker in normalized_symbols:
        old = _existing_row(existing, ticker)
        ticker_records = normalized_records.loc[normalized_records["Ticker"].eq(ticker)]
        row = _summary_from_records(
            ticker,
            ticker_records,
            old,
            str(industries.get(ticker, "") or "").strip(),
            target,
            as_of,
        )
        if row is None and old is not None:
            row = _legacy_summary(old, str(industries.get(ticker, "") or "").strip())
        if row is not None:
            rows.append(row)
    summary = normalize_summary_frame(pd.DataFrame(rows))
    if not summary.empty:
        summary["IndustryGrossMarginPercentile"] = _industry_margin_percentiles(summary)
    return normalize_summary_frame(summary)


def summary_hard_financial_coverage(frame: pd.DataFrame, symbols: Sequence[str]) -> float:
    normalized = normalize_summary_frame(frame).set_index("Ticker")
    requested = [ticker for ticker in dict.fromkeys(normalize_ticker(value) for value in symbols) if ticker]
    if not requested:
        return 1.0
    aligned = normalized.reindex(requested)
    complete = pd.to_numeric(aligned["ROE"], errors="coerce").notna()
    for column in ("NetProfitY1", "NetProfitY2", "NetProfitY3"):
        complete &= pd.to_numeric(aligned[column], errors="coerce").notna()
    return float(complete.mean()) if len(complete) else 0.0


def summary_latest_period_coverage(
    frame: pd.DataFrame,
    symbols: Sequence[str],
    *,
    as_of: date,
) -> float:
    normalized = normalize_summary_frame(frame).set_index("Ticker")
    requested = [ticker for ticker in dict.fromkeys(normalize_ticker(value) for value in symbols) if ticker]
    if not requested:
        return 1.0
    aligned = normalized.reindex(requested)
    target = latest_completed_period(as_of).iso_date
    return float(aligned["LatestReportPeriod"].eq(target).mean())
