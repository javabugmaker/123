from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import config
import signal_lifecycle


class V54TradeLiquidityIntegrityTests(unittest.TestCase):
    def test_versions_and_thresholds_mark_v54_contract(self) -> None:
        self.assertIn("v54", config.PIPELINE_VERSION)
        self.assertIn("v54", config.DECISION_INTEGRITY_VERSION)
        self.assertIn("v54", config.OUTPUT_CONTRACT_VERSION)
        self.assertEqual(config.TRADE_READY_MIN_MEDIAN_TURNOVER_60D, 5_000_000.0)
        self.assertEqual(config.TRADE_READY_MAX_ASSUMED_PARTICIPATION_RATE, 0.01)
        self.assertEqual(signal_lifecycle._trade_liquidity_threshold(), 5_000_000.0)

    def test_execution_gate_demotes_thin_ready_without_changing_rank(self) -> None:
        frame = pd.DataFrame(
            {
                "Ticker": ["510600.SH", "600000.SH", "430001.BJ"],
                "MedianTurnover60": [2_516_500.0, 6_000_000.0, 5_000_000.0],
                "DecisionState": ["READY", "READY", "CAUTIOUS"],
                "RankingEligibility": ["推荐", "推荐", "谨慎候选"],
                "TradeReadiness": ["推荐", "推荐", "谨慎候选"],
                "RankingScore": [91.0, 82.0, 73.0],
            }
        )
        original_rank = frame["RankingScore"].copy()

        demoted = signal_lifecycle._apply_trade_liquidity_gate(frame)

        self.assertTrue(bool(demoted.iloc[0]))
        self.assertFalse(bool(demoted.iloc[1]))
        self.assertFalse(bool(demoted.iloc[2]))
        self.assertEqual(frame.loc[0, "DecisionState"], "OBSERVE")
        self.assertEqual(frame.loc[0, "RankingEligibility"], "观察")
        self.assertEqual(frame.loc[1, "DecisionState"], "READY")
        self.assertEqual(frame.loc[2, "DecisionState"], "CAUTIOUS")
        self.assertEqual(frame.loc[0, "TradeLiquidityStatus"], "FAIL")
        self.assertEqual(frame.loc[1, "TradeLiquidityStatus"], "PASS")
        self.assertEqual(frame.loc[2, "TradeLiquidityStatus"], "PASS")
        pd.testing.assert_series_equal(frame["RankingScore"], original_rank)

    def test_missing_current_turnover_fails_closed_but_legacy_schema_is_compatible(self) -> None:
        current = pd.DataFrame(
            {
                "Ticker": ["300001.SZ"],
                "MedianTurnover60": [np.nan],
                "DecisionState": ["READY"],
                "RankingEligibility": ["推荐"],
                "TradeReadiness": ["推荐"],
            }
        )
        signal_lifecycle._apply_trade_liquidity_gate(current)
        self.assertEqual(current.loc[0, "DecisionState"], "OBSERVE")
        self.assertEqual(current.loc[0, "TradeLiquidityStatus"], "FAIL")

        legacy = pd.DataFrame(
            {
                "Ticker": ["300001.SZ"],
                "DecisionState": ["READY"],
                "RankingEligibility": ["推荐"],
                "TradeReadiness": ["推荐"],
            }
        )
        signal_lifecycle._apply_trade_liquidity_gate(legacy)
        self.assertEqual(legacy.loc[0, "DecisionState"], "READY")
        self.assertEqual(legacy.loc[0, "TradeLiquidityStatus"], "LEGACY_UNKNOWN")
        self.assertFalse(bool(legacy.loc[0, "TradeLiquidityApplicable"]))

    def test_board_diagnostics_are_observability_only(self) -> None:
        frame = pd.DataFrame(
            {
                "Ticker": [
                    "510300.SH",
                    "430001.BJ",
                    "688001.SH",
                    "300001.SZ",
                    "600000.SH",
                    "000001.SZ",
                ],
                "RankingScore": [90.0, 80.0, 70.0, 60.0, 50.0, 40.0],
            }
        )
        corrected = pd.Series([90.0, 80.0, 70.0, 60.0, 50.0, 40.0])
        is_etf = pd.Series([True, False, False, False, False, False])
        original_rank = frame["RankingScore"].copy()

        signal_lifecycle._add_board_diagnostics(frame, corrected, is_etf)

        self.assertEqual(
            frame["TradingBoard"].tolist(),
            ["ETF", "北交所", "科创板", "创业板", "沪市主板", "深市主板"],
        )
        self.assertTrue(frame["BoardDiagnosticOnly"].all())
        pd.testing.assert_series_equal(frame["RankingScore"], original_rank)


if __name__ == "__main__":
    unittest.main()
