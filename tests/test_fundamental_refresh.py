from __future__ import annotations

import json
import threading
from collections.abc import Iterator, Sequence
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from institution_scanner import fundamentals
from institution_scanner.akshare_fundamentals import (
    AkShareUnavailable,
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
                "Provider": "akshare",
                "FetchedAt": "2026-09-01T01:00:00+00:00",
            }
        )
    return normalize_report_frame(pd.DataFrame(rows))


class _Provider:
    provider_name = "akshare"
    provider_version = "test-1.18.94"

    def __init__(self) -> None:
        self.calls = 0

    def fetch(
        self,
        plans: Sequence[FundamentalFetchPlan],
    ) -> Iterator[FundamentalFetchOutcome]:
        self.calls += 1
        for plan in plans:
            records = _records(plan.ticker)
            if plan.latest_periods:
                period = plan.latest_periods[0].iso_date
                records = records.loc[records["ReportPeriod"].eq(period)].copy()
            elif plan.annual_periods:
                periods = {period.iso_date for period in plan.annual_periods}
                records = records.loc[records["ReportPeriod"].isin(periods)].copy()
            yield FundamentalFetchOutcome(
                ticker=plan.ticker,
                records=normalize_report_frame(records),
                checked=True,
            )


class _UnavailableProvider(_Provider):
    def fetch(
        self,
        plans: Sequence[FundamentalFetchPlan],
    ) -> Iterator[FundamentalFetchOutcome]:
        del plans
        self.calls += 1
        raise AkShareUnavailable("offline")


class _InterruptedProvider(_Provider):
    def __init__(self, successful_outcomes: int) -> None:
        super().__init__()
        self.successful_outcomes = successful_outcomes

    def fetch(
        self,
        plans: Sequence[FundamentalFetchPlan],
    ) -> Iterator[FundamentalFetchOutcome]:
        self.calls += 1
        for index, plan in enumerate(plans):
            if index >= self.successful_outcomes:
                raise AkShareUnavailable("interrupted")
            period = plan.latest_periods[0].iso_date
            records = _records(plan.ticker)
            records = records.loc[records["ReportPeriod"].eq(period)].copy()
            yield FundamentalFetchOutcome(
                ticker=plan.ticker,
                records=normalize_report_frame(records),
                checked=True,
            )


class _CancellingProvider(_Provider):
    def __init__(self, cancel_event: threading.Event) -> None:
        super().__init__()
        self.cancel_event = cancel_event

    def fetch(
        self,
        plans: Sequence[FundamentalFetchPlan],
    ) -> Iterator[FundamentalFetchOutcome]:
        self.calls += 1
        for index, plan in enumerate(plans):
            if index == 1:
                self.cancel_event.set()
            period = plan.latest_periods[0].iso_date
            records = _records(plan.ticker)
            records = records.loc[records["ReportPeriod"].eq(period)].copy()
            yield FundamentalFetchOutcome(
                ticker=plan.ticker,
                records=normalize_report_frame(records),
                checked=True,
            )


class _RecordingProvider(_Provider):
    def __init__(self) -> None:
        super().__init__()
        self.batches: list[tuple[str, ...]] = []

    def fetch(
        self,
        plans: Sequence[FundamentalFetchPlan],
    ) -> Iterator[FundamentalFetchOutcome]:
        self.batches.append(tuple(plan.ticker for plan in plans))
        yield from super().fetch(plans)


class _ParallelRecordingProvider(_Provider):
    def __init__(self) -> None:
        super().__init__()
        self.parallel_calls: list[tuple[int, int, int | None]] = []

    def fetch_parallel(
        self,
        plans: Sequence[FundamentalFetchPlan],
        *,
        workers: int,
        max_in_flight: int | None = None,
    ) -> Iterator[FundamentalFetchOutcome]:
        self.parallel_calls.append((len(plans), workers, max_in_flight))
        yield from self.fetch(plans)


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

    assert provider.calls == 2
    assert set(first["Ticker"]) == {"600000.SH", "000001.SZ"}
    assert set(first["LatestReportPeriod"]) == {"2026-06-30"}
    assert set(first["FundamentalProvider"]) == {"akshare"}
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
    assert provider.calls == 2

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


def test_force_refresh_resumes_from_fsynced_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fundamentals, "_CACHE_PATH", tmp_path / "fundamental_data.csv")
    monkeypatch.setattr(
        fundamentals,
        "_REPORT_CACHE_PATH",
        tmp_path / "fundamental_reports.csv",
    )
    monkeypatch.setattr(
        fundamentals,
        "_META_PATH",
        tmp_path / "fundamental_data_meta.json",
    )
    monkeypatch.setattr(fundamentals, "FUNDAMENTAL_DATA_PATH", "")
    monkeypatch.setattr(fundamentals, "_CHECKPOINT_EVERY", 2)
    symbols = ["600000.SH", "000001.SZ", "000002.SZ"]

    interrupted = _InterruptedProvider(successful_outcomes=2)
    fundamentals.refresh_fundamental_data(
        symbols,
        force=True,
        provider=interrupted,
        as_of=date(2026, 9, 1),
    )

    journal = tmp_path / "fundamental_refresh_journal.jsonl"
    assert journal.is_file()
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 3

    resumed = _RecordingProvider()
    fundamentals.refresh_fundamental_data(
        symbols,
        force=True,
        provider=resumed,
        as_of=date(2026, 9, 1),
    )

    assert resumed.batches[0] == ("000002.SZ",)
    assert set(resumed.batches[1]) == set(symbols)
    assert not journal.exists()
    metadata = json.loads((tmp_path / "fundamental_data_meta.json").read_text())
    assert metadata["symbols_completed"] == 3
    assert metadata["symbols_remaining"] == 0
    assert metadata["checkpoint_every"] == 2


def test_refresh_keeps_provider_calls_serial_even_if_workers_are_requested(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fundamentals, "_CACHE_PATH", tmp_path / "fundamental_data.csv")
    monkeypatch.setattr(
        fundamentals,
        "_REPORT_CACHE_PATH",
        tmp_path / "fundamental_reports.csv",
    )
    monkeypatch.setattr(
        fundamentals,
        "_META_PATH",
        tmp_path / "fundamental_data_meta.json",
    )
    monkeypatch.setattr(fundamentals, "FUNDAMENTAL_DATA_PATH", "")
    provider = _ParallelRecordingProvider()

    fundamentals.refresh_fundamental_data(
        ["600000.SH", "000001.SZ", "000002.SZ"],
        force=True,
        workers=6,
        provider=provider,
        as_of=date(2026, 9, 1),
    )

    assert provider.parallel_calls == []
    assert provider.calls == 2


def test_cancel_flushes_short_batch_and_resumes_remaining_plans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fundamentals, "_CACHE_PATH", tmp_path / "fundamental_data.csv")
    monkeypatch.setattr(
        fundamentals,
        "_REPORT_CACHE_PATH",
        tmp_path / "fundamental_reports.csv",
    )
    monkeypatch.setattr(
        fundamentals,
        "_META_PATH",
        tmp_path / "fundamental_data_meta.json",
    )
    monkeypatch.setattr(fundamentals, "FUNDAMENTAL_DATA_PATH", "")
    monkeypatch.setattr(fundamentals, "_CHECKPOINT_EVERY", 5)
    symbols = ["600000.SH", "000001.SZ", "000002.SZ"]
    cancel_event = threading.Event()

    with pytest.raises(fundamentals.FundamentalRefreshCancelled):
        fundamentals.refresh_fundamental_data(
            symbols,
            force=True,
            provider=_CancellingProvider(cancel_event),
            as_of=date(2026, 9, 1),
            cancel_event=cancel_event,
        )

    journal = tmp_path / "fundamental_refresh_journal.jsonl"
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 3

    resumed = _RecordingProvider()
    fundamentals.refresh_fundamental_data(
        symbols,
        force=True,
        provider=resumed,
        as_of=date(2026, 9, 1),
    )

    assert resumed.batches[0] == ("000002.SZ",)
    assert set(resumed.batches[1]) == set(symbols)
    assert not journal.exists()
