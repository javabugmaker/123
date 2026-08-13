from __future__ import annotations

import unittest
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from analytics import (
    BacktestSummary,
    _historical_entry_signal,
    apply_backtest_ranking,
)
from config import (
    DECISION_INTEGRITY_VERSION,
    OUTPUT_CONTRACT_VERSION,
    PIPELINE_VERSION,
    SCORING_VERSION,
)
from report import _results_to_dataframe
from scanner import ScanResult
from score import entry_point
from signal_lifecycle import finalize_signal_ranking


class V44OutputReproducibilityTests(unittest.TestCase):
    def test_lifecycle_preserves_complete_upstream_backtest_calibration(self) -> None:
        final_score = 24.11
        adjusted_score = 50.0
        effective_weight = 0.15
        composite_score = (
            final_score * (1.0 - effective_weight)
            + adjusted_score * effective_weight
        )
        frame = pd.DataFrame(
            {
                "Ticker": ["512190.SH"],
                "Score": [final_score],
                "FinalScore": [final_score],
                "InstitutionalScore": [final_score],
                "EntrySignal": ["WAIT_PULLBACK"],
                "IsETF": [True],
                "AssetType": ["etf"],
                "BacktestSamples": [0],
                "BacktestEffectiveSamples": [0.0],
                "BacktestScore": [50.0],
                "BacktestReliability": [0.0],
                "BacktestEffectiveWeight": [effective_weight],
                "BacktestConfidenceTier": ["全局校准"],
                "BacktestAdjustedScore": [adjusted_score],
                "CompositeScore": [composite_score],
            }
        )

        result = finalize_signal_ranking(frame).iloc[0]

        self.assertEqual(result["BacktestReliability"], 0.0)
        self.assertEqual(result["BacktestEffectiveWeight"], effective_weight)
        self.assertEqual(result["BacktestConfidenceTier"], "全局校准")
        self.assertEqual(result["BacktestAdjustedScore"], adjusted_score)
        self.assertAlmostEqual(result["CompositeScore"], composite_score, places=12)

    def test_repeated_backtest_ranking_is_numerically_idempotent(self) -> None:
        summary = BacktestSummary(
            mode="exact",
            by_ticker=[
                {
                    "ticker": "510050.SH",
                    "entry_signal": "WAIT_PULLBACK",
                    "samples": 12,
                    "effective_samples": 10.0,
                    "win_rate_20d": 0.65,
                    "win_rate_60d": 0.60,
                    "average_return_20d": 4.0,
                    "average_return_60d": 7.0,
                    "objective_value": 3.0,
                    "backtest_score": 75.0,
                    "profit_factor": 1.4,
                    "max_drawdown_60d": -8.0,
                    "return_std_20d": 5.0,
                }
            ],
        )
        source = pd.DataFrame(
            {
                "Ticker": ["510050.SH"],
                "Name": ["上证50ETF"],
                "Sector": ["宽基"],
                "Score": [70.0],
                "FinalScore": [70.0],
                "InstitutionalScore": [80.0],
                "EntrySignal": ["WAIT_PULLBACK"],
                "RawEntrySignal": ["WAIT_PULLBACK"],
                "PassedFilters": [True],
                "UniverseEligible": [True],
                "SignalConfirmed": [True],
                "IsETF": [True],
                "AssetType": ["etf"],
                "QualityApplicable": [False],
                "SignalStartDate": ["2026-08-13"],
                "DataAsOf": ["2026-08-13"],
                "DataAgeDays": [0],
                "DataTradingAgeDays": [0],
            }
        )

        with TemporaryDirectory() as temp_dir, patch(
            "analytics.OUTPUT_DIR", Path(temp_dir)
        ), patch("report._atomic_write_parquet"):
            output_path = Path(temp_dir) / "AllResults.csv"
            source.to_csv(output_path, index=False, encoding="utf-8-sig")
            apply_backtest_ranking(summary)
            first = pd.read_csv(output_path, encoding="utf-8-sig")
            apply_backtest_ranking(summary)
            second = pd.read_csv(output_path, encoding="utf-8-sig")

        columns = [
            "InstitutionalScore",
            "CrossAssetScore",
            "RankingScore",
            "CompositeScore",
            "BacktestReliability",
            "BacktestEffectiveWeight",
            "BacktestAdjustedScore",
        ]
        np.testing.assert_allclose(
            first[columns].to_numpy(dtype=float),
            second[columns].to_numpy(dtype=float),
            rtol=0.0,
            atol=1e-12,
        )
        self.assertEqual(first.loc[0, "PreBacktestInstitutionalScore"], 80.0)
        self.assertEqual(second.loc[0, "PreBacktestInstitutionalScore"], 80.0)

    def test_etf_execution_prices_and_risk_share_mill_precision(self) -> None:
        index = pd.date_range("2026-06-01", periods=40, freq="B")
        close = pd.Series(np.linspace(0.500, 0.550, len(index)), index=index)
        frame = pd.DataFrame(
            {
                "Close": close,
                "High": close + 0.0067,
                "Low": close - 0.0043,
                "Volume": 1_000_000.0,
                "ATR14": 0.01234,
                "MA20": 0.535,
                "MA50": 0.520,
                "RSI14": 58.0,
                "CMF": 0.10,
                "AD_Slope": 1.0,
                "OBV": np.arange(len(index), dtype=float),
            },
            index=index,
        )

        entry = entry_point(frame, breakout=60.0, price_decimals=3)
        for column in ("low", "high", "breakout", "stop", "projected_target"):
            self.assertAlmostEqual(entry[column], round(entry[column], 3), places=12)
        self.assertNotEqual(entry["stop"], round(entry["stop"], 2))

        price = float(frame["Close"].iloc[-1])
        risk = max(price - entry["stop"], 0.0)
        reward = max(entry["projected_target"] - price, 0.0)
        self.assertAlmostEqual(
            entry["stop_distance_pct"], risk / price * 100.0, places=12
        )
        self.assertAlmostEqual(
            entry["reward_risk_ratio"], reward / risk, places=12
        )

        exported = _results_to_dataframe(
            [
                ScanResult(
                    ticker="510050.SH",
                    is_etf=True,
                    asset_type="etf",
                    close=price,
                    breakout_buy_price=entry["breakout"],
                    stop_loss=entry["stop"],
                    projected_target=entry["projected_target"],
                    stop_distance_pct=entry["stop_distance_pct"],
                    reward_risk_ratio=entry["reward_risk_ratio"],
                )
            ]
        ).iloc[0]
        self.assertEqual(exported["StopLoss"], entry["stop"])
        self.assertEqual(exported["ProjectedTarget"], entry["projected_target"])
        self.assertAlmostEqual(
            exported["StopDistancePct"],
            (price - exported["StopLoss"]) / price * 100.0,
            places=4,
        )
        self.assertAlmostEqual(
            exported["RewardRiskRatio"],
            (exported["ProjectedTarget"] - price)
            / (price - exported["StopLoss"]),
            places=4,
        )

    def test_backtest_entry_signal_uses_same_asset_price_precision(self) -> None:
        historical_score = SimpleNamespace(
            breakout_score=60.0,
            volume=10.0,
            value_trap_risk=0.0,
        )
        with patch(
            "analytics.entry_point", return_value={"signal": "WAIT_PULLBACK"}
        ) as mocked_entry:
            signal = _historical_entry_signal(
                pd.DataFrame(), historical_score, is_etf=True
            )

        self.assertEqual(signal, "WAIT_PULLBACK")
        self.assertEqual(mocked_entry.call_args.kwargs["price_decimals"], 3)

    def test_v44_provenance_keeps_prior_contract_markers(self) -> None:
        self.assertIn("v44", SCORING_VERSION)
        self.assertIn("v43", SCORING_VERSION)
        for marker in ("v44", "v43", "v42", "v41", "v40", "v39", "v38"):
            self.assertIn(marker, PIPELINE_VERSION)
        self.assertIn("v44", DECISION_INTEGRITY_VERSION)
        self.assertIn("v44", OUTPUT_CONTRACT_VERSION)

    def test_empty_risk_text_columns_accept_stale_data_explanation(self) -> None:
        frame = pd.DataFrame(
            {
                "Ticker": ["000001.SZ"],
                "Score": [60.0],
                "FinalScore": [60.0],
                "InstitutionalScore": [60.0],
                "EntrySignal": ["WAIT_PULLBACK"],
                "DataAgeDays": [30],
                "DataTradingAgeDays": [30],
                "OperationAdvice": [np.nan],
                "RiskWarning": [np.nan],
            }
        )

        with warnings.catch_warnings():
            warnings.simplefilter("error", FutureWarning)
            result = finalize_signal_ranking(frame).iloc[0]

        self.assertEqual(result["OperationAdvice"], "行情数据已过期，请刷新后再判断。")
        self.assertEqual(result["RiskWarning"], "行情数据过期")


if __name__ == "__main__":
    unittest.main()
