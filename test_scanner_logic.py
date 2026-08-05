from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import Mock, patch

import pandas as pd

import downloader
from downloader import (
    TickerInfo,
    _download_from_akshare,
    _download_from_sina,
    _download_from_tencent,
    _download_single,
    _download_worker_count,
    _fetch_a_share_etfs,
    _fetch_a_share_stocks,
    _is_excluded_security_name,
    is_etf_ticker,
    normalize_data_source,
    normalize_ticker,
)
from filters import (
    filter_min_market_cap,
    filter_min_price,
    filter_volatility_contraction,
)
from fundamental_quality import FundamentalQuality
from scanner import ScanResult, run_parallel_indicator_scan, scan_single_from_df
from score import score_ticker


class ScannerLogicTests(TestCase):
    def test_scan_preserves_fundamental_quality_fields(self):
        ticker = TickerInfo(ticker="000001.SZ", market_cap=1e10)
        frame = pd.DataFrame({"Close": [10.0] * 20})
        filters = Mock()
        filters.all_passed.return_value = True
        for name in (
            "min_price",
            "min_volume",
            "min_market_cap",
            "sufficient_history",
            "bear_market",
            "consolidation",
            "volume_accumulation",
            "obv_divergence",
            "cmf_positive",
            "ad_slope",
            "volatility_contraction",
        ):
            setattr(filters, name, Mock(passed=True, details={}))
        filters.signal_count.return_value = 1
        filters.passed_count.return_value = 11
        quality = FundamentalQuality(
            ticker="000001.SZ",
            roe=12.0,
            gross_margin=30.0,
            institution_holding_trend="increasing",
            institution_holding_periods=3,
            net_profit_y1=30.0,
            net_profit_y2=20.0,
            net_profit_y3=10.0,
            industry_gross_margin_percentile=0.2,
            roe_factor=True,
            gross_margin_factor=True,
            institution_holding_factor=True,
            net_profit_factor=True,
            quality_score=100.0,
            quality_gate=True,
            quality_reason="全部通过",
            data_available=True,
        )

        with patch("scanner.run_all_filters", return_value=filters), patch(
            "scanner.score_ticker"
        ), patch("scanner.classify_style", return_value="均衡"), patch(
            "scanner.get_quality", return_value=quality
        ):
            result = scan_single_from_df(ticker, frame, indicators_computed=True)

        self.assertEqual(result.quality_roe, 12.0)
        self.assertEqual(result.quality_score, 100.0)
        self.assertTrue(result.quality_gate)
        self.assertTrue(result.quality_data_available)
        self.assertTrue(result.quality_net_profit_factor)

    def test_parallel_scan_limits_pending_analysis_tasks(self):
        tickers = [TickerInfo(ticker=f"{index:06d}.SZ") for index in range(9)]

        with patch("scanner._analyse_one_ticker", side_effect=lambda ticker, _: ScanResult(ticker=ticker.ticker)) as analyse:
            results = run_parallel_indicator_scan(tickers, max_workers=2)

        self.assertEqual(len(results), len(tickers))
        self.assertEqual(analyse.call_count, len(tickers))

    def test_normalize_ticker_adds_a_share_exchange_suffix(self):
        self.assertEqual(normalize_ticker("002438"), "002438.SZ")
        self.assertEqual(normalize_ticker("600036"), "600036.SH")
        self.assertEqual(normalize_ticker("688981"), "688981.SH")
        self.assertEqual(normalize_ticker("002438.SZ"), "002438.SZ")

    def test_is_etf_ticker_recognizes_shanghai_50_prefix(self):
        self.assertTrue(is_etf_ticker("510300.SH"))
        self.assertTrue(is_etf_ticker("588000.SH"))
        self.assertFalse(is_etf_ticker("600036.SH"))

    @patch("downloader._eastmoney_get")
    def test_full_universe_uses_all_pages(self, request_get):
        first = Mock()
        first.raise_for_status.return_value = None
        first.json.return_value = {
            "data": {
                "total": 4001,
                "diff": [
                    {
                        "f12": f"{index:06d}",
                        "f13": 0,
                        "f14": f"股票{index}",
                        "f20": 1e9,
                        "f100": "软件服务",
                        "f102": "北京板块",
                    }
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
                        {
                            "f12": f"{page:02d}{index:04d}",
                            "f13": 1,
                            "f14": "股票",
                            "f20": 1e9,
                        }
                        for index in range(100)
                    ]
                }
            }
            pages.append(response)
        request_get.side_effect = [first] + pages

        with patch("downloader._load_universe_cache", return_value=[]), patch(
            "downloader._save_universe_cache"
        ):
            stocks = _fetch_a_share_stocks()

        self.assertGreaterEqual(len(stocks), 4000)
        self.assertEqual(request_get.call_count, 41)
        self.assertEqual(stocks[0].industry, "软件服务")
        self.assertEqual(stocks[0].sector, "北京板块")

    @patch("downloader._eastmoney_get", side_effect=RuntimeError("接口不可用"))
    def test_static_stock_fallback_sets_stock_metadata(self, request_get):
        with (
            TemporaryDirectory() as temp_dir,
            patch.object(
                downloader, "_UNIVERSE_CACHE_PATH", Path(temp_dir) / "missing.json"
            ),
        ):
            stocks = _fetch_a_share_stocks()

        self.assertEqual(len(stocks), len(downloader._STATIC_A_STOCKS))
        self.assertTrue(
            all(item.asset_type == "stock" and not item.is_etf for item in stocks)
        )
        self.assertEqual(stocks[0].ticker, downloader._STATIC_A_STOCKS[0][0])

    @patch("downloader._eastmoney_get")
    def test_full_etf_universe(self, request_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": {
                "total": 2,
                "diff": [
                    {"f12": "588000", "f13": 1, "f14": "科创50ETF", "f20": 1e9},
                    {"f12": "159915", "f13": 0, "f14": "创业板ETF", "f20": 1e9},
                ],
            }
        }
        request_get.return_value = response
        with patch("downloader._load_universe_cache", return_value=[]), patch(
            "downloader._save_universe_cache"
        ):
            etfs = _fetch_a_share_etfs()
        self.assertEqual([item.ticker for item in etfs], ["159915.SZ", "588000.SH"])
        self.assertTrue(all(item.is_etf for item in etfs))

    @patch("downloader._eastmoney_get")
    def test_etf_name_filter_keeps_stock_etfs_and_excludes_non_stock_etfs(
        self, request_get
    ):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": {
                "total": 12,
                "diff": [
                    {"f12": f"{index:06d}", "f13": 1, "f14": name, "f20": 1e9}
                    for index, name in enumerate(
                        [
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
                        ]
                    )
                ]
                + [
                    {"f12": "510300", "f13": 1, "f14": "沪深300 ETF", "f20": 1e9},
                ],
            }
        }
        request_get.return_value = response

        with patch("downloader._load_universe_cache", return_value=[]), patch(
            "downloader._save_universe_cache"
        ):
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
            with patch("downloader._load_universe_cache", return_value=[]):
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
            "data": {
                "klines": ["2026-07-20,10,11,12,9,1000", "2026-07-21,11,12,13,10,1200"]
            }
        }
        request_get.return_value = response

        frame = _download_single("000001.SZ")

        self.assertEqual(
            list(frame.columns), ["Open", "High", "Low", "Close", "Volume"]
        )
        self.assertEqual(frame.iloc[-1]["Close"], 12)

    @patch("downloader._eastmoney_get")
    def test_eastmoney_history_does_not_use_realtime_price(self, request_get):
        history = Mock()
        history.json.return_value = {"data": {"klines": ["2026-07-21,10,11,12,9,1000"]}}
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
        assert frame is not None
        self.assertEqual(frame.iloc[-1]["Close"], 1.5)

    @patch("downloader._HTTP.get")
    def test_tencent_history_fallback_is_normalized(self, request_get):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "data": {
                "sz000001": {"qfqday": [["2026-07-21", "11", "12", "13", "10", "1200"]]}
            }
        }
        request_get.return_value = response

        frame = _download_from_tencent("000001.SZ")

        self.assertEqual(frame.iloc[-1]["Close"], 12)

    @patch("downloader.ak")
    def test_akshare_stock_history_is_normalized(self, akshare):
        akshare.stock_zh_a_hist.return_value = pd.DataFrame({
            "日期": ["2026-07-20", "2026-07-21"],
            "开盘": [10.0, 11.0],
            "最高": [12.0, 13.0],
            "最低": [9.0, 10.0],
            "收盘": [11.0, 12.0],
            "成交量": [1000.0, 1200.0],
        })

        frame = _download_from_akshare("000001.SZ")

        assert frame is not None
        self.assertEqual(list(frame.columns), ["Open", "High", "Low", "Close", "Volume"])
        self.assertEqual(frame.iloc[-1]["Close"], 12.0)
        self.assertEqual(akshare.stock_zh_a_hist.call_args.kwargs["symbol"], "000001")
        self.assertEqual(akshare.stock_zh_a_hist.call_args.kwargs["adjust"], "qfq")
        self.assertEqual(
            akshare.stock_zh_a_hist.call_args.kwargs["timeout"],
            downloader.DOWNLOAD_TIMEOUT,
        )

    @patch("downloader.ak")
    def test_akshare_etf_history_uses_timed_eastmoney_endpoint(self, akshare):
        akshare.fund_etf_hist_em.return_value = pd.DataFrame({
            "日期": ["2026-07-21"],
            "开盘": [4.0],
            "最高": [4.2],
            "最低": [3.9],
            "收盘": [4.1],
            "成交量": [10000.0],
        })

        frame = _download_from_akshare("510300.SH")

        assert frame is not None
        self.assertEqual(frame.iloc[-1]["Close"], 4.1)
        self.assertEqual(akshare.fund_etf_hist_em.call_args.kwargs["symbol"], "510300")
        self.assertEqual(akshare.fund_etf_hist_em.call_args.kwargs["adjust"], "qfq")

    @patch("downloader.ak")
    def test_akshare_etf_history_returns_none_when_timed_endpoint_fails(self, akshare):
        akshare.fund_etf_hist_em.side_effect = RuntimeError("Eastmoney unavailable")

        frame = _download_from_akshare("510300.SH")

        self.assertIsNone(frame)
        self.assertTrue(akshare.fund_etf_hist_em.called)

    def test_empty_download_batch_returns_without_creating_a_worker_pool(self):
        with patch("downloader._log_download_progress") as progress:
            result = downloader.download_batch([], source="akshare")

        self.assertEqual(result, {})
        progress.assert_called_once_with(0, 0, 0, 0)

    def test_download_batch_emits_initial_progress_before_first_request(self):
        frame = pd.DataFrame(
            {
                "Open": [10.0],
                "High": [11.0],
                "Low": [9.0],
                "Close": [10.5],
                "Volume": [1000.0],
            },
            index=pd.to_datetime(["2026-08-05"]),
        )
        with patch("downloader.download_ticker", return_value=frame), patch(
            "downloader._log_download_progress"
        ) as progress:
            result = downloader.download_batch(
                [TickerInfo(ticker="000001.SZ")], source="akshare"
            )

        self.assertEqual(list(result), ["000001.SZ"])
        self.assertEqual(progress.call_args_list[0].args, (0, 1, 0, 0))
        self.assertEqual(progress.call_args_list[-1].args, (1, 1, 1, 0))

    @patch("downloader._download_from_eastmoney")
    @patch("downloader._download_from_akshare", return_value=None)
    def test_akshare_falls_back_to_eastmoney(self, akshare, eastmoney):
        eastmoney.return_value = pd.DataFrame({
            "Open": [10.0], "High": [11.0], "Low": [9.0], "Close": [10.5], "Volume": [1000.0],
        }, index=pd.to_datetime(["2026-07-21"]))

        frame = _download_single("000001.SZ", source="akshare")

        assert frame is not None
        self.assertEqual(frame.iloc[-1]["Close"], 10.5)
        eastmoney.assert_called_once()

    def test_akshare_is_a_supported_data_source(self):
        self.assertEqual(normalize_data_source("AkShare"), "akshare")

    def test_auto_is_a_supported_data_source_with_safe_worker_cap(self):
        self.assertEqual(normalize_data_source("auto"), "auto")
        self.assertEqual(_download_worker_count("auto", 100), 4)
        self.assertEqual(_download_worker_count("auto", 2), 2)
        self.assertEqual(_download_worker_count("eastmoney", 100), downloader.DOWNLOAD_THREADS)

    @patch("downloader._download_from_eastmoney")
    @patch("downloader._download_from_akshare", return_value=None)
    def test_auto_source_falls_back_from_akshare(self, akshare, eastmoney):
        eastmoney.return_value = pd.DataFrame({
            "Open": [10.0], "High": [11.0], "Low": [9.0], "Close": [10.5], "Volume": [1000.0],
        }, index=pd.to_datetime(["2026-07-21"]))

        frame = _download_single("000001.SZ", source="auto")

        assert frame is not None
        self.assertEqual(frame.iloc[-1]["Close"], 10.5)
        akshare.assert_called_once()
        eastmoney.assert_called_once()

    @patch("downloader._download_single")
    @patch("downloader._load_cache")
    @patch("downloader._save_cache")
    def test_cached_latest_daily_bar_is_refreshed_incrementally(
        self, save_cache, load_cache, download_single
    ):
        cached = pd.DataFrame(
            {
                "Open": [10.0],
                "High": [10.5],
                "Low": [9.5],
                "Close": [10.0],
                "Volume": [1000.0],
            },
            index=pd.to_datetime(["2026-07-21"]),
        )
        refreshed = pd.DataFrame(
            {
                "Open": [10.0, 11.0],
                "High": [11.5, 12.5],
                "Low": [9.5, 10.5],
                "Close": [11.0, 12.0],
                "Volume": [2000.0, 3000.0],
            },
            index=pd.to_datetime(["2026-07-21", "2026-07-22"]),
        )
        load_cache.return_value = cached
        download_single.return_value = refreshed

        frame = downloader.download_ticker("000001.SZ")

        self.assertEqual(frame.iloc[-1]["Close"], 12.0)
        self.assertEqual(len(frame), 2)
        self.assertEqual(
            download_single.call_args.kwargs["start_date"].date().isoformat(),
            "2026-07-14",
        )
        save_cache.assert_called_once()

    @patch("downloader._download_single")
    @patch("downloader._load_cache")
    @patch("downloader._cache_has_completed_daily_bar", return_value=True)
    def test_completed_cache_skips_incremental_download(
        self, cache_is_current, load_cache, download_single
    ):
        cached = pd.DataFrame(
            {
                "Open": [10.0],
                "High": [10.5],
                "Low": [9.5],
                "Close": [10.0],
                "Volume": [1000.0],
            },
            index=pd.to_datetime(["2026-08-05"]),
        )
        load_cache.return_value = cached

        frame = downloader.download_ticker("000001.SZ")

        self.assertIs(frame, cached)
        cache_is_current.assert_called_once_with(cached)
        download_single.assert_not_called()

    @patch("downloader._eastmoney_get")
    def test_batch_realtime_prices_cover_stocks_and_etfs(self, request_get):
        response = Mock()
        response.json.return_value = {
            "data": {
                "total": 2,
                "diff": [
                    {"f12": "000858", "f13": 0, "f43": 1280, "f60": 1275},
                    {"f12": "510300", "f13": 1, "f43": 401, "f60": 400},
                ],
            }
        }
        request_get.return_value = response

        prices = downloader._fetch_eastmoney_realtime_prices(
            ["000858.SZ", "510300.SH"]
        )

        self.assertEqual(prices, {"000858.SZ": 12.8, "510300.SH": 4.01})
        self.assertEqual(request_get.call_count, 2)

    def test_etf_universe_reuses_fresh_local_snapshot(self):
        rows = [{"f12": "510300", "f13": 1, "f14": "沪深300ETF", "f20": 1e9}]
        with TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "etfs.json"
            downloader._save_universe_cache(cache_path, rows)
            with patch.object(downloader, "_ETF_UNIVERSE_CACHE_PATH", cache_path), patch(
                "downloader._eastmoney_get"
            ) as request_get:
                etfs = _fetch_a_share_etfs()

        self.assertEqual([item.ticker for item in etfs], ["510300.SH"])
        request_get.assert_not_called()

    def test_save_cache_writes_parquet(self):
        frame = pd.DataFrame(
            {
                "Open": [10.0],
                "High": [10.5],
                "Low": [9.5],
                "Close": [10.0],
                "Volume": [1000.0],
            },
            index=pd.to_datetime(["2026-07-21"]),
        )
        with (
            TemporaryDirectory() as temp_dir,
            patch("downloader.CACHE_DIR", Path(temp_dir)),
            patch("pandas.DataFrame.to_parquet") as to_parquet,
            patch("pandas.read_parquet", return_value=frame) as read_parquet,
        ):
            downloader._save_cache("000001.SZ", frame, "eastmoney")
            cached = downloader._load_cache("000001.SZ", "eastmoney")
            self.assertTrue(downloader._cache_path("000001.SZ", "eastmoney").exists())

        assert cached is not None
        to_parquet.assert_called_once()
        read_parquet.assert_called_once()
        self.assertEqual(cached.iloc[-1]["Close"], 10.0)

    def test_load_cache_reads_legacy_csv(self):
        frame = pd.DataFrame(
            {
                "Open": [10.0],
                "High": [10.5],
                "Low": [9.5],
                "Close": [10.0],
                "Volume": [1000.0],
            },
            index=pd.to_datetime(["2026-07-21"]),
        )
        with (
            TemporaryDirectory() as temp_dir,
            patch("downloader.CACHE_DIR", Path(temp_dir)),
        ):
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
        self.assertTrue(
            _is_excluded_security_name("浙商\u2009沪杭甬\u202f仓储物流REIT")
        )
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
        candidates = [
            result for result in results if result.passed_filters and not result.error
        ]

        self.assertEqual([result.ticker for result in candidates], ["600000.SH"])

    def test_score_structure_returns_zero_when_ohlc_columns_are_missing(self):
        frame = pd.DataFrame({"Close": [10.0] * 252})
        self.assertEqual(score_ticker(frame).structure, 0.0)

    def test_volatility_filter_uses_atr14_to_atr50_ratio(self):
        frame = pd.DataFrame(
            {
                "ATR14": [8.0] * 60,
                "ATR50": [10.0] * 60,
                "BB_Width": [1.0] * 60,
            }
        )
        result = filter_volatility_contraction(frame)
        self.assertTrue(result.passed)
        self.assertTrue(result.details["atr_compressing"])


if __name__ == "__main__":
    main()
