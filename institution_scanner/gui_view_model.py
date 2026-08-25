"""Pure view-model helpers for the desktop research terminal.

The GUI loads a very wide result surface (hundreds of columns). Derived display
labels should not materialize a full row dictionary for every ticker. This module
keeps label formatting testable and projects only the fields required by the two
hot-path derived columns.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

GUI_VIEW_MODEL_VERSION: Final = "2026-08-25-v108-compact-derived-row-projection-v1"

_CALIBRATION_FIELDS: Final = (
    "BacktestScore",
    "BacktestEffectiveWeight",
    "CompositeScore",
    "FinalScore",
    "Score",
)
_RESONANCE_FIELDS: Final = (
    "BacktestResonanceMeanCount",
    "BacktestResonanceStrongBullShare",
    "BacktestResonanceRisingShare",
)
DERIVED_ROW_REQUIRED_FIELDS: Final = _CALIBRATION_FIELDS + _RESONANCE_FIELDS


def finite_number(value: object) -> float | None:
    """Return a finite float or ``None`` for GUI formatting."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def resonance_history_label(data: Mapping[str, object]) -> str:
    """Return one compact label for held-out five-factor backtest evidence."""
    mean_count = finite_number(data.get("BacktestResonanceMeanCount"))
    strong_share = finite_number(data.get("BacktestResonanceStrongBullShare"))
    rising_share = finite_number(data.get("BacktestResonanceRisingShare"))
    if mean_count is None:
        return "—"
    parts = [f"{mean_count:.1f}/5"]
    if strong_share is not None:
        parts.append(f"强{strong_share:.0%}")
    if rising_share is not None:
        parts.append(f"↑{rising_share:.0%}")
    return " · ".join(parts)


def backtest_calibration_label(data: Mapping[str, object]) -> str:
    """Compact production backtest score, blend weight and direct score delta."""
    score = finite_number(data.get("BacktestScore"))
    weight = finite_number(data.get("BacktestEffectiveWeight"))
    composite = finite_number(data.get("CompositeScore"))
    raw = finite_number(data.get("FinalScore"))
    if raw is None:
        raw = finite_number(data.get("Score"))
    if score is None and composite is None:
        return "—"
    parts: list[str] = []
    if score is not None:
        parts.append(f"S{score:.1f}")
    if weight is not None:
        parts.append(f"W{weight:.0%}")
    if composite is not None and raw is not None:
        parts.append(f"Δ{composite - raw:+.1f}")
    return " · ".join(parts) or "—"


def backtest_detail_label(data: Mapping[str, object]) -> str:
    """Explain the complete production calibration chain for one selected row."""
    score = finite_number(data.get("BacktestScore"))
    adjusted = finite_number(data.get("BacktestAdjustedScore"))
    weight = finite_number(data.get("BacktestEffectiveWeight"))
    composite = finite_number(data.get("CompositeScore"))
    raw = finite_number(data.get("FinalScore"))
    if raw is None:
        raw = finite_number(data.get("Score"))
    samples = finite_number(data.get("BacktestSamples"))
    effective = finite_number(data.get("BacktestEffectiveSamples"))
    confidence = str(data.get("BacktestConfidenceTier", "") or "").strip()
    if score is None and adjusted is None and composite is None:
        return "—"
    parts: list[str] = []
    if score is not None:
        parts.append(f"回测分 {score:.1f}")
    if adjusted is not None:
        parts.append(f"校准分 {adjusted:.1f}")
    if weight is not None:
        parts.append(f"权重 {weight:.0%}")
    if composite is not None:
        parts.append(f"回测后 {composite:.1f}")
    if composite is not None and raw is not None:
        parts.append(f"Δ{composite - raw:+.1f}")
    if samples is not None:
        sample_text = f"n={round(samples)}"
        if effective is not None:
            sample_text += f"/eff={effective:.1f}"
        parts.append(sample_text)
    if confidence:
        parts.append(confidence)
    return " · ".join(parts) or "—"


def _project_fields(
    row: Sequence[object],
    indexes: Mapping[str, int],
    fields: Sequence[str],
) -> dict[str, object]:
    projected: dict[str, object] = {}
    row_length = len(row)
    for field in fields:
        index = indexes.get(field)
        if index is not None and 0 <= index < row_length:
            projected[field] = row[index]
    return projected


def derived_row_labels(
    row: Sequence[object],
    indexes: Mapping[str, int],
) -> tuple[str, str]:
    """Build hot-path GUI labels from only eight source fields.

    Older GUI code copied every column into a dict for every row. With a 400+
    column result surface and a full-market scan this created millions of
    unnecessary key/value insertions during CSV load. The two labels need only
    five calibration fields and three resonance fields.
    """
    calibration = _project_fields(row, indexes, _CALIBRATION_FIELDS)
    resonance = _project_fields(row, indexes, _RESONANCE_FIELDS)
    return backtest_calibration_label(calibration), resonance_history_label(resonance)
