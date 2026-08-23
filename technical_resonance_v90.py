"""Independent five-factor technical resonance diagnostics.

The layer mirrors the TradingView MACD/KDJ/RSI/OBV/BOLL experiment as an
orthogonal 0..5 vote.  It never changes production scoring, entry eligibility,
or ranking.  Historical samples are annotated at ``signal_date`` (the known
close) rather than the next-session execution date, preventing look-ahead.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

RESONANCE_VERSION = "2026-08-23-v90-five-factor-v1"
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
KDJ_N = 9
KDJ_M1 = 3
KDJ_M2 = 3
RSI_PERIOD = 14
OBV_EMA_PERIOD = 20
BOLL_PERIOD = 20

_FACTOR_COLUMNS = (
    "ResonanceMACDBull",
    "ResonanceKDJBull",
    "ResonanceRSIBull",
    "ResonanceOBVBull",
    "ResonanceBOLLBull",
)


def _numeric(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=np.float64)
    return pd.to_numeric(frame[name], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )


def _wilder_rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(
        alpha=1.0 / float(period), min_periods=period, adjust=False
    ).mean()
    avg_loss = loss.ewm(
        alpha=1.0 / float(period), min_periods=period, adjust=False
    ).mean()
    result = pd.Series(50.0, index=close.index, dtype=np.float64)
    positive_only = (avg_loss == 0.0) & (avg_gain > 0.0)
    negative_only = (avg_gain == 0.0) & (avg_loss > 0.0)
    both = (avg_gain > 0.0) & (avg_loss > 0.0)
    result.loc[positive_only] = 100.0
    result.loc[negative_only] = 0.0
    rs = avg_gain.loc[both] / avg_loss.loc[both]
    result.loc[both] = 100.0 - 100.0 / (1.0 + rs)
    return result.where(avg_gain.notna() & avg_loss.notna())


def _obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    direction = np.sign(close.diff()).fillna(0.0)
    valid = close.notna() & volume.notna()
    return (volume.fillna(0.0) * direction).cumsum().astype(np.float64).where(valid)


def compute_five_factor_resonance(frame: pd.DataFrame) -> pd.DataFrame:
    """Compute equal-weight MACD/KDJ/RSI/OBV/BOLL votes for each bar."""
    if frame is None or frame.empty:
        return pd.DataFrame(index=getattr(frame, "index", None))

    close = _numeric(frame, "Close")
    high = _numeric(frame, "High")
    low = _numeric(frame, "Low")
    volume = _numeric(frame, "Volume")

    ema_fast = close.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=MACD_SLOW, adjust=False).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=MACD_SIGNAL, adjust=False).mean()

    lowest = low.rolling(KDJ_N, min_periods=KDJ_N).min()
    highest = high.rolling(KDJ_N, min_periods=KDJ_N).max()
    rsv = (close - lowest) / (highest - lowest).replace(0.0, np.nan) * 100.0
    k = rsv.rolling(KDJ_M1, min_periods=KDJ_M1).mean()
    d = k.rolling(KDJ_M2, min_periods=KDJ_M2).mean()

    existing_rsi = _numeric(frame, "RSI14")
    rsi = existing_rsi.where(existing_rsi.notna(), _wilder_rsi(close))

    existing_obv = _numeric(frame, "OBV")
    obv = existing_obv.where(existing_obv.notna(), _obv(close, volume))
    obv_ema = obv.ewm(span=OBV_EMA_PERIOD, adjust=False).mean()

    existing_middle = _numeric(frame, "BB_Middle")
    boll_middle = existing_middle.where(
        existing_middle.notna(),
        close.rolling(BOLL_PERIOD, min_periods=BOLL_PERIOD).mean(),
    )

    available = pd.DataFrame(
        {
            "macd": macd.notna() & macd_signal.notna(),
            "kdj": k.notna() & d.notna(),
            "rsi": rsi.notna(),
            "obv": obv.notna() & obv_ema.notna(),
            "boll": close.notna() & boll_middle.notna(),
        },
        index=frame.index,
    )
    available_count = available.sum(axis=1).astype(np.int8)
    full = available_count.eq(5)

    result = pd.DataFrame(index=frame.index)
    result["ResonanceMACDBull"] = (macd > macd_signal).where(available["macd"])
    result["ResonanceKDJBull"] = (k > d).where(available["kdj"])
    result["ResonanceRSIBull"] = (rsi > 50.0).where(available["rsi"])
    result["ResonanceOBVBull"] = (obv > obv_ema).where(available["obv"])
    result["ResonanceBOLLBull"] = (close > boll_middle).where(available["boll"])
    result["ResonanceAvailable"] = available_count

    votes = (
        result[list(_FACTOR_COLUMNS)]
        .fillna(False)
        .astype(np.int8)
        .sum(axis=1)
        .astype(np.float64)
    )
    result["ResonanceCount"] = votes.where(full)
    result["ResonanceDelta1"] = result["ResonanceCount"].diff()
    result["ResonanceDelta3"] = result["ResonanceCount"].diff(3)
    result["ResonanceStrongBull"] = result["ResonanceCount"].ge(4).where(full)
    result["ResonanceStrongBear"] = result["ResonanceCount"].le(1).where(full)
    result["ResonanceTurnUp"] = (
        result["ResonanceCount"].ge(4)
        & result["ResonanceCount"].shift(1).le(3)
    ).where(full)
    result["ResonanceTurnDown"] = (
        result["ResonanceCount"].le(1)
        & result["ResonanceCount"].shift(1).ge(2)
    ).where(full)
    direction = np.select(
        [result["ResonanceDelta3"].gt(0.0), result["ResonanceDelta3"].lt(0.0)],
        ["RISING", "FALLING"],
        default="FLAT",
    )
    result["ResonanceDirection"] = pd.Series(direction, index=frame.index).where(
        full, "UNAVAILABLE"
    )
    result["ResonanceVersion"] = RESONANCE_VERSION
    return result


def _date_key(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else pd.Timestamp(parsed).strftime("%Y-%m-%d")


def attach_resonance_to_samples(
    samples: list[dict[str, Any]], frame: pd.DataFrame | None
) -> list[dict[str, Any]]:
    """Attach the exact signal-close resonance snapshot to return samples."""
    if not samples:
        return samples
    if frame is None or frame.empty:
        return [dict(item) for item in samples]

    resonance = compute_five_factor_resonance(frame)
    lookup = {
        pd.Timestamp(value).strftime("%Y-%m-%d"): resonance.iloc[position]
        for position, value in enumerate(pd.DatetimeIndex(resonance.index))
    }
    field_map = {
        "ResonanceCount": "resonance_count",
        "ResonanceAvailable": "resonance_available",
        "ResonanceDelta1": "resonance_delta_1d",
        "ResonanceDelta3": "resonance_delta_3d",
        "ResonanceDirection": "resonance_direction",
        "ResonanceStrongBull": "resonance_strong_bull",
        "ResonanceStrongBear": "resonance_strong_bear",
        "ResonanceTurnUp": "resonance_turn_up",
        "ResonanceTurnDown": "resonance_turn_down",
        "ResonanceMACDBull": "resonance_macd_bull",
        "ResonanceKDJBull": "resonance_kdj_bull",
        "ResonanceRSIBull": "resonance_rsi_bull",
        "ResonanceOBVBull": "resonance_obv_bull",
        "ResonanceBOLLBull": "resonance_boll_bull",
    }
    boolean_targets = {
        "resonance_strong_bull",
        "resonance_strong_bear",
        "resonance_turn_up",
        "resonance_turn_down",
        "resonance_macd_bull",
        "resonance_kdj_bull",
        "resonance_rsi_bull",
        "resonance_obv_bull",
        "resonance_boll_bull",
    }

    attached: list[dict[str, Any]] = []
    for original in samples:
        item = dict(original)
        row = lookup.get(_date_key(item.get("signal_date")))
        item["resonance_version"] = RESONANCE_VERSION
        if row is None:
            item.update(
                resonance_count=np.nan,
                resonance_available=0,
                resonance_direction="UNAVAILABLE",
            )
            attached.append(item)
            continue
        for source, target in field_map.items():
            value = row.get(source, np.nan)
            if target in boolean_targets:
                item[target] = bool(value) if pd.notna(value) else False
            elif target == "resonance_direction":
                item[target] = str(value)
            elif target == "resonance_available":
                item[target] = int(value) if pd.notna(value) else 0
            else:
                item[target] = float(value) if pd.notna(value) else np.nan
        attached.append(item)
    return attached


def _weights(frame: pd.DataFrame) -> pd.Series:
    if "sample_weight" not in frame:
        return pd.Series(1.0, index=frame.index, dtype=np.float64)
    return (
        pd.to_numeric(frame["sample_weight"], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
        .clip(lower=0.0)
    )


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    valid = numeric.notna() & weights.gt(0.0)
    if not valid.any():
        return np.nan
    return float(
        np.average(numeric.loc[valid].astype(float), weights=weights.loc[valid])
    )


def _weighted_rate(values: pd.Series, weights: pd.Series) -> float:
    numeric = pd.to_numeric(values, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    valid = numeric.notna() & weights.gt(0.0)
    if not valid.any():
        return np.nan
    return float(
        np.average((numeric.loc[valid] > 0.0).astype(float), weights=weights.loc[valid])
    )


def _group_metrics(group: pd.DataFrame) -> dict[str, Any]:
    weights = _weights(group)
    benchmark20 = pd.to_numeric(
        group.get("benchmark_return20", pd.Series(np.nan, index=group.index)),
        errors="coerce",
    )
    benchmark60 = pd.to_numeric(
        group.get("benchmark_return60", pd.Series(np.nan, index=group.index)),
        errors="coerce",
    )
    net20 = pd.to_numeric(
        group.get("net_return20", pd.Series(np.nan, index=group.index)),
        errors="coerce",
    )
    net60 = pd.to_numeric(
        group.get("net_return60", pd.Series(np.nan, index=group.index)),
        errors="coerce",
    )
    drawdown60 = pd.to_numeric(
        group.get("drawdown60", pd.Series(np.nan, index=group.index)),
        errors="coerce",
    ).replace([np.inf, -np.inf], np.nan)
    excess20 = net20 - benchmark20
    excess60 = net60 - benchmark60
    return {
        "samples": len(group),
        "effective_samples": round(float(weights.sum()), 4),
        "net_excess_win_rate_20d": round(_weighted_rate(excess20, weights), 4),
        "average_net_excess_20d": round(_weighted_mean(excess20, weights), 4),
        "average_net_excess_60d": round(_weighted_mean(excess60, weights), 4),
        "max_drawdown_60d": (
            round(float(drawdown60.min()), 4) if drawdown60.notna().any() else np.nan
        ),
    }


def summarize_resonance_samples(sample_frame: pd.DataFrame) -> dict[str, Any]:
    """Summarize held-out outcomes by vote count, band, and vote direction."""
    empty = {
        "version": RESONANCE_VERSION,
        "status": "NO_RESONANCE_SAMPLES",
        "by_count": [],
        "by_band": [],
        "by_transition": [],
    }
    if sample_frame is None or sample_frame.empty or "resonance_count" not in sample_frame:
        return empty

    frame = sample_frame.copy()
    frame["resonance_count"] = pd.to_numeric(
        frame["resonance_count"], errors="coerce"
    )
    frame = frame.loc[frame["resonance_count"].between(0, 5, inclusive="both")].copy()
    if frame.empty:
        return {**empty, "status": "NO_FULL_COVERAGE_SAMPLES"}

    by_count = [
        {"group": f"{int(count)}/5", **_group_metrics(group)}
        for count, group in frame.groupby("resonance_count", sort=True)
    ]
    frame["_band"] = pd.cut(
        frame["resonance_count"],
        bins=[-0.5, 1.5, 3.5, 5.5],
        labels=["0-1/5", "2-3/5", "4-5/5"],
    )
    by_band = [
        {"group": str(band), **_group_metrics(group)}
        for band, group in frame.groupby("_band", observed=True, sort=False)
    ]

    delta3 = pd.to_numeric(
        frame.get("resonance_delta_3d", pd.Series(np.nan, index=frame.index)),
        errors="coerce",
    )
    strong = frame["resonance_count"].ge(4)
    frame["_transition"] = np.select(
        [strong & delta3.gt(0.0), strong & delta3.le(0.0), ~strong & delta3.gt(0.0)],
        ["RISING_TO_4PLUS", "4PLUS_NOT_RISING", "BELOW4_RISING"],
        default="BELOW4_NOT_RISING",
    )
    by_transition = [
        {"group": str(name), **_group_metrics(group)}
        for name, group in frame.groupby("_transition", sort=False)
    ]
    return {
        "version": RESONANCE_VERSION,
        "status": "EXPERIMENTAL_DIAGNOSTIC_ONLY",
        "definition": {
            "macd": "DIF > DEA (12,26,9)",
            "kdj": "K > D (RSV9,SMA3,SMA3)",
            "rsi": "RSI14 > 50",
            "obv": "OBV > EMA20(OBV)",
            "boll": "Close > SMA20 middle band",
            "strong_bull": "ResonanceCount >= 4",
            "direction": "3-bar vote change; RISING/FALLING/FLAT",
        },
        "samples": len(frame),
        "by_count": by_count,
        "by_band": by_band,
        "by_transition": by_transition,
    }
