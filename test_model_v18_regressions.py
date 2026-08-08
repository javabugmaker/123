from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from analytics import _sector_confirmation_factor
from classification import etf_theme_key
from fundamental_quality import get_quality
from model_calibration import calibration_details_for_frame
from report import _diversify_ranked_candidates
from scanner import _latest_atr_from_ohlc
from score import entry_point
from signal_lifecycle import finalize_signal_ranking


class ModelV18RegressionTests(unittest.TestCase):
    def test_quality_gate_does_not_rewrite_raw_entry_signal(self):
        frame = pd.DataFrame(
            [
                {
                    "Ticker": "000001.SZ",
                    "EntrySignal": "BUY_NOW",
                    "FinalScore": 72.0,
                    "InstitutionalScore": 72.0,
                    "Score": 70.0,
                    "QualityApplicable": True,
                    "QualityDataAvailable": True,
                    "QualityDataCompleteness": 1.0,
                    "QualityGate": False,
                    "QualityROE": False,
                    "ROE": 3.0,
                    "IsETF": False,
                    "AssetType": "stock",
                    "PassedFilters": True,
                    "SignalStatus": "ACTIVE",
                    "LifecycleStage": "趋势确认",
                    "ValueTrapRisk": 0.0,
                    "ScoreCoverage": 1.0,
                    "DataTradingAgeDays": 0,
                    "DataAgeDays": 0,
                    "SignalRecencyFactor": 1.0,
                    "SignalRecencyDays": 0,
                    "RSI14": 55.0,
                    "DistToLow52W": 15.0,
                    "DistToMA20": 1.0,
                    "RecentReturn20D": 3.0,
                    "ATRExpansion": 1.0,
                    "BacktestSamples": 0,
                    "BacktestEffectiveSamples": 0.0,
                    "BacktestScore": 50.0,
                    "FailureSignalFactor": 1.0,
                }
            ]
        )
        result = finalize_signal_ranking(frame)
        self.assertEqual(result.loc[0, "EntrySignal"], "BUY_NOW")
        self.assertEqual(result.loc[0, "DecisionState"], "OBSERVE")
        self.assertEqual(result.loc[0, "RankingEligibility"], "观察")
        self.assertIn("买入区间", result.loc[0, "OperationAdvice"])

    def test_etf_fundamentals_are_not_applicable_or_fake_complete(self):
        quality = get_quality("510050.SH", is_etf=True)
        self.assertFalse(quality.applicable)
        self.assertFalse(quality.data_available)
        self.assertEqual(quality.quality_data_completeness, 0.0)
        self.assertTrue(quality.quality_gate)

    def test_atr_expansion_has_ohlc_fallback(self):
        index = pd.date_range("2026-01-01", periods=80, freq="B")
        close = pd.Series(np.linspace(10.0, 12.0, len(index)), index=index)
        frame = pd.DataFrame(
            {
                "High": close + 0.25,
                "Low": close - 0.25,
                "Close": close,
            },
            index=index,
        )
        atr14 = _latest_atr_from_ohlc(frame, 14)
        atr50 = _latest_atr_from_ohlc(frame, 50)
        self.assertTrue(np.isfinite(atr14))
        self.assertTrue(np.isfinite(atr50))
        self.assertGreater(atr14 / atr50, 0.0)

    def test_relative_strength_rescues_strong_stock_in_weak_industry(self):
        weak_and_weak = _sector_confirmation_factor(-18.0, -10.0)
        weak_but_strong = _sector_confirmation_factor(-18.0, 22.0)
        self.assertGreater(weak_but_strong, weak_and_weak)
        self.assertGreaterEqual(weak_but_strong, 0.80)
        self.assertGreaterEqual(weak_and_weak, 0.72)

    def test_entry_zone_state_is_consistent_and_continuous(self):
        index = pd.date_range("2026-01-01", periods=60, freq="B")
        frame = pd.DataFrame(
            {
                "Close": 10.0,
                "High": 10.2,
                "Low": 9.8,
                "Volume": 1_000_000.0,
                "ATR14": 0.4,
                "MA20": 10.0,
                "MA50": 9.9,
                "RSI14": 55.0,
                "CMF": 0.1,
                "AD_Slope": 1.0,
                "OBV": np.arange(60, dtype=float),
            },
            index=index,
        )
        result = entry_point(
            frame,
            breakout=40.0,
            volume_score=20.0,
            value_trap_risk_value=0.0,
        )
        self.assertEqual(result["signal"], "BUY_NOW")
        self.assertEqual(result["zone_distance_atr"], 0.0)
        self.assertEqual(result["pullback_quality"], 100.0)
        self.assertFalse(result["signal"] == "WAIT_PULLBACK" and result["low"] <= 10.0 <= result["high"])

    def test_etf_theme_never_contains_literal_nan(self):
        theme = etf_theme_key(
            name="上证50ETF",
            industry=np.nan,
            sector=np.nan,
            ticker="510050.SH",
        )
        self.assertNotIn("NAN", theme.upper())
        self.assertTrue(theme)

    def test_research_pool_caps_repeated_stock_industry(self):
        frame = pd.DataFrame(
            [
                {
                    "Ticker": f"00000{i}.SZ",
                    "Name": f"股票{i}",
                    "IsETF": False,
                    "AssetType": "stock",
                    "Industry": "医疗器械" if i < 6 else f"行业{i}",
                    "Sector": "",
                    "ModelClassification": "医疗器械" if i < 6 else f"行业{i}",
                }
                for i in range(10)
            ]
        )
        selected = _diversify_ranked_candidates(
            frame,
            limit=6,
            max_per_theme=2,
            max_per_stock_industry=2,
        )
        self.assertLessEqual((selected["Industry"] == "医疗器械").sum(), 2)
        self.assertEqual(len(selected), 6)

    def test_calibration_provenance_returns_matching_level(self):
        frame = pd.DataFrame(
            [
                {
                    "AssetType": "stock",
                    "EntrySignal": "WAIT_PULLBACK",
                    "FinalScore": 62.0,
                    "BaseScore": 58.0,
                    "MarketRegime": "RISK_ON",
                }
            ]
        )
        rows = [
            {
                "level": "global",
                "calibration_score": 55.0,
                "confidence": 0.4,
            }
        ]
        details = calibration_details_for_frame(frame, rows)
        self.assertEqual(details.loc[0, "level"], "global")
        self.assertEqual(details.loc[0, "score"], 55.0)
        self.assertEqual(details.loc[0, "confidence"], 0.4)


if __name__ == "__main__":
    unittest.main()
