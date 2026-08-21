from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd

import analytics_core as analytics
import config
import model_calibration
import trading_calendar
from classification import etf_research_eligibility, etf_theme_key
from model_calibration import build_global_calibration
from score import score_ticker


class ResearchIntegrityV87Tests(unittest.TestCase):
    def test_official_2026_exchange_closures_survive_missing_dependency(self) -> None:
        with patch.object(trading_calendar, "holidays", None):
            trading_calendar._china_holidays.cache_clear()
            try:
                for closed_day in (
                    date(2026, 1, 1),
                    date(2026, 2, 16),
                    date(2026, 4, 6),
                    date(2026, 5, 4),
                    date(2026, 6, 19),
                    date(2026, 9, 25),
                    date(2026, 10, 7),
                ):
                    self.assertFalse(trading_calendar.is_trading_day(closed_day))
                self.assertTrue(trading_calendar.is_trading_day(date(2026, 10, 8)))
            finally:
                trading_calendar._china_holidays.cache_clear()

    def test_wealth_treasure_cash_etf_is_not_directional_research(self) -> None:
        classification = etf_theme_key(
            name="招商财富宝ETF",
            industry="",
            sector="",
            ticker="511850.SH",
        )
        eligible, reason = etf_research_eligibility(
            is_etf=True,
            name="招商财富宝ETF",
            classification=classification,
            ticker="511850.SH",
        )

        self.assertEqual(classification, "货币现金管理")
        self.assertFalse(eligible)
        self.assertIn("排除", reason)

        factor_eligible, _ = etf_research_eligibility(
            is_etf=True,
            name="现金流ETF",
            classification="现金流因子",
            ticker="159001.SZ",
        )
        self.assertTrue(factor_eligible)

    def test_outcomes_crossing_split_boundaries_are_purged(self) -> None:
        boundaries = (pd.Timestamp("2025-07-01"), pd.Timestamp("2026-01-01"))

        self.assertEqual(
            analytics._purged_sample_split(
                "2025-05-01", "2025-06-30", boundaries
            ),
            "train",
        )
        self.assertEqual(
            analytics._purged_sample_split(
                "2025-05-01", "2025-07-01", boundaries
            ),
            "purged",
        )
        self.assertEqual(
            analytics._purged_sample_split(
                "2025-08-01", "2025-12-31", boundaries
            ),
            "validation",
        )
        self.assertEqual(
            analytics._purged_sample_split(
                "2025-11-01", "2026-01-01", boundaries
            ),
            "purged",
        )
        self.assertEqual(
            analytics._purged_sample_split(
                "2026-01-01", "2026-03-31", boundaries
            ),
            "test",
        )

    def test_missing_setup_dimensions_are_zero_evidence_not_score_uplift(self) -> None:
        frame = pd.DataFrame(
            {
                "Close": [10.0] * 252,
                "High": [11.0] * 252,
                "Low": [9.0] * 252,
                "Volume": [1000.0] * 252,
                "MA200": [9.0] * 252,
                "VolMA20": [np.nan] * 252,
                "OBV": [np.nan] * 252,
                "ATR14": [np.nan] * 252,
            }
        )

        result = score_ticker(frame)

        self.assertEqual(result.indicator_coverage, 0.4)
        self.assertGreater(result.total, 0.0)
        self.assertEqual(result.total, result.trend + result.structure)

    def test_overlap_weights_change_ticker_estimates_not_only_confidence(self) -> None:
        frame = pd.DataFrame(
            {
                "ticker": ["000001.SZ", "000001.SZ"],
                "entry_signal": ["WAIT_PULLBACK", "WAIT_PULLBACK"],
                "signal_date": ["2025-01-01", "2025-01-02"],
                "entry_date": ["2025-01-02", "2025-01-03"],
                "return20": [10.0, -10.0],
                "return60": [10.0, -10.0],
                "net_return20": [10.0, -10.0],
                "net_return60": [10.0, -10.0],
                "benchmark_return20": [0.0, 0.0],
                "benchmark_return60": [0.0, 0.0],
                "drawdown20": [-2.0, -4.0],
                "drawdown60": [-3.0, -5.0],
                "sample_weight": [1.0, 0.1],
            }
        )

        row = analytics._ticker_backtest_rows(frame)[0]

        self.assertAlmostEqual(row["win_rate_20d"], 1.0 / 1.1, places=4)
        self.assertEqual(row["average_return_20d"], 10.0)
        self.assertEqual(row["raw_objective_value"], 10.0)
        self.assertEqual(row["profit_factor"], 10.0)

    def test_global_calibration_equal_weights_signal_dates(self) -> None:
        rows: list[dict[str, object]] = []
        for index in range(20):
            rows.append(
                {
                    "ticker": f"000{index:03d}.SZ",
                    "asset_type": "stock",
                    "entry_signal": "WAIT_PULLBACK",
                    "entry_date": "2025-01-02",
                    "score": 60.0,
                    "setup_score": 60.0,
                    "sample_weight": 1.0,
                    "net_return20": 10.0,
                    "benchmark_return20": 0.0,
                    "net_return60": 10.0,
                    "benchmark_return60": 0.0,
                }
            )
        for entry_date, outcome in (("2025-01-03", -10.0), ("2025-01-06", 0.0)):
            rows.append(
                {
                    "ticker": f"90000{len(rows)}.SH",
                    "asset_type": "stock",
                    "entry_signal": "WAIT_PULLBACK",
                    "entry_date": entry_date,
                    "score": 60.0,
                    "setup_score": 60.0,
                    "sample_weight": 1.0,
                    "net_return20": outcome,
                    "benchmark_return20": 0.0,
                    "net_return60": outcome,
                    "benchmark_return60": 0.0,
                }
            )

        calibration = build_global_calibration(pd.DataFrame(rows), min_samples=3)
        global_row = next(row for row in calibration if row["level"] == "global")

        self.assertEqual(global_row["independent_entry_dates"], 3)
        self.assertAlmostEqual(global_row["effective_samples"], 3.0, places=4)
        self.assertAlmostEqual(global_row["mean_net_excess20"], 0.0, places=4)

    def test_component_rank_ic_uses_sample_independence_weights(self) -> None:
        score = pd.Series([1.0, 2.0, 3.0])
        target = pd.Series([1.0, 2.0, -10.0])

        raw_ic = model_calibration._spearman(score, target)
        independent_ic = model_calibration._spearman(
            score,
            target,
            pd.Series([1.0, 1.0, 0.01]),
        )

        self.assertGreater(independent_ic, raw_ic)
        self.assertGreater(independent_ic, 0.8)

    def test_only_observed_point_in_time_samples_can_calibrate_ranking(self) -> None:
        frame = pd.DataFrame(
            {
                "universe_snapshot_status": [
                    "ELIGIBLE",
                    "UNAVAILABLE",
                    "ELIGIBLE",
                ],
                "ticker": ["000001.SZ", "000002.SZ", "510300.SH"],
            }
        )

        verified = analytics._verified_point_in_time_frame(frame)

        self.assertEqual(verified["ticker"].tolist(), ["000001.SZ", "510300.SH"])

    def test_v87_formula_and_provenance_versions_are_explicit(self) -> None:
        self.assertIn("v87", config.SCORING_VERSION)
        self.assertIn("v87", config.BACKTEST_PROVENANCE_VERSION)
        self.assertIn("v87", config.OUTPUT_CONTRACT_VERSION)


if __name__ == "__main__":
    unittest.main()
