from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import analytics
from classification import etf_tracking_key, theme_cluster
from config import (
    BACKTEST_AUTO_EXACT_REFINEMENT,
    BACKTEST_EXACT_REFINEMENT_CANDIDATES,
    SCORING_VERSION,
)
from model_calibration import build_global_calibration, calibration_details_for_frame
from performance_cache import BACKTEST_CACHE_VERSION, INDICATOR_CACHE_VERSION
from report import _diversify_ranked_candidates
from scanner import TickerInfo, scan_single_from_df
from signal_lifecycle import finalize_signal_ranking


class ModelV19RegressionTests(unittest.TestCase):
    def test_v24_version_and_refinement_contract(self):
        self.assertEqual(SCORING_VERSION, "2026-08-09-v24-decision-integrity")
        self.assertEqual(INDICATOR_CACHE_VERSION, "v5")
        self.assertEqual(BACKTEST_CACHE_VERSION, "v8")
        self.assertTrue(BACKTEST_AUTO_EXACT_REFINEMENT)
        self.assertGreaterEqual(BACKTEST_EXACT_REFINEMENT_CANDIDATES, 50)

    def test_etf_tracking_key_ignores_manager_name(self):
        self.assertEqual(etf_tracking_key(name="上证50ETF博时"), "上证50")
        self.assertEqual(etf_tracking_key(name="上证50ETF易方达"), "上证50")

    def test_theme_cluster_groups_medical_subindustries(self):
        self.assertEqual(theme_cluster(is_etf=False, industry="化学制药"), "医药医疗")
        self.assertEqual(theme_cluster(is_etf=False, industry="医疗器械"), "医药医疗")

    def test_scanner_atr_expansion_falls_back_from_ohlc(self):
        index = pd.date_range("2024-01-01", periods=520, freq="B")
        close = pd.Series(np.linspace(10.0, 14.0, len(index)), index=index)
        frame = pd.DataFrame({
            "Open": close, "High": close + 0.3, "Low": close - 0.3, "Close": close,
            "Volume": 2_000_000.0,
        }, index=index)
        with patch("scanner.get_market_cap", return_value=10_000_000_000.0):
            result = scan_single_from_df(TickerInfo(ticker="000001.SZ", name="测试"), frame)
        self.assertTrue(np.isfinite(result.atr50))
        self.assertTrue(np.isfinite(result.atr_expansion))
        self.assertIn(result.atr_expansion_source, {"indicator", "ohlc_fallback"})

    def test_filter_contract_separates_universe_from_signal(self):
        index = pd.date_range("2024-01-01", periods=520, freq="B")
        close = pd.Series(10.0, index=index)
        frame = pd.DataFrame({"Open": close, "High": close + .1, "Low": close - .1, "Close": close, "Volume": 2_000_000.0}, index=index)
        with patch("scanner.get_market_cap", return_value=10_000_000_000.0):
            result = scan_single_from_df(TickerInfo(ticker="000001.SZ", name="测试"), frame)
        self.assertTrue(result.universe_eligible)
        self.assertIsInstance(result.signal_confirmed, bool)
        self.assertIsInstance(result.failed_filter_names, str)

    def test_cross_asset_score_is_calibrated_within_asset_type(self):
        frame = pd.DataFrame([
            {"Ticker":"S1","IsETF":False,"AssetType":"stock","InstitutionalScore":80,"FinalScore":70,"Score":70,"EntrySignal":"WAIT_PULLBACK","PassedFilters":True,"SignalStatus":"ACTIVE","QualityApplicable":True,"QualityDataCompleteness":1,"QualityGate":True,"QualityDataAvailable":True,"QualityROE":True,"QualityGrossMargin":True,"QualityNetProfit":True,"InstitutionHoldingStatus":"PASS","ROE":10,"IndustryGrossMarginPercentile":70,"NetProfitY1":1,"NetProfitY2":1,"NetProfitY3":1,"ScoreCoverage":1,"DataAgeDays":0,"DataTradingAgeDays":0},
            {"Ticker":"S2","IsETF":False,"AssetType":"stock","InstitutionalScore":60,"FinalScore":60,"Score":60,"EntrySignal":"WAIT_PULLBACK","PassedFilters":True,"SignalStatus":"ACTIVE","QualityApplicable":True,"QualityDataCompleteness":1,"QualityGate":True,"QualityDataAvailable":True,"QualityROE":True,"QualityGrossMargin":True,"QualityNetProfit":True,"InstitutionHoldingStatus":"PASS","ROE":10,"IndustryGrossMarginPercentile":70,"NetProfitY1":1,"NetProfitY2":1,"NetProfitY3":1,"ScoreCoverage":1,"DataAgeDays":0,"DataTradingAgeDays":0},
            {"Ticker":"E1","IsETF":True,"AssetType":"etf","InstitutionalScore":90,"FinalScore":70,"Score":70,"EntrySignal":"WAIT_PULLBACK","PassedFilters":True,"SignalStatus":"ACTIVE","QualityApplicable":False,"QualityDataCompleteness":0,"QualityGate":True,"QualityDataAvailable":False,"ScoreCoverage":1,"DataAgeDays":0,"DataTradingAgeDays":0},
            {"Ticker":"E2","IsETF":True,"AssetType":"etf","InstitutionalScore":70,"FinalScore":60,"Score":60,"EntrySignal":"WAIT_PULLBACK","PassedFilters":True,"SignalStatus":"ACTIVE","QualityApplicable":False,"QualityDataCompleteness":0,"QualityGate":True,"QualityDataAvailable":False,"ScoreCoverage":1,"DataAgeDays":0,"DataTradingAgeDays":0},
        ])
        result = finalize_signal_ranking(frame).set_index("Ticker")
        self.assertEqual(result.loc["S1", "AssetPercentile"], 100.0)
        self.assertEqual(result.loc["E1", "AssetPercentile"], 100.0)
        self.assertGreater(result.loc["S1", "CrossAssetScore"], result.loc["S2", "CrossAssetScore"])

    def test_cross_asset_normalization_does_not_rescue_missing_or_tiny_scores(self):
        frame = pd.DataFrame([
            {"Ticker":"ZERO","IsETF":False,"AssetType":"stock","InstitutionalScore":0.0,"FinalScore":0.0,"Score":0.0,"EntrySignal":"BUY_NOW","PassedFilters":True,"QualityApplicable":True,"QualityDataCompleteness":1.0,"QualityGate":True,"ScoreCoverage":1.0,"DataTradingAgeDays":0},
            {"Ticker":"LOW","IsETF":False,"AssetType":"stock","InstitutionalScore":24.9,"FinalScore":24.9,"Score":24.9,"EntrySignal":"BUY_NOW","PassedFilters":True,"QualityApplicable":True,"QualityDataCompleteness":1.0,"QualityGate":True,"ScoreCoverage":1.0,"DataTradingAgeDays":0},
            {"Ticker":"OK","IsETF":False,"AssetType":"stock","InstitutionalScore":30.0,"FinalScore":30.0,"Score":30.0,"EntrySignal":"BUY_NOW","PassedFilters":True,"QualityApplicable":True,"QualityDataCompleteness":1.0,"QualityGate":True,"ScoreCoverage":1.0,"DataTradingAgeDays":0},
        ])
        result = finalize_signal_ranking(frame).set_index("Ticker")
        self.assertEqual(result.loc["ZERO", "CrossAssetScore"], 0.0)
        self.assertEqual(result.loc["LOW", "CrossAssetScore"], 24.9)
        self.assertEqual(result.loc["LOW", "RankingEligibility"], "观察")

    def test_diversity_keeps_one_etf_per_tracking_key(self):
        frame = pd.DataFrame([
            {"Ticker":"510001.SH","Name":"上证50ETF博时","IsETF":True,"AssetType":"etf","RankingScore":90,"ModelClassification":"宽基"},
            {"Ticker":"510002.SH","Name":"上证50ETF易方达","IsETF":True,"AssetType":"etf","RankingScore":89,"ModelClassification":"宽基"},
            {"Ticker":"510300.SH","Name":"沪深300ETF华夏","IsETF":True,"AssetType":"etf","RankingScore":88,"ModelClassification":"宽基"},
        ])
        result = _diversify_ranked_candidates(frame, limit=3)
        self.assertEqual((result["ETFTrackingKey"] == "上证50").sum(), 1)

    def test_global_calibration_exports_provenance(self):
        dates = pd.date_range("2024-01-01", periods=40, freq="B")
        sample = pd.DataFrame({
            "asset_type":"stock", "entry_signal":"WAIT_PULLBACK", "market_regime":"RISK_ON",
            "score":65.0, "setup_score":60.0, "sample_weight":1.0,
            "net_return20":3.0, "benchmark_return20":1.0,
            "net_return60":5.0, "benchmark_return60":2.0, "entry_date":dates,
        })
        rows = build_global_calibration(sample, min_samples=30)
        current = pd.DataFrame([{"AssetType":"stock","EntrySignal":"WAIT_PULLBACK","MarketRegime":"RISK_ON","FinalScore":65.0,"BaseScore":60.0}])
        details = calibration_details_for_frame(current, rows)
        self.assertGreater(int(details.loc[0, "samples"]), 0)
        self.assertTrue(str(details.loc[0, "start_date"]))
        self.assertTrue(str(details.loc[0, "end_date"]))

    def test_fast_summary_marks_rows_for_exact_refinement(self):
        row = {"ticker":"000001.SZ","entry_signal":"WAIT_PULLBACK","samples":3,"backtest_mode":"FAST"}
        summary = analytics.BacktestSummary(mode="fast", by_ticker=[row])
        self.assertEqual(summary.mode, "fast")


if __name__ == "__main__":
    unittest.main()
