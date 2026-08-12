from __future__ import annotations

import unittest

import pandas as pd

import config
import report
import signal_lifecycle_core as lifecycle_core
from fundamental_quality import calculate_quality
from scanner import ScanResult


class V39DecisionIntegrityTests(unittest.TestCase):
    def test_gate2_holding_decline_is_supporting_evidence_not_veto_end_to_end(self):
        quality = calculate_quality(
            {
                "Ticker": "600377.SH",
                "Industry": "铁路公路",
                "ROE": 11.48,
                "GrossMargin": 20.0,
                "IndustryGrossMarginPercentile": 0.50,
                "InstitutionHoldingTrend": "decreasing",
                "InstitutionHoldingPeriods": 3,
                "NetProfitY1": 100.0,
                "NetProfitY2": 95.0,
                "NetProfitY3": 90.0,
            }
        )
        self.assertTrue(quality.quality_gate)
        self.assertEqual(quality.institution_holding_status, "FAIL")

        frame = pd.DataFrame(
            [
                {
                    "Ticker": "600377.SH",
                    "AssetType": "stock",
                    "IsETF": False,
                    "Score": 60.0,
                    "FinalScore": 60.0,
                    "InstitutionalScore": 60.0,
                    "EntrySignal": "WAIT_PULLBACK",
                    "QualityApplicable": True,
                    "QualityGate": quality.quality_gate,
                    "QualityDataAvailable": quality.data_available,
                    "QualityDataCompleteness": quality.quality_data_completeness,
                    "QualityGateReason": quality.quality_gate_reason,
                    "QualityMultiplier": quality.quality_multiplier,
                    "QualityProfile": quality.quality_profile,
                    "ProfitTrendStatus": quality.profit_trend_status,
                    "CyclicalQualityOverride": quality.cyclical_quality_override,
                    "QualityROE": quality.roe_factor,
                    "QualityGrossMargin": quality.gross_margin_factor,
                    "QualityNetProfit": quality.net_profit_factor,
                    "ROE": quality.roe,
                    "IndustryGrossMarginPercentile": quality.industry_gross_margin_percentile,
                    "NetProfitY1": quality.net_profit_y1,
                    "NetProfitY2": quality.net_profit_y2,
                    "NetProfitY3": quality.net_profit_y3,
                    "InstitutionHoldingTrend": quality.institution_holding_trend,
                    "InstitutionHoldingPeriods": quality.institution_holding_periods,
                    "InstitutionHoldingStatus": quality.institution_holding_status,
                    "PassedFilters": True,
                    "UniverseEligible": True,
                    "ScoreCoverage": 1.0,
                    "SignalRecencyDays": 0,
                }
            ]
        )
        out = lifecycle_core.finalize_signal_ranking(frame).iloc[0]
        self.assertTrue(bool(out["QualityGate"]))
        self.assertAlmostEqual(float(out["QualityMultiplier"]), float(quality.quality_multiplier), places=6)
        self.assertIn("不单独否决", str(out["QualityGateReason"]))

    def test_hard_gate_failure_cannot_enter_research_topn(self):
        frame = pd.DataFrame(
            [
                {
                    "Ticker": "000001.SZ",
                    "AssetType": "stock",
                    "IsETF": False,
                    "RankingScore": 99.0,
                    "HardGatePassed": False,
                    "HardGateFailedNames": "min_price",
                    "UniverseEligible": False,
                    "Error": "",
                },
                {
                    "Ticker": "000002.SZ",
                    "AssetType": "stock",
                    "IsETF": False,
                    "RankingScore": 90.0,
                    "HardGatePassed": True,
                    "UniverseEligible": True,
                    "Error": "",
                },
            ]
        )
        prepared = report._apply_research_policy(frame)
        self.assertFalse(bool(prepared.loc[0, "ResearchEligible"]))
        self.assertIn("min_price", str(prepared.loc[0, "ResearchExclusionReason"]))
        ranked = report._rank_valid_candidates(frame)
        self.assertEqual(ranked["Ticker"].tolist(), ["000002.SZ"])

    def test_strict_breakout_override_is_lifecycle_active(self):
        frame = pd.DataFrame(
            [
                {
                    "Score": 30.0,
                    "SignalCount": 1,
                    "PassedFilters": False,
                    "UniverseEligible": True,
                    "EntrySignal": "BREAKOUT_CONFIRM",
                    "BreakoutVolumeConfirmed": True,
                    "BreakoutFlowConfirmed": True,
                }
            ]
        )
        self.assertTrue(bool(lifecycle_core._is_active(frame).iloc[0]))
        frame.loc[0, "UniverseEligible"] = False
        self.assertFalse(bool(lifecycle_core._is_active(frame).iloc[0]))

    def test_publication_invariant_rejects_actionable_without_lifecycle(self):
        frame = pd.DataFrame(
            [
                {
                    "Ticker": "000001.SZ",
                    "ResearchEligible": True,
                    "HardGatePassed": True,
                    "RankingEligibility": "谨慎候选",
                    "SignalStatus": "",
                    "SignalDays": 0,
                    "QualityReason": "通用严格模型；行业自适应硬门槛通过",
                    "QualityGate": True,
                }
            ]
        )
        with self.assertRaisesRegex(ValueError, "active lifecycle"):
            report.validate_decision_integrity(frame)

    def test_legacy_combined_filter_export_does_not_invent_hard_failures(self):
        result = ScanResult(
            ticker="000001.SZ",
            passed_filters=True,
            filter_details={"signal_count": 4},
        )
        frame = report._results_to_dataframe([result])
        row = frame.iloc[0]
        self.assertTrue(bool(row["UniverseEligible"]))
        self.assertTrue(bool(row["HardGatePassed"]))
        self.assertEqual(str(row["HardGateFailedNames"]), "")
        self.assertEqual(int(row["HardGateFailedCount"]), 0)

    def test_versions_advance_without_replacing_v38_gate_policy(self):
        self.assertIn("v39", config.SCORING_VERSION)
        self.assertIn("v39", config.PIPELINE_VERSION)
        self.assertIn("v38", config.FUNDAMENTAL_GATE_VERSION)
        self.assertIn("v39", config.DECISION_INTEGRITY_VERSION)


if __name__ == "__main__":
    unittest.main()
