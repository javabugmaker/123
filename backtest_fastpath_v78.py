"""v78 vectorised FAST-backtest candidate gate.

The full-market FAST screen previously sliced a 252-bar DataFrame and ran
``breakout_score -> value_trap_risk -> entry_point`` for every endpoint that
survived the cheap candidate matrix (often dozens/hundreds of times per ticker).
Those three functions only consume rolling endpoint statistics, so compute the
same quick gate once for the whole ticker and reserve full ``score_ticker`` work
for dates that can actually survive the quick gate.

Exact backtests are untouched. FAST cooldown, candidate-gap, score-window,
T+1 execution and all score thresholds remain unchanged.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import analytics_core as _core
from config import BREAKOUT_CONFIRM_MIN_VOLUME_RATIO

_LEGACY_SIGNAL_EVALUATIONS = _core._signal_evaluations
_INSTALLED = False

_REQUIRED_FAST_COLUMNS = (
    "Close",
    "High",
    "Low",
    "Volume",
    "MA20",
    "MA50",
    "MA200",
    "ATR14",
    "RSI14",
    "CMF",
    "AD_Slope",
    "OBV",
)


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    return pd.to_numeric(frame[column], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )


def _fast_quick_gate(
    enriched: pd.DataFrame,
    *,
    is_etf: bool,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Return (legacy-quick-gate, price-breakout) for every row.

    This is deliberately fail-safe: unusual histories with holes in the mature
    region fall back to the stable per-candidate implementation instead of
    trying to approximate ``dropna`` chronology.
    """
    if enriched is None or len(enriched) < 252:
        return None
    if not all(column in enriched.columns for column in _REQUIRED_FAST_COLUMNS):
        return None

    series = {column: _numeric(enriched, column) for column in _REQUIRED_FAST_COLUMNS}
    mature_start = max(0, 251 - 120)
    for column in _REQUIRED_FAST_COLUMNS:
        values = series[column].iloc[mature_start:]
        if series[column].iloc[251:].isna().any():
            return None
        if column in {"Close", "High", "Low", "Volume"} and values.isna().any():
            return None

    close = series["Close"]
    high = series["High"]
    low = series["Low"]
    volume = series["Volume"]
    ma20 = series["MA20"]
    ma50 = series["MA50"]
    ma200 = series["MA200"]
    atr14 = series["ATR14"]
    rsi14 = series["RSI14"]
    cmf = series["CMF"]
    ad_slope = series["AD_Slope"]
    obv = series["OBV"]
    n = len(enriched)

    points = np.zeros(n, dtype=np.float64)
    trend15 = close.gt(ma20) & ma20.gt(ma50)
    trend8 = close.gt(ma20) & ~trend15
    points += np.where(
        trend15.to_numpy(), 15.0, np.where(trend8.to_numpy(), 8.0, 0.0)
    )
    points += np.where(close.gt(ma200).to_numpy(), 10.0, 0.0)

    resistance = high.shift(1).rolling(20, min_periods=20).max()
    prior_volume20 = volume.shift(1).rolling(20, min_periods=20).mean()
    raw_breakout = close.gt(resistance)
    points += np.where(raw_breakout.to_numpy(), 25.0, 0.0)
    breakout_volume = raw_breakout & volume.ge(prior_volume20 * 1.5)
    points += np.where(breakout_volume.to_numpy(), 15.0, 0.0)

    up = close.diff().gt(0)
    up_volume = volume.where(up).rolling(10, min_periods=1).mean()
    down_volume = volume.where(~up).rolling(10, min_periods=1).mean()
    up_volume_dominant = (
        up_volume.notna()
        & down_volume.notna()
        & down_volume.gt(0)
        & up_volume.gt(down_volume * 1.15)
    )
    points += np.where(up_volume_dominant.to_numpy(), 15.0, 0.0)

    recent_max5 = close.rolling(5, min_periods=5).max()
    recent_min5 = close.rolling(5, min_periods=5).min()
    prior_max15 = close.shift(5).rolling(15, min_periods=15).max()
    prior_min15 = close.shift(5).rolling(15, min_periods=15).min()
    recent_range = (recent_max5 - recent_min5) / close.clip(lower=1e-9)
    prior_range = (prior_max15 - prior_min15) / close.clip(lower=1e-9)
    contraction = prior_range.gt(0) & recent_range.lt(prior_range * 0.75)
    points += np.where(contraction.to_numpy(), 10.0, 0.0)

    ma20_mean10 = ma20.rolling(10, min_periods=10).mean()
    trend_acceleration = close.gt(close.shift(9)) & ma20.gt(ma20_mean10)
    points += np.where(trend_acceleration.to_numpy(), 10.0, 0.0)
    breakout = np.clip(points, 0.0, 100.0)

    def return_pct(period: int) -> pd.Series:
        prior = close.shift(period)
        return (close / prior - 1.0) * 100.0

    ret20 = return_pct(20)
    ret60 = return_pct(60)
    ret120 = return_pct(120)
    risk = np.zeros(n, dtype=np.float64)

    ret120_values = ret120.to_numpy(dtype=np.float64)
    risk += np.where(
        np.isfinite(ret120_values) & (ret120_values < 0),
        np.clip(np.abs(ret120_values) / 45.0, 0.0, 1.0) * 15.0,
        0.0,
    )

    old_ma50 = ma50.shift(24)
    ma50_decline = old_ma50.gt(0) & ma50.lt(old_ma50)
    decline_fraction = (old_ma50 - ma50) / old_ma50.replace(0, np.nan) / 0.12
    risk += np.where(
        ma50_decline.to_numpy(),
        np.clip(decline_fraction.to_numpy(dtype=np.float64), 0.0, 1.0) * 12.0,
        0.0,
    )
    risk += np.where((close.lt(ma50) & ret20.lt(0)).to_numpy(), 8.0, 0.0)

    recent_low40 = close.rolling(40, min_periods=40).min()
    prior_low40 = close.shift(40).rolling(40, min_periods=40).min()
    lower_low = prior_low40.gt(0) & recent_low40.lt(prior_low40 * 0.98)
    low_fraction = (prior_low40 - recent_low40) / prior_low40.replace(0, np.nan) / 0.12
    risk += np.where(
        lower_low.to_numpy(),
        np.clip(low_fraction.to_numpy(dtype=np.float64), 0.0, 1.0) * 15.0,
        0.0,
    )
    risk += np.where((ret20.lt(0) & ret60.lt(0)).to_numpy(), 10.0, 0.0)

    cmf_available = cmf.notna().to_numpy(dtype=bool)
    ad_available = ad_slope.notna().to_numpy(dtype=bool)
    obv_prior = obv.shift(19)
    obv_available = (obv.notna() & obv_prior.notna()).to_numpy(dtype=bool)
    flow_available = (
        cmf_available.astype(np.int8)
        + ad_available.astype(np.int8)
        + obv_available.astype(np.int8)
    )
    flow_positive = (
        (cmf.gt(0).to_numpy(dtype=bool) & cmf_available).astype(np.int8)
        + (ad_slope.gt(0).to_numpy(dtype=bool) & ad_available).astype(np.int8)
        + (obv.gt(obv_prior).to_numpy(dtype=bool) & obv_available).astype(np.int8)
    )
    risk += np.where(
        (flow_available > 0) & (flow_positive == 0),
        25.0,
        np.where(
            (flow_available > 0) & (flow_positive == 1),
            10.0,
            np.where(flow_positive >= 2, -8.0, 0.0),
        ),
    )

    volume20 = volume.rolling(20, min_periods=20).mean()
    volume_prior40 = volume.shift(20).rolling(40, min_periods=40).mean()
    volume_dry = volume_prior40.gt(0) & volume20.lt(volume_prior40 * 0.75)
    risk += np.where(
        (volume_dry & ret20.lt(0)).to_numpy(),
        10.0,
        np.where((volume_dry & ret20.ge(0)).to_numpy(), -3.0, 0.0),
    )

    recovery = close.ge(ma20) & ma20.ge(ma50) & ret20.gt(0)
    risk += np.where(
        recovery.to_numpy(),
        -15.0,
        np.where((ret20.gt(5.0).to_numpy()) & (flow_positive >= 2), -8.0, 0.0),
    )
    quick_trap = np.clip(risk, 0.0, 100.0)

    decimals = int(_core.tradable_price_decimals(is_etf))
    rounded_resistance = pd.Series(
        np.round(resistance.to_numpy(dtype=np.float64), decimals),
        index=enriched.index,
    )
    support = low.rolling(20, min_periods=20).min()
    volume_ratio = volume / prior_volume20.replace(0, np.nan)
    flow_confirmed = cmf.gt(0) | ad_slope.gt(0) | obv.gt(obv.shift(5))
    volume_confirmed = volume_ratio.ge(float(BREAKOUT_CONFIRM_MIN_VOLUME_RATIO))
    price_breakout = (
        pd.Series(breakout >= 75.0, index=enriched.index)
        & close.gt(rounded_resistance)
    )

    effective_atr = atr14.where(atr14.gt(0), close * 0.03)
    support_anchor = support + effective_atr * 0.55
    ma_support = ma20.where(ma20.le(close))
    support_anchor = pd.concat([support_anchor, ma_support], axis=1).max(
        axis=1, skipna=True
    )
    support_anchor = pd.concat([support_anchor, close], axis=1).min(
        axis=1, skipna=True
    )
    low_zone = pd.Series(
        np.round(
            np.maximum(
                support.to_numpy(dtype=np.float64),
                (support_anchor - effective_atr * 0.35).to_numpy(dtype=np.float64),
            ),
            decimals,
        ),
        index=enriched.index,
    )
    high_zone = pd.Series(
        np.round(
            np.minimum(
                rounded_resistance.to_numpy(dtype=np.float64),
                (support_anchor + effective_atr * 0.35).to_numpy(dtype=np.float64),
            ),
            decimals,
        ),
        index=enriched.index,
    )
    high_zone = high_zone.where(high_zone.ge(low_zone), low_zone)

    entry_score = np.zeros(n, dtype=np.float64)
    entry_score += np.where(close.ge(ma20).to_numpy(), 20.0, 0.0)
    entry_score += np.where(ma20.ge(ma50).to_numpy(), 20.0, 0.0)
    entry_score += np.where(
        (close.ge(support) & close.le(support + effective_atr * 1.5)).to_numpy(),
        20.0,
        0.0,
    )
    entry_score += np.where(
        breakout >= 65.0,
        25.0,
        np.where(breakout >= 45.0, 10.0, 0.0),
    )
    entry_score += np.where(close.ge(close.shift(5)).to_numpy(), 15.0, 0.0)
    entry_score = np.clip(entry_score, 0.0, 100.0)

    blocked = (quick_trap >= 70.0) | (
        rsi14.notna().to_numpy(dtype=bool) & rsi14.ge(78.0).to_numpy(dtype=bool)
    )
    inside_zone = (close.ge(low_zone) & close.le(high_zone)).to_numpy(dtype=bool)
    wait_pullback = (entry_score >= 50.0) & close.gt(high_zone).to_numpy(dtype=bool)
    buy_now = (entry_score >= 70.0) & inside_zone
    breakout_confirm = (
        price_breakout.to_numpy(dtype=bool)
        & volume_confirmed.to_numpy(dtype=bool)
        & flow_confirmed.to_numpy(dtype=bool)
    )
    actionable = (~blocked) & (
        breakout_confirm
        | ((~price_breakout.to_numpy(dtype=bool)) & (buy_now | wait_pullback))
    )

    quick_gate = actionable | price_breakout.to_numpy(dtype=bool)
    quick_gate[:251] = False
    return quick_gate, price_breakout.to_numpy(dtype=bool)


def _signal_evaluations(
    enriched: pd.DataFrame,
    cooldown: int = _core.BACKTEST_SIGNAL_COOLDOWN_DAYS,
    is_etf: bool = False,
    *,
    profile: Any | None = None,
    start_index: int | None = None,
    component_sink: dict[int, tuple[float, float, float]] | None = None,
) -> list[tuple[int, float, str]]:
    if profile is None or not bool(getattr(profile, "fast_prefilter", False)):
        return _LEGACY_SIGNAL_EVALUATIONS(
            enriched,
            cooldown=cooldown,
            is_etf=is_etf,
            profile=profile,
            start_index=start_index,
            component_sink=component_sink,
        )

    vectorized = _fast_quick_gate(enriched, is_etf=is_etf)
    if vectorized is None:
        return _LEGACY_SIGNAL_EVALUATIONS(
            enriched,
            cooldown=cooldown,
            is_etf=is_etf,
            profile=profile,
            start_index=start_index,
            component_sink=component_sink,
        )
    quick_gate, _ = vectorized

    cooldown = max(1, int(profile.cooldown))
    candidates, breakout_flags = _core._candidate_endpoint_matrix(
        enriched,
        fast_prefilter=bool(profile.fast_prefilter),
    )
    minimum_index = max(251, int(start_index) if start_index is not None else 251)
    last_signal = minimum_index - cooldown
    last_evaluated = minimum_index - max(1, int(profile.candidate_gap))
    evaluations: list[tuple[int, float, str]] = []

    for raw_index in candidates:
        index = int(raw_index)
        if index < minimum_index:
            continue
        if index >= len(enriched) - _core.BACKTEST_OUTCOME_HORIZON_DAYS:
            continue
        if index - last_signal < cooldown:
            continue
        if (
            profile.candidate_gap > 1
            and index - last_evaluated < profile.candidate_gap
            and not breakout_flags[index]
        ):
            continue
        last_evaluated = index
        if not bool(quick_gate[index]):
            continue

        scoring_frame = _core._backtest_scoring_window(
            enriched,
            index,
            score_window=profile.score_window,
            include_volume_profile=False,
        )
        historical_score = _core.score_ticker(scoring_frame, is_etf=is_etf)
        historical_entry = _core.entry_point(
            scoring_frame,
            breakout=_core._finite_float(
                getattr(historical_score, "breakout_score", np.nan), np.nan
            ),
            volume_score=_core._finite_float(
                getattr(historical_score, "volume", np.nan), np.nan
            ),
            value_trap_risk_value=_core._finite_float(
                getattr(historical_score, "value_trap_risk", np.nan), np.nan
            ),
            price_decimals=_core.tradable_price_decimals(is_etf),
        )
        signal = str(historical_entry.get("signal", "AVOID")).upper()
        if signal not in _core._BACKTEST_ACTIONABLE_SIGNALS:
            continue
        final_score = _core._finite_float(
            getattr(historical_score, "final_score", np.nan), np.nan
        )
        if not np.isfinite(final_score):
            final_score = _core._finite_float(
                getattr(historical_score, "total", np.nan), 0.0
            )
        evaluations.append((index, float(final_score), signal))
        if component_sink is not None:
            component_sink[index] = (
                _core._finite_float(
                    getattr(historical_score, "base_score", np.nan), 0.0
                ),
                _core._finite_float(
                    getattr(historical_score, "trigger_score", np.nan), 0.0
                ),
                _core._finite_float(
                    getattr(historical_score, "execution_score", np.nan),
                    _core._finite_float(
                        getattr(historical_score, "entry_score", np.nan), 0.0
                    ),
                ),
            )
        last_signal = index
    return evaluations


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _core._signal_evaluations = _signal_evaluations
    _INSTALLED = True


install()
