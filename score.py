"""v95 scoring policy facade.

``score_core`` contains the stable feature/entry implementation. This facade
keeps style labels descriptive, keeps TriggerScore orthogonal to setup trend,
uses one volatility-contraction definition for filters/scoring, shares the
continuous breakout-price evidence used by the execution integrity gate, and
installs the canonical v95+ score runtime composition.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import config as _config
import score_threshold_migration_v95 as _threshold_migration_v95

# analytics_core imports score before signal_lifecycle. Publish the migrated
# constants now so lifecycle_core reads the canonical thresholds on first load.
_threshold_migration_v95.install(_config)

import score_core as _core  # noqa: E402
from execution_integrity_v87 import smooth_breakout_price_component  # noqa: E402
from score_core import *  # noqa: E402,F403
from volatility_state import volatility_contraction_score  # noqa: E402

_legacy_score_ticker = _core.score_ticker


def _style_adjustment(
    df: pd.DataFrame, style: str
) -> tuple[float, float, float, float, float]:
    """Keep style descriptive instead of rewarding its source features twice."""
    _ = (df, style)
    return (1.0, 1.0, 1.0, 1.0, 1.0)


def score_volatility(df: pd.DataFrame) -> float:
    """Score exactly the same robust volatility state used by the filter gate."""
    return volatility_contraction_score(df, max_score=15.0)


def _finite_values(series: pd.Series) -> np.ndarray:
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=np.float64)
    return values[np.isfinite(values)]


def _continuous_breakout_price_points(clearance_pct: float) -> float:
    """Map resistance clearance to the canonical continuous price component."""
    component, _ = smooth_breakout_price_component(
        np.asarray([clearance_pct], dtype=np.float64)
    )
    return float(component[0])


def trigger_event_score(df: pd.DataFrame) -> float:
    """Score new launch evidence without reusing MA trend/setup evidence."""
    close = _core._series(df, "Close").to_numpy(dtype=np.float64, copy=False)
    high = _core._series(df, "High").to_numpy(dtype=np.float64, copy=False)
    volume = _core._series(df, "Volume").to_numpy(dtype=np.float64, copy=False)
    valid = np.isfinite(close) & np.isfinite(high) & np.isfinite(volume)
    if np.count_nonzero(valid) < 21:
        return 0.0

    close_valid = close[valid]
    high_valid = high[valid]
    volume_valid = volume[valid]
    price = float(close_valid[-1])
    resistance = float(np.max(high_valid[-21:-1]))
    volume_now = float(volume_valid[-1])
    volume_baseline = float(np.mean(volume_valid[-21:-1]))
    points = 0.0

    if resistance > 0.0:
        clearance_pct = (price / resistance - 1.0) * 100.0
        points += _continuous_breakout_price_points(clearance_pct)

    if volume_baseline > 0.0:
        volume_ratio = volume_now / volume_baseline
        points += _core._clamp((volume_ratio - 1.0) / 1.25) * 25.0

    cmf = _finite_values(_core._series(df, "CMF"))
    if len(cmf) >= 6:
        cmf_delta = float(cmf[-1] - cmf[-6])
        points += _core._clamp(cmf_delta / 0.12) * 10.0

    ad_slope = _finite_values(_core._series(df, "AD_Slope"))
    if len(ad_slope) >= 6:
        current_ad = float(ad_slope[-1])
        prior_ad = float(np.median(ad_slope[-6:-1]))
        if current_ad > 0.0 and prior_ad <= 0.0:
            points += 8.0
        elif current_ad > 0.0 and current_ad > prior_ad:
            points += 4.0

    obv = _finite_values(_core._series(df, "OBV"))
    if len(obv) >= 11:
        recent_change = float(obv[-1] - obv[-6])
        prior_change = float(obv[-6] - obv[-11])
        if recent_change > 0.0 and recent_change > max(prior_change, 0.0):
            points += 7.0

    return _core._clamp(points, 0.0, 100.0)


def score_ticker(df: pd.DataFrame, is_etf: bool = False):
    """Run one cache-safe scoring transaction, then replace TriggerScore."""
    # Compatibility modules are still importable and a few historically install
    # themselves at import time. Repair only if such an import displaced the
    # canonical v95+ public bindings; the normal hot path is identity checks.
    _score_runtime_v97.ensure()
    acceleration = sys.modules.get("score_acceleration_v79")
    clear_cache = getattr(acceleration, "clear_thread_score_cache", None)
    if callable(clear_cache):
        clear_cache()
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
_core.score_volatility = score_volatility
_core.trigger_event_score = trigger_event_score
_core.score_ticker = score_ticker

import score_runtime_v97 as _score_runtime_v97  # noqa: E402

_score_runtime_v97.install()

sys.modules[__name__] = _core
