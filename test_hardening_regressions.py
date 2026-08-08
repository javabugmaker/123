from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import analytics
import downloader
import score
import signal_lifecycle
from fundamental_quality import calculate_quality


class HardeningRegressionTests(unittest.TestCase):
    def test_raw_beijing_exchange_code_is_normalized_to_bj(self) -> None:
        self.assertEqual(downloader.normalize_ticker("920001"), "920001.BJ")
        self.assertEqual(downloader.normalize_ticker("830001"), "830001.BJ")

    def test_market_source_is_tickflow_only_with_legacy_alias_migration(self) -> None:
        for source in ("tickflow", "auto", "akshare", "eastmoney", "sina", "tencent"):
            self.assertEqual(downloader.normalize_data_source(source), "tickflow")
        with self.assertRaises(ValueError):
            downloader.normalize_data_source("unknown-provider")

    def test_tickflow_cache_schema_invalidates_all_legacy_market_caches(self) -> None:
        path = downloader._cache_path("600000.SH", "eastmoney")
        self.assertEqual(path.parent.name, "v3-tickflow-forward")
        self.assertEqual(path.name, "600000.SH.parquet")

    def test_sparse_fundamental_quality_is_shrunk_toward_neutral(self) -> None:
        quality = calculate_quality(
            {
                "Ticker": "000001.SZ",
                "ROE": 12.0,
                "GrossMargin": np.nan,
                "InstitutionHoldingTrend": "unknown",
                "InstitutionHoldingPeriods": 0,
                "NetProfitY1": np.nan,
                "NetProfitY2": np.nan,
                "NetProfitY3": np.nan,
                "IndustryGrossMarginPercentile": np.nan,
            }
        )
        self.assertEqual(quality.quality_data_completeness, 0.25)
        self.assertAlmostEqual(quality.quality_score, 52.5)
        self.assertLess(quality.quality_score, 100.0)

    def test_score_coverage_caps_final_score(self) -> None:
        frame = pd.DataFrame({"Close": [10.0] * 300})
        fake_entry = {
            "score": 100.0,
            "signal": "BUY_NOW",
            "low": 9.5,
            "high": 10.0,
            "breakout": 10.5,
            "stop": 9.0,
            "volume_ratio": 2.0,
            "volume_confirmed": True,
            "flow_confirmed": True,
            "price_breakout": False,
        }
        with patch.object(
            score, "_score_dimensions_available", return_value=(True, True, True, False, False)
        ), patch.object(score, "score_trend", return_value=20.0), patch.object(
            score, "score_volume", return_value=25.0
        ), patch.object(score, "score_accumulation", return_value=25.0), patch.object(
            score, "classify_style", return_value="均衡"
        ), patch.object(score, "value_trap_risk", return_value=0.0), patch.object(
            score, "breakout_score", return_value=100.0
        ), patch.object(score, "entry_point", return_value=fake_entry):
            result = score.score_ticker(frame)

        self.assertAlmostEqual(result.indicator_coverage, 0.6)
        self.assertLessEqual(result.final_score, 76.0)

    def test_backtest_rows_are_calibrated_by_real_entry_signal(self) -> None:
        frame = pd.DataFrame(
            {
                "ticker": ["000001.SZ"] * 4,
                "entry_signal": [
                    "BUY_NOW",
                    "BUY_NOW",
                    "WAIT_PULLBACK",
                    "WAIT_PULLBACK",
                ],
                "signal_date": [
                    "2025-01-01",
                    "2025-03-01",
                    "2025-05-01",
                    "2025-07-01",
                ],
                "return20": [5.0, 3.0, -1.0, 2.0],
                "return60": [8.0, 6.0, -2.0, 4.0],
                "benchmark_return20": [1.0, 1.0, 1.0, 1.0],
                "benchmark_return60": [2.0, 2.0, 2.0, 2.0],
                "net_return20": [4.7, 2.7, -1.3, 1.7],
                "net_return60": [7.7, 5.7, -2.3, 3.7],
                "drawdown20": [-2.0, -1.0, -4.0, -2.0],
                "drawdown60": [-3.0, -2.0, -6.0, -3.0],
                "sample_weight": [1.0, 1.0, 1.0, 1.0],
            }
        )
        rows = analytics._ticker_backtest_rows(frame, "net_excess_return_20d")
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row["entry_signal"] for row in rows},
            {"BUY_NOW", "WAIT_PULLBACK"},
        )

    def test_recency_changes_ranking_not_institutional_score(self) -> None:
        frame = pd.DataFrame(
            {
                "Ticker": ["000001.SZ", "000002.SZ"],
                "Score": [50.0, 50.0],
                "FinalScore": [50.0, 50.0],
                "InstitutionalScore": [50.0, 50.0],
                "EntrySignal": ["HOLD_WAIT", "HOLD_WAIT"],
                "SignalRecencyFactor": [1.0, 0.7],
                "SignalRecencyDays": [0, 30],
                "ValueTrapRisk": [0.0, 0.0],
                "LifecycleStage": ["观察", "观察"],
                "ScoreCoverage": [1.0, 1.0],
                "QualityDataCompleteness": [1.0, 1.0],
                "QualityDataAvailable": [False, False],
                "QualityGate": [True, True],
                "DataTradingAgeDays": [0, 0],
                "BacktestSamples": [0, 0],
                "BacktestEffectiveSamples": [0.0, 0.0],
                "BacktestReturnStd20D": [np.nan, np.nan],
                "RSI14": [50.0, 50.0],
                "DistToLow52W": [20.0, 20.0],
                "DistToMA20": [0.0, 0.0],
                "RecentReturn20D": [0.0, 0.0],
                "ATRExpansion": [1.0, 1.0],
            }
        )
        result = signal_lifecycle.finalize_signal_ranking(frame)
        self.assertTrue((result["InstitutionalScore"] == 50.0).all())
        scores = result.set_index("Ticker")["RankingScore"]
        self.assertGreater(scores["000001.SZ"], scores["000002.SZ"])


if __name__ == "__main__":
    unittest.main()
