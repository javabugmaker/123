from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

import numpy as np
import pandas as pd

import config
import daily_pipeline
import execution_costs
import gui
from execution_costs import BrokerFeeSchedule, round_trip_cost_percent
from historical_universe import (
    historical_universe_status,
    point_in_time_eligibility,
)
from model_calibration import calibration_stability_stats
from result_contract import (
    decision_policy_signature,
    stamp_ranking_contract,
    validate_ranking_input,
)
from tradeability import resolve_exit_index


class V50AtomicExecutionContractTests(TestCase):
    def test_broker_schedule_matches_supplied_stock_and_etf_rates(self):
        schedule = BrokerFeeSchedule()

        self.assertAlmostEqual(schedule.stock_commission_rate, 0.00008499999)
        self.assertAlmostEqual(schedule.etf_commission_rate, 0.00005000001)
        self.assertEqual(schedule.stock_min_commission, 0.0)
        self.assertEqual(schedule.etf_min_commission, 0.0)

        with patch.object(
            execution_costs, "BACKTEST_LIQUIDITY_IMPACT_AT_ONE_PERCENT", 0.0
        ), patch.object(execution_costs, "BACKTEST_MAX_LIQUIDITY_SLIPPAGE", 0.0):
            stock_cost = round_trip_cost_percent(
                is_etf=False,
                entry_price=10.0,
                entry_volume=1_000_000.0,
                exit_price=10.5,
                exit_volume=1_000_000.0,
                base_slippage=0.0,
                stamp_duty=0.0005,
                schedule=schedule,
            )
            etf_cost = round_trip_cost_percent(
                is_etf=True,
                entry_price=1.0,
                entry_volume=10_000_000.0,
                exit_price=1.05,
                exit_volume=10_000_000.0,
                base_slippage=0.0,
                stamp_duty=0.0005,
                schedule=schedule,
            )

        self.assertAlmostEqual(
            stock_cost, (2 * 0.00008499999 + 0.0005) * 100.0
        )
        self.assertAlmostEqual(etf_cost, 2 * 0.00005000001 * 100.0)

    def test_policy_signature_changes_when_a_decision_parameter_changes(self):
        original = decision_policy_signature()
        with patch.object(
            config,
            "TRADE_READY_MIN_REWARD_RISK",
            config.TRADE_READY_MIN_REWARD_RISK + 0.125,
        ):
            changed = decision_policy_signature()

        self.assertNotEqual(original, changed)
        self.assertEqual(original, decision_policy_signature())
        with patch.object(
            config, "BACKTEST_MAX_PROCESSES", config.BACKTEST_MAX_PROCESSES + 1
        ):
            self.assertEqual(original, decision_policy_signature())

    def test_ranked_subset_cannot_be_ranked_again(self):
        full = stamp_ranking_contract(
            pd.DataFrame(
                {
                    "Ticker": ["000001.SZ", "000002.SZ", "510300.SH"],
                    "RunId": ["run-1", "run-1", "run-1"],
                }
            )
        )
        validate_ranking_input(full)

        with self.assertRaisesRegex(ValueError, "candidate subsets must not be re-ranked"):
            validate_ranking_input(full.head(2))

        with self.assertRaisesRegex(ValueError, "mixed RunId"):
            stamp_ranking_contract(
                pd.DataFrame(
                    {
                        "Ticker": ["000001.SZ", "000002.SZ"],
                        "RunId": ["run-1", "run-2"],
                    }
                )
            )

    def test_exit_is_delayed_through_limit_down_and_suspension(self):
        frame = pd.DataFrame(
            {
                "Open": [10.0, 9.0, 9.0, 8.9],
                "High": [10.1, 9.0, 9.0, 9.2],
                "Low": [9.9, 9.0, 9.0, 8.8],
                "Close": [10.0, 9.0, 9.0, 9.1],
                "Volume": [1_000.0, 1_000.0, 0.0, 1_000.0],
            },
            index=pd.date_range("2026-08-10", periods=4, freq="D"),
        )

        index, delay, reason = resolve_exit_index(
            "000001.SZ", frame, 1, max_delay_days=3
        )

        self.assertEqual(index, 3)
        self.assertEqual(delay, 2)
        self.assertEqual(reason, "suspended_or_zero_volume")

    def test_historical_universe_cache_invalidates_when_snapshot_changes(self):
        with TemporaryDirectory() as temp_dir:
            snapshot_dir = Path(temp_dir)
            path = snapshot_dir / "universe.csv"
            pd.DataFrame(
                {
                    "Ticker": ["000001.SZ"],
                    "AsOf": ["2026-01-01"],
                    "Eligible": [True],
                }
            ).to_csv(path, index=False, encoding="utf-8-sig")

            self.assertEqual(
                point_in_time_eligibility(
                    "000001.SZ", "2026-08-01", snapshot_dir
                )[0],
                True,
            )
            self.assertTrue(historical_universe_status(snapshot_dir)["available"])

            pd.DataFrame(
                {
                    "Ticker": ["000001.SZ"],
                    "AsOf": ["2026-01-01"],
                    "Eligible": [False],
                    "ExclusionReason": ["ST"],
                }
            ).to_csv(path, index=False, encoding="utf-8-sig")

            self.assertEqual(
                point_in_time_eligibility(
                    "000001.SZ", "2026-08-01", snapshot_dir
                ),
                (False, "ST"),
            )

    def test_gui_reads_last_archive_while_publication_is_in_progress(self):
        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            old_run = output_dir / "runs" / "old-run"
            old_run.mkdir(parents=True)
            (output_dir / "LatestRun.json").write_text(
                json.dumps({"run_dir": "runs/old-run"}), encoding="utf-8"
            )
            (output_dir / "PublicationStatus.json").write_text(
                json.dumps({"status": "publishing"}), encoding="utf-8"
            )

            self.assertEqual(gui._published_output_dir(output_dir), old_run)

            (output_dir / "PublicationStatus.json").write_text(
                json.dumps({"status": "published"}), encoding="utf-8"
            )
            self.assertEqual(gui._published_output_dir(output_dir), output_dir)

    def test_staged_outputs_publish_together_and_include_scan_performance(self):
        with TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir)
            stage_dir = output_dir / ".staging" / "run-1"
            stage_dir.mkdir(parents=True)
            (output_dir / "Top50Mixed.csv").write_text("old", encoding="utf-8")
            (stage_dir / "Top50Mixed.csv").write_text("new", encoding="utf-8")
            (stage_dir / "ScanPerformance.json").write_text(
                '{"total_seconds": 1}', encoding="utf-8"
            )

            self.assertEqual(
                (output_dir / "Top50Mixed.csv").read_text(encoding="utf-8"),
                "old",
            )
            with patch.object(daily_pipeline, "OUTPUT_DIR", output_dir):
                daily_pipeline._publish_staging(stage_dir)

            self.assertEqual(
                (output_dir / "Top50Mixed.csv").read_text(encoding="utf-8"),
                "new",
            )
            self.assertTrue((output_dir / "ScanPerformance.json").exists())

    def test_calibration_stability_requires_multiple_positive_folds(self):
        stable = calibration_stability_stats(
            [
                {"rank_ic": 0.12, "top_bottom_spread20": 1.5},
                {"rank_ic": 0.08, "top_bottom_spread20": 0.8},
                {"rank_ic": -0.03, "top_bottom_spread20": -0.4},
            ]
        )
        unstable = calibration_stability_stats(
            [
                {"rank_ic": 0.10, "top_bottom_spread20": 1.0},
                {"rank_ic": -0.05, "top_bottom_spread20": -0.5},
                {"rank_ic": -0.02, "top_bottom_spread20": 0.2},
            ]
        )

        self.assertEqual(stable["status"], "STABLE")
        self.assertAlmostEqual(stable["confidence_multiplier"], 0.6667)
        self.assertEqual(unstable["status"], "UNSTABLE")
        self.assertAlmostEqual(unstable["confidence_multiplier"], 0.3333)

    def test_daily_health_and_run_diff_expose_decision_changes(self):
        with TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            previous = directory / "previous.csv"
            current = directory / "current.csv"
            base = {
                "HardGatePassed": True,
                "QualityApplicable": True,
                "QualityHardDataComplete": True,
                "BacktestRequested": True,
                "BacktestEligibleForRanking": True,
                "HardRiskFlag": False,
                "DataTradingAgeDays": 0,
            }
            pd.DataFrame(
                [
                    {
                        **base,
                        "Ticker": "000001.SZ",
                        "RankingEligibility": "观察",
                        "RankingScore": 40.0,
                    }
                ]
            ).to_csv(previous, index=False, encoding="utf-8-sig")
            pd.DataFrame(
                [
                    {
                        **base,
                        "Ticker": "000001.SZ",
                        "RankingEligibility": "推荐",
                        "RankingScore": 47.0,
                    },
                    {
                        **base,
                        "Ticker": "510300.SH",
                        "RankingEligibility": "观察",
                        "RankingScore": 38.0,
                    },
                ]
            ).to_csv(current, index=False, encoding="utf-8-sig")

            health = daily_pipeline._decision_health(current)
            difference = daily_pipeline._run_diff(previous, current)

        self.assertEqual(health["rows"], 2)
        self.assertEqual(health["eligibility"]["推荐"], 1)
        self.assertEqual(difference["added"], 1)
        self.assertEqual(difference["eligibility_upgraded"], 1)
        self.assertEqual(difference["score_up_5_plus"], 1)

    def test_execution_cost_is_finite_for_missing_liquidity_inputs(self):
        value = round_trip_cost_percent(
            is_etf=False,
            entry_price=np.nan,
            entry_volume=np.nan,
            exit_price=np.nan,
            exit_volume=np.nan,
            base_slippage=0.001,
            stamp_duty=0.0005,
            schedule=BrokerFeeSchedule(),
        )

        self.assertTrue(np.isfinite(value))
        self.assertGreater(value, 0.0)
