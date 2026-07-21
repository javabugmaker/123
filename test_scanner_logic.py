from unittest import TestCase, main
from unittest.mock import Mock, patch

import pandas as pd

from downloader import TickerInfo, _download_from_sina, _download_from_tencent, _download_single, _fetch_a_share_etfs, _fetch_a_share_stocks
from filters import filter_min_market_cap, filter_min_price, filter_volatility_contraction
from scanner import ScanResult
from score import classify_style, score_ticker


class ScannerLogicTests(TestCase):
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

    def test_a_share_filters_handle_price_market_cap_and_missing_indicators(self):
        frame = pd.DataFrame({"Close": [4.0]})
        self.assertFalse(filter_min_price(frame).passed)
        frame.iloc[-1, 0] = 900.0
        self.assertFalse(filter_min_price(frame).passed)
        self.assertTrue(filter_min_market_cap(None, required=False).passed)
        volatility = pd.DataFrame({"Close": range(60)})
        self.assertFalse(filter_volatility_contraction(volatility).passed)

    def test_candidate_output_excludes_failed_filters(self):
        results = [
            ScanResult(ticker="000001.SZ", passed_filters=False),
            ScanResult(ticker="600000.SH", passed_filters=True),
        ]
        candidates = [result for result in results if result.passed_filters and not result.error]

        self.assertEqual([result.ticker for result in candidates], ["600000.SH"])


if __name__ == "__main__":
    main()
