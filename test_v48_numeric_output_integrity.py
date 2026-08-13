from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

import config
import gui_core
import report
from report import _candidate_generation_stage, validate_decision_integrity
from scanner import ScanResult
from score import ScoreBreakdown


def _audit_row(**overrides: object) -> dict[str, object]:
    base = 40.1234
    trigger = 60.2345
    execution = 80.3456
    final = base * 0.60 + trigger * 0.15 + execution * 0.25
    row: dict[str, object] = {
        "Ticker": "000001.SZ",
        "Error": "",
        "BaseScore": base,
        "TriggerScore": trigger,
        "ExecutionScore": execution,
        "FinalScore": final,
        "ScoreCoverage": 1.0,
        "ModelWeightSignature": "0.6000:0.1500:0.2500",
        "ModelVersion": config.SCORING_VERSION,
        "Close": 10.0,
        "StopLoss": 9.0,
        "ProjectedTarget": 12.0,
        "StopDistancePct": 10.0,
        "RewardRiskRatio": 2.0,
        "ATR14": 0.5,
        "ATR50": 0.4,
        "ATRExpansion": 1.25,
        "ATRExpansionSource": "indicator",
        "IsETF": False,
        "AssetType": "stock",
        "MinPricePassed": True,
        "MinVolumePassed": True,
        "MinMarketCapPassed": True,
        "SufficientHistoryPassed": True,
        "BearMarket": False,
        "VolAccum": True,
        "OBV_Div": True,
        "CMF_Pos": False,
        "AD_SlopePos": False,
        "Consolidation": True,
        "VolContract": False,
        "UniverseEligible": True,
        "HardGatePassed": True,
        "SignalConfirmed": True,
        "PassedFilters": True,
        "SignalCount": 3,
        "FilterCount": 7,
        "HardGateFailedCount": 0,
        "HardGateFailedNames": "",
        "DiagnosticFailedCount": 3,
        "DiagnosticFailedNames": "cmf_positive,ad_slope,volatility_contraction",
        "QualityApplicable": True,
        "QualityGate": True,
        "QualityHardDataComplete": True,
        "QualityMultiplier": 0.95,
        "BacktestRequested": True,
        "BacktestMode": "EXACT",
        "BacktestStage": "EXACT_REFINEMENT",
        "BacktestSamples": 12,
        "BacktestStatus": "SAMPLES",
        "BacktestEligibleForRanking": True,
        "CandidateGenerationStage": "EXACT_REFINED",
    }
    row.update(overrides)
    return row


class V48NumericOutputIntegrityTests(unittest.TestCase):
    def test_versions_mark_v48_without_losing_v47_boundaries(self) -> None:
        self.assertIn("v48", config.SCORING_VERSION)
        self.assertIn("v48", config.PIPELINE_VERSION)
        self.assertIn("v48", config.DECISION_INTEGRITY_VERSION)
        self.assertIn("v48", config.OUTPUT_CONTRACT_VERSION)
        self.assertIn("v48", config.GUI_VERSION)
        self.assertIn("v47", config.SCORING_VERSION)
        self.assertIn("v47", config.PIPELINE_VERSION)

    def test_export_preserves_score_chain_and_atr_audit_precision(self) -> None:
        result = ScanResult(
            ticker="510050.SH",
            is_etf=True,
            asset_type="etf",
            score=ScoreBreakdown(total=12.34567, execution_score=73.45678),
            base_score=41.23456,
            trigger_score=62.34567,
            final_score=51.23456,
            backtest_score=55.55556,
            composite_score=56.66666,
            failure_adjusted_score=57.77777,
            institutional_score=58.88888,
            atr14=0.01234567,
            atr50=0.01098765,
            atr_expansion=1.1236,
        )
        with patch.object(report, "finalize_signal_ranking", side_effect=lambda frame: frame):
            row = report._results_to_dataframe([result]).iloc[0]

        self.assertEqual(row["Score"], 12.3457)
        self.assertEqual(row["BaseScore"], 41.2346)
        self.assertEqual(row["TriggerScore"], 62.3457)
        self.assertEqual(row["ExecutionScore"], 73.4568)
        self.assertEqual(row["FinalScore"], 51.2346)
        self.assertEqual(row["BacktestScore"], 55.5556)
        self.assertEqual(row["CompositeScore"], 56.6667)
        self.assertEqual(row["FailureAdjustedScore"], 57.7778)
        self.assertEqual(row["InstitutionalScore"], 58.8889)
        self.assertEqual(row["ATR14"], 0.012346)
        self.assertEqual(row["ATR50"], 0.010988)

    def test_numeric_and_policy_provenance_accepts_consistent_row(self) -> None:
        validate_decision_integrity(pd.DataFrame([_audit_row()]))

    def test_numeric_provenance_rejects_each_tampered_chain(self) -> None:
        cases = (
            (
                {"FinalScore": 99.0},
                "final score disagrees with signed model weights",
            ),
            (
                {"StopDistancePct": 5.0},
                "stop-distance percentage disagrees with exported prices",
            ),
            (
                {"RewardRiskRatio": 1.0},
                "reward-risk ratio disagrees with exported prices",
            ),
            (
                {"ATRExpansion": 1.50},
                "ATR expansion disagrees with ATR14/ATR50",
            ),
            (
                {"HardGatePassed": False},
                "hard-gate result disagrees with hard filters",
            ),
            (
                {"BacktestStage": "FAST_SCREEN"},
                "per-ticker backtest mode disagrees with stage",
            ),
        )
        for overrides, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    validate_decision_integrity(pd.DataFrame([_audit_row(**overrides)]))

    def test_etf_neutrality_and_filter_count_are_fail_closed_for_v48(self) -> None:
        etf = _audit_row(
            Ticker="510050.SH",
            IsETF=True,
            AssetType="etf",
            QualityApplicable=False,
            QualityGate=True,
            QualityHardDataComplete=True,
            QualityMultiplier=1.0,
        )
        validate_decision_integrity(pd.DataFrame([etf]))

        with self.assertRaisesRegex(ValueError, "filter count disagrees"):
            validate_decision_integrity(pd.DataFrame([{**etf, "FilterCount": 6}]))
        with self.assertRaisesRegex(ValueError, "ETF fundamental neutrality"):
            validate_decision_integrity(pd.DataFrame([{**etf, "QualityApplicable": True}]))

    def test_legacy_etf_filter_count_remains_readable(self) -> None:
        legacy = _audit_row(
            Ticker="510050.SH",
            IsETF=True,
            AssetType="etf",
            ModelVersion="2026-08-13-v47-legacy",
            QualityApplicable=False,
            QualityGate=True,
            QualityHardDataComplete=True,
            QualityMultiplier=1.0,
        )
        for legacy_count in (5, 6, 7):
            with self.subTest(filter_count=legacy_count):
                validate_decision_integrity(pd.DataFrame([{**legacy, "FilterCount": legacy_count}]))

    def test_failed_rows_are_skipped_but_success_identity_is_unique(self) -> None:
        failed = {column: None for column in _audit_row()}
        failed.update({"Ticker": "", "Error": "provider failure"})
        validate_decision_integrity(pd.DataFrame([_audit_row(), failed]))

        duplicate = pd.DataFrame([_audit_row(), _audit_row()])
        with self.assertRaisesRegex(ValueError, "duplicate successful ticker"):
            validate_decision_integrity(duplicate)

    def test_direct_exact_backtest_is_never_mislabeled_fast(self) -> None:
        stages = _candidate_generation_stage(pd.Series(["EXACT", "EXACT_REFINEMENT", "FAST_SCREEN", "NOT_EVALUATED"]))
        self.assertEqual(
            stages.tolist(),
            ["EXACT_REFINED", "EXACT_REFINED", "FAST_SCREEN", "NOT_EVALUATED"],
        )

    def test_gui_renders_audit_precision_without_rounding_it_to_two_decimals(self) -> None:
        instance = gui_core.ScannerGUI.__new__(gui_core.ScannerGUI)
        self.assertEqual(instance._format_table_value("ATR14", "0.012346"), "0.0123")
        self.assertEqual(instance._format_table_value("ATRExpansion", "1.23456"), "1.2346")
        self.assertEqual(
            instance._format_table_value("EntryZoneDistancePct", "1.2345"),
            "1.23%",
        )
        self.assertEqual(
            instance._format_table_value("ATRExpansionSource", "ohlc_fallback"),
            "OHLC回退计算",
        )


if __name__ == "__main__":
    unittest.main()
