from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from gui_v85 import resonance_history_label
from resonance_reporting_v90 import materialize_resonance_outputs
from web_report import _resonance


def _summary_payload() -> dict[str, object]:
    return {
        "by_ticker": [
            {
                "ticker": "000001.SZ",
                "entry_signal": "BUY_NOW",
                "backtest_stage": "EXACT",
                "resonance_mean_count": 4.2,
                "resonance_strong_bull_share": 0.75,
                "resonance_rising_share": 0.50,
            },
            {
                "ticker": "000002.SZ",
                "entry_signal": "UNKNOWN",
                "backtest_stage": "FAST_SCREEN",
                "resonance_mean_count": 2.4,
                "resonance_strong_bull_share": 0.20,
                "resonance_rising_share": 0.40,
            },
        ],
        "resonance_analysis": {
            "version": "2026-08-23-v90-five-factor-v1",
            "status": "EXPERIMENTAL_DIAGNOSTIC_ONLY",
            "samples": 25,
            "by_count": [
                {
                    "group": "4/5",
                    "samples": 8,
                    "effective_samples": 5.5,
                    "net_excess_win_rate_20d": 0.625,
                    "average_net_excess_20d": 2.1,
                    "average_net_excess_60d": 5.4,
                    "max_drawdown_60d": -11.2,
                }
            ],
            "by_band": [
                {
                    "group": "4-5/5",
                    "samples": 12,
                    "effective_samples": 8.0,
                    "net_excess_win_rate_20d": 0.6667,
                    "average_net_excess_20d": 2.6,
                    "average_net_excess_60d": 6.1,
                    "max_drawdown_60d": -12.0,
                }
            ],
            "by_transition": [
                {
                    "group": "RISING_TO_4PLUS",
                    "samples": 7,
                    "effective_samples": 4.5,
                    "net_excess_win_rate_20d": 0.7143,
                    "average_net_excess_20d": 3.2,
                    "average_net_excess_60d": 7.8,
                    "max_drawdown_60d": -9.4,
                }
            ],
        },
    }


class ResonancePublicationV90Tests(unittest.TestCase):
    def test_materialize_resonance_outputs_joins_current_signal_and_writes_diagnostics(
        self,
    ) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            pd.DataFrame(
                [
                    {
                        "Ticker": "000001.SZ",
                        "EntrySignal": "BUY_NOW",
                        "RankingScore": 71.2,
                    },
                    {
                        "Ticker": "000002.SZ",
                        "EntrySignal": "BREAKOUT_CONFIRM",
                        "RankingScore": 68.1,
                    },
                ]
            ).to_csv(root / "AllResults.csv", index=False, encoding="utf-8-sig")
            (root / "BacktestSummary.json").write_text(
                json.dumps(_summary_payload(), ensure_ascii=False),
                encoding="utf-8",
            )

            result = materialize_resonance_outputs(
                root,
                refresh_candidate_exports=False,
            )

            self.assertEqual(result["status"], "MATERIALIZED")
            frame = pd.read_csv(root / "AllResults.csv", encoding="utf-8-sig")
            first = frame.loc[frame["Ticker"].eq("000001.SZ")].iloc[0]
            second = frame.loc[frame["Ticker"].eq("000002.SZ")].iloc[0]
            self.assertEqual(first["BacktestResonanceMeanCount"], 4.2)
            self.assertEqual(first["BacktestResonanceStrongBullShare"], 0.75)
            self.assertEqual(second["BacktestResonanceMeanCount"], 2.4)
            self.assertEqual(second["EntrySignal"], "BREAKOUT_CONFIRM")
            self.assertTrue((root / "FiveFactorResonance.csv").is_file())
            self.assertTrue((root / "FiveFactorResonanceByTicker.csv").is_file())

            groups = pd.read_csv(
                root / "FiveFactorResonance.csv", encoding="utf-8-sig"
            )
            self.assertTrue(
                {"BAND", "TRANSITION", "COUNT"}.issubset(set(groups["Dimension"]))
            )
            self.assertIn("RISING_TO_4PLUS", set(groups["Group"]))

    def test_gui_resonance_label_is_compact_and_explicit(self) -> None:
        label = resonance_history_label(
            {
                "BacktestResonanceMeanCount": "4.125",
                "BacktestResonanceStrongBullShare": "0.625",
                "BacktestResonanceRisingShare": "0.375",
            }
        )
        self.assertEqual(label, "4.1/5 · 强62% · ↑38%")
        self.assertEqual(resonance_history_label({}), "—")

    def test_web_resonance_block_discloses_diagnostic_only_semantics(self) -> None:
        block = _resonance(_summary_payload())
        self.assertIn("五因子共振回测", block)
        self.assertIn("4-5/5", block)
        self.assertIn("不进入排名", block)
        self.assertIn("DIAGNOSTIC ONLY", block)


if __name__ == "__main__":
    unittest.main()
