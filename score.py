"""v35 scoring policy facade.

``score_core`` contains the stable v34 feature/entry implementation.  This
module changes only model semantics that need clean out-of-sample validation:
style labels stop self-reinforcing the same features, and TriggerScore becomes
an incremental launch-event score instead of reusing setup trend evidence.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import score_core as _core
from score_core import *  # noqa: F403

_legacy_score_ticker = _core.score_ticker


def _style_adjustment(
    df: pd.DataFrame, style: str
) -> tuple[float, float, float, float, float]:
    """Keep style descriptive instead of rewarding its source features twice."""
    _ = (df, style)
    return (1.0, 1.0, 1.0, 1.0, 1.0)


def trigger_event_score(df: pd.DataFrame) -> float:
    """Score new launch evidence without reusing MA trend/setup evidence."""
    close = _core._series(df, "Close")
    high = _core._series(df, "High")
    volume = _core._series(df, "Volume")
    valid = pd.concat(
        {"close": close, "high": high, "volume": volume}, axis=1
    ).dropna()
    if len(valid) < 21:
        return 0.0

    price = float(valid["close"].iloc[-1])
    resistance = float(valid["high"].iloc[-21:-1].max())
    volume_now = float(valid["volume"].iloc[-1])
    volume_baseline = float(valid["volume"].iloc[-21:-1].mean())
    points = 0.0

    if resistance > 0.0:
        clearance_pct = (price / resistance - 1.0) * 100.0
        if clearance_pct > 0.0:
            points += 35.0 + _core._clamp(clearance_pct / 3.0) * 15.0
        elif clearance_pct >= -1.5:
            points += _core._clamp((clearance_pct + 1.5) / 1.5) * 12.0

    if volume_baseline > 0.0:
        volume_ratio = volume_now / volume_baseline
        points += _core._clamp((volume_ratio - 1.0) / 1.25) * 25.0

    cmf = _core._series(df, "CMF").dropna()
    if len(cmf) >= 6:
        cmf_delta = float(cmf.iloc[-1] - cmf.iloc[-6])
        points += _core._clamp(cmf_delta / 0.12) * 10.0

    ad_slope = _core._series(df, "AD_Slope").dropna()
    if len(ad_slope) >= 6:
        current_ad = float(ad_slope.iloc[-1])
        prior_ad = float(ad_slope.iloc[-6:-1].median())
        if current_ad > 0.0 and prior_ad <= 0.0:
            points += 8.0
        elif current_ad > 0.0 and current_ad > prior_ad:
            points += 4.0

    obv = _core._series(df, "OBV").dropna()
    if len(obv) >= 11:
        recent_change = float(obv.iloc[-1] - obv.iloc[-6])
        prior_change = float(obv.iloc[-6] - obv.iloc[-11])
        if recent_change > 0.0 and recent_change > max(prior_change, 0.0):
            points += 7.0

    return _core._clamp(points, 0.0, 100.0)


def score_ticker(df: pd.DataFrame, is_etf: bool = False):
    """Run the stable feature engine, then replace duplicated TriggerScore."""
    result = _legacy_score_ticker(df, is_etf=is_etf)
    if result.confidence <= 0.0:
        return result

    trigger_raw = trigger_event_score(df)
    trigger_coverage = 0.75 + 0.25 * float(result.indicator_coverage)
    trigger_score = _core._clamp(trigger_raw * trigger_coverage, 0.0, 100.0)

    setup_weight, trigger_weight, execution_weight = _core._model_component_weights()
    final_score = _core._clamp(
        result.base_score * setup_weight
        + trigger_score * trigger_weight
        + result.execution_score * execution_weight,
        0.0,
        100.0,
    )
    coverage_cap = 40.0 + 60.0 * float(result.indicator_coverage)

    result.trigger_score = trigger_score
    result.final_score = min(final_score, coverage_cap)
    result.contributions["trigger_event"] = trigger_raw
    result.contributions["coverage_cap"] = coverage_cap
    return result


_core._style_adjustment = _style_adjustment
_core.trigger_event_score = trigger_event_score
_core.score_ticker = score_ticker

sys.modules[__name__] = _core
