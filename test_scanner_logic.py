from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import Mock, patch

import pandas as pd

import downloader
from downloader import TickerInfo, is_etf_ticker, normalize_data_source, normalize_ticker
from filters import (
    filter_min_market_cap,
    filter_min_price,
    filter_volatility_contraction,
)
from fundamental_quality import FundamentalQuality
from scanner import ScanResult, run_parallel_indicator_scan, scan_single_from_df
from score import ScoreBreakdown, score_ticker


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
            institution_holding_periods=2,
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
        score = ScoreBreakdown(total=50.0, final_score=50.0)

        with patch("scanner.run_all_filters", return_value=filters), patch(
            "scanner.score_ticker", return_value=score
        ), patch("scanner.classify_style", return_value="均衡"), patch(
            "scanner.get_quality", return_value=quality
        ), patch("scanner.entry_point", return_value={
            "score": 0.0,
            "signal": "HOLD_WAIT",
            "low": float("nan"),
            "high": float("nan"),
            "breakout": float("nan"),
            "stop": float("nan"),
            "volume_ratio": float("nan"),
            "volume_confirmed": False,
            "flow_confirmed": False,
            "price_breakout": False,
        }), patch("scanner.smart_money_stage", return_value="NONE"):
            result = scan_single_from_df(ticker, frame, indicators_computed=True)

        self.assertEqual(result.quality_roe, 12.0)
        self.assertEqual(result.quality_score, 100.0)
        self.assertTrue(result.quality_gate)
        self.assertTrue(result.quality_data_available)
        self.assertTrue(result.quality_net_profit_factor)

    def test_parallel_scan_limits_pending_analysis_tasks(self):
        tickers = [TickerInfo(ticker=f"{index:06d}.SZ") for index in range(9)]
        with patch(
            "scanner._analyse_one_ticker",
            side_effect=lambda ticker, _: ScanResult(ticker=ticker.ticker),
        ) as analyse:
            results = run_parallel_indicator_scan(tickers, max_workers=2)
        self.assertEqual(len(results), len(tickers))
        self.assertEqual(analyse.call_count, len(tickers))

    def test_normalize_ticker_adds_a_share_exchange_suffix(self):
        self.assertEqual(normalize_ticker("002438"), "002438.SZ")
        self.assertEqual(normalize_ticker("600036"), "600036.SH")
        self.assertEqual(normalize_ticker("688981"), "688981.SH")
        self.assertEqual(normalize_ticker("920001"), "920001.BJ")
        self.assertEqual(normalize_ticker("002438.SZ"), "002438.SZ")

    def test_is_etf_ticker_recognizes_stock_etf_prefixes(self):
        self.assertTrue(is_etf_ticker("510300.SH"))
        self.assertTrue(is_etf_ticker("588000.SH"))
        self.assertTrue(is_etf_ticker("159915.SZ"))
        self.assertFalse(is_etf_ticker("600036.SH"))

    def test_only_tickflow_market_source_is_selected(self):
        self.assertEqual(normalize_data_source("tickflow"), "tickflow")
        self.assertEqual(normalize_data_source("eastmoney"), "tickflow")
        self.assertEqual(normalize_data_source("akshare"), "tickflow")
        with self.assertRaises(ValueError):
            normalize_data_source("other")

    def test_tickflow_complete_universe_uses_stock_and_etf_sets(self):
        client = Mock()
        client.universes.get.side_effect = [
            {"symbols": ["600036.SH", "920001.BJ"]},
            {"symbols": ["510300.SH"]},
        ]
        metadata = [
            {"symbol": "600036.SH", "name": "招商银行", "exchange": "SH", "ext": {}},
            {"symbol": "920001.BJ", "name": "北交样本", "exchange": "BJ", "ext": {}},
            {"symbol": "510300.SH", "name": "沪深300ETF", "exchange": "SH", "ext": {}},
        ]
        with patch.object(downloader, "_tickflow", return_value=client), patch.object(
            downloader, "_instrument_batches", return_value=metadata
        ), patch.object(downloader, "_save_universe_cache"):
            payload = downloader._fetch_complete_universe()

        self.assertEqual(payload["stocks"], ["600036.SH", "920001.BJ"])
        self.assertEqual(payload["etfs"], ["510300.SH"])
        self.assertEqual(
            [call.args[0] for call in client.universes.get.call_args_list],
            ["CN_Equity_A", "CN_ETF"],
        )

    def test_build_universe_reuses_complete_local_snapshot(self):
        payload = {
            "stocks": ["600036.SH"],
            "etfs": ["510300.SH"],
            "metadata": {
                "600036.SH": {"symbol": "600036.SH", "name": "招商银行", "ext": {}},
                "510300.SH": {"symbol": "510300.SH", "name": "沪深300ETF", "ext": {}},
            },
        }
        with patch.object(downloader, "_load_universe_cache", return_value=payload), patch.object(
            downloader, "_tickflow"
        ) as client:
            stocks, etfs = downloader.build_ticker_universe()
        client.assert_not_called()
        self.assertEqual([item.ticker for item in stocks], ["600036.SH"])
        self.assertEqual([item.ticker for item in etfs], ["510300.SH"])
        self.assertTrue(etfs[0].is_etf)

    def test_tickflow_frame_is_normalized(self):
        raw = pd.DataFrame(
            {
                "trade_date": ["2026-07-20", "2026-07-21"],
                "open": [10.0, 11.0],
                "high": [12.0, 13.0],
                "low": [9.0, 10.0],
                "close": [11.0, 12.0],
                "volume": [1000.0, 1200.0],
                "amount": [11000.0, 14400.0],
            }
        )
        frame = downloader._normalize_tickflow_frame(raw)
        assert frame is not None
        self.assertEqual(
            list(frame.columns), ["Open", "High", "Low", "Close", "Volume", "Amount"]
        )
        self.assertEqual(frame.iloc[-1]["Close"], 12.0)

    def test_completed_cache_skips_tickflow_request(self):
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
        with patch.object(downloader, "_load_cache", return_value=cached), patch.object(
            downloader, "_cache_has_completed_daily_bar", return_value=True
        ), patch.object(downloader, "_fetch_one") as fetch:
            frame = downloader.download_ticker("000001.SZ")
        self.assertIs(frame, cached)
        fetch.assert_not_called()

    def test_empty_download_batch_returns_without_provider_request(self):
        with patch.object(downloader, "_batch_fetch") as batch:
            result = downloader.download_batch([])
        self.assertEqual(result, {})
        batch.assert_not_called()

    def test_save_cache_writes_tickflow_schema_parquet(self):
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
        with TemporaryDirectory() as temp_dir, patch.object(
            downloader, "_PRICE_CACHE_DIR", Path(temp_dir)
        ):
            downloader._save_cache("000001.SZ", frame)
            cached = downloader._load_cache("000001.SZ")
            self.assertTrue(downloader._cache_path("000001.SZ").exists())
        assert cached is not None
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
        self.assertTrue(downloader._is_excluded_security_name("城投债ETF"))
        self.assertTrue(downloader._is_excluded_security_name("货币ETF"))
        self.assertTrue(downloader._is_excluded_security_name("浙商沪杭甬REIT"))
        self.assertFalse(downloader._is_excluded_security_name("沪深300ETF"))

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
