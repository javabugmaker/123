from __future__ import annotations

import unittest

import pandas as pd

import analytics_core
import config
import model_calibration
import signal_lifecycle
from classification import etf_research_eligibility, etf_tracking_key
from research_policy_v87 import vectorized_etf_research_policy


class DirectionalResearchV87Tests(unittest.TestCase):
    def test_versions_preserve_raw_scoring_formula_boundary(self) -> None:
        self.assertIn("v87", config.PIPELINE_VERSION)
        self.assertIn("v87", config.DECISION_INTEGRITY_VERSION)
        self.assertIn("v87", config.BACKTEST_PROVENANCE_VERSION)
        self.assertNotIn("v87", config.SCORING_VERSION)

    def test_cash_equivalent_name_and_behaviour_are_excluded(self) -> None:
        explicit, reason = etf_research_eligibility(
            is_etf=True,
            name="招商财富宝ETF",
            industry="财富宝",
        )
        self.assertFalse(explicit)
        self.assertIn("货币现金管理", reason)

        frame = pd.DataFrame(
            {
                "Ticker": ["511850.SH", "159001.SZ", "510300.SH"],
                "Name": ["未知稳健ETF", "现金流ETF", "沪深300ETF"],
                "Industry": ["其他", "因子", "宽基"],
                "Sector": ["其他", "股票", "股票"],
                "AssetType": ["etf", "etf", "etf"],
                "IsETF": [True, True, True],
                "ModelClassification": ["未知", "现金流因子", "沪深300"],
                "Close": [100.0, 1.0, 4.0],
                "ATR14": [0.10, 0.015, 0.05],
                "RecentReturn20D": [0.10, 2.0, 1.0],
            },
            index=[7, 7, 9],
        )
        eligible, reasons = vectorized_etf_research_policy(frame)
        self.assertEqual(eligible.tolist(), [False, True, True])
        self.assertIn("现金等价特征", str(reasons[0]))
        self.assertEqual(str(reasons[1]), "")

    def test_gold_products_share_one_tracking_key(self) -> None:
        self.assertEqual(etf_tracking_key(name="上海金ETF"), "黄金")
        self.assertEqual(etf_tracking_key(name="金ETF广发"), "黄金")

    def test_directional_gate_blocks_cash_product_without_rewriting_rank(self) -> None:
        frame = pd.DataFrame(
            {
                "Ticker": ["511850.SH", "510300.SH"],
                "Name": ["招商财富宝ETF", "沪深300ETF"],
                "Industry": ["财富宝", "宽基"],
                "AssetType": ["etf", "etf"],
                "IsETF": [True, True],
                "Close": [100.016, 4.0],
                "ATR14": [0.0141, 0.05],
                "RecentReturn20D": [0.006, 1.0],
                "DecisionState": ["READY", "READY"],
                "RankingEligibility": ["推荐", "推荐"],
                "TradeReadiness": ["推荐", "推荐"],
                "RankingScore": [44.4643, 60.0],
            }
        )
        before = frame["RankingScore"].copy()
        signal_lifecycle._apply_directional_research_gate(
            frame, pd.Series([True, True])
        )
        self.assertEqual(frame.loc[0, "DecisionState"], "BLOCKED")
        self.assertEqual(frame.loc[0, "RankingEligibility"], "风险过滤")
        self.assertEqual(frame.loc[1, "DecisionState"], "READY")
        pd.testing.assert_series_equal(frame["RankingScore"], before)


class ExecutionEconomicsV87Tests(unittest.TestCase):
    def _frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "Ticker": ["511850.SH", "513170.SH"],
                "IsETF": [True, True],
                "AssetType": ["etf", "etf"],
                "EntrySignal": ["BREAKOUT_CONFIRM", "BREAKOUT_CONFIRM"],
                "Close": [100.016, 1.02],
                "BreakoutBuyPrice": [100.014, 1.00],
                "ProjectedTarget": [100.051, 1.05],
                "MedianTurnover60": [100_000_000.0, 100_000_000.0],
                "DecisionState": ["READY", "READY"],
                "RankingEligibility": ["推荐", "推荐"],
                "TradeReadiness": ["推荐", "推荐"],
                "RankingScore": [44.4643, 70.0],
            }
        )

    def test_weak_breakout_is_observe_but_strong_breakout_remains_ready(self) -> None:
        frame = self._frame()
        before = frame["RankingScore"].copy()
        signal_lifecycle._apply_breakout_price_gate(frame)
        self.assertLess(frame.loc[0, "BreakoutPriceConfirmationScore"], 60.0)
        self.assertFalse(bool(frame.loc[0, "BreakoutPriceGatePassed"]))
        self.assertEqual(frame.loc[0, "DecisionState"], "OBSERVE")
        self.assertTrue(bool(frame.loc[1, "BreakoutPriceGatePassed"]))
        self.assertEqual(frame.loc[1, "DecisionState"], "READY")
        pd.testing.assert_series_equal(frame["RankingScore"], before)

    def test_target_must_cover_estimated_round_trip_cost(self) -> None:
        frame = self._frame()
        signal_lifecycle._apply_trade_economics_gate(
            frame, pd.Series([True, True])
        )
        self.assertLess(frame.loc[0, "TradeTargetCostMultiple"], 1.0)
        self.assertFalse(bool(frame.loc[0, "TradeEconomicsPassed"]))
        self.assertEqual(frame.loc[0, "DecisionState"], "OBSERVE")
        self.assertGreater(frame.loc[1, "TradeTargetCostMultiple"], 10.0)
        self.assertTrue(bool(frame.loc[1, "TradeEconomicsPassed"]))


class BacktestMathV87Tests(unittest.TestCase):
    def test_outcome_windows_crossing_split_boundaries_are_purged(self) -> None:
        split_dates = (pd.Timestamp("2024-01-01"), pd.Timestamp("2025-01-01"))
        cases = [
            ("2023-10-01", "2023-12-29", "train"),
            ("2023-12-01", "2024-02-01", "purged"),
            ("2024-02-01", "2024-12-31", "validation"),
            ("2024-12-01", "2025-02-01", "purged"),
            ("2025-01-02", "2025-04-01", "test"),
        ]
        for entry, outcome, expected in cases:
            with self.subTest(entry=entry, outcome=outcome):
                self.assertEqual(
                    analytics_core._purged_split_label(
                        entry, outcome, split_dates
                    ),
                    expected,
                )

    def test_backtest_score_uses_weighted_net_excess_not_gross_returns(self) -> None:
        base = pd.DataFrame(
            {
                "ticker": ["000001.SZ", "000001.SZ"],
                "entry_signal": ["BUY_NOW", "BUY_NOW"],
                "return20": [10.0, -1.0],
                "return60": [12.0, -2.0],
                "benchmark_return20": [12.0, -5.0],
                "benchmark_return60": [14.0, -6.0],
                "net_return20": [9.0, -2.0],
                "net_return60": [11.0, -3.0],
                "drawdown20": [-4.0, -4.0],
                "drawdown60": [-7.0, -7.0],
                "sample_weight": [1.0, 0.1],
            }
        )
        weighted_bad = analytics_core._ticker_backtest_rows(base)[0]
        reversed_weights = base.copy()
        reversed_weights["sample_weight"] = [0.1, 1.0]
        weighted_good = analytics_core._ticker_backtest_rows(reversed_weights)[0]

        self.assertGreater(weighted_bad["win_rate_20d"], 0.9)
        self.assertLess(weighted_bad["net_excess_win_rate_20d"], 0.1)
        self.assertLess(weighted_bad["average_net_excess_return_20d"], 0.0)
        self.assertLess(
            weighted_bad["backtest_score"], weighted_good["backtest_score"]
        )

    def test_component_calibration_requires_effective_not_raw_sample_count(self) -> None:
        rows = []
        for index in range(40):
            rows.append(
                {
                    "split": "validation",
                    "ticker": "000001.SZ",
                    "entry_date": pd.Timestamp("2024-01-01")
                    + pd.Timedelta(index, unit="D"),
                    "asset_type": "stock",
                    "entry_signal": "BUY_NOW",
                    "score": 60.0,
                    "setup_score": float(index),
                    "trigger_score": float(40 - index),
                    "execution_score": 50.0,
                    "sample_weight": 0.1,
                    "net_return20": float(index),
                    "benchmark_return20": 0.0,
                    "net_return60": float(index),
                    "benchmark_return60": 0.0,
                }
            )
        result = model_calibration.calibrate_component_weights(pd.DataFrame(rows))
        self.assertFalse(result.accepted)
        self.assertEqual(result.validation_samples, 40)
        self.assertAlmostEqual(result.validation_effective_samples, 4.0)

    def test_exact_refinement_requires_effective_fast_evidence(self) -> None:
        frame = pd.DataFrame(
            {
                "Ticker": ["000001.SZ", "000002.SZ"],
                "EntrySignal": ["BUY_NOW", "BUY_NOW"],
                "RankingEligibility": ["推荐", "推荐"],
                "RankingScore": [80.0, 70.0],
            }
        )
        fast_rows = [
            {
                "ticker": "000001.SZ",
                "entry_signal": "BUY_NOW",
                "samples": 30,
                "effective_samples": 4.0,
            },
            {
                "ticker": "000002.SZ",
                "entry_signal": "BUY_NOW",
                "samples": 20,
                "effective_samples": 10.0,
            },
        ]
        pool = analytics_core._select_exact_refinement_pool(
            frame, fast_rows, top_n=2
        )
        self.assertEqual(pool["Ticker"].tolist(), ["000002.SZ"])


if __name__ == "__main__":
    unittest.main()
