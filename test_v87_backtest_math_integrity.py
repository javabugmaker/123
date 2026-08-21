from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import analytics
import config
import model_calibration
import report_core
from classification import etf_research_eligibility


class V87BacktestMathIntegrityTests(unittest.TestCase):
    @staticmethod
    def _metric_frame(returns: list[float], weights: list[float]) -> pd.DataFrame:
        count = len(returns)
        return pd.DataFrame(
            {
                "ticker": ["TEST.SH"] * count,
                "entry_signal": ["BUY_NOW"] * count,
                "signal_date": pd.date_range("2024-01-01", periods=count).strftime(
                    "%Y-%m-%d"
                ),
                "return20": returns,
                "return60": returns,
                "benchmark_return20": [0.0] * count,
                "benchmark_return60": [0.0] * count,
                "net_return20": returns,
                "net_return60": returns,
                "drawdown20": [-5.0] * count,
                "drawdown60": [-8.0] * count,
                "sample_weight": weights,
            }
        )

    def test_complete_outcome_window_is_purged_at_split_boundaries(self) -> None:
        samples = [
            {
                "entry_date": "2024-01-01",
                "exit20_date": "2024-01-05",
                "exit60_date": "2024-01-09",
            },
            {
                "entry_date": "2024-01-02",
                "exit20_date": "2024-01-08",
                "exit60_date": "2024-01-10",
            },
            {
                "entry_date": "2024-01-10",
                "exit20_date": "2024-01-15",
                "exit60_date": "2024-01-19",
            },
            {
                "entry_date": "2024-01-11",
                "exit20_date": "2024-01-18",
                "exit60_date": "2024-01-20",
            },
            {
                "entry_date": "2024-01-20",
                "exit20_date": "2024-01-25",
                "exit60_date": "2024-02-01",
            },
            {"entry_date": "2024-01-03"},
        ]

        relabeled = analytics._relabel_sample_splits(
            samples,
            (pd.Timestamp("2024-01-10"), pd.Timestamp("2024-01-20")),
        )

        self.assertEqual(
            [row["split"] for row in relabeled],
            ["train", "purged", "validation", "purged", "test", "train"],
        )

    def test_overlap_weights_drive_backtest_statistics_and_objective(self) -> None:
        frame = self._metric_frame([100.0, -10.0], [0.01, 1.0])

        row = analytics._ticker_backtest_rows(frame)[0]

        self.assertAlmostEqual(row["win_rate_20d"], 0.0099, places=4)
        self.assertEqual(row["average_return_20d"], -10.0)
        self.assertEqual(row["raw_objective_value"], -10.0)
        self.assertEqual(row["profit_factor"], 0.1)

    def test_all_winning_history_receives_saturated_profit_factor(self) -> None:
        frame = self._metric_frame([2.0, 5.0], [1.0, 1.0])

        row = analytics._ticker_backtest_rows(frame)[0]

        self.assertEqual(row["profit_factor"], 3.0)

    def test_walk_forward_purges_cross_year_target_labels(self) -> None:
        frame = pd.DataFrame(
            {
                "ticker": ["A", "B", "C", "D", "E"],
                "asset_type": ["stock"] * 5,
                "entry_signal": ["BUY_NOW"] * 5,
                "market_regime": ["NEUTRAL"] * 5,
                "entry_date": [
                    "2020-06-01",
                    "2020-12-20",
                    "2021-02-01",
                    "2021-04-01",
                    "2021-12-20",
                ],
                "exit20_date": [
                    "2020-07-01",
                    "2021-01-15",
                    "2021-03-01",
                    "2021-05-01",
                    "2022-01-20",
                ],
                "score": [40.0, 45.0, 50.0, 60.0, 70.0],
                "setup_score": [40.0, 45.0, 50.0, 60.0, 70.0],
                "sample_weight": [1.0] * 5,
                "net_return20": [1.0, 1.0, 2.0, 3.0, 4.0],
                "net_return60": [1.0, 1.0, 2.0, 3.0, 4.0],
                "benchmark_return20": [0.0] * 5,
                "benchmark_return60": [0.0] * 5,
            }
        )
        observed_train_sizes: list[int] = []

        def fake_calibration(train: pd.DataFrame) -> list[dict[str, object]]:
            observed_train_sizes.append(len(train))
            return []

        def fake_scores(
            test: pd.DataFrame, calibration: list[dict[str, object]]
        ) -> tuple[pd.Series, pd.Series]:
            del calibration
            return (
                pd.to_numeric(test["FinalScore"], errors="coerce").reset_index(
                    drop=True
                ),
                pd.Series(1.0, index=range(len(test))),
            )

        with patch.object(
            model_calibration,
            "build_global_calibration",
            side_effect=fake_calibration,
        ), patch.object(
            model_calibration,
            "calibration_scores_for_frame",
            side_effect=fake_scores,
        ):
            rows = model_calibration.walk_forward_stats(
                frame, min_train_samples=1, min_test_samples=1
            )

        self.assertEqual(observed_train_sizes, [1])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["year"], 2021)
        self.assertEqual(rows[0]["test_samples"], 2)

    def test_wealth_management_etf_is_excluded_on_scalar_and_bulk_paths(self) -> None:
        eligible, reason = etf_research_eligibility(
            is_etf=True,
            name="招商财富宝ETF",
            ticker="511850.SH",
        )
        self.assertFalse(eligible)
        self.assertIn("排除", reason)

        frame = pd.DataFrame(
            {
                "Ticker": ["511850.SH", "560000.SH", "159999.SZ"],
                "Name": ["招商财富宝ETF", "现金流ETF", "数字货币ETF"],
                "Industry": ["", "", ""],
                "Sector": ["", "", ""],
                "ModelClassification": ["", "现金流因子", "数字货币"],
            }
        )
        bulk_eligible, _ = report_core._vectorized_etf_research_policy(
            frame, np.array([True, True, True], dtype=bool)
        )
        self.assertEqual(bulk_eligible.tolist(), [False, True, True])

    def test_v87_provenance_versions_are_published(self) -> None:
        self.assertIn("v87", config.PIPELINE_VERSION)
        self.assertIn("v87", config.OUTPUT_CONTRACT_VERSION)
        self.assertIn("v87", config.BACKTEST_PROVENANCE_VERSION)


if __name__ == "__main__":
    unittest.main()
