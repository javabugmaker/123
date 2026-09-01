from __future__ import annotations

from types import SimpleNamespace

from institution_scanner.baostock_fundamentals import (
    BaoStockFundamentalProvider,
    FundamentalFetchPlan,
)
from institution_scanner.fundamental_schema import ReportPeriod


class _Result:
    error_code = "0"
    error_msg = "success"

    def __init__(self, fields: list[str], rows: list[list[str]]) -> None:
        self.fields = fields
        self._rows = rows
        self._index = -1

    def next(self) -> bool:
        self._index += 1
        return self._index < len(self._rows)

    def get_row_data(self) -> list[str]:
        return self._rows[self._index]


class _BaoStockModule:
    __version__ = "0.9.3"

    def __init__(self) -> None:
        self.login_count = 0
        self.logout_count = 0
        self.codes: list[str] = []
        self.enrichment_calls = 0

    def login(self) -> SimpleNamespace:
        self.login_count += 1
        return SimpleNamespace(error_code="0", error_msg="success")

    def logout(self) -> SimpleNamespace:
        self.logout_count += 1
        return SimpleNamespace(error_code="0", error_msg="success")

    def query_profit_data(self, *, code: str, year: int, quarter: int) -> _Result:
        self.codes.append(code)
        fields = [
            "code",
            "pubDate",
            "statDate",
            "roeAvg",
            "npMargin",
            "gpMargin",
            "netProfit",
            "epsTTM",
            "MBRevenue",
        ]
        if (year, quarter) != (2026, 3):
            return _Result(fields, [])
        return _Result(
            fields,
            [[code, "2026-10-30", "2026-09-30", "12.5", "9.1", "35.2", "380", "1.2", "1200"]],
        )

    def query_growth_data(self, *, code: str, year: int, quarter: int) -> _Result:
        del code, year, quarter
        self.enrichment_calls += 1
        return _Result(
            ["pubDate", "statDate", "YOYNI", "YOYEquity", "YOYAsset"],
            [["2026-10-30", "2026-09-30", "18.2", "7.1", "8.3"]],
        )

    def query_balance_data(self, *, code: str, year: int, quarter: int) -> _Result:
        del code, year, quarter
        self.enrichment_calls += 1
        return _Result(
            ["pubDate", "statDate", "liabilityToAsset", "currentRatio", "quickRatio"],
            [["2026-10-30", "2026-09-30", "42.0", "1.8", "1.3"]],
        )

    def query_cash_flow_data(self, *, code: str, year: int, quarter: int) -> _Result:
        del code, year, quarter
        self.enrichment_calls += 1
        return _Result(
            ["pubDate", "statDate", "CFOToOR", "CFOToNP"],
            [["2026-10-30", "2026-09-30", "0.21", "1.14"]],
        )


def test_provider_maps_latest_quarter_and_optional_financial_tables() -> None:
    module = _BaoStockModule()
    provider = BaoStockFundamentalProvider(module=module, timeout_seconds=2)
    plan = FundamentalFetchPlan(
        ticker="600000.SH",
        latest_periods=(ReportPeriod(2026, 3),),
        annual_periods=(),
    )

    outcomes = list(provider.fetch([plan]))

    assert module.login_count == 1
    assert module.logout_count == 1
    assert module.codes == ["sh.600000"]
    assert len(outcomes) == 1
    assert outcomes[0].error == ""
    row = outcomes[0].records.iloc[0]
    assert row["ReportPeriod"] == "2026-09-30"
    assert row["AnnouncementDate"] == "2026-10-30"
    assert row["ROE"] == 12.5
    assert row["NetProfitYoY"] == 18.2
    assert row["DebtToAssets"] == 42.0
    assert row["OperatingCashFlowToNetProfit"] == 1.14
    assert module.enrichment_calls == 3


def test_annual_backfill_skips_latest_quarter_enrichment_queries() -> None:
    module = _BaoStockModule()
    provider = BaoStockFundamentalProvider(module=module, timeout_seconds=2)
    plan = FundamentalFetchPlan(
        ticker="600000.SH",
        latest_periods=(),
        annual_periods=(ReportPeriod(2026, 3),),
        enrich_latest=False,
    )

    outcomes = list(provider.fetch([plan]))

    assert len(outcomes) == 1
    assert not outcomes[0].records.empty
    assert module.enrichment_calls == 0
