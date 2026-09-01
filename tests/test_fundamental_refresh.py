from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from institution_scanner import fundamentals
from institution_scanner.baostock_fundamentals import (
    BaoStockUnavailable,
    FundamentalFetchOutcome,
    FundamentalFetchPlan,
)
from institution_scanner.fundamental_schema import normalize_report_frame


def _records(ticker: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for period, announcement, quarter, profit in (
        ("2026-06-30", "2026-08-28", 2, 260.0),
        ("2025-12-31", "2026-03-28", 4, 300.0),
        ("2024-12-31", "2025-03-28", 4, 230.0),
        ("2023-12-31", "2024-03-28", 4, 190.0),
    ):
        rows.append(
            {
                "Ticker": ticker,
                "ReportPeriod": period,
                "AnnouncementDate": announcement,
                "ReportYear": int(period[:4]),
                "ReportQuarter": quarter,
                "ROE": 13.0,
                "GrossMargin": 36.0,
                "NetProfit": profit,
                "Revenue": profit * 4,
                "Provider": "baostock",
                "FetchedAt": "2026-09-01T01:00:00+00:00",
            }
        )
    return normalize_report_frame(pd.DataFrame(rows))


class _Provider:
    provider_name = "baostock"
    provider_version = "test-0.9.3"

    def __init__(self) -> None:
        self.calls = 0

    def fetch(
        self,
        plans: Sequence[FundamentalFetchPlan],
    ) -> Iterator[FundamentalFetchOutcome]:
        self.calls += 1
        yield from (
            FundamentalFetchOutcome(
                ticker=plan.ticker,
                records=_records(plan.ticker),
                checked=True,
            )
            for plan in plans
        )


class _UnavailableProvider(_Provider):
    def fetch(
        self,
        plans: Sequence[FundamentalFetchPlan],
    ) -> Iterator[FundamentalFetchOutcome]:
        del plans
        self.calls += 1
        raise BaoStockUnavailable("offline")


def test_refresh_is_incremental_cached_and_preserves_last_good_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache_dir = tmp_path
    monkeypatch.setattr(fundamentals, "_CACHE_PATH", cache_dir / "fundamental_data.csv")
    monkeypatch.setattr(
        fundamentals,
        "_REPORT_CACHE_PATH",
        cache_dir / "fundamental_reports.csv",
    )
    monkeypatch.setattr(
        fundamentals,
        "_META_PATH",
        cache_dir / "fundamental_data_meta.json",
    )
    monkeypatch.setattr(fundamentals, "FUNDAMENTAL_DATA_PATH", "")

    provider = _Provider()
    path = fundamentals.refresh_fundamental_data(
        ["600000.SH", "000001.SZ", "510300.SH"],
        provider=provider,
        industry_by_ticker={"600000.SH": "银行", "000001.SZ": "银行"},
        as_of=date(2026, 9, 1),
    )
    first = pd.read_csv(path, dtype={"Ticker": str})
    first_bytes = path.read_bytes()

    assert provider.calls == 1
    assert set(first["Ticker"]) == {"600000.SH", "000001.SZ"}
    assert set(first["LatestReportPeriod"]) == {"2026-06-30"}
    assert set(first["FundamentalProvider"]) == {"baostock"}
    assert (cache_dir / "fundamental_reports.csv").is_file()
    metadata = json.loads((cache_dir / "fundamental_data_meta.json").read_text())
    assert metadata["provider_check_status"] == "SUCCESS"
    assert metadata["symbols_requested"] == 2

    fundamentals.refresh_fundamental_data(
        ["600000.SH", "000001.SZ", "510300.SH"],
        provider=provider,
        industry_by_ticker={"600000.SH": "银行", "000001.SZ": "银行"},
        as_of=date(2026, 9, 1),
    )
    assert provider.calls == 1

    unavailable = _UnavailableProvider()
    fundamentals.refresh_fundamental_data(
        ["600000.SH", "000001.SZ"],
        provider=unavailable,
        industry_by_ticker={"600000.SH": "银行", "000001.SZ": "银行"},
        as_of=date(2026, 10, 1),
    )
    preserved = pd.read_csv(path, dtype={"Ticker": str})
    failed_metadata = json.loads(
        (cache_dir / "fundamental_data_meta.json").read_text()
    )

    assert unavailable.calls == 1
    assert set(preserved["Ticker"]) == set(first["Ticker"])
    assert path.read_bytes() != b""
    assert first_bytes != b""
    assert failed_metadata["provider_check_status"] == "PARTIAL"
    assert failed_metadata["last_error"] == "offline"

    fundamentals.refresh_fundamental_data(
        ["600000.SH", "000001.SZ"],
        provider=unavailable,
        industry_by_ticker={"600000.SH": "银行", "000001.SZ": "银行"},
        as_of=date(2026, 10, 1),
    )
    assert unavailable.calls == 1
