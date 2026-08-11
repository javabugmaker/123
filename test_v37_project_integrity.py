from __future__ import annotations

# Connector-authored trigger so the validated bot commit receives normal push CI.
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import config
import daily_pipeline
from classification import etf_research_eligibility
from evidence import enrich_evidence_fields
from report import _rank_valid_candidates


class V37ProjectIntegrityTests(unittest.TestCase):
    def test_cash_management_etf_is_excluded_but_cashflow_factor_remains(self) -> None:
        excluded, reason = etf_research_eligibility(
            is_etf=True,
            name="快钱ETF汇添富",
            classification="货币现金管理",
            ticker="159005.SZ",
        )
        self.assertFalse(excluded)
        self.assertIn("排除", reason)
        eligible, _ = etf_research_eligibility(
            is_etf=True,
            name="现金流ETF",
            classification="现金流因子",
            ticker="560000.SH",
        )
        self.assertTrue(eligible)

    def test_candidate_rank_omits_cash_management_etf(self) -> None:
        frame = pd.DataFrame(
            {
                "Ticker": ["159005.SZ", "560000.SH", "000001.SZ"],
                "Name": ["快钱ETF汇添富", "现金流ETF", "平安银行"],
                "IsETF": [True, True, False],
                "AssetType": ["etf", "etf", "stock"],
                "ModelClassification": ["货币现金管理", "现金流因子", "银行"],
                "RankingScore": [99.0, 60.0, 55.0],
                "InstitutionalScore": [50.0, 40.0, 38.0],
                "Error": ["", "", ""],
            }
        )
        ranked = _rank_valid_candidates(frame)
        self.assertNotIn("159005.SZ", set(ranked["Ticker"]))
        self.assertIn("560000.SH", set(ranked["Ticker"]))
        self.assertIn("000001.SZ", set(ranked["Ticker"]))
        self.assertTrue(ranked["ResearchEligible"].all())

    def test_evidence_strength_uses_ticker_and_peer_coverage_without_ranking(self) -> None:
        frame = pd.DataFrame(
            {
                "Ticker": ["A", "B"],
                "RankingScore": [77.0, 66.0],
                "BacktestMode": ["FAST", "FAST"],
                "BacktestSamples": [0, 20],
                "BacktestEffectiveSamples": [0.0, 20.0],
                "BacktestConfidenceTier": ["样本不足", "中可信度"],
                "GlobalCalibrationSamples": [10000, 10000],
                "GlobalCalibrationEffectiveSamples": [1000.0, 1000.0],
                "GlobalCalibrationConfidence": [1.0, 1.0],
                "GlobalCalibrationLevel": ["asset_signal", "asset_signal"],
            }
        )
        enriched = enrich_evidence_fields(frame)
        self.assertEqual(list(enriched["RankingScore"]), [77.0, 66.0])
        self.assertGreater(float(enriched.loc[0, "EvidenceStrengthScore"]), 50.0)
        self.assertGreater(
            float(enriched.loc[1, "EvidenceStrengthScore"]),
            float(enriched.loc[0, "EvidenceStrengthScore"]),
        )
        self.assertIn(enriched.loc[0, "EvidenceTier"], {"中高", "高"})

    def test_cache_health_distinguishes_cold_start_and_persistent_miss(self) -> None:
        cold = daily_pipeline._cache_health(
            {"pipeline_version": "old", "backtest": {"cache_hit_rate": 0.9}},
            0.0,
            6000,
        )
        self.assertEqual(cold["status"], "冷启动")
        warm = daily_pipeline._cache_health(
            {"pipeline_version": config.PIPELINE_VERSION, "backtest": {"cache_hit_rate": 0.8}},
            0.1,
            6000,
        )
        self.assertEqual(warm["status"], "异常偏低")
        self.assertTrue(warm["warning"])

    def test_run_archive_is_immutable_and_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output / "Top50Mixed.csv").write_text("Ticker\n000001.SZ\n", encoding="utf-8")
            with patch.object(daily_pipeline, "OUTPUT_DIR", output):
                run_dir = daily_pipeline._archive_run(
                    "run-1",
                    {"data_run_id": "data-1", "expected_trading_date": "2026-08-11"},
                )
                manifest = json.loads((run_dir / "RunManifest.json").read_text(encoding="utf-8"))
                self.assertTrue(manifest["archive_immutable"])
                self.assertIn("Top50Mixed.csv", manifest["archive_hashes_sha256"])
                with self.assertRaises(FileExistsError):
                    daily_pipeline._archive_run("run-1", {})

    def test_v37_does_not_change_scoring_model_version(self) -> None:
        self.assertTrue(
            any(f"v{version}" in config.SCORING_VERSION for version in range(35, 100))
        )
        self.assertIn("v37", config.PIPELINE_VERSION)
        self.assertIn("v37", config.GUI_VERSION)


if __name__ == "__main__":
    unittest.main()
