from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

import config
import downloader
import performance_cache
import report
import signal_lifecycle
from scanner import ScanResult
from tradeability import daily_limit_pct, price_limit_source


class V52RuntimeOutputIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        downloader.get_price_limit_pct.cache_clear()
        if hasattr(downloader, "get_price_limit_evidence") and hasattr(
            downloader.get_price_limit_evidence, "cache_clear"
        ):
            downloader.get_price_limit_evidence.cache_clear()

    def tearDown(self) -> None:
        downloader.get_price_limit_pct.cache_clear()

    def _with_metadata(self, symbol: str, metadata: dict) -> object:
        previous = downloader._INSTRUMENT_META.get(symbol)
        downloader._INSTRUMENT_META[symbol] = metadata
        downloader.get_price_limit_pct.cache_clear()
        return previous

    def _restore_metadata(self, symbol: str, previous: object) -> None:
        downloader.get_price_limit_pct.cache_clear()
        if previous is None:
            downloader._INSTRUMENT_META.pop(symbol, None)
        else:
            downloader._INSTRUMENT_META[symbol] = previous

    def test_v52_versions_and_backtest_cache_boundary(self) -> None:
        self.assertIn("v52", config.SCORING_VERSION)
        self.assertIn("v51", config.SCORING_VERSION)
        self.assertIn("v52", config.PIPELINE_VERSION)
        self.assertIn("v52", config.DECISION_INTEGRITY_VERSION)
        self.assertIn("v52", config.OUTPUT_CONTRACT_VERSION)
        self.assertIn("v52", config.MARKET_DATA_VERSION)
        self.assertIn("v52", config.BACKTEST_PROVENANCE_VERSION)
        # v52 originally established the v9 cache boundary. v62 deliberately
        # advanced it to v10 when full-history OHLCV revision fingerprints were
        # added; later versions must preserve that stronger integrity contract.
        self.assertEqual(performance_cache.BACKTEST_CACHE_VERSION, "v10")

    def test_tickflow_limit_price_levels_are_not_misread_as_ratios(self) -> None:
        symbol = "600769.SH"
        previous = self._with_metadata(
            symbol,
            {
                "symbol": symbol,
                "name": "祥龙电业",
                "ext": {"limit_up": 12.34, "limit_down": 10.10},
            },
        )
        try:
            self.assertAlmostEqual(daily_limit_pct(symbol), 0.10)
            self.assertEqual(price_limit_source(symbol), "standard_10pct_rule")
        finally:
            self._restore_metadata(symbol, previous)

    def test_low_priced_etf_limit_fields_do_not_become_five_percent_rule(self) -> None:
        symbol = "159697.SZ"
        previous = self._with_metadata(
            symbol,
            {
                "symbol": symbol,
                "name": "石油ETF鹏华",
                "ext": {"limit_up": 0.05, "limit_down": 0.04},
            },
        )
        try:
            self.assertAlmostEqual(daily_limit_pct(symbol, is_etf=True), 0.10)
            self.assertEqual(price_limit_source(symbol, is_etf=True), "standard_10pct_rule")
        finally:
            self._restore_metadata(symbol, previous)

    def test_star_etf_and_explicit_ratio_metadata_are_supported(self) -> None:
        star_symbol = "588000.SH"
        star_previous = self._with_metadata(
            star_symbol,
            {"symbol": star_symbol, "name": "科创50ETF", "ext": {}},
        )
        try:
            self.assertAlmostEqual(daily_limit_pct(star_symbol, is_etf=True), 0.20)
            self.assertEqual(
                price_limit_source(star_symbol, is_etf=True),
                "star_etf_20pct_rule",
            )
        finally:
            self._restore_metadata(star_symbol, star_previous)

        stock_symbol = "600000.SH"
        stock_previous = self._with_metadata(
            stock_symbol,
            {
                "symbol": stock_symbol,
                "name": "浦发银行",
                "limit_pct": 0.10,
                "limit_pct_source": "tickflow_explicit_ratio",
            },
        )
        try:
            self.assertAlmostEqual(daily_limit_pct(stock_symbol), 0.10)
        finally:
            self._restore_metadata(stock_symbol, stock_previous)

    def test_chinext_backtest_rule_is_date_aware(self) -> None:
        symbol = "300001.SZ"
        previous = self._with_metadata(
            symbol,
            {
                "symbol": symbol,
                "name": "特锐德",
                "limit_pct": 0.20,
                "limit_pct_source": "security_metadata_rule",
            },
        )
        try:
            self.assertAlmostEqual(
                daily_limit_pct(symbol, trade_date=pd.Timestamp("2020-08-21")),
                0.10,
            )
            self.assertAlmostEqual(
                daily_limit_pct(symbol, trade_date=pd.Timestamp("2020-08-24")),
                0.20,
            )
        finally:
            self._restore_metadata(symbol, previous)

    def test_etf_market_cap_provenance_is_explicitly_not_applicable(self) -> None:
        result = ScanResult(ticker="510300.SH", name="沪深300ETF", sector="ETF")
        result.market_cap = float("nan")
        frame = pd.DataFrame([result.to_dict()])
        enriched = report.ensure_output_schema(frame)
        self.assertIn("MarketCapApplicability", enriched.columns)
        self.assertEqual(enriched.iloc[0]["MarketCapApplicability"], "NOT_APPLICABLE")

    def test_breakout_override_keeps_one_setup_clue_plus_three_signals(self) -> None:
        row = {
            "PassAll": False,
            "HardRisk": False,
            "BreakoutScore": 80.0,
            "FinalScore": 72.0,
            "TrendScore": 12.0,
            "VolumeScore": 18.0,
            "AccumulationScore": 8.0,
            "VolatilityScore": 5.0,
            "StructureScore": 9.0,
            "ValueTrapRisk": 20.0,
            "SignalCount": 3,
            "SetupClueCount": 1,
            "EventSignalCount": 2,
        }
        with patch.object(signal_lifecycle, "FILTER_OVERRIDE_MIN_SIGNAL_COUNT", 3):
            self.assertTrue(signal_lifecycle._breakout_override_eligible(pd.Series(row)))

    def test_breakout_override_rejects_event_only_confirmation(self) -> None:
        row = {
            "PassAll": False,
            "HardRisk": False,
            "BreakoutScore": 80.0,
            "FinalScore": 72.0,
            "TrendScore": 12.0,
            "VolumeScore": 18.0,
            "AccumulationScore": 8.0,
            "VolatilityScore": 5.0,
            "StructureScore": 9.0,
            "ValueTrapRisk": 20.0,
            "SignalCount": 3,
            "SetupClueCount": 0,
            "EventSignalCount": 3,
        }
        with patch.object(signal_lifecycle, "FILTER_OVERRIDE_MIN_SIGNAL_COUNT", 3):
            self.assertFalse(signal_lifecycle._breakout_override_eligible(pd.Series(row)))


if __name__ == "__main__":
    unittest.main()
