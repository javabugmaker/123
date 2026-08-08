from __future__ import annotations

import unittest
import warnings
from unittest.mock import patch

import numpy as np
import pandas as pd
from pandas.errors import PerformanceWarning

import analytics
import gui
from config import (
    ETF_MIN_PRICE,
    MIN_ETF_AVG_AMOUNT_60D,
    MIN_STOCK_AVG_AMOUNT_60D,
    SCORING_VERSION,
)
from filters import filter_min_price, filter_min_volume
from model_calibration import build_global_calibration
from performance_cache import BACKTEST_CACHE_VERSION, INDICATOR_CACHE_VERSION
from scanner import ScanResult
from signal_lifecycle import finalize_signal_ranking


class ModelV20RegressionTests(unittest.TestCase):
    def test_v20_version_and_cache_contract(self):
        self.assertEqual(SCORING_VERSION, "2026-08-08-v20-consistency-performance")
        self.assertEqual(INDICATOR_CACHE_VERSION, "v5")
        self.assertEqual(BACKTEST_CACHE_VERSION, "v9")

    def test_etf_uses_etf_price_floor_while_stock_keeps_stock_floor(self):
        frame = pd.DataFrame({"Close": [0.53]})
        self.assertGreater(0.53, ETF_MIN_PRICE)
        self.assertTrue(filter_min_price(frame, is_etf=True).passed)
        self.assertFalse(filter_min_price(frame, is_etf=False).passed)

    def test_liquidity_prefers_amount_and_uses_asset_specific_threshold(self):
        stock_low = pd.DataFrame({
            "Volume": [10_000_000.0] * 60,
            "Amount": [MIN_STOCK_AVG_AMOUNT_60D * 0.5] * 60,
        })
        etf_ok = pd.DataFrame({
            "Volume": [10_000_000.0] * 60,
            "Amount": [MIN_ETF_AVG_AMOUNT_60D * 1.5] * 60,
        })
        self.assertFalse(filter_min_volume(stock_low, is_etf=False).passed)
        self.assertTrue(filter_min_volume(etf_ok, is_etf=True).passed)

    def test_universe_ineligible_breakout_can_never_be_trade_ready(self):
        frame = pd.DataFrame([{
            "Ticker": "159873.SZ", "IsETF": True, "AssetType": "etf",
            "Score": 70.0, "FinalScore": 70.0, "InstitutionalScore": 70.0,
            "EntrySignal": "BREAKOUT_CONFIRM", "BreakoutVolumeConfirmed": True,
            "BreakoutFlowConfirmed": True, "PassedFilters": False,
            "UniverseEligible": False, "SignalConfirmed": False,
            "QualityApplicable": False, "QualityGate": True,
            "QualityDataCompleteness": 0.0, "ScoreCoverage": 1.0,
            "DataTradingAgeDays": 0, "LifecycleStage": "趋势确认",
            "ValueTrapRisk": 0.0, "RSI14": 55.0, "DistToLow52W": 15.0,
            "DistToMA20": 1.0, "RecentReturn20D": 3.0,
        }])
        result = finalize_signal_ranking(frame)
        self.assertNotEqual(result.loc[0, "RankingEligibility"], "推荐")
        self.assertEqual(result.loc[0, "DecisionState"], "OBSERVE")
        self.assertIn("基础准入", result.loc[0, "TradeReadinessReason"])

    def test_strict_breakout_can_override_signal_confirmation_only(self):
        frame = pd.DataFrame([{
            "Ticker": "510300.SH", "IsETF": True, "AssetType": "etf",
            "Score": 70.0, "FinalScore": 70.0, "InstitutionalScore": 70.0,
            "EntrySignal": "BREAKOUT_CONFIRM", "BreakoutVolumeConfirmed": True,
            "BreakoutFlowConfirmed": True, "PassedFilters": False,
            "UniverseEligible": True, "SignalConfirmed": False,
            "QualityApplicable": False, "QualityGate": True,
            "QualityDataCompleteness": 0.0, "ScoreCoverage": 1.0,
            "DataTradingAgeDays": 0, "LifecycleStage": "趋势确认",
            "ValueTrapRisk": 0.0, "RSI14": 55.0, "DistToLow52W": 15.0,
            "DistToMA20": 1.0, "RecentReturn20D": 3.0,
        }])
        result = finalize_signal_ranking(frame)
        self.assertEqual(result.loc[0, "RankingEligibility"], "推荐")

    def test_enrichment_preserves_or_rebuilds_atr_expansion(self):
        index = pd.date_range("2026-01-01", periods=80, freq="B")
        close = pd.Series(np.linspace(10.0, 12.0, len(index)), index=index)
        frame = pd.DataFrame({
            "Open": close,
            "High": close + 0.30,
            "Low": close - 0.25,
            "Close": close,
            "Volume": 1_000_000.0,
            "MA20": close.rolling(20).mean(),
            "MA50": close.rolling(50).mean(),
            "RSI14": 55.0,
            "ATR14": 0.30,
        }, index=index)
        result = ScanResult(ticker="000001.SZ", atr14=0.30, atr50=0.28, atr_expansion=1.0714, atr_expansion_source="scanner")
        enriched, _, _ = analytics._enrich_one_result(
            result, "tickflow", "震荡", "test", frames={result.ticker: frame}
        )
        self.assertTrue(np.isfinite(enriched.atr50))
        self.assertTrue(np.isfinite(enriched.atr_expansion))
        self.assertGreater(enriched.atr_expansion, 0.0)
        self.assertNotEqual(enriched.atr_expansion_source, "unavailable")

    def test_calibration_confidence_does_not_saturate_on_small_peer_group(self):
        def sample(count: int) -> pd.DataFrame:
            dates = pd.date_range("2022-01-01", periods=count, freq="7D")
            return pd.DataFrame({
                "asset_type": "stock",
                "entry_signal": "WAIT_PULLBACK",
                "market_regime": "RISK_ON",
                "score": 65.0,
                "setup_score": 60.0,
                "sample_weight": 1.0,
                "net_return20": 3.0,
                "benchmark_return20": 1.0,
                "net_return60": 5.0,
                "benchmark_return60": 2.0,
                "entry_date": dates,
            })
        small = build_global_calibration(sample(80), min_samples=30)
        large = build_global_calibration(sample(500), min_samples=30)
        small_global = next(row for row in small if row["level"] == "global")
        large_global = next(row for row in large if row["level"] == "global")
        self.assertLess(float(small_global["confidence"]), float(large_global["confidence"]))
        self.assertLess(float(small_global["confidence"]), 1.0)
        self.assertLessEqual(float(large_global["confidence"]), 0.70)

    def test_wide_lifecycle_frame_does_not_emit_fragmentation_warning(self):
        base = {f"Filler{i}": [float(i)] for i in range(180)}
        base.update({
            "Ticker": ["000001.SZ"], "Score": [60.0], "FinalScore": [60.0],
            "InstitutionalScore": [60.0], "EntrySignal": ["WAIT_PULLBACK"],
            "QualityGate": [True], "QualityDataCompleteness": [1.0],
            "QualityApplicable": [True], "ScoreCoverage": [1.0],
            "DataTradingAgeDays": [0], "LifecycleStage": ["趋势确认"],
        })
        frame = pd.DataFrame(base)
        with warnings.catch_warnings():
            warnings.simplefilter("error", PerformanceWarning)
            result = finalize_signal_ranking(frame)
        self.assertEqual(len(result), 1)

    def test_gui_main_table_stays_decision_focused(self):
        self.assertLessEqual(len(gui.DISPLAY_COLUMNS), 22)
        self.assertIn("RankingEligibility", gui.DISPLAY_COLUMNS)
        self.assertIn("EntrySignal", gui.DISPLAY_COLUMNS)
        self.assertNotIn("GlobalCalibrationSamples", gui.DISPLAY_COLUMNS)


if __name__ == "__main__":
    unittest.main()
