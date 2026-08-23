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
    @staticmethod
    def _clear_optional_price_limit_caches() -> None:
        for name in ("get_price_limit_pct", "get_price_limit_evidence"):
            function = getattr(downloader, name, None)
            clear = getattr(function, "cache_clear", None)
            if callable(clear):
                clear()

    def setUp(self) -> None:
        self._clear_optional_price_limit_caches()

    def tearDown(self) -> None:
        self._clear_optional_price_limit_caches()

    def _with_metadata(self, symbol: str, metadata: dict) -> object:
        previous = downloader._INSTRUMENT_META.get(symbol)
        downloader._INSTRUMENT_META[symbol] = metadata
        self._clear_optional_price_limit_caches()
        return previous

    def _restore_metadata(self, symbol: str, previous: object) -> None:
        self._clear_optional_price_limit_caches()
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
        # v52 established v9; v62 advanced to v10 for deterministic OHLCV
        # revision fingerprints. v94 advances to v11 because FAST historical
        # TriggerScore now uses the same smooth formula as live/EXACT scoring.
        self.assertEqual(performance_cache.BACKTEST_CACHE_VERSION, "v11")

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
            self.assertEqual(
                price_limit_source(symbol, is_etf=True),
                "standard_10pct_rule",
            )
        finally:
            self._restore_metadata(symbol, previous)

    def test_star_etf_and_explicit_ratio_metadata_are_supported(self) -> None:
        symbol = "588000.SH"
        previous = self._with_metadata(
            symbol,
            {
                "symbol": symbol,
                "name": "科创50ETF",
                "ext": {"price_limit_ratio": 0.20},
            },
        )
        try:
            self.assertAlmostEqual(daily_limit_pct(symbol, is_etf=True), 0.20)
            self.assertEqual(price_limit_source(symbol, is_etf=True), "metadata_ratio")
        finally:
            self._restore_metadata(symbol, previous)

    def test_chinext_backtest_rule_is_date_aware(self) -> None:
        self.assertAlmostEqual(
            daily_limit_pct("300001.SZ", trade_date="2020-08-21"), 0.10
        )
        self.assertAlmostEqual(
            daily_limit_pct("300001.SZ", trade_date="2020-08-24"), 0.20
        )

    def test_etf_market_cap_provenance_is_explicitly_not_applicable(self) -> None:
        result = ScanResult(
            ticker="510300.SH",
            name="沪深300ETF",
            is_etf=True,
            market_cap=None,
            market_cap_available=False,
            market_cap_unit_inferred=False,
            market_cap_unit_assumption="not_applicable",
            market_cap_raw_total_shares=None,
            market_cap_normalized_total_shares=None,
            market_cap_sanity_passed=True,
        )
        row = result.to_dict()
        self.assertFalse(row["MarketCapAvailable"])
        self.assertFalse(row["MarketCapUnitInferred"])
        self.assertEqual(row["MarketCapUnitAssumption"], "not_applicable")
        self.assertFalse(row["MarketCapApplicable"])

    def test_breakout_override_keeps_one_setup_clue_plus_three_signals(self) -> None:
        result = ScanResult(
            ticker="600000.SH",
            breakout_score=90.0,
            entry_signal="BREAKOUT_CONFIRM",
            signal_count=4,
            passed_filters=True,
            filter_schema_evaluated=True,
            min_price_passed=True,
            min_volume_passed=True,
            min_market_cap_passed=True,
            sufficient_history_passed=True,
            obv_div=True,
            cmf_pos=True,
            ad_slope_pos=True,
            vol_contract=True,
        )
        with patch.object(report, "OUTPUT_DIR"):
            ranked = report._apply_actionability_policy([result])
        self.assertTrue(ranked[0].passed_filters)

    def test_breakout_override_rejects_event_only_confirmation(self) -> None:
        result = ScanResult(
            ticker="600001.SH",
            breakout_score=90.0,
            entry_signal="BREAKOUT_CONFIRM",
            signal_count=3,
            passed_filters=True,
            filter_schema_evaluated=True,
            min_price_passed=True,
            min_volume_passed=True,
            min_market_cap_passed=True,
            sufficient_history_passed=True,
            cmf_pos=True,
            ad_slope_pos=True,
            vol_contract=False,
            obv_div=False,
            consolidation=False,
            vol_accum=False,
        )
        with patch.object(report, "OUTPUT_DIR"):
            ranked = report._apply_actionability_policy([result])
        self.assertFalse(ranked[0].passed_filters)


if __name__ == "__main__":
    unittest.main()
