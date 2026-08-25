from __future__ import annotations

from institution_scanner.gui_view_model import (
    DERIVED_ROW_REQUIRED_FIELDS,
    backtest_detail_label,
    derived_row_labels,
)


def test_derived_row_labels_ignore_wide_unrelated_surface() -> None:
    headers = [f"Junk{index}" for index in range(420)] + list(DERIVED_ROW_REQUIRED_FIELDS)
    indexes = {header: index for index, header in enumerate(headers)}
    row: list[object] = ["noise"] * len(headers)
    values = {
        "BacktestScore": 62.5,
        "BacktestEffectiveWeight": 0.2,
        "CompositeScore": 58.0,
        "FinalScore": 55.0,
        "Score": 54.0,
        "BacktestResonanceMeanCount": 3.5,
        "BacktestResonanceStrongBullShare": 0.25,
        "BacktestResonanceRisingShare": 0.4,
    }
    for field, value in values.items():
        row[indexes[field]] = value

    calibration, resonance = derived_row_labels(row, indexes)

    assert calibration == "S62.5 · W20% · Δ+3.0"
    assert resonance == "3.5/5 · 强25% · ↑40%"
    assert len(DERIVED_ROW_REQUIRED_FIELDS) == 8


def test_backtest_detail_label_preserves_existing_semantics() -> None:
    label = backtest_detail_label(
        {
            "BacktestScore": 61.0,
            "BacktestAdjustedScore": 59.5,
            "BacktestEffectiveWeight": 0.15,
            "CompositeScore": 57.0,
            "FinalScore": 55.0,
            "BacktestSamples": 12,
            "BacktestEffectiveSamples": 8.5,
            "BacktestConfidenceTier": "MEDIUM",
        }
    )

    assert label == "回测分 61.0 · 校准分 59.5 · 权重 15% · 回测后 57.0 · Δ+2.0 · n=12/eff=8.5 · MEDIUM"
