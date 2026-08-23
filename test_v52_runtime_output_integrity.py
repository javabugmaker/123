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
        star = "588000.SH"
        previous_star = self._with_metadata(
            star,
            {"symbol": star, "name": "科创50ETF", "ext": {}},
        )
        try:
            self.assertAlmostEqual(daily_limit_pct(star, is_etf=True), 0.20)
            self.assertEqual(
                price_limit_source(star, is_etf=True),
                "star_etf_20pct_rule",
            )
        finally:
            self._restore_metadata(star, previous_star)

        explicit = "510050.SH"
        previous_explicit = self._with_metadata(
            explicit,
            {
                "symbol": explicit,
                "name": "上证50ETF",
                "ext": {"price_limit_pct": 20},
            },
        )
        try:
            self.assertAlmostEqual(daily_limit_pct(explicit, is_etf=True), 0.20)
            self.assertEqual(
                price_limit_source(explicit, is_etf=True),
                "explicit_ratio_metadata",
            )
        finally:
            self._restore_metadata(explicit, previous_explicit)

    def test_chinext_backtest_rule_is_date_aware(self) -> None:
        symbol = "300001.SZ"
        previous = self._with_metadata(
            symbol,
            {"symbol": symbol, "name": "测试创业板", "ext": {}},
        )
        try:
            self.assertAlmostEqual(
                daily_limit_pct(symbol, trade_date="2020-08-21"), 0.10
            )
            self.assertAlmostEqual(
                daily_limit_pct(symbol, trade_date="2020-08-24"), 0.20
            )
        finally:
            self._restore_metadata(symbol, previous)

    @staticmethod
    def _override_frame(*, setup: bool, signal_count: int) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "EntrySignal": "BREAKOUT_CONFIRM",
                    "PassedFilters": False,
                    "UniverseEligible": True,
                    "BreakoutVolumeConfirmed": True,
                    "BreakoutFlowConfirmed": True,
                    "BreakoutVolumeRatio": 2.0,
                    "SignalCount": signal_count,
                    "VolAccum": setup,
                    "OBV_Div": False,
                    "Consolidation": False,
                    "VolContract": False,
                    "SignalStatus": "NEW",
                    "SignalTrend": "新出现",
                    "Score": 30.0,
                }
            ]
        )

    def test_breakout_override_rejects_event_only_confirmation(self) -> None:
        frame = self._override_frame(setup=False, signal_count=2)
        mask = signal_lifecycle.strict_filter_override_mask(frame)
        self.assertFalse(bool(mask.iloc[0]))
        self.assertFalse(bool(signal_lifecycle._is_active(frame).iloc[0]))

    def test_breakout_override_keeps_one_setup_clue_plus_three_signals(self) -> None:
        frame = self._override_frame(setup=True, signal_count=3)
        mask = signal_lifecycle.strict_filter_override_mask(frame)
        self.assertTrue(bool(mask.iloc[0]))
        self.assertTrue(bool(signal_lifecycle._is_active(frame).iloc[0]))

    def test_etf_market_cap_provenance_is_explicitly_not_applicable(self) -> None:
        result = ScanResult(
            ticker="159697.SZ",
            name="石油ETF鹏华",
            is_etf=True,
            asset_type="etf",
            filter_details={
                "market_cap": None,
                "market_cap_available": False,
                "market_cap_unit_inferred": False,
                "market_cap_unit_assumption": "individual_shares",
                "market_cap_raw_total_shares": 123_000_000.0,
                "market_cap_normalized_total_shares": 123_000_000.0,
                "market_cap_sanity_passed": True,
                "price_limit_pct": 0.05,
            },
        )
        with patch.object(
            report, "finalize_signal_ranking", side_effect=lambda frame: frame
        ):
            row = report._results_to_dataframe([result]).iloc[0]

        self.assertFalse(bool(row["MarketCapApplicable"]))
        self.assertFalse(bool(row["MarketCapAvailable"]))
        self.assertEqual(row["MarketCapUnitAssumption"], "not_applicable")
        self.assertTrue(pd.isna(row["MarketCapRawTotalShares"]))
        self.assertTrue(pd.isna(row["MarketCapNormalizedTotalShares"]))
        self.assertFalse(bool(row["MarketCapSanityPassed"]))
        self.assertAlmostEqual(float(row["PriceLimitPct"]), 0.10)
        self.assertEqual(row["PriceLimitSource"], "standard_10pct_rule")


if __name__ == "__main__":
    unittest.main()
