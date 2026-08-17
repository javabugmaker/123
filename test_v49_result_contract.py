from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

import config
import gui
import report
import signal_lifecycle
from classification import etf_theme_key, etf_tracking_key, theme_cluster
from result_contract import decision_policy_signature
from scanner import ScanResult


def _current_contract_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "Ticker": "000001.SZ",
        "Error": "",
        "ModelVersion": config.SCORING_VERSION,
        "PipelineVersion": config.PIPELINE_VERSION,
        "OutputContractVersion": config.OUTPUT_CONTRACT_VERSION,
        "DecisionIntegrityVersion": config.DECISION_INTEGRITY_VERSION,
        "FundamentalGateVersion": config.FUNDAMENTAL_GATE_VERSION,
        "DecisionPolicySignature": decision_policy_signature(),
        "RunId": "run-1",
        "RankingRunId": "run-1",
        "RankingScope": "FULL_UNIVERSE",
        "RankingUniverseSize": 1,
        "DataAsOf": "2026-08-14",
        "PriceAdjustmentMode": config.TICKFLOW_ADJUST,
        "AdjustmentBaseDate": "2026-08-14",
        "ATRAsOf": "2026-08-14",
        "CorporateActionRebaseDetected": False,
    }
    row.update(overrides)
    return row


class V49ResultContractTests(unittest.TestCase):
    def test_v49_is_pipeline_only_and_keeps_v48_score_boundary(self) -> None:
        self.assertIn("v48", config.SCORING_VERSION)
        self.assertNotIn("v49", config.SCORING_VERSION)
        self.assertIn("v49", config.PIPELINE_VERSION)
        self.assertIn("v49", config.DECISION_INTEGRITY_VERSION)
        self.assertIn("v49", config.OUTPUT_CONTRACT_VERSION)
        self.assertIn("v49", config.GUI_VERSION)

    def test_full_result_export_carries_explicit_policy_versions(self) -> None:
        with patch.object(
            report, "finalize_signal_ranking", side_effect=lambda frame: frame
        ):
            row = report._results_to_dataframe(
                [ScanResult(ticker="510050.SH", is_etf=True, asset_type="etf")]
            ).iloc[0]

        self.assertEqual(row["ModelVersion"], config.SCORING_VERSION)
        self.assertEqual(row["PipelineVersion"], config.PIPELINE_VERSION)
        self.assertEqual(
            row["OutputContractVersion"], config.OUTPUT_CONTRACT_VERSION
        )
        self.assertEqual(
            row["DecisionIntegrityVersion"], config.DECISION_INTEGRITY_VERSION
        )
        self.assertEqual(
            row["FundamentalGateVersion"], config.FUNDAMENTAL_GATE_VERSION
        )

    def test_current_contract_rejects_missing_or_mismatched_provenance(self) -> None:
        report.validate_decision_integrity(pd.DataFrame([_current_contract_row()]))

        missing = pd.DataFrame([_current_contract_row()]).drop(
            columns="OutputContractVersion"
        )
        with self.assertRaisesRegex(ValueError, "missing OutputContractVersion"):
            report.validate_decision_integrity(missing)

        mismatched = pd.DataFrame(
            [_current_contract_row(DecisionIntegrityVersion="v48-old")]
        )
        with self.assertRaisesRegex(ValueError, "mismatched DecisionIntegrityVersion"):
            report.validate_decision_integrity(mismatched)

    def test_current_actionable_row_discloses_local_evidence_limit(self) -> None:
        base = _current_contract_row(
            RankingEligibility="推荐",
            BacktestRequested=True,
            BacktestSamples=2,
            BacktestEligibleForRanking=False,
            TradeReadinessReason="执行条件满足",
        )
        with self.assertRaisesRegex(
            ValueError, "omits local backtest evidence limitation"
        ):
            report.validate_decision_integrity(pd.DataFrame([base]))

        base["TradeReadinessReason"] += "；本票回测样本不足，不参与本票校准"
        report.validate_decision_integrity(pd.DataFrame([base]))

        legacy = dict(base)
        legacy["PipelineVersion"] = "2026-08-13-v47-legacy"
        legacy.pop("OutputContractVersion")
        legacy.pop("DecisionIntegrityVersion")
        legacy.pop("FundamentalGateVersion")
        legacy["TradeReadinessReason"] = "旧版说明"
        report.validate_decision_integrity(pd.DataFrame([legacy]))

    def test_lifecycle_explanations_preserve_actionability_and_add_caveat(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "TradeReadinessReason": "买点、质量、数据与综合评分均满足执行条件",
                    "RankingReason": "",
                    "RankingPenaltyReason": "",
                    "BacktestRequested": True,
                    "BacktestSamples": 2,
                    "BacktestEligibleForRanking": False,
                    "BacktestConfidenceTier": "样本不足",
                    "BacktestStatus": "SAMPLES",
                    "OperationAdvice": "执行条件满足",
                }
            ]
        )
        strong = pd.Series([True])
        cautious = pd.Series([False])
        override = pd.Series([False])

        signal_lifecycle._sync_final_explanations(
            frame, strong, cautious, override
        )
        signal_lifecycle._sync_local_backtest_advice(frame, strong | cautious)

        self.assertIn("回测样本不足", frame.loc[0, "RankingReason"])
        self.assertIn("本票回测样本不足", frame.loc[0, "TradeReadinessReason"])
        self.assertEqual(
            frame.loc[0, "TradeReadinessReason"], frame.loc[0, "DecisionReason"]
        )
        self.assertIn("本票回测样本不足", frame.loc[0, "OperationAdvice"])

    def test_etf_aliases_share_one_canonical_exposure(self) -> None:
        self.assertEqual(etf_tracking_key(name="日经ETF工银"), "日经225")
        self.assertEqual(etf_tracking_key(name="日经225ETF华安"), "日经225")
        self.assertEqual(etf_tracking_key(name="恒生国企ETF"), "恒生中国企业")
        self.assertEqual(etf_tracking_key(name="恒生中国企业ETF"), "恒生中国企业")
        self.assertEqual(etf_tracking_key(name="地产ETF"), "房地产")
        self.assertEqual(etf_theme_key(name="日经ETF工银"), "日本股市")
        self.assertEqual(
            theme_cluster(is_etf=True, name="日经225ETF华安"), "日本股市"
        )

        frame = pd.DataFrame(
            [
                {
                    "Ticker": "159866.SZ",
                    "Name": "日经ETF工银",
                    "AssetType": "etf",
                    "IsETF": True,
                    "ETFTheme": "日经",
                    "ETFTrackingKey": "日经",
                    "ThemeCluster": "日经",
                    "RankingScore": 100.0,
                    "RankingEligibility": "推荐",
                },
                {
                    "Ticker": "513880.SH",
                    "Name": "日经225ETF华安",
                    "AssetType": "etf",
                    "IsETF": True,
                    "ETFTheme": "日经225",
                    "ETFTrackingKey": "日经225",
                    "ThemeCluster": "日经225",
                    "RankingScore": 99.0,
                    "RankingEligibility": "推荐",
                },
                {
                    "Ticker": "000001.SZ",
                    "Name": "测试股票",
                    "AssetType": "stock",
                    "IsETF": False,
                    "Industry": "银行",
                    "RankingScore": 98.0,
                    "RankingEligibility": "观察",
                },
            ]
        )
        prepared = report._ensure_diversity_columns(frame)
        self.assertEqual(
            prepared.loc[prepared["IsETF"], "ETFTrackingKey"].tolist(),
            ["日经225", "日经225"],
        )
        with patch.object(report, "ETF_TRACKING_MAX_PER_TOP_LIST", 1):
            selected = report._diversify_ranked_candidates(
                prepared, 3, diversity_prepared=True
            )
        self.assertEqual(
            selected["Ticker"].tolist(), ["159866.SZ", "000001.SZ"]
        )

    def test_gui_warns_for_stale_or_mixed_results_without_blocking_load(self) -> None:
        headers = ["RunId", "ModelVersion", "PipelineVersion"]
        current = [
            ["run-1", config.SCORING_VERSION, config.PIPELINE_VERSION]
        ]
        self.assertEqual(gui._result_contract_warning(headers, current), "")

        stale = [["run-1", "2026-08-13-v47-score", "2026-08-13-v47-pipeline"]]
        warning = gui._result_contract_warning(headers, stale)
        runtime_tag = gui._primary_version_tag(config.PIPELINE_VERSION)
        self.assertIn(f"结果 v47 / 程序 {runtime_tag}", warning)

        mixed = [*current, ["run-2", config.SCORING_VERSION, config.PIPELINE_VERSION]]
        self.assertIn("多个 RunId", gui._result_contract_warning(headers, mixed))


if __name__ == "__main__":
    unittest.main()
