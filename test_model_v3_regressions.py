from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from downloader import TickerInfo
from fundamental_quality import calculate_quality
from model_calibration import build_global_calibration, resolve_global_calibration
from scanner import scan_single_from_df
from score import entry_point
from signal_lifecycle import finalize_signal_ranking


class ModelV3RegressionTests(unittest.TestCase):
    def test_quality_score_is_continuous_and_preserves_industry(self):
        a = calculate_quality({
            "Ticker": "600000.SH", "Industry": "银行", "ROE": 11.0,
            "GrossMargin": 30.0, "IndustryGrossMarginPercentile": 0.25,
            "InstitutionHoldingTrend": "increasing", "InstitutionHoldingPeriods": 2,
            "NetProfitY1": 110.0, "NetProfitY2": 100.0, "NetProfitY3": 95.0,
        })
        b = calculate_quality({
            "Ticker": "600001.SH", "Industry": "银行", "ROE": 17.0,
            "GrossMargin": 35.0, "IndustryGrossMarginPercentile": 0.10,
            "InstitutionHoldingTrend": "increasing", "InstitutionHoldingPeriods": 2,
            "NetProfitY1": 130.0, "NetProfitY2": 105.0, "NetProfitY3": 95.0,
        })
        self.assertEqual(a.industry, "银行")
        self.assertNotEqual(a.quality_score, b.quality_score)
        self.assertGreater(b.quality_score, a.quality_score)

    def test_scanner_fills_industry_from_fundamentals_when_tickflow_metadata_is_blank(self):
        index = pd.date_range("2024-01-01", periods=320, freq="B")
        close = np.linspace(10.0, 12.0, len(index))
        frame = pd.DataFrame({
            "Open": close, "High": close * 1.01, "Low": close * 0.99,
            "Close": close, "Volume": np.full(len(index), 1_000_000.0),
        }, index=index)
        quality = calculate_quality({
            "Ticker": "600000.SH", "Industry": "银行", "ROE": 12.0,
            "GrossMargin": 30.0, "IndustryGrossMarginPercentile": 0.20,
            "InstitutionHoldingTrend": "increasing", "InstitutionHoldingPeriods": 2,
            "NetProfitY1": 110.0, "NetProfitY2": 100.0, "NetProfitY3": 90.0,
        })
        with patch("scanner.get_quality", return_value=quality), patch("scanner.get_market_cap", return_value=1e10):
            result = scan_single_from_df(TickerInfo("600000.SH", name="测试"), frame)
        self.assertEqual(result.industry, "银行")
        self.assertEqual(result.sector, "银行")

    def test_wait_pullback_is_above_its_entry_zone(self):
        index = pd.date_range("2024-01-01", periods=260, freq="B")
        close = np.linspace(10.0, 15.0, len(index))
        frame = pd.DataFrame(index=index)
        frame["Close"] = close
        frame["High"] = close * 1.01
        frame["Low"] = close * 0.99
        frame["Volume"] = 1_000_000.0
        frame["ATR14"] = 0.35
        frame["MA20"] = pd.Series(close, index=index).rolling(20).mean()
        frame["MA50"] = pd.Series(close, index=index).rolling(50).mean()
        frame["RSI14"] = 60.0
        frame["CMF"] = 0.05
        frame["AD_Slope"] = 1.0
        result = entry_point(frame, breakout=50.0, volume_score=10.0, value_trap_risk_value=10.0)
        if result["signal"] == "WAIT_PULLBACK":
            self.assertGreater(float(frame["Close"].iloc[-1]), float(result["high"]))

    def test_failed_lifecycle_cannot_be_recommended(self):
        frame = pd.DataFrame([{
            "Ticker": "600000.SH", "Score": 80.0, "FinalScore": 80.0,
            "InstitutionalScore": 80.0, "EntrySignal": "BREAKOUT_CONFIRM",
            "BreakoutVolumeConfirmed": True, "BreakoutFlowConfirmed": True,
            "PassedFilters": True, "SignalStatus": "FAILED",
            "LifecycleStage": "趋势确认", "QualityGate": True,
            "QualityDataAvailable": True, "QualityDataCompleteness": 1.0,
            "ROE": 15.0, "QualityROE": True,
            "IndustryGrossMarginPercentile": 0.2, "QualityGrossMargin": True,
            "NetProfitY1": 3.0, "NetProfitY2": 2.0, "NetProfitY3": 1.0,
            "QualityNetProfit": True, "InstitutionHoldingStatus": "PASS",
            "InstitutionHoldingPeriods": 2, "DataTradingAgeDays": 0,
            "ScoreCoverage": 1.0, "RSI14": 60.0, "DistToLow52W": 10.0,
            "DistToMA20": 1.0, "RecentReturn20D": 5.0, "ATRExpansion": 1.0,
        }])
        ranked = finalize_signal_ranking(frame)
        self.assertNotEqual(ranked.loc[0, "RankingEligibility"], "推荐")

    def test_global_calibration_uses_regime_and_setup_peer_group(self):
        rows = []
        dates = pd.date_range("2020-01-01", periods=80, freq="B")
        for index, date in enumerate(dates):
            regime = "RISK_ON" if index < 40 else "RISK_OFF"
            excess = 5.0 if regime == "RISK_ON" else -4.0
            rows.append({
                "ticker": f"{index:06d}.SH", "asset_type": "stock",
                "entry_signal": "BREAKOUT_CONFIRM", "market_regime": regime,
                "score": 65.0, "setup_score": 60.0, "sample_weight": 1.0,
                "net_return20": excess, "benchmark_return20": 0.0,
                "net_return60": excess, "benchmark_return60": 0.0,
                "entry_date": date,
            })
        calibration = build_global_calibration(pd.DataFrame(rows), min_samples=20)
        on_score, on_conf, on_level = resolve_global_calibration(
            "stock", "BREAKOUT_CONFIRM", 65.0, calibration,
            market_regime="风险偏好", setup_score=60.0,
        )
        off_score, off_conf, off_level = resolve_global_calibration(
            "stock", "BREAKOUT_CONFIRM", 65.0, calibration,
            market_regime="风险规避", setup_score=60.0,
        )
        self.assertGreater(on_conf, 0.0)
        self.assertGreater(off_conf, 0.0)
        self.assertGreater(on_score, off_score)
        self.assertIn("regime", on_level)
        self.assertIn("regime", off_level)


if __name__ == "__main__":
    unittest.main()
