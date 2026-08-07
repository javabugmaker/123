from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import pandas as pd

import downloader
import fundamental_data


class _FakeAkShare:
    def __init__(self) -> None:
        self.holder_symbols: list[str] = []

    def stock_institute_hold(self, symbol: str) -> pd.DataFrame:
        self.holder_symbols.append(symbol)
        if len(self.holder_symbols) == 1:
            return pd.DataFrame(
                {
                    "证券代码": ["000001", "600000"],
                    "证券简称": ["平安银行", "浦发银行"],
                    "机构数": [120, 80],
                    "机构数变化": [5, -2],
                }
            )
        if len(self.holder_symbols) == 2:
            return pd.DataFrame(
                {
                    "证券代码": ["000001", "600000"],
                    "证券简称": ["平安银行", "浦发银行"],
                    "机构数": [115, 82],
                    "机构数变化": [3, -1],
                }
            )
        return pd.DataFrame()


class DataSourceRegressionTests(unittest.TestCase):
    def test_market_layer_has_only_tickflow_provider(self) -> None:
        self.assertEqual(downloader.normalize_data_source("tickflow"), "tickflow")
        self.assertEqual(downloader.get_data_source_label(), "TickFlow Free")
        self.assertFalse(hasattr(downloader, "_HTTP"))
        self.assertFalse(hasattr(downloader, "_download_from_eastmoney"))
        self.assertFalse(hasattr(downloader, "_download_from_sina"))
        self.assertFalse(hasattr(downloader, "_download_from_tencent"))
        self.assertFalse(hasattr(downloader, "_download_from_akshare"))

    def test_fundamental_akshare_preserves_active_proxy(self) -> None:
        proxy = "http://127.0.0.1:7897"
        with patch.dict(
            os.environ,
            {"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy},
            clear=False,
        ), patch.object(
            fundamental_data,
            "configure_akshare_proxy_from_system",
            return_value={"http": proxy, "https": proxy},
        ):
            before = (os.environ.get("HTTP_PROXY"), os.environ.get("HTTPS_PROXY"))
            with fundamental_data._direct_network_environment():
                inside = (os.environ.get("HTTP_PROXY"), os.environ.get("HTTPS_PROXY"))
            after = (os.environ.get("HTTP_PROXY"), os.environ.get("HTTPS_PROXY"))
        self.assertEqual(inside, before)
        self.assertEqual(after, before)

    def test_institutional_batch_passes_required_report_symbol(self) -> None:
        fake = _FakeAkShare()
        with patch.object(fundamental_data, "ak", fake), patch.object(
            fundamental_data,
            "_run_akshare_dataframe",
            side_effect=lambda _label, operation: operation(),
        ):
            result = fundamental_data._batch_fetch_institutional_data()

        self.assertEqual(len(fake.holder_symbols), 2)
        self.assertTrue(
            all(symbol.isdigit() and len(symbol) == 5 for symbol in fake.holder_symbols)
        )
        self.assertEqual(result["000001.SZ"]["OrgNumChange1"], 5.0)
        self.assertEqual(result["000001.SZ"]["OrgNumChange2"], 3.0)
        self.assertEqual(result["600000.SH"]["OrgNumChange1"], -2.0)
        self.assertEqual(result["600000.SH"]["OrgNumChange2"], -1.0)

    def test_two_consistent_holder_periods_drive_status_not_missing_data(self) -> None:
        old_finance = fundamental_data._batch_finance_cache
        old_holders = fundamental_data._batch_holders_cache
        try:
            fundamental_data._batch_finance_cache = {
                "000001.SZ": {
                    "Industry": "银行",
                    "ROE": 10.0,
                    "GrossMargin": 40.0,
                    "NetProfitY1": 100.0,
                    "NetProfitY2": 90.0,
                    "NetProfitY3": 80.0,
                },
                "600000.SH": {
                    "Industry": "银行",
                    "ROE": 9.0,
                    "GrossMargin": 39.0,
                    "NetProfitY1": 90.0,
                    "NetProfitY2": 95.0,
                    "NetProfitY3": 100.0,
                },
            }
            fundamental_data._batch_holders_cache = {
                "000001.SZ": {"OrgNumChange1": 5.0, "OrgNumChange2": 3.0},
                "600000.SH": {"OrgNumChange1": -2.0, "OrgNumChange2": -1.0},
            }
            rising = fundamental_data._fetch_ticker_from_batch("000001.SZ")
            falling = fundamental_data._fetch_ticker_from_batch("600000.SH")
        finally:
            fundamental_data._batch_finance_cache = old_finance
            fundamental_data._batch_holders_cache = old_holders

        self.assertIsNotNone(rising)
        self.assertIsNotNone(falling)
        assert rising is not None
        assert falling is not None
        self.assertEqual(rising["InstitutionHoldingTrend"], "increasing")
        self.assertEqual(rising["InstitutionHoldingPeriods"], 2.0)
        self.assertEqual(falling["InstitutionHoldingTrend"], "not_increasing")
        self.assertEqual(falling["InstitutionHoldingPeriods"], 2.0)

    def test_one_or_mixed_holder_period_is_neutral(self) -> None:
        old_finance = fundamental_data._batch_finance_cache
        old_holders = fundamental_data._batch_holders_cache
        try:
            fundamental_data._batch_finance_cache = {"000001.SZ": {"ROE": 10.0}}
            fundamental_data._batch_holders_cache = {
                "000001.SZ": {"OrgNumChange1": 5.0, "OrgNumChange2": -1.0}
            }
            row = fundamental_data._fetch_ticker_from_batch("000001.SZ")
        finally:
            fundamental_data._batch_finance_cache = old_finance
            fundamental_data._batch_holders_cache = old_holders

        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["InstitutionHoldingTrend"], "unknown")
        self.assertEqual(row["InstitutionHoldingPeriods"], 2.0)


if __name__ == "__main__":
    unittest.main()
