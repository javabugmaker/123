from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

import analytics
import config
import gui
import model_calibration
import report
import scan_service
import scanner

# Keep this suite as the v30 performance/workstation contract exercised by PR CI.


class V30PerformanceWorkstationTests(unittest.TestCase):
    def test_engineering_contract_survives_later_scoring_versions(self):
        self.assertRegex(config.SCORING_VERSION, r"-v(?:2[4-9]|[3-9][0-9]+)-")
        self.assertTrue(any(f"v{version}" in config.PIPELINE_VERSION for version in range(30, 100)))
        self.assertTrue(
            any(f"v{version}" in config.GUI_VERSION for version in range(30, 100))
        )

    def test_calibration_details_uses_indexed_lookup_not_rowwise_resolver(self):
        rows = [
            {
                "level": "asset_signal",
                "asset_type": "etf",
                "entry_signal": "WAIT_PULLBACK",
                "calibration_score": 63.0,
                "confidence": 0.5,
                "samples": 40,
                "effective_samples": 20.0,
                "mean_net_excess20": 1.2,
                "win_rate_net_excess20": 0.6,
                "start_date": "2025-01-01",
                "end_date": "2026-01-01",
            },
            {
                "level": "global",
                "calibration_score": 48.0,
                "confidence": 0.2,
                "samples": 100,
                "effective_samples": 70.0,
            },
        ]
        frame = pd.DataFrame(
            {
                "AssetType": ["etf", "stock"],
                "EntrySignal": ["WAIT_PULLBACK", "BUY_NOW"],
                "FinalScore": [60.0, 55.0],
                "BaseScore": [58.0, 52.0],
                "MarketRegime": ["震荡", "震荡"],
            }
        )
        with patch.object(
            model_calibration,
            "resolve_global_calibration",
            side_effect=AssertionError("rowwise resolver should not be called"),
        ):
            details = model_calibration.calibration_details_for_frame(frame, rows)
        self.assertEqual(details.loc[0, "level"], "asset_signal")
        self.assertEqual(details.loc[0, "score"], 63.0)
        self.assertEqual(details.loc[0, "samples"], 40)
        self.assertEqual(details.loc[1, "level"], "global")
        self.assertEqual(details.loc[1, "score"], 48.0)

    def test_diversity_preparation_preserves_complete_cached_classification(self):
        frame = pd.DataFrame(
            [
                {
                    "Ticker": "159915.SZ",
                    "Name": "消费ETF",
                    "AssetType": "etf",
                    "IsETF": True,
                    "ETFTheme": "消费",
                    "ETFTrackingKey": "消费ETF",
                    "ThemeCluster": "消费",
                    "RankingScore": 50.0,
                }
            ]
        )
        with patch.object(report, "_etf_theme_key", side_effect=AssertionError("should reuse theme")):
            prepared = report._ensure_diversity_columns(frame)
        self.assertEqual(prepared.loc[0, "ETFTheme"], "消费")
        self.assertEqual(prepared.loc[0, "ETFTrackingKey"], "消费ETF")
        self.assertEqual(prepared.loc[0, "ThemeCluster"], "消费")

    def test_decision_projection_discards_wide_research_columns_first(self):
        row = {
            "Ticker": "000001.SZ",
            "Name": "测试",
            "AssetType": "stock",
            "Industry": "银行",
            "RankingScore": 42.0,
        }
        row.update({f"ResearchJunk{i}": i for i in range(300)})
        projected = report._decision_projection(pd.DataFrame([row]))
        self.assertEqual(tuple(projected.columns), report.DECISION_RESULT_COLUMNS)
        self.assertNotIn("ResearchJunk1", projected.columns)

    def test_scan_reports_expose_stage_timings(self):
        scan_report = scanner.ScanReport(
            download_seconds=1.0,
            analysis_seconds=2.0,
            enrichment_seconds=3.0,
        )
        result = scan_service.ScanExecutionResult(
            report=scan_report,
            top_csv=report.OUTPUT_DIR / "Top50.csv",
            top_parquet=report.OUTPUT_DIR / "Top200.parquet",
            full_csv=report.OUTPUT_DIR / "AllResults.csv",
            full_parquet=report.OUTPUT_DIR / "AllResults.parquet",
            stock_count=1,
            etf_count=1,
            prepare_seconds=0.1,
            fundamentals_seconds=0.2,
            scan_seconds=6.0,
            export_seconds=0.3,
            elapsed_seconds=6.6,
        )
        self.assertEqual(result.scan_seconds, 6.0)
        self.assertEqual(result.report.analysis_seconds, 2.0)

    def test_backtest_summary_has_postprocess_observability(self):
        summary = analytics.BacktestSummary()
        self.assertEqual(summary.calibration_lookup_elapsed_seconds, 0.0)
        self.assertEqual(summary.ranking_compute_elapsed_seconds, 0.0)
        self.assertEqual(summary.persistence_elapsed_seconds, 0.0)
        self.assertEqual(summary.postprocess_elapsed_seconds, 0.0)

    def test_gui_has_collapsible_detail_and_performance_dialog(self):
        self.assertTrue(hasattr(gui.DecisionScannerGUI, "_toggle_detail_panel"))
        self.assertTrue(hasattr(gui.DecisionScannerGUI, "_show_run_performance"))
        self.assertEqual(gui._duration_label(95), "1m35s")


if __name__ == "__main__":
    unittest.main()
