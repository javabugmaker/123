from __future__ import annotations

import unittest
from itertools import product
from unittest.mock import patch

import pandas as pd

import config
import report
from report import validate_decision_integrity
from signal_lifecycle import finalize_signal_ranking, strict_filter_override_mask


def _breakout_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "Ticker": "603998.SH",
        "Name": "LifecycleOverrideCase",
        "Score": 80.0,
        "FinalScore": 80.0,
        "InstitutionalScore": 80.0,
        "TechnicalInstitutionalScore": 80.0,
        "EntrySignal": "BREAKOUT_CONFIRM",
        "RawEntrySignal": "BREAKOUT_CONFIRM",
        "PassedFilters": False,
        "UniverseEligible": True,
        "SignalConfirmed": False,
        "BreakoutVolumeRatio": 1.50,
        "BreakoutVolumeConfirmed": True,
        "BreakoutFlowConfirmed": True,
        "SignalStatus": "ACTIVE",
        "SignalTrend": "持续增强",
        "SignalDays": 3,
        "SignalRecencyDays": 0,
        "SignalRecencyFactor": 1.0,
        "LifecycleStage": "趋势确认",
        "ScoreCoverage": 1.0,
        "DataAgeDays": 0,
        "DataTradingAgeDays": 0,
        "DataFreshnessStatus": "新鲜",
        "ValueTrapRisk": 0.0,
        "ChaseRiskScore": 0.0,
        "StopDistancePct": 5.0,
        "RewardRiskRatio": 2.0,
        "IsETF": False,
        "AssetType": "stock",
        "QualityApplicable": True,
        "QualityDataAvailable": True,
        "QualityDataCompleteness": 1.0,
        "QualityHardDataComplete": True,
        "QualityGate": True,
        "QualityProfile": "GENERAL",
        "QualityGateReason": "行业自适应硬门槛通过",
        "InstitutionHoldingStatus": "PASS",
        "SectorConfirmationFactor": 1.0,
        "FailureSignalFactor": 1.0,
        "BreakoutQualityFactor": 1.0,
        "Error": "",
    }
    row.update(overrides)
    return row


class V47FullRunIntegrityTests(unittest.TestCase):
    def test_versions_mark_canonical_override_and_preflight(self) -> None:
        self.assertIn("v47", config.SCORING_VERSION)
        self.assertIn("v47", config.PIPELINE_VERSION)
        self.assertIn("v47", config.DECISION_INTEGRITY_VERSION)
        self.assertIn("v47", config.OUTPUT_CONTRACT_VERSION)
        self.assertIn("v47", config.GUI_VERSION)
        self.assertIn("v46", config.SCORING_VERSION)
        self.assertIn("v46", config.PIPELINE_VERSION)

    def test_rapidly_weakening_breakout_revokes_override_everywhere(self) -> None:
        result = finalize_signal_ranking(
            pd.DataFrame(
                [
                    _breakout_row(
                        SignalStatus="WEAKEN",
                        SignalTrend="快速下降",
                    )
                ]
            )
        )
        row = result.iloc[0]

        self.assertFalse(bool(row["FilterOverrideApplied"]))
        self.assertEqual(row["FilterOverrideReason"], "")
        self.assertEqual(row["DecisionState"], "OBSERVE")
        self.assertIn("基础筛选未全通过", row["RankingPenaltyReason"])
        self.assertIn("基础筛选未全通过", row["TradeReadinessReason"])
        self.assertIn("快速下降", row["TradeReadinessReason"])
        validate_decision_integrity(result)

    def test_override_policy_is_shared_across_lifecycle_boundaries(self) -> None:
        cases = (
            ("ACTIVE", "持续增强", True),
            ("WEAKEN", "横盘观察", True),
            ("WEAKEN", "RAPID DECLINE", False),
            ("FAILED", "信号失效", False),
            ("EXPIRED", "无信号", False),
            ("INACTIVE", "无信号", False),
        )
        for status, trend, expected in cases:
            with self.subTest(status=status, trend=trend):
                result = finalize_signal_ranking(
                    pd.DataFrame(
                        [_breakout_row(SignalStatus=status, SignalTrend=trend)]
                    )
                )
                canonical = strict_filter_override_mask(result)
                self.assertEqual(bool(canonical.iloc[0]), expected)
                self.assertEqual(bool(result.loc[0, "FilterOverrideApplied"]), expected)
                if expected:
                    self.assertIn(
                        "严格覆盖基础筛选缺口",
                        result.loc[0, "FilterOverrideReason"],
                    )
                else:
                    self.assertIn(
                        "基础筛选未全通过",
                        result.loc[0, "TradeReadinessReason"],
                    )
                validate_decision_integrity(result)

    def test_repeated_finalization_keeps_override_reasons_idempotent(self) -> None:
        first = finalize_signal_ranking(
            pd.DataFrame(
                [
                    _breakout_row(
                        SignalStatus="WEAKEN",
                        SignalTrend="快速下降",
                    )
                ]
            )
        )
        second = finalize_signal_ranking(first)

        for column in (
            "TradeReadinessReason",
            "DecisionReason",
            "RankingPenaltyReason",
        ):
            tokens = str(second.loc[0, column]).split("；")
            self.assertEqual(len(tokens), len(set(tokens)), column)
        self.assertEqual(
            first.loc[0, "FilterOverrideApplied"],
            second.loc[0, "FilterOverrideApplied"],
        )
        self.assertEqual(
            first.loc[0, "ReadinessPenaltyFactor"],
            second.loc[0, "ReadinessPenaltyFactor"],
        )
        validate_decision_integrity(second)

    def test_complete_override_state_matrix_remains_self_consistent(self) -> None:
        rows: list[dict[str, object]] = []
        cases = product(
            (False, True),
            (False, True),
            ("BUY_NOW", "BREAKOUT_CONFIRM", "AVOID"),
            (False, True),
            (False, True),
            (0.80, 1.50, None),
            ("ACTIVE", "WEAKEN", "FAILED"),
            ("持续增强", "快速下降"),
        )
        for index, (
            passed,
            universe_eligible,
            signal,
            volume_confirmed,
            flow_confirmed,
            volume_ratio,
            status,
            trend,
        ) in enumerate(cases):
            rows.append(
                _breakout_row(
                    Ticker=f"CASE{index:04d}",
                    EntrySignal=signal,
                    RawEntrySignal=signal,
                    PassedFilters=passed,
                    UniverseEligible=universe_eligible,
                    SignalConfirmed=passed,
                    BreakoutVolumeConfirmed=volume_confirmed,
                    BreakoutFlowConfirmed=flow_confirmed,
                    BreakoutVolumeRatio=volume_ratio,
                    SignalStatus=status,
                    SignalTrend=trend,
                )
            )

        result = finalize_signal_ranking(pd.DataFrame(rows))
        canonical = strict_filter_override_mask(result)
        recorded = result["FilterOverrideApplied"].astype(bool)
        passed = result["PassedFilters"].astype(bool)
        unresolved = ~passed & ~canonical
        readiness_reason = result["TradeReadinessReason"].fillna("").astype(str)
        override_reason = result["FilterOverrideReason"].fillna("").astype(str)

        pd.testing.assert_series_equal(
            recorded.reset_index(drop=True),
            canonical.reset_index(drop=True),
            check_names=False,
        )
        self.assertTrue(
            readiness_reason.loc[unresolved]
            .str.contains("基础筛选未全通过", regex=False)
            .all()
        )
        self.assertTrue(
            override_reason.loc[canonical]
            .str.contains("严格覆盖基础筛选缺口", regex=False)
            .all()
        )
        self.assertTrue(override_reason.loc[~canonical].eq("").all())
        validate_decision_integrity(result)

    def test_integrity_gate_rejects_tampered_override_audit(self) -> None:
        result = finalize_signal_ranking(pd.DataFrame([_breakout_row()]))
        self.assertTrue(bool(result.loc[0, "FilterOverrideApplied"]))
        result.loc[0, "FilterOverrideApplied"] = False

        with self.assertRaisesRegex(
            ValueError,
            "recorded base-filter override disagrees with canonical policy",
        ):
            validate_decision_integrity(result)

    def test_export_preflight_runs_before_any_result_file_is_replaced(self) -> None:
        frame = pd.DataFrame([_breakout_row()])
        with (
            patch.object(report, "_results_to_dataframe", return_value=frame),
            patch.object(report, "enrich_signal_lifecycle", return_value=frame),
            patch.object(report, "_apply_research_policy", return_value=frame),
            patch.object(report, "enrich_evidence_fields", return_value=frame),
            patch.object(
                report,
                "_rank_valid_candidates",
                side_effect=ValueError("preflight integrity failure"),
            ),
            patch.object(report, "_atomic_write_csv") as write_csv,
            patch.object(report, "_atomic_write_parquet") as write_parquet,
            patch.object(report, "refresh_research_outcomes") as refresh_outcomes,
        ):
            with self.assertRaisesRegex(ValueError, "preflight integrity failure"):
                report.export_all([])

        write_csv.assert_not_called()
        write_parquet.assert_not_called()
        refresh_outcomes.assert_not_called()


if __name__ == "__main__":
    unittest.main()
