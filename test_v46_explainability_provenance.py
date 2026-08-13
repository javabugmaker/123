from __future__ import annotations

import csv
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

import config
import gui
from analytics import BacktestSummary, _apply_backtest_freshness
from daily_pipeline import _csv_profile
from report import validate_decision_integrity
from signal_lifecycle import finalize_signal_ranking


def _decision_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "Ticker": "000001.SZ",
        "EntrySignal": "BUY_NOW",
        "RawEntrySignal": "BUY_NOW",
        "Score": 55.0,
        "FinalScore": 55.0,
        "InstitutionalScore": 55.0,
        "TechnicalInstitutionalScore": 55.0,
        "IsETF": False,
        "AssetType": "stock",
        "PassedFilters": False,
        "UniverseEligible": True,
        "SignalStatus": "ACTIVE",
        "SignalDays": 1,
        "SignalTrend": "STRENGTHENING",
        "LifecycleStage": "趋势确认",
        "SignalRecencyDays": 0,
        "ScoreCoverage": 1.0,
        "DataAgeDays": 0,
        "DataTradingAgeDays": 0,
        "ValueTrapRisk": 0.0,
        "ChaseRiskScore": 0.0,
        "QualityApplicable": True,
        "QualityDataAvailable": True,
        "QualityDataCompleteness": 1.0,
        "QualityHardDataComplete": True,
        "QualityGate": False,
        "QualityProfile": "GENERAL",
        "QualityGateReason": "行业自适应硬门槛未通过",
        "InstitutionHoldingStatus": "PASS",
        "SignalRecencyFactor": 1.0,
        "FailureSignalFactor": 1.0,
        "SectorConfirmationFactor": 1.0,
        "BreakoutQualityFactor": 1.0,
        "StopDistancePct": 5.0,
        "RewardRiskRatio": 0.5,
    }
    row.update(overrides)
    return row


class V46ExplainabilityProvenanceTests(unittest.TestCase):
    def test_versions_mark_v46_without_losing_prior_boundaries(self) -> None:
        self.assertIn("v46", config.SCORING_VERSION)
        self.assertIn("v46", config.PIPELINE_VERSION)
        self.assertIn("v46", config.DECISION_INTEGRITY_VERSION)
        self.assertIn("v46", config.OUTPUT_CONTRACT_VERSION)
        self.assertIn("v46", config.GUI_VERSION)
        for marker in ("v45", "v44", "v43", "v41", "v40", "v39"):
            self.assertIn(marker, config.PIPELINE_VERSION)

    def test_all_simultaneous_blockers_are_auditable(self) -> None:
        result = finalize_signal_ranking(pd.DataFrame([_decision_row()]))
        row = result.iloc[0]
        self.assertEqual(row["DecisionState"], "OBSERVE")
        self.assertIn("基础筛选未全通过", row["RankingPenaltyReason"])
        self.assertIn("基础筛选未全通过", row["TradeReadinessReason"])
        self.assertIn("质量门槛", row["TradeReadinessReason"])
        self.assertIn("止损距离或预期盈亏比", row["TradeReadinessReason"])
        tokens = str(row["TradeReadinessReason"]).split("；")
        self.assertEqual(len(tokens), len(set(tokens)))
        validate_decision_integrity(result)

    def test_backtest_cutoff_and_freshness_are_explicit(self) -> None:
        frame = pd.DataFrame(
            {
                "Ticker": ["000001.SZ", "510050.SH"],
                "DataAsOf": ["2026-08-13", "2026-08-13"],
                "BacktestRequested": [True, False],
                "BacktestLastEvaluatedDate": ["2026-08-11", ""],
            }
        )
        summary = BacktestSummary(split_dates={"global_end": "2026-08-11"})
        result = _apply_backtest_freshness(frame, summary)
        self.assertEqual(result.loc[0, "BacktestDataCutoffDate"], "2026-08-11")
        self.assertEqual(result.loc[0, "BacktestLastEvaluatedDate"], "2026-08-11")
        self.assertEqual(result.loc[0, "BacktestFreshnessTradingDays"], 2.0)
        self.assertEqual(result.loc[0, "BacktestFreshnessStatus"], "延迟")
        self.assertIn("2 个交易日", result.loc[0, "BacktestFreshnessReason"])
        self.assertEqual(result.loc[1, "BacktestFreshnessStatus"], "未请求")

    def test_gui_distinguishes_etf_exemption_and_invalid_target(self) -> None:
        instance = gui.DecisionScannerGUI.__new__(gui.DecisionScannerGUI)
        instance._detail_format_data = {
            "AssetType": "etf",
            "IsETF": "True",
            "QualityApplicable": "False",
            "DecisionState": "OBSERVE",
            "RankingEligibility": "观察",
            "Close": "1.200",
            "RewardRiskRatio": "0",
        }
        self.assertEqual(
            instance._format_table_value("QualityGate", "True"),
            "不适用（ETF豁免）",
        )
        self.assertEqual(
            instance._format_table_value("ProjectedTarget", "1.200"),
            "暂不适用",
        )
        instance._detail_format_data["DecisionState"] = "READY"
        instance._detail_format_data["RankingEligibility"] = "推荐"
        instance._detail_format_data["RewardRiskRatio"] = "2"
        self.assertEqual(
            instance._format_table_value("ProjectedTarget", "1.260"),
            "1.260",
        )

    def test_daily_profile_exposes_quality_gate_bottleneck(self) -> None:
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "AllResults.csv"
            rows = [
                {
                    "Ticker": "000001.SZ",
                    "AssetType": "stock",
                    "QualityApplicable": "True",
                    "QualityGate": "True",
                    "QualityHardDataComplete": "True",
                    "DataAsOf": "2026-08-13",
                    "RunId": "run-1",
                    "Error": "",
                },
                {
                    "Ticker": "000002.SZ",
                    "AssetType": "stock",
                    "QualityApplicable": "True",
                    "QualityGate": "False",
                    "QualityHardDataComplete": "True",
                    "DataAsOf": "2026-08-13",
                    "RunId": "run-1",
                    "Error": "",
                },
                {
                    "Ticker": "510050.SH",
                    "AssetType": "etf",
                    "QualityApplicable": "False",
                    "QualityGate": "True",
                    "QualityHardDataComplete": "True",
                    "DataAsOf": "2026-08-13",
                    "RunId": "run-1",
                    "Error": "",
                },
            ]
            with path.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.DictWriter(file, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            profile = _csv_profile(path, "2026-08-13")

        self.assertEqual(profile["quality_applicable_stocks"], 2)
        self.assertEqual(profile["quality_gate_passed_stocks"], 1)
        self.assertEqual(profile["quality_gate_pass_rate"], 0.5)
        self.assertEqual(profile["quality_hard_data_complete_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
