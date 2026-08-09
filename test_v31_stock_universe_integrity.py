from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import daily_pipeline
import downloader
import scanner
from downloader import TickerInfo
from filters import AllFilterResults, FilterResult
from score import ScoreBreakdown


class V31StockUniverseIntegrityTests(unittest.TestCase):
    def test_small_tickflow_share_metadata_is_normalized_from_10k_scale(self):
        self.assertEqual(downloader._normalize_cn_share_count(500_000), 5_000_000_000)
        self.assertEqual(downloader._normalize_cn_share_count(50_000_000), 50_000_000)
        self.assertIsNone(downloader._normalize_cn_share_count(None))

    def test_ticker_info_from_meta_normalizes_total_and_float_shares(self):
        info = downloader._ticker_info_from_meta(
            "000001.SZ",
            {"name": "测试银行", "ext": {"total_shares": 500_000, "float_shares": 450_000}},
            False,
        )
        self.assertEqual(info.total_shares, 5_000_000_000)
        self.assertEqual(info.float_shares, 4_500_000_000)

    def test_market_cap_provider_failure_is_not_a_scan_error(self):
        index = pd.date_range("2024-01-01", periods=260, freq="B")
        frame = pd.DataFrame(
            {
                "Open": 10.0,
                "High": 10.5,
                "Low": 9.5,
                "Close": 10.0,
                "Volume": 1_000_000.0,
                "MA20": 10.0,
                "MA50": 10.0,
                "ATR14": 0.5,
                "ATR50": 0.6,
                "RSI14": 50.0,
                "OBV": np.arange(260, dtype=float),
                "CMF": 0.1,
                "AD": np.arange(260, dtype=float),
                "DistToLow52W": 10.0,
                "WyckoffPhase": "ACCUMULATION",
            },
            index=index,
        )
        filters = AllFilterResults(
            min_price=FilterResult(True),
            min_volume=FilterResult(True),
            min_market_cap=FilterResult(False, "市值数据不可用"),
            sufficient_history=FilterResult(True),
            bear_market=FilterResult(False),
            consolidation=FilterResult(True),
            volume_accumulation=FilterResult(True, details={"consecutive_days": 20}),
            obv_divergence=FilterResult(True),
            cmf_positive=FilterResult(True, details={"cmf_improving": True}),
            ad_slope=FilterResult(True),
            volatility_contraction=FilterResult(True),
        )
        score = ScoreBreakdown(total=50.0, trend=10.0, volume=10.0, accumulation=10.0, volatility=10.0, structure=10.0)
        with patch.object(scanner, "get_market_cap", side_effect=OSError("metadata unavailable")), \
             patch.object(scanner, "run_all_filters", return_value=filters), \
             patch.object(scanner, "score_ticker", return_value=score), \
             patch.object(scanner, "classify_style", return_value="均衡"), \
             patch.object(scanner, "get_quality") as quality, \
             patch.object(scanner, "entry_point", return_value={
                 "low": 9.5, "high": 10.0, "score": 50.0, "signal": "WAIT_PULLBACK",
                 "breakout": 10.5, "stop": 9.0, "zone_distance_pct": 0.0,
                 "zone_distance_atr": 0.0, "pullback_quality": 50.0,
                 "volume_ratio": 1.0, "volume_confirmed": False,
                 "flow_confirmed": False, "price_breakout": False,
             }), \
             patch.object(scanner, "smart_money_stage", return_value="ACCUMULATION"):
            quality.return_value = type("Q", (), {
                "industry": "", "roe": np.nan, "gross_margin": np.nan,
                "institution_holding_trend": None, "institution_holding_periods": np.nan,
                "net_profit_y1": np.nan, "net_profit_y2": np.nan, "net_profit_y3": np.nan,
                "industry_gross_margin_percentile": np.nan, "roe_factor": False,
                "gross_margin_factor": False, "institution_holding_factor": False,
                "net_profit_factor": False, "quality_score": np.nan, "quality_gate": True,
                "quality_reason": "missing", "data_available": False, "applicable": True,
                "institution_holding_status": "UNKNOWN", "quality_data_completeness": 0.0,
                "quality_gate_reason": "missing", "quality_multiplier": 0.95,
            })()
            result = scanner.scan_single_from_df(
                TickerInfo(ticker="000001.SZ", name="测试", asset_type="stock"),
                frame,
                indicators_computed=True,
            )
        self.assertEqual(result.error, "")
        self.assertFalse(result.filter_details["market_cap_available"])
        self.assertFalse(result.filter_details["min_market_cap"])

    def test_csv_profile_counts_valid_stock_rows_separately(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AllResults.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=["Ticker", "AssetType", "IsETF", "DataAsOf", "RunId", "Error"])
                writer.writeheader()
                writer.writerow({"Ticker": "000001.SZ", "AssetType": "stock", "IsETF": False, "DataAsOf": "2026-08-07", "RunId": "r", "Error": ""})
                writer.writerow({"Ticker": "000002.SZ", "AssetType": "stock", "IsETF": False, "DataAsOf": "2026-08-07", "RunId": "r", "Error": "市值错误"})
                writer.writerow({"Ticker": "510300.SH", "AssetType": "etf", "IsETF": True, "DataAsOf": "2026-08-07", "RunId": "r", "Error": ""})
            profile = daily_pipeline._csv_profile(path, "2026-08-07")
        self.assertEqual(profile["stocks"], 2)
        self.assertEqual(profile["valid_stocks"], 1)
        self.assertEqual(profile["valid_etfs"], 1)
        self.assertEqual(profile["error_rows"], 1)
        self.assertEqual(profile["valid_stock_ratio"], 0.5)

    def test_quality_gate_rejects_stock_validity_collapse(self):
        profile = {
            "rows": 6800, "stocks": 5300, "etfs": 1500,
            "valid_rows": 1501, "valid_stocks": 1, "valid_etfs": 1500,
            "valid_stock_ratio": 1 / 5300, "valid_etf_ratio": 1.0,
            "fresh_ratio": 1.0,
        }
        errors = daily_pipeline._quality_gate_errors(profile, {}, quality_gates=True)
        self.assertTrue(any("有效股票仅 1/5300" in item for item in errors))
        self.assertTrue(any("股票有效率" in item for item in errors))

    def test_final_gate_requires_full_stock_top50_when_pool_is_large(self):
        scan_profile = {
            "run_ids": ["r"], "valid_rows": 6800, "valid_stocks": 5300,
            "valid_etfs": 1500,
        }
        profiles = {
            "Top50Mixed.csv": {"rows": 50, "fresh_ratio": 1.0, "run_ids": ["r"]},
            "Top50Stocks.csv": {"rows": 1, "fresh_ratio": 1.0, "run_ids": ["r"]},
            "Top50ETF.csv": {"rows": 50, "fresh_ratio": 1.0, "run_ids": ["r"]},
        }
        errors = daily_pipeline._final_output_errors(scan_profile, profiles, quality_gates=True)
        self.assertTrue(any("Top50Stocks.csv 仅 1 条" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
