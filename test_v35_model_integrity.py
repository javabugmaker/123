from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import config
import score
from signal_lifecycle import finalize_signal_ranking


class ModelV35IntegrityTests(unittest.TestCase):
    def _decision_row(self, status: str, trend: str) -> dict[str, object]:
        return {
            "Ticker": "159999.SZ",
            "IsETF": True,
            "AssetType": "etf",
            "EntrySignal": "BUY_NOW",
            "InstitutionalScore": 60.0,
            "TechnicalInstitutionalScore": 60.0,
            "Score": 60.0,
            "FinalScore": 60.0,
            "ScoreCoverage": 1.0,
            "PassedFilters": True,
            "UniverseEligible": True,
            "ValueTrapRisk": 0.0,
            "LifecycleStage": "趋势确认",
            "SignalStatus": status,
            "SignalTrend": trend,
            "SignalRecencyDays": 0,
            "DataTradingAgeDays": 0,
            "DataAgeDays": 0,
            "RSI14": 55.0,
            "DistToLow52W": 10.0,
            "DistToMA20": 1.0,
            "RecentReturn20D": 3.0,
            "ATRExpansion": 1.0,
            "BacktestSamples": 0,
        }

    def test_fast_weaken_cannot_remain_trade_ready(self):
        frame = finalize_signal_ranking(
            pd.DataFrame([self._decision_row("WEAKEN", "快速下降")])
        )
        self.assertEqual(frame.iloc[0]["DecisionState"], "OBSERVE")
        self.assertEqual(frame.iloc[0]["RankingEligibility"], "观察")
        self.assertIn("重新增强", frame.iloc[0]["TradeReadinessReason"])
        self.assertLess(float(frame.iloc[0]["ReadinessPenaltyFactor"]), 1.0)

    def test_strengthening_signal_can_still_be_ready(self):
        frame = finalize_signal_ranking(
            pd.DataFrame([self._decision_row("STRENGTHEN", "持续增强")])
        )
        self.assertEqual(frame.iloc[0]["DecisionState"], "READY")
        self.assertEqual(frame.iloc[0]["RankingEligibility"], "推荐")

    def test_terminal_lifecycle_is_blocked(self):
        frame = finalize_signal_ranking(
            pd.DataFrame([self._decision_row("EXPIRED", "已过期")])
        )
        self.assertEqual(frame.iloc[0]["DecisionState"], "BLOCKED")
        self.assertEqual(frame.iloc[0]["RankingEligibility"], "风险过滤")
        self.assertTrue(bool(frame.iloc[0]["HardRiskFlag"]))

    def test_cross_asset_percentile_adjustment_is_bounded(self):
        rows = []
        for index, raw in enumerate((20.0, 25.0, 30.0, 35.0, 40.0)):
            row = self._decision_row("WATCH", "横盘观察")
            row.update(
                {
                    "Ticker": f"15999{index}.SZ",
                    "EntrySignal": "WAIT_PULLBACK",
                    "InstitutionalScore": raw,
                    "TechnicalInstitutionalScore": raw,
                    "Score": raw,
                    "FinalScore": raw,
                }
            )
            rows.append(row)
        frame = finalize_signal_ranking(pd.DataFrame(rows))
        adjustment = pd.to_numeric(frame["CrossAssetAdjustment"], errors="coerce")
        self.assertLessEqual(
            float(adjustment.abs().max()),
            config.CROSS_ASSET_PERCENTILE_MAX_ADJUSTMENT,
        )
        top = frame.loc[
            pd.to_numeric(frame["InstitutionalScore"], errors="coerce").idxmax()
        ]
        self.assertAlmostEqual(float(top["CrossAssetScore"]), 45.0, places=4)

    def test_style_label_no_longer_reweights_source_features(self):
        dummy = pd.DataFrame({"Close": [1.0]})
        for style in (
            "高波动成长",
            "趋势成长",
            "资金吸筹",
            "低波动防守",
            "ETF趋势/资金",
            "均衡",
        ):
            self.assertEqual(
                score._style_adjustment(dummy, style),
                (1.0, 1.0, 1.0, 1.0, 1.0),
            )

    def test_trigger_event_is_independent_of_moving_average_levels(self):
        index = pd.date_range("2026-01-01", periods=40, freq="B")
        close = np.linspace(10.0, 10.8, len(index))
        high = close * 1.002
        volume = np.full(len(index), 1_000_000.0)
        volume[-1] = 2_000_000.0
        base = pd.DataFrame(
            {
                "Close": close,
                "High": high,
                "Volume": volume,
                "CMF": np.linspace(0.0, 0.15, len(index)),
                "AD_Slope": np.linspace(-1.0, 1.0, len(index)),
                "OBV": np.linspace(1_000.0, 2_000.0, len(index)),
            },
            index=index,
        )
        bearish_ma = base.assign(MA20=20.0, MA50=21.0, MA200=22.0)
        bullish_ma = base.assign(MA20=9.0, MA50=8.5, MA200=8.0)
        self.assertAlmostEqual(
            score.trigger_event_score(bearish_ma),
            score.trigger_event_score(bullish_ma),
            places=8,
        )

    def test_scoring_version_advances_for_changed_model_semantics(self):
        self.assertTrue(
            any(f"v{version}" in config.SCORING_VERSION for version in range(35, 100))
        )
        self.assertTrue(
            any(f"v{version}" in config.PIPELINE_VERSION for version in range(35, 100))
        )


if __name__ == "__main__":
    unittest.main()
