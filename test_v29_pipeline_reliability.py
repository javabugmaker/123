from __future__ import annotations

# Final connector-authored CI trigger; product behavior is covered below.
import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import analytics
import config
import daily_pipeline
import gui
import report


class V29PipelineReliabilityTests(unittest.TestCase):
    def test_pipeline_reliability_contract_survives_later_model_versions(self):
        self.assertRegex(config.SCORING_VERSION, r"-v(?:2[4-9]|[3-9][0-9]+)-")
        self.assertRegex(config.PIPELINE_VERSION, r"v(?:29|3[0-9])")

    def test_hybrid_run_does_not_pollute_per_ticker_mode(self):
        frame = pd.DataFrame(
            {
                "Ticker": ["A", "B", "C"],
                "BacktestMode": ["FAST", "", ""],
                "BacktestEngine": ["process", "", ""],
                "BacktestStage": ["FAST_SCREEN", "", ""],
                "BacktestEffectiveSamples": [1.0, np.nan, np.nan],
            }
        )
        summary = analytics.BacktestSummary(
            ticker_count=2, mode="hybrid", engine="process+exact:sequential"
        )
        summary.requested_tickers = ["A", "B"]
        result = analytics._apply_backtest_provenance(
            frame, summary, pd.Series([1.0, 0.0, 0.0])
        )
        self.assertTrue((result["BacktestRunMode"] == "HYBRID").all())
        self.assertEqual(result.loc[0, "BacktestMode"], "FAST")
        self.assertEqual(result.loc[1, "BacktestMode"], "FAST")
        self.assertEqual(result.loc[1, "BacktestStatus"], "NO_SIGNAL_SAMPLES")
        self.assertEqual(result.loc[2, "BacktestMode"], "NONE")
        self.assertEqual(result.loc[2, "BacktestStage"], "NOT_EVALUATED")
        self.assertEqual(result.loc[2, "BacktestStatus"], "SKIPPED")
        self.assertEqual(result.loc[1, "BacktestSamples"], 0)

    def test_decision_projection_is_lightweight_and_keeps_decision_fields(self):
        frame = pd.DataFrame(
            [
                {
                    "Ticker": "159915.SZ", "Name": "ETF", "IsETF": True,
                    "AssetType": "etf", "Industry": "", "Sector": "ETF",
                    "ModelClassification": "消费", "RankingScore": 42,
                    "EntrySignal": "WAIT_PULLBACK", "BacktestMode": "FAST",
                    "BacktestEligibleForRanking": False, "RunId": "run-1",
                }
            ]
        )
        projected = report._decision_projection(frame)
        # v37 adds evidence/research-integrity fields while keeping the GUI projection
        # far smaller than the 200+ column audit surface.
        self.assertLessEqual(len(projected.columns), 72)
        self.assertIn("EvidenceTier", projected.columns)
        self.assertIn("BacktestSkipReason", projected.columns)
        self.assertIn("RunId", projected.columns)
        self.assertEqual(projected.loc[0, "ETFTheme"], "消费")

    def test_transaction_rollback_restores_previous_canonical_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            old = output / "Top50Mixed.csv"
            old.write_text("old", encoding="utf-8")
            with patch.object(daily_pipeline, "OUTPUT_DIR", output):
                tx, existing = daily_pipeline._begin_transaction("test-run")
                old.write_text("new", encoding="utf-8")
                (output / "Top50Stocks.csv").write_text("new-stock", encoding="utf-8")
                daily_pipeline._rollback_transaction(tx, existing)
            self.assertEqual(old.read_text(encoding="utf-8"), "old")
            self.assertFalse((output / "Top50Stocks.csv").exists())

    def test_quality_gate_detects_collapsed_universe_and_stale_data(self):
        profile = {
            "rows": 100,
            "stocks": 80,
            "etfs": 20,
            "fresh_ratio": 0.50,
        }
        errors = daily_pipeline._quality_gate_errors(
            profile, {"universe": {"rows": 6000, "stocks": 5000, "etfs": 1000}},
            quality_gates=True,
        )
        joined = " ".join(errors)
        self.assertIn("低于安全下限", joined)
        self.assertIn("覆盖率", joined)
        self.assertIn("异常降至", joined)

    def test_csv_profile_tracks_run_id_asset_mix_and_freshness(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "AllResults.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=["Ticker", "AssetType", "IsETF", "DataAsOf", "RunId"],
                )
                writer.writeheader()
                writer.writerow({"Ticker": "000001.SZ", "AssetType": "stock", "DataAsOf": "2026-08-07", "RunId": "r1"})
                writer.writerow({"Ticker": "159915.SZ", "AssetType": "etf", "IsETF": True, "DataAsOf": "2026-08-07", "RunId": "r1"})
            profile = daily_pipeline._csv_profile(path, "2026-08-07")
        self.assertEqual(profile["rows"], 2)
        self.assertEqual(profile["stocks"], 1)
        self.assertEqual(profile["etfs"], 1)
        self.assertEqual(profile["fresh_ratio"], 1.0)
        self.assertEqual(profile["run_ids"], ["r1"])

    def test_gui_all_results_prefers_lightweight_decision_surface(self):
        self.assertEqual(gui.NAV_FILES["all"], "DecisionResults.csv")
        self.assertIn("BacktestEligibleForRanking", gui.COLUMN_NAMES)


if __name__ == "__main__":
    unittest.main()
