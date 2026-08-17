"""Shared volatility-contraction state used by both filters and scoring.

The scanner previously used two different definitions for volatility squeeze:
the hard filter compared the current Bollinger width with one endpoint 60 bars
ago, while the score compared it with a more representative historical
baseline.  This module is the single source of truth for both paths.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import (
    ATR_CONTRACTION_RATIO,
    BB_CONTRACTION_EXCLUDE_RECENT,
    BB_CONTRACTION_MAX_PERCENTILE,
    BB_CONTRACTION_RATIO,
    BB_WIDTH_COMPRESSION_LOOKBACK,
    HV_CONTRACTION_RATIO,
)


@dataclass(frozen=True)
class VolatilityContractionState:
    atr_ratio: float = np.nan
    bb_ratio: float = np.nan
    bb_percentile: float = np.nan
    hv_ratio: float = np.nan
    atr_contracting: bool = False
    bb_contracting: bool = False
    hv_contracting: bool = False
    available_components: int = 0

    @property
    def passed(self) -> bool:
        return bool(
            self.atr_contracting or self.bb_contracting or self.hv_contracting
        )

    def details(self) -> dict[str, float | bool | int]:
        return {
            "atr_ratio": self.atr_ratio,
            "bb_ratio": self.bb_ratio,
            "bb_percentile": self.bb_percentile,
            "hv_ratio": self.hv_ratio,
            "atr_contracting": self.atr_contracting,
            "bb_contracting": self.bb_contracting,
            "hv_contracting": self.hv_contracting,
            "available_components": self.available_components,
        }


def _latest_ratio(frame: pd.DataFrame, numerator: str, denominator: str) -> float:
    if numerator not in frame.columns or denominator not in frame.columns:
        return np.nan
    values = frame[[numerator, denominator]].apply(
        pd.to_numeric, errors="coerce"
    ).replace([np.inf, -np.inf], np.nan).dropna()
    if values.empty:
        return np.nan
    den = float(values.iloc[-1][denominator])
    num = float(values.iloc[-1][numerator])
    return float(num / den) if den > 0 else np.nan


def evaluate_volatility_contraction(df: pd.DataFrame) -> VolatilityContractionState:
    """Return one robust contraction state for gate and score consumers."""
    if df is None or df.empty:
        return VolatilityContractionState()

    atr_ratio = _latest_ratio(df, "ATR14", "ATR50")
    hv_ratio = _latest_ratio(df, "HV20", "HV60")
    atr_contracting = bool(
        np.isfinite(atr_ratio) and atr_ratio <= float(ATR_CONTRACTION_RATIO)
    )
    hv_contracting = bool(
        np.isfinite(hv_ratio) and hv_ratio <= float(HV_CONTRACTION_RATIO)
    )

    bb_ratio = np.nan
    bb_percentile = np.nan
    bb_contracting = False
    if "BB_Width" in df.columns:
        bb = pd.to_numeric(df["BB_Width"], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        ).dropna()
        lookback = max(20, int(BB_WIDTH_COMPRESSION_LOOKBACK))
        if len(bb) >= lookback:
            recent = bb.iloc[-lookback:]
            exclude_recent = min(
                max(1, int(BB_CONTRACTION_EXCLUDE_RECENT)),
                max(1, len(recent) // 3),
            )
            baseline_window = recent.iloc[:-exclude_recent]
            baseline = float(baseline_window.median()) if not baseline_window.empty else np.nan
            current = float(recent.iloc[-1])
            if np.isfinite(baseline) and baseline > 0 and np.isfinite(current):
                bb_ratio = float(current / baseline)
                bb_percentile = float((recent <= current).mean())
                bb_contracting = bool(
                    bb_ratio <= float(BB_CONTRACTION_RATIO)
                    and bb_percentile <= float(BB_CONTRACTION_MAX_PERCENTILE)
                )

    available = int(np.isfinite(atr_ratio)) + int(np.isfinite(bb_ratio)) + int(
        np.isfinite(hv_ratio)
    )
    return VolatilityContractionState(
        atr_ratio=atr_ratio,
        bb_ratio=bb_ratio,
        bb_percentile=bb_percentile,
        hv_ratio=hv_ratio,
        atr_contracting=atr_contracting,
        bb_contracting=bb_contracting,
        hv_contracting=hv_contracting,
        available_components=available,
    )


def volatility_contraction_score(
    df: pd.DataFrame,
    *,
    max_score: float = 15.0,
) -> float:
    """Convert the shared contraction state into a bounded score."""
    state = evaluate_volatility_contraction(df)
    components: list[float] = []
    if np.isfinite(state.atr_ratio):
        components.append(float(np.clip((1.0 - state.atr_ratio) / 0.35, 0.0, 1.0)))
    if np.isfinite(state.bb_ratio):
        ratio_score = float(np.clip((1.0 - state.bb_ratio) / 0.35, 0.0, 1.0))
        percentile_score = (
            float(np.clip((0.5 - state.bb_percentile) / 0.5, 0.0, 1.0))
            if np.isfinite(state.bb_percentile)
            else 0.0
        )
        components.append((ratio_score + percentile_score) / 2.0)
    if np.isfinite(state.hv_ratio):
        components.append(float(np.clip((1.0 - state.hv_ratio) / 0.50, 0.0, 1.0)))
    if not components:
        return 0.0
    coverage = len(components) / 3.0
    return float(np.clip(np.mean(components) * coverage * max_score, 0.0, max_score))
