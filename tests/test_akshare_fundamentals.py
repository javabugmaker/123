from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from institution_scanner.akshare_fundamentals import (
    AkShareFundamentalProvider,
    FundamentalFetchPlan,
)
from institution_scanner.fundamental_schema import ReportPeriod


class _AkShareModule:
    __version__ = "1.18.94"

    def __init__(self) -> None:
        self.dates: list[str] = []

    def stock_yjbb_em(self, *, date: str) -> pd.DataFrame:
        self.dates.append(date)
        values: dict[str, Sequence[object]] = {
            "股票代码": ["600000", "000001"],
            "所处行业": ["银行", "银行"],
            "最新公告日期": ["2026-10-30", "2026-10-29"],
            "净资产收益率": [12.5, 11.0],
            "销售毛利率": [35.2, 33.0],
            "净利润-净利润": [380.0, 260.0],
            "净利润-同比增长": [18.2, 9.0],
            "营业总收入-营业总收入": [1200.0, 900.0],
            "每股收益": [1.2, 1.0],
            "每股经营现金流量": [1.44, 0.8],
        }
        return pd.DataFrame(values)


def test_provider_fetches_each_report_period_once_for_the_whole_batch() -> None:
    module = _AkShareModule()
    provider = AkShareFundamentalProvider(module=module, timeout_seconds=15)
    period = ReportPeriod(2026, 3)
    plans = [
        FundamentalFetchPlan(
            ticker=ticker,
            latest_periods=(period,),
            annual_periods=(),
        )
        for ticker in ("600000.SH", "000001.SZ")
    ]

    outcomes = list(provider.fetch(plans))

    assert module.dates == ["20260930"]
    assert len(outcomes) == 2
    first = outcomes[0].records.iloc[0]
    assert first["Ticker"] == "600000.SH"
    assert first["ReportPeriod"] == "2026-09-30"
    assert first["AnnouncementDate"] == "2026-10-30"
    assert first["ROE"] == 12.5
    assert first["NetProfitYoY"] == 18.2
    assert first["OperatingCashFlowToNetProfit"] == 1.2
    assert first["Provider"] == "akshare"


def test_provider_reuses_period_cache_between_latest_and_annual_phases() -> None:
    module = _AkShareModule()
    provider = AkShareFundamentalProvider(module=module, timeout_seconds=15)
    period = ReportPeriod(2025, 4)
    latest = FundamentalFetchPlan(
        ticker="600000.SH",
        latest_periods=(period,),
        annual_periods=(),
    )
    annual = FundamentalFetchPlan(
        ticker="600000.SH",
        latest_periods=(),
        annual_periods=(period,),
        enrich_latest=False,
    )

    assert not list(provider.fetch([latest]))[0].records.empty
    assert not list(provider.fetch([annual]))[0].records.empty
    assert module.dates == ["20251231"]


def test_provider_preserves_missing_optional_fields_as_unknown() -> None:
    provider = AkShareFundamentalProvider(module=_AkShareModule(), timeout_seconds=15)
    plan = FundamentalFetchPlan(
        ticker="600000.SH",
        latest_periods=(ReportPeriod(2026, 3),),
        annual_periods=(),
    )

    row = list(provider.fetch([plan]))[0].records.iloc[0]

    assert pd.isna(row["DebtToAssets"])
    assert pd.isna(row["CurrentRatio"])
    assert pd.isna(row["QuickRatio"])
