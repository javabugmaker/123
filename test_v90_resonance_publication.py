from __future__ import annotations

import json

import pandas as pd

from gui_v85 import resonance_history_label
from resonance_reporting_v90 import materialize_resonance_outputs
from web_report_v90 import _resonance_block


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


def test_materialize_resonance_outputs_joins_current_signal_and_writes_diagnostics(
    tmp_path,
) -> None:
    pd.DataFrame(
        [
            {"Ticker": "000001.SZ", "EntrySignal": "BUY_NOW", "RankingScore": 71.2},
            {"Ticker": "000002.SZ", "EntrySignal": "BREAKOUT_CONFIRM", "RankingScore": 68.1},
        ]
    ).to_csv(tmp_path / "AllResults.csv", index=False, encoding="utf-8-sig")
    (tmp_path / "BacktestSummary.json").write_text(
        json.dumps(_summary_payload(), ensure_ascii=False),
        encoding="utf-8",
    )

    result = materialize_resonance_outputs(
        tmp_path,
        refresh_candidate_exports=False,
    )

    assert result["status"] == "MATERIALIZED"
    frame = pd.read_csv(tmp_path / "AllResults.csv", encoding="utf-8-sig")
    first = frame.loc[frame["Ticker"].astype(str).str.zfill(6).eq("000001")].iloc[0]
    second = frame.loc[frame["Ticker"].astype(str).str.zfill(6).eq("000002")].iloc[0]
    assert first["BacktestResonanceMeanCount"] == 4.2
    assert first["BacktestResonanceStrongBullShare"] == 0.75
    assert second["BacktestResonanceMeanCount"] == 2.4
    assert second["EntrySignal"] == "BREAKOUT_CONFIRM"
    assert (tmp_path / "FiveFactorResonance.csv").is_file()
    assert (tmp_path / "FiveFactorResonanceByTicker.csv").is_file()

    groups = pd.read_csv(tmp_path / "FiveFactorResonance.csv", encoding="utf-8-sig")
    assert {"BAND", "TRANSITION", "COUNT"}.issubset(set(groups["Dimension"]))
    assert "RISING_TO_4PLUS" in set(groups["Group"])


def test_gui_resonance_label_is_compact_and_explicit() -> None:
    label = resonance_history_label(
        {
            "BacktestResonanceMeanCount": "4.125",
            "BacktestResonanceStrongBullShare": "0.625",
            "BacktestResonanceRisingShare": "0.375",
        }
    )
    assert label == "4.1/5 · 强62% · ↑38%"
    assert resonance_history_label({}) == "—"


def test_web_resonance_block_discloses_diagnostic_only_semantics() -> None:
    block = _resonance_block(_summary_payload())
    assert "五因子共振回测" in block
    assert "RISING_TO_4PLUS" in block
    assert "4-5/5" in block
    assert "仅作独立诊断，不进入当前排名" in block
    assert "信号日收盘快照" in block
