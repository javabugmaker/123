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


def _date_series(values: pd.Series) -> pd.Series:
    """Normalize a complete date column in one compiled pandas operation."""
    parsed = pd.to_datetime(values, errors="coerce", format="mixed")
    return parsed.dt.strftime("%Y-%m-%d").fillna("")


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
    normalized["ReportPeriod"] = _date_series(normalized["ReportPeriod"])
    normalized["AnnouncementDate"] = _date_series(normalized["AnnouncementDate"])
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
    if not normalized_symbols:
        return empty_summary_frame()

    requested = pd.Index(normalized_symbols, dtype="object")
    industry_map = {
        ticker: _text(value)
        for raw_ticker, value in industries.items()
        if (ticker := normalize_ticker(raw_ticker)) and _text(value)
    }
    old_by_ticker = existing.set_index("Ticker", drop=False)

    usable = normalized_records.loc[
        normalized_records["Ticker"].isin(requested)
    ].copy()
    if not usable.empty:
        usable["_ReportDate"] = pd.to_datetime(
            usable["ReportPeriod"], errors="coerce"
        )
        usable["_AnnouncementDate"] = pd.to_datetime(
            usable["AnnouncementDate"], errors="coerce"
        )
        usable = usable.loc[
            usable["_ReportDate"].notna()
            & usable["_ReportDate"].le(pd.Timestamp(as_of))
            & usable["_AnnouncementDate"].notna()
            & usable["_AnnouncementDate"].le(pd.Timestamp(as_of))
        ].copy()
        usable = usable.sort_values(
            [
                "Ticker",
                "_ReportDate",
                "_AnnouncementDate",
                "FetchedAt",
            ],
            kind="stable",
        ).drop_duplicates(["Ticker", "ReportPeriod"], keep="last")

    if usable.empty:
        latest = pd.DataFrame()
    else:
        latest = usable.drop_duplicates("Ticker", keep="last").set_index(
            "Ticker", drop=False
        )

    if latest.empty:
        summary = empty_summary_frame()
    else:
        new_summary = pd.DataFrame(index=latest.index)
        ticker_index = latest.index.to_series()
        configured_industry = ticker_index.map(industry_map).fillna("")
        report_industry = latest["Industry"].map(_text)
        if old_by_ticker.empty:
            old_industry = pd.Series("", index=latest.index, dtype=object)
        else:
            old_industry = (
                old_by_ticker["Industry"].reindex(latest.index).map(_text).fillna("")
            )
        resolved_industry = configured_industry.where(
            configured_industry.ne(""), report_industry
        )
        resolved_industry = resolved_industry.where(
            resolved_industry.ne(""), old_industry
        )

        latest_period = pd.to_datetime(latest["ReportPeriod"], errors="coerce")
        expected_day = latest_period.dt.month.map({3: 31, 6: 30, 9: 30, 12: 31})
        valid_period = expected_day.notna() & latest_period.dt.day.eq(expected_day)
        latest_quarter = latest_period.dt.month.map({3: 1, 6: 2, 9: 3, 12: 4})
        latest_type = latest_quarter.map(_REPORT_TYPE_NAMES).fillna("")
        latest_type = latest_type.where(valid_period, "")

        def numeric(column: str) -> pd.Series:
            return pd.to_numeric(latest[column], errors="coerce").replace(
                [np.inf, -np.inf], np.nan
            )

        roe = numeric("ROE")
        net_profit = numeric("NetProfit")
        report_complete = roe.notna() & net_profit.notna()
        status_default = (
            "AWAITING_RELEASE" if as_of <= target.filing_deadline else "STALE"
        )
        status = pd.Series(status_default, index=latest.index, dtype=object)
        status.loc[latest_period.eq(pd.Timestamp(target.end_date))] = "CURRENT"
        status.loc[~report_complete] = "PARTIAL"
        status.loc[~valid_period] = "INVALID"

        new_summary["Ticker"] = ticker_index
        new_summary["Industry"] = resolved_industry
        new_summary["LatestReportPeriod"] = latest["ReportPeriod"]
        new_summary["LatestAnnouncementDate"] = latest["AnnouncementDate"]
        new_summary["LatestReportType"] = latest_type
        new_summary["FundamentalProvider"] = latest["Provider"]
        new_summary["FundamentalFetchedAt"] = latest["FetchedAt"]
        new_summary["FundamentalDataStatus"] = status
        new_summary["ROE"] = roe
        new_summary["GrossMargin"] = numeric("GrossMargin")
        new_summary["NetProfitLatest"] = net_profit
        new_summary["RevenueLatest"] = numeric("Revenue")
        new_summary["NetProfitYoY"] = numeric("NetProfitYoY")
        new_summary["DebtToAssets"] = numeric("DebtToAssets")
        new_summary["OperatingCashFlowToNetProfit"] = numeric(
            "OperatingCashFlowToNetProfit"
        )

        annual = usable.loc[
            pd.to_numeric(usable["ReportQuarter"], errors="coerce").eq(4)
        ].copy()
        if annual.empty:
            annual_count = pd.Series(0, index=latest.index, dtype=np.int64)
            annual_profit = pd.DataFrame(index=latest.index, columns=range(3))
        else:
            annual["_ReportYear"] = pd.to_numeric(
                annual["ReportYear"], errors="coerce"
            )
            annual = annual.sort_values(
                ["Ticker", "_ReportYear", "_AnnouncementDate"],
                ascending=[True, False, False],
                kind="stable",
            ).drop_duplicates(["Ticker", "_ReportYear"], keep="first")
            annual_count = (
                annual.groupby("Ticker", sort=False).size().reindex(latest.index, fill_value=0)
            )
            annual["_AnnualRank"] = annual.groupby("Ticker", sort=False).cumcount()
            annual_top = annual.loc[annual["_AnnualRank"].lt(3)].copy()
            annual_top["NetProfit"] = pd.to_numeric(
                annual_top["NetProfit"], errors="coerce"
            ).replace([np.inf, -np.inf], np.nan)
            annual_profit = annual_top.pivot(
                index="Ticker",
                columns="_AnnualRank",
                values="NetProfit",
            ).reindex(index=latest.index, columns=range(3))

        if old_by_ticker.empty:
            old_annual = pd.DataFrame(
                np.nan,
                index=latest.index,
                columns=("NetProfitY1", "NetProfitY2", "NetProfitY3"),
            )
        else:
            old_annual = old_by_ticker.reindex(latest.index).loc[
                :, ("NetProfitY1", "NetProfitY2", "NetProfitY3")
            ]
            old_annual = old_annual.apply(pd.to_numeric, errors="coerce").replace(
                [np.inf, -np.inf], np.nan
            )
        legacy_fallback = annual_count.eq(0) & old_annual.notna().all(axis=1)
        for position, column in enumerate(
            ("NetProfitY1", "NetProfitY2", "NetProfitY3")
        ):
            values = pd.to_numeric(
                annual_profit[position], errors="coerce"
            ).replace([np.inf, -np.inf], np.nan)
            new_summary[column] = values.where(
                ~legacy_fallback, old_annual[column]
            )

        new_summary["IndustryGrossMarginPercentile"] = np.nan
        new_summary["InstitutionHoldingTrend"] = ""
        new_summary["InstitutionHoldingPeriods"] = np.nan
        summary = normalize_summary_frame(new_summary.reset_index(drop=True))

    latest_tickers = set(latest.index) if not latest.empty else set()
    if existing.empty:
        legacy = empty_summary_frame()
    else:
        legacy = existing.loc[
            existing["Ticker"].isin(requested)
            & ~existing["Ticker"].isin(latest_tickers)
        ].copy()
        if not legacy.empty:
            configured = legacy["Ticker"].map(industry_map).fillna("")
            legacy["Industry"] = configured.where(
                configured.ne(""), legacy["Industry"]
            )
            missing_provider = legacy["FundamentalProvider"].map(_text).eq("")
            legacy.loc[missing_provider, "FundamentalProvider"] = "legacy-cache"
            missing_status = legacy["FundamentalDataStatus"].map(_text).eq("")
            legacy.loc[missing_status, "FundamentalDataStatus"] = "LEGACY"
    if summary.empty:
        summary = normalize_summary_frame(legacy)
    elif not legacy.empty:
        summary = normalize_summary_frame(
            pd.concat([summary, legacy], ignore_index=True, sort=False)
        )
    order = {ticker: position for position, ticker in enumerate(normalized_symbols)}
    if not summary.empty:
        summary["_InputOrder"] = summary["Ticker"].map(order)
        summary = summary.sort_values("_InputOrder", kind="stable").drop(
            columns="_InputOrder"
        )
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
