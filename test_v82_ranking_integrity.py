from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

import historical_universe
from backtest_rank_integrity_v82 import (
    BACKTEST_RECENCY_NORMALIZATION_VERSION,
    install_single_recency_ranking_guard,
    single_recency_ranking_context,
    strip_embedded_backtest_recency,
)
from model_audit import _validate_full_universe, build_scenarios
from universe_snapshot_v82 import record_universe_snapshot


class _DummyLifecycleModule:
    def __init__(self) -> None:
        self.seen: pd.DataFrame | None = None

    def finalize_signal_ranking(self, frame: pd.DataFrame) -> pd.DataFrame:
        self.seen = frame.copy()
        return frame.copy()


class RankingIntegrityV82Tests(unittest.TestCase):
    def test_backtest_recency_is_removed_before_canonical_ranker(self) -> None:
        frame = pd.DataFrame(
            {
                "Ticker": ["000001.SZ", "000002.SZ"],
                "SignalRecencyFactor": [0.8, 1.0],
                "InstitutionalScore": [96.0, 80.0],
                "TechnicalInstitutionalScore": [48.0, 40.0],
            }
        )
        normalized = strip_embedded_backtest_recency(frame)

        self.assertAlmostEqual(normalized.loc[0, "InstitutionalScore"], 100.0)
        self.assertAlmostEqual(
            normalized.loc[0, "TechnicalInstitutionalScore"], 50.0
        )
        self.assertAlmostEqual(normalized.loc[1, "InstitutionalScore"], 80.0)
        self.assertTrue(normalized.loc[0, "BacktestRecencyNormalizationApplied"])
        self.assertFalse(normalized.loc[1, "BacktestRecencyNormalizationApplied"])
        self.assertEqual(
            normalized.loc[0, "BacktestRecencyNormalizationVersion"],
            BACKTEST_RECENCY_NORMALIZATION_VERSION,
        )

    def test_recency_guard_is_context_scoped_without_global_replacement(self) -> None:
        module = _DummyLifecycleModule()
        install_single_recency_ranking_guard(module)
        guarded = module.finalize_signal_ranking
        frame = pd.DataFrame(
            {
                "SignalRecencyFactor": [0.8],
                "InstitutionalScore": [96.0],
                "TechnicalInstitutionalScore": [48.0],
            }
        )

        module.finalize_signal_ranking(frame)
        assert module.seen is not None
        self.assertAlmostEqual(module.seen.loc[0, "InstitutionalScore"], 96.0)

        with single_recency_ranking_context():
            module.finalize_signal_ranking(frame)
            assert module.seen is not None
            self.assertAlmostEqual(module.seen.loc[0, "InstitutionalScore"], 100.0)

        module.finalize_signal_ranking(frame)
        assert module.seen is not None
        self.assertAlmostEqual(module.seen.loc[0, "InstitutionalScore"], 96.0)
        self.assertIs(module.finalize_signal_ranking, guarded)

        install_single_recency_ranking_guard(module)
        self.assertIs(module.finalize_signal_ranking, guarded)

    def test_full_universe_audit_rejects_ranked_subset(self) -> None:
        subset = pd.DataFrame(
            {
                "Ticker": ["000001.SZ", "000002.SZ"],
                "RankingScope": ["FULL_UNIVERSE", "FULL_UNIVERSE"],
                "RankingUniverseSize": [6821, 6821],
            }
        )
        with self.assertRaisesRegex(ValueError, "ranking scope violation"):
            _validate_full_universe(subset)

    def test_audit_recovers_ranking_time_decision_factor(self) -> None:
        frame = pd.DataFrame(
            {
                "Ticker": ["000001.SZ", "000002.SZ"],
                "RankingScore": [72.16, 80.0],
                "CrossAssetScore": [100.0, 80.0],
                "EntrySignal": ["BUY_NOW", "BUY_NOW"],
                "HardRiskPenalty": [1.0, 1.0],
                "ChaseRiskFactor": [1.0, 1.0],
                "DataConfidenceFactor": [1.0, 1.0],
                "SignalRecencyFactor": [1.0, 1.0],
                "ReadinessPenaltyFactor": [0.82, 1.0],
                # Final state can differ from the state used in RankingScore.
                "DecisionState": ["OBSERVE", "READY"],
                "QualityApplicable": [True, True],
                "QualityDataCompleteness": [0.4, 1.0],
                "QualityHardDataComplete": [True, True],
                "QualityGate": [False, True],
                "PassedFilters": [True, True],
                "FilterOverrideApplied": [False, False],
                "SignalStatus": ["ACTIVE", "ACTIVE"],
            }
        )
        scenarios, diagnostics = build_scenarios(frame)
        self.assertAlmostEqual(diagnostics.loc[0, "InferredDecisionFactor"], 0.88)
        self.assertAlmostEqual(diagnostics.loc[1, "InferredDecisionFactor"], 1.0)
        no_decision = next(item for item in scenarios if item.name == "no_decision")
        self.assertAlmostEqual(no_decision.score.loc[0], 82.0)

    def test_snapshot_is_date_aware_and_readable_by_point_in_time_engine(self) -> None:
        frame = pd.DataFrame(
            {
                "Ticker": ["000001.SZ", "600000.SH"],
                "DataAsOf": ["2026-08-20", "2026-08-20"],
                "UniverseEligible": [True, False],
                "UniverseExclusionReason": ["", "snapshot-test-excluded"],
            }
        )
        with TemporaryDirectory() as temp_dir:
            snapshot_dir = Path(temp_dir)
            path = record_universe_snapshot(frame, snapshot_dir=snapshot_dir)
            self.assertEqual(path, snapshot_dir / "2026-08-20.csv")
            self.assertTrue(path.exists())

            eligible, reason = historical_universe.point_in_time_eligibility(
                "000001.SZ", "2026-08-20", snapshot_dir=snapshot_dir
            )
            self.assertTrue(eligible)
            self.assertEqual(reason, "eligible")

            excluded, excluded_reason = historical_universe.point_in_time_eligibility(
                "600000.SH", "2026-08-20", snapshot_dir=snapshot_dir
            )
            self.assertFalse(excluded)
            self.assertEqual(excluded_reason, "snapshot-test-excluded")


if __name__ == "__main__":
    unittest.main()
