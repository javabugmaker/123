from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import analytics_core
from classification import (
    etf_research_eligibility,
    etf_theme_key,
)
from research_policy_v87 import vectorized_etf_research_policy


class BacktestMathIntegrityFollowupTests(unittest.TestCase):
    def test_all_winning_history_keeps_saturated_profit_factor(self) -> None:
        frame = pd.DataFrame(
            {
                "ticker": ["TEST.SH", "TEST.SH"],
                "entry_signal": ["BUY_NOW", "BUY_NOW"],
                "return20": [2.0, 5.0],
                "return60": [3.0, 7.0],
                "benchmark_return20": [0.0, 0.0],
                "benchmark_return60": [0.0, 0.0],
                "net_return20": [1.8, 4.8],
                "net_return60": [2.8, 6.8],
                "drawdown20": [-2.0, -3.0],
                "drawdown60": [-4.0, -5.0],
                "sample_weight": [1.0, 0.5],
            }
        )

        row = analytics_core._ticker_backtest_rows(frame)[0]

        self.assertEqual(row["profit_factor"], 3.0)
        self.assertEqual(row["net_excess_profit_factor"], 3.0)
        self.assertFalse(np.isnan(row["profit_factor"]))

    def test_summary_discloses_complete_outcome_split_policy(self) -> None:
        summary = analytics_core.BacktestSummary()

        self.assertEqual(
            summary.to_dict()["split_policy"],
            "purged_by_complete_60d_outcome_window_v1",
        )


class DirectionalEtfPolicyFollowupTests(unittest.TestCase):
    def test_digital_currency_is_not_treated_as_a_cash_etf(self) -> None:
        self.assertEqual(etf_theme_key(name="数字货币ETF"), "数字货币")
        eligible, reason = etf_research_eligibility(
            is_etf=True,
            name="数字货币ETF",
        )

        self.assertTrue(eligible)
        self.assertEqual(reason, "")

    def test_cash_management_aliases_are_excluded_without_harming_factors(self) -> None:
        for name in ("银华日利ETF", "保证金ETF", "招商快线ETF", "场内货币ETF"):
            with self.subTest(name=name):
                eligible, reason = etf_research_eligibility(
                    is_etf=True,
                    name=name,
                )
                self.assertFalse(eligible)
                self.assertIn("排除", reason)

        cashflow_eligible, _ = etf_research_eligibility(
            is_etf=True,
            name="现金流ETF",
            classification="现金流因子",
        )
        self.assertTrue(cashflow_eligible)

    def test_bulk_policy_matches_scalar_currency_and_cash_alias_rules(self) -> None:
        frame = pd.DataFrame(
            {
                "Ticker": [
                    "159999.SZ",
                    "560000.SH",
                    "511880.SH",
                    "159003.SZ",
                ],
                "Name": [
                    "数字货币ETF",
                    "现金流ETF",
                    "银华日利ETF",
                    "招商快线ETF",
                ],
                "Industry": ["", "因子", "", ""],
                "Sector": ["", "股票", "", ""],
                "ModelClassification": ["", "现金流因子", "", ""],
                "AssetType": ["etf"] * 4,
                "IsETF": [True] * 4,
            }
        )

        eligible, reasons = vectorized_etf_research_policy(frame)

        self.assertEqual(eligible.tolist(), [True, True, False, False])
        self.assertEqual(str(reasons[0]), "")
        self.assertEqual(str(reasons[1]), "")
        self.assertIn("排除", str(reasons[2]))
        self.assertIn("排除", str(reasons[3]))


if __name__ == "__main__":
    unittest.main()
