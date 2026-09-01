from __future__ import annotations

from datetime import date

import pandas as pd

from institution_scanner.fundamental_schema import (
    ReportPeriod,
    annual_candidate_periods,
    build_fundamental_summary,
    empty_summary_frame,
    latest_completed_period,
)


def _record(
    period: str,
    announcement: str,
    *,
    quarter: int,
    profit: float,
) -> dict[str, object]:
    return {
        "Ticker": "600000.SH",
        "Industry": "银行",
        "ReportPeriod": period,
        "AnnouncementDate": announcement,
        "ReportYear": int(period[:4]),
        "ReportQuarter": quarter,
        "ROE": 12.0,
        "GrossMargin": 42.0,
        "NetProfit": profit,
        "Revenue": profit * 4,
        "Provider": "akshare",
        "FetchedAt": "2026-10-30T08:00:00+00:00",
    }


def test_latest_completed_period_does_not_treat_september_as_finished_early() -> None:
    assert latest_completed_period(date(2026, 9, 1)) == ReportPeriod(2026, 2)
    assert latest_completed_period(date(2026, 9, 30)) == ReportPeriod(2026, 3)
    assert annual_candidate_periods(date(2026, 9, 1)) == (
        ReportPeriod(2025, 4),
        ReportPeriod(2024, 4),
        ReportPeriod(2023, 4),
    )
    assert len(annual_candidate_periods(date(2026, 4, 1))) == 4


def test_summary_uses_only_reports_announced_by_the_requested_date() -> None:
    records = pd.DataFrame(
        [
            _record("2026-06-30", "2026-08-28", quarter=2, profit=250.0),
            _record("2026-09-30", "2026-10-30", quarter=3, profit=390.0),
            _record("2025-12-31", "2026-03-28", quarter=4, profit=300.0),
            _record("2024-12-31", "2025-03-28", quarter=4, profit=220.0),
            _record("2023-12-31", "2024-03-28", quarter=4, profit=180.0),
            _record("2022-12-31", "", quarter=4, profit=9999.0),
        ]
    )

    before_release = build_fundamental_summary(
        records,
        empty_summary_frame(),
        ["600000.SH"],
        {"600000.SH": "银行"},
        as_of=date(2026, 10, 29),
    ).iloc[0]
    on_release = build_fundamental_summary(
        records,
        empty_summary_frame(),
        ["600000.SH"],
        {"600000.SH": "银行"},
        as_of=date(2026, 10, 30),
    ).iloc[0]

    assert before_release["LatestReportPeriod"] == "2026-06-30"
    assert before_release["NetProfitLatest"] == 250.0
    assert before_release["FundamentalDataStatus"] == "AWAITING_RELEASE"
    assert on_release["LatestReportPeriod"] == "2026-09-30"
    assert on_release["LatestAnnouncementDate"] == "2026-10-30"
    assert on_release["LatestReportType"] == "三季报"
    assert on_release["NetProfitLatest"] == 390.0
    assert on_release["NetProfitY1"] == 300.0
    assert on_release["NetProfitY2"] == 220.0
    assert on_release["NetProfitY3"] == 180.0
    assert on_release["FundamentalDataStatus"] == "CURRENT"


def test_partial_new_annual_history_is_not_replaced_by_legacy_values() -> None:
    records = pd.DataFrame(
        [
            _record("2026-09-30", "2026-10-30", quarter=3, profit=390.0),
            _record("2025-12-31", "2026-03-28", quarter=4, profit=300.0),
        ]
    )
    legacy = pd.DataFrame(
        [
            {
                "Ticker": "600000.SH",
                "Industry": "银行",
                "ROE": 10.0,
                "GrossMargin": 30.0,
                "NetProfitY1": 220.0,
                "NetProfitY2": 180.0,
                "NetProfitY3": 150.0,
                "IndustryGrossMarginPercentile": 0.2,
            }
        ]
    )

    row = build_fundamental_summary(
        records,
        legacy,
        ["600000.SH"],
        {"600000.SH": "银行"},
        as_of=date(2026, 10, 30),
    ).iloc[0]

    assert row["NetProfitY1"] == 300.0
    assert pd.isna(row["NetProfitY2"])
    assert pd.isna(row["NetProfitY3"])


def test_old_baostock_report_records_are_explicitly_legacy_after_migration() -> None:
    records = pd.DataFrame(
        [_record("2026-06-30", "2026-08-28", quarter=2, profit=250.0)]
    )
    records["Provider"] = "baostock"

    row = build_fundamental_summary(
        records,
        empty_summary_frame(),
        ["600000.SH"],
        {"600000.SH": "银行"},
        as_of=date(2026, 9, 1),
    ).iloc[0]

    assert row["FundamentalProvider"] == "baostock"
    assert row["FundamentalDataStatus"] == "LEGACY"


def test_old_summary_cannot_keep_current_status_without_akshare_records() -> None:
    legacy = pd.DataFrame(
        [
            {
                "Ticker": "600000.SH",
                "Industry": "银行",
                "FundamentalProvider": "baostock",
                "FundamentalDataStatus": "CURRENT",
                "ROE": 12.0,
                "GrossMargin": 42.0,
                "NetProfitY1": 300.0,
                "NetProfitY2": 220.0,
                "NetProfitY3": 180.0,
                "IndustryGrossMarginPercentile": 0.1,
            }
        ]
    )

    row = build_fundamental_summary(
        pd.DataFrame(),
        legacy,
        ["600000.SH"],
        {"600000.SH": "银行"},
        as_of=date(2026, 9, 1),
    ).iloc[0]

    assert row["FundamentalDataStatus"] == "LEGACY"
