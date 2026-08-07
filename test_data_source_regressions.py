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
    def test_downloader_session_ignores_environment_proxy(self) -> None:
        self.assertFalse(downloader._HTTP.trust_env)

    def test_fundamental_akshare_calls_temporarily_bypass_proxy(self) -> None:
        original_http = os.environ.get("HTTP_PROXY")
        original_https = os.environ.get("HTTPS_PROXY")
        try:
            os.environ["HTTP_PROXY"] = "http://127.0.0.1:7897"
            os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7897"
            with fundamental_data._direct_network_environment():
                self.assertNotIn("HTTP_PROXY", os.environ)
                self.assertNotIn("HTTPS_PROXY", os.environ)
                self.assertEqual(os.environ.get("NO_PROXY"), "*")
                self.assertEqual(os.environ.get("no_proxy"), "*")
            self.assertEqual(
                os.environ.get("HTTP_PROXY"), "http://127.0.0.1:7897"
            )
            self.assertEqual(
                os.environ.get("HTTPS_PROXY"), "http://127.0.0.1:7897"
            )
        finally:
            if original_http is None:
                os.environ.pop("HTTP_PROXY", None)
            else:
                os.environ["HTTP_PROXY"] = original_http
            if original_https is None:
                os.environ.pop("HTTPS_PROXY", None)
            else:
                os.environ["HTTPS_PROXY"] = original_https

    def test_institutional_batch_passes_required_report_symbol(self) -> None:
        fake = _FakeAkShare()
        with patch.object(fundamental_data, "ak", fake), patch.object(
            fundamental_data,
            "_run_akshare_dataframe",
            side_effect=lambda _label, operation: operation(),
        ):
            result = fundamental_data._batch_fetch_institutional_data()

        self.assertEqual(len(fake.holder_symbols), 2)
        self.assertTrue(all(symbol.isdigit() and len(symbol) == 5 for symbol in fake.holder_symbols))
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
