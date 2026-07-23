from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import Mock, patch

import pandas as pd

import downloader
from downloader import (
    TickerInfo,
    _download_from_sina,
    _download_from_tencent,
    _download_single,
    _fetch_a_share_etfs,
    _fetch_a_share_stocks,
    _is_excluded_security_name,
    is_etf_ticker,
    normalize_ticker,
)
from filters import filter_min_market_cap, filter_min_price, filter_volatility_contraction
from scanner import ScanResult
from score import classify_style, score_ticker


class ScannerLogicTests(TestCase):
    def test_normalize_ticker_adds_a_share_exchange_suffix(self):
        self.assertEqual(normalize_ticker("002438"), "002438.SZ")
        self.assertEqual(normalize_ticker("600036"), "600036.SH")
        self.assertEqual(normalize_ticker("688981"), "688981.SH")
        self.assertEqual(normalize_ticker("002438.SZ"), "002438.SZ")

    @patch("downloader._eastmoney_get")
    def test_full_universe_uses_all_pages(self, request_get):
        first = Mock()
        first.raise_for_status.return_value = None
        first.json.return_value = {
            "data": {
                "total": 4001,
                "diff": [
                    {"f12": f"{index:06d}", "f13": 0, "f14": f"股票{index}", "f20": 1e9}
                    for index in range(100)
                ],
            }
        }
        pages = []
        for page in range(2, 42):
            response = Mock()
            response.raise_for_status.return_value = None
            response.json.return_value = {
                "data": {
                    "diff": [
                        {"f12": f"{page:02d}{index:04d}", "f13": 1, "f14": "股票", "f20": 1e9}
                        for index in range(100)
                    ]
                }
            }
            pages.append(response)
        request_get.side_effect = [first] + pages

        stocks = _fetch_a_share_stocks()

        self.assertGreaterEqual(len(stocks), 4000)
        self.assertEqual(request_get.call_count, 41)

    @patch("downloader._eastmoney_get")
    def test_full_etf_universe(self, request_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": {"total": 2, "diff": [
            {"f12": "588000", "f13": 1, "f14": "科创50ETF", "f20": 1e9},
            {"f12": "159915", "f13": 0, "f14": "创业板ETF", "f20": 1e9},
        ]}}
        request_get.return_value = response
        etfs = _fetch_a_share_etfs()
        self.assertEqual([item.ticker for item in etfs], ["159915.SZ", "588000.SH"])
        self.assertTrue(all(item.is_etf for item in etfs))

    @patch("downloader._eastmoney_get")
    def test_etf_name_filter_keeps_stock_etfs_and_excludes_non_stock_etfs(self, request_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"data": {"total": 12, "diff": [
            {"f12": f"{index:06d}", "f13": 1, "f14": name, "f20": 1e9}
            for index, name in enumerate([
                "公司债ETF",
                "国债ETF",
                "货币ETF",
                "信用债ETF",
                "城投债ETF",
                "同业存单ETF",
                "短融ETF",
                "中票ETF",
                "国开债ETF",
                "政金债ETF",
                "REIT ETF",
                "沪 杭 甬ETF",
            ])
        ] + [
            {"f12": "510300", "f13": 1, "f14": "沪深300 ETF", "f20": 1e9},
        ]}}
        request_get.return_value = response

        etfs = _fetch_a_share_etfs()

        self.assertEqual([item.name for item in etfs], ["沪深300 ETF"])
        self.assertEqual(etfs[0].asset_type, "etf")

    @patch("downloader._eastmoney_get", side_effect=RuntimeError("接口不可用"))
    def test_static_etf_fallback_filters_names_and_sets_asset_type(self, request_get):
        original = downloader._STATIC_A_ETFS
        downloader._STATIC_A_ETFS = [
            ("510300.SH", "沪深300ETF"),
            ("511010.SH", "国债ETF"),
        ]
        try:
            etfs = _fetch_a_share_etfs()
        finally:
            downloader._STATIC_A_ETFS = original

        self.assertEqual([item.ticker for item in etfs], ["510300.SH"])
        self.assertEqual(etfs[0].asset_type, "etf")

    @patch("downloader._eastmoney_get")
    def test_history_response_is_normalized(self, request_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": {"klines": ["2026-07-20,10,11,12,9,1000", "2026-07-21,11,12,13,10,1200"]}
        }
        request_get.return_value = response

        frame = _download_single("000001.SZ")

        self.assertEqual(list(frame.columns), ["Open", "High", "Low", "Close", "Volume"])
        self.assertEqual(frame.iloc[-1]["Close"], 12)

    @patch("downloader._eastmoney_get")
    def test_eastmoney_history_does_not_use_realtime_price(self, request_get):
        history = Mock()
        history.json.return_value = {
            "data": {"klines": ["2026-07-21,10,11,12,9,1000"]}
        }
        request_get.return_value = history

        frame = downloader._download_from_eastmoney("000001.SZ")

        self.assertEqual(frame.iloc[-1]["Close"], 11.0)
        self.assertEqual(request_get.call_count, 1)

    @patch("downloader._HTTP.get")
    def test_sina_history_fallback_is_normalized(self, request_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.text = 'var _data=([{"day":"2026-07-21","open":"1","high":"2","low":"0.9","close":"1.5","volume":"100"}]);'
        request_get.return_value = response
        frame = _download_from_sina("588000.SH")
        self.assertEqual(frame.iloc[-1]["Close"], 1.5)

    @patch("downloader._HTTP.get")
    def test_tencent_history_fallback_is_normalized(self, request_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": {"sz000001": {"qfqday": [["2026-07-21", "11", "12", "13", "10", "1200"]]}}
        }
        request_get.return_value = response

        frame = _download_from_tencent("000001.SZ")

        self.assertEqual(frame.iloc[-1]["Close"], 12)

    @patch("downloader._download_single")
    @patch("downloader._load_cache")
    @patch("downloader._save_cache")
    def test_cached_latest_daily_bar_is_refreshed_incrementally(self, save_cache, load_cache, download_single):
        cached = pd.DataFrame({
            "Open": [10.0], "High": [10.5], "Low": [9.5], "Close": [10.0], "Volume": [1000.0],
        }, index=pd.to_datetime(["2026-07-21"]))
        refreshed = pd.DataFrame({
            "Open": [10.0, 11.0], "High": [11.5, 12.5], "Low": [9.5, 10.5], "Close": [11.0, 12.0], "Volume": [2000.0, 3000.0],
        }, index=pd.to_datetime(["2026-07-21", "2026-07-22"]))
        load_cache.return_value = cached
        download_single.return_value = refreshed

        frame = downloader.download_ticker("000001.SZ")

        self.assertEqual(frame.iloc[-1]["Close"], 12.0)
        self.assertEqual(len(frame), 2)
        self.assertEqual(download_single.call_args.kwargs["start_date"].date().isoformat(), "2026-07-14")
        save_cache.assert_called_once()

    def test_save_cache_writes_parquet(self):
        frame = pd.DataFrame({
            "Open": [10.0], "High": [10.5], "Low": [9.5], "Close": [10.0], "Volume": [1000.0],
        }, index=pd.to_datetime(["2026-07-21"]))
        with TemporaryDirectory() as temp_dir, patch("downloader.CACHE_DIR", Path(temp_dir)):
            downloader._save_cache("000001.SZ", frame, "eastmoney")
            cached = downloader._load_cache("000001.SZ", "eastmoney")
            self.assertTrue(downloader._cache_path("000001.SZ", "eastmoney").exists())

        self.assertEqual(cached.iloc[-1]["Close"], 10.0)

    def test_load_cache_reads_legacy_csv(self):
        frame = pd.DataFrame({
            "Open": [10.0], "High": [10.5], "Low": [9.5], "Close": [10.0], "Volume": [1000.0],
        }, index=pd.to_datetime(["2026-07-21"]))
        with TemporaryDirectory() as temp_dir, patch("downloader.CACHE_DIR", Path(temp_dir)):
            frame.to_csv(downloader._legacy_cache_path("000001.SZ", "eastmoney"))
            cached = downloader._load_cache("000001.SZ", "eastmoney")

        self.assertEqual(cached.iloc[-1]["Close"], 10.0)

    def test_a_share_filters_handle_price_market_cap_and_missing_indicators(self):
        frame = pd.DataFrame({"Close": [4.0]})
        self.assertFalse(filter_min_price(frame).passed)
        frame.iloc[-1, 0] = 900.0
        self.assertFalse(filter_min_price(frame).passed)
        self.assertTrue(filter_min_market_cap(None, required=False).passed)
        volatility = pd.DataFrame({"Close": range(60)})
        self.assertFalse(filter_volatility_contraction(volatility).passed)

    def test_excluded_security_names(self):
        self.assertTrue(_is_excluded_security_name("城投债ETF"))
        self.assertTrue(_is_excluded_security_name("货币ETF"))
        self.assertTrue(_is_excluded_security_name("浙商沪"))
        self.assertTrue(_is_excluded_security_name("浙商沪杭甬REIT"))
        self.assertTrue(_is_excluded_security_name("浙商\u3000沪杭甬\u00a0REIT"))
        self.assertTrue(_is_excluded_security_name("浙商\u2009沪杭甬\u202f仓储物流REIT"))
        self.assertFalse(_is_excluded_security_name("沪深300ETF"))

    def test_ticker_info_defaults_to_stock_and_etf_is_explicit(self):
        self.assertEqual(TickerInfo(ticker="600036.SH").asset_type, "stock")
        etf = TickerInfo(ticker="510300.SH", is_etf=True, asset_type="etf")
        self.assertEqual(etf.asset_type, "etf")

    def test_candidate_output_excludes_failed_filters(self):
        results = [
            ScanResult(ticker="000001.SZ", passed_filters=False),
            ScanResult(ticker="600000.SH", passed_filters=True),
        ]
        candidates = [result for result in results if result.passed_filters and not result.error]

        self.assertEqual([result.ticker for result in candidates], ["600000.SH"])


if __name__ == "__main__":
    main()
