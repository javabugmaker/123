"""Independent five-factor technical resonance diagnostics.

The layer mirrors the TradingView MACD/KDJ/RSI/OBV/BOLL experiment as an
orthogonal 0..5 vote. It never changes production scoring, entry eligibility,
or ranking. Historical samples are annotated at ``signal_date`` (the known
close) rather than the next-session execution date, preventing look-ahead.

v91 vectorizes vote construction, sample-date attachment and grouped outcome
aggregation. The only remaining iteration boundary is per security when an
external caller must load separate OHLCV histories; no row-wise DataFrame loop
is required inside this module.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

RESONANCE_VERSION = "2026-08-23-v91-five-factor-vectorized-v1"
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

_FIELD_MAP = {
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

_BOOLEAN_TARGETS = {
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


def _nullable_bool(
    values: np.ndarray,
    available: np.ndarray,
    index: pd.Index,
) -> pd.Series:
    array = pd.array(values, dtype="boolean")
    array[~available] = pd.NA
    return pd.Series(array, index=index)


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

    available_matrix = np.column_stack(
        (
            (macd.notna() & macd_signal.notna()).to_numpy(dtype=bool),
            (k.notna() & d.notna()).to_numpy(dtype=bool),
            rsi.notna().to_numpy(dtype=bool),
            (obv.notna() & obv_ema.notna()).to_numpy(dtype=bool),
            (close.notna() & boll_middle.notna()).to_numpy(dtype=bool),
        )
    )
    bull_matrix = np.column_stack(
        (
            (macd > macd_signal).to_numpy(dtype=bool),
            (k > d).to_numpy(dtype=bool),
            (rsi > 50.0).to_numpy(dtype=bool),
            (obv > obv_ema).to_numpy(dtype=bool),
            (close > boll_middle).to_numpy(dtype=bool),
        )
    )

    available_count = available_matrix.sum(axis=1, dtype=np.int16)
    full_mask = available_count == len(_FACTOR_COLUMNS)
    votes = bull_matrix.sum(axis=1, dtype=np.int16).astype(np.float64)
    votes[~full_mask] = np.nan

    result = pd.DataFrame(index=frame.index)
    for position, column in enumerate(_FACTOR_COLUMNS):
        result[column] = _nullable_bool(
            bull_matrix[:, position],
            available_matrix[:, position],
            frame.index,
        )
    result["ResonanceAvailable"] = available_count.astype(np.int8)
    result["ResonanceCount"] = votes

    count = result["ResonanceCount"]
    result["ResonanceDelta1"] = count.diff()
    result["ResonanceDelta3"] = count.diff(3)
    result["ResonanceStrongBull"] = count.ge(4).where(full_mask)
    result["ResonanceStrongBear"] = count.le(1).where(full_mask)
    result["ResonanceTurnUp"] = (count.ge(4) & count.shift(1).le(3)).where(full_mask)
    result["ResonanceTurnDown"] = (
        count.le(1) & count.shift(1).ge(2)
    ).where(full_mask)

    delta3 = result["ResonanceDelta3"]
    direction = np.select(
        [delta3.gt(0.0).to_numpy(), delta3.lt(0.0).to_numpy()],
        ["RISING", "FALLING"],
        default="FLAT",
    )
    result["ResonanceDirection"] = np.where(
        full_mask, direction, "UNAVAILABLE"
    )
    result["ResonanceVersion"] = RESONANCE_VERSION
    return result


def _date_key(value: Any) -> str:
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else pd.Timestamp(parsed).strftime("%Y-%m-%d")


def attach_resonance_to_sample_frame(
    sample_frame: pd.DataFrame,
    frame: pd.DataFrame | None,
) -> pd.DataFrame:
    """Vectorized signal-date join for one security's historical samples."""
    if sample_frame is None or sample_frame.empty:
        return sample_frame.copy()
    if frame is None or frame.empty:
        return sample_frame.copy()

    resonance = compute_five_factor_resonance(frame)
    snapshot = resonance[list(_FIELD_MAP)].rename(columns=_FIELD_MAP).copy()
    snapshot["_res_signal_date"] = pd.to_datetime(
        snapshot.index, errors="coerce"
    ).normalize()
    snapshot = snapshot.loc[snapshot["_res_signal_date"].notna()].drop_duplicates(
        "_res_signal_date", keep="last"
    )

    working = sample_frame.copy()
    working["_res_row_position"] = np.arange(len(working), dtype=np.int64)
    if "signal_date" in working.columns:
        working["_res_signal_date"] = pd.to_datetime(
            working["signal_date"], errors="coerce"
        ).dt.normalize()
    else:
        working["_res_signal_date"] = pd.NaT

    replace_columns = [*(_FIELD_MAP.values()), "resonance_version"]
    working = working.drop(
        columns=[column for column in replace_columns if column in working.columns]
    )
    merged = working.merge(
        snapshot,
        on="_res_signal_date",
        how="left",
        sort=False,
        validate="many_to_one",
    ).sort_values("_res_row_position", kind="stable")

    merged["resonance_version"] = RESONANCE_VERSION
    merged["resonance_available"] = (
        pd.to_numeric(merged.get("resonance_available"), errors="coerce")
        .fillna(0)
        .astype(np.int8)
    )
    merged["resonance_direction"] = (
        merged.get(
            "resonance_direction",
            pd.Series("UNAVAILABLE", index=merged.index, dtype=object),
        )
        .fillna("UNAVAILABLE")
        .astype(str)
    )
    for column in _BOOLEAN_TARGETS:
        if column not in merged.columns:
            merged[column] = False
        else:
            merged[column] = pd.Series(
                pd.array(merged[column], dtype="boolean"),
                index=merged.index,
            ).fillna(False).astype(bool)

    merged = merged.drop(columns=["_res_signal_date", "_res_row_position"])
    merged.index = sample_frame.index
    return merged


def attach_resonance_to_samples(
    samples: list[dict[str, Any]], frame: pd.DataFrame | None
) -> list[dict[str, Any]]:
    """Attach the exact signal-close resonance snapshot without row-wise loops."""
    if not samples:
        return samples
    sample_frame = pd.DataFrame.from_records(samples)
    attached = attach_resonance_to_sample_frame(sample_frame, frame)
    return attached.to_dict(orient="records")


def _weights(frame: pd.DataFrame) -> pd.Series:
    if "sample_weight" not in frame:
        return pd.Series(1.0, index=frame.index, dtype=np.float64)
    return (
        pd.to_numeric(frame["sample_weight"], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
        .clip(lower=0.0)
        .astype(np.float64)
    )


def _numeric_or_nan(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=np.float64)
    return pd.to_numeric(frame[name], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )


def _aggregate_groups(
    frame: pd.DataFrame,
    group_values: pd.Series,
    *,
    group_labels: dict[object, str] | None = None,
) -> list[dict[str, Any]]:
    """Vectorized weighted statistics for a small categorical partition."""
    weights = _weights(frame)
    excess20 = _numeric_or_nan(frame, "net_return20") - _numeric_or_nan(
        frame, "benchmark_return20"
    )
    excess60 = _numeric_or_nan(frame, "net_return60") - _numeric_or_nan(
        frame, "benchmark_return60"
    )
    drawdown60 = _numeric_or_nan(frame, "drawdown60")

    valid20 = excess20.notna() & weights.gt(0.0)
    valid60 = excess60.notna() & weights.gt(0.0)

    work = pd.DataFrame(
        {
            "_group": group_values,
            "_weight": weights,
            "_den20": weights.where(valid20, 0.0),
            "_sum20": (excess20 * weights).where(valid20, 0.0),
            "_win20": (excess20.gt(0.0).astype(np.float64) * weights).where(
                valid20, 0.0
            ),
            "_den60": weights.where(valid60, 0.0),
            "_sum60": (excess60 * weights).where(valid60, 0.0),
            "_drawdown60": drawdown60,
        },
        index=frame.index,
    )
    work = work.loc[work["_group"].notna()]
    if work.empty:
        return []

    grouped = work.groupby("_group", observed=True, sort=False)
    aggregate = grouped.agg(
        samples=("_group", "size"),
        effective_samples=("_weight", "sum"),
        _den20=("_den20", "sum"),
        _sum20=("_sum20", "sum"),
        _win20=("_win20", "sum"),
        _den60=("_den60", "sum"),
        _sum60=("_sum60", "sum"),
        max_drawdown_60d=("_drawdown60", "min"),
    )
    aggregate["net_excess_win_rate_20d"] = aggregate["_win20"].div(
        aggregate["_den20"].replace(0.0, np.nan)
    )
    aggregate["average_net_excess_20d"] = aggregate["_sum20"].div(
        aggregate["_den20"].replace(0.0, np.nan)
    )
    aggregate["average_net_excess_60d"] = aggregate["_sum60"].div(
        aggregate["_den60"].replace(0.0, np.nan)
    )

    public_columns = [
        "samples",
        "effective_samples",
        "net_excess_win_rate_20d",
        "average_net_excess_20d",
        "average_net_excess_60d",
        "max_drawdown_60d",
    ]
    aggregate = aggregate[public_columns].copy()
    for column in public_columns[1:]:
        aggregate[column] = aggregate[column].round(4)

    records = aggregate.reset_index().to_dict(orient="records")
    return [
        {
            "group": (
                group_labels.get(record["_group"], str(record["_group"]))
                if group_labels
                else str(record["_group"])
            ),
            "samples": int(record["samples"]),
            "effective_samples": float(record["effective_samples"]),
            "net_excess_win_rate_20d": float(
                record["net_excess_win_rate_20d"]
            ),
            "average_net_excess_20d": float(record["average_net_excess_20d"]),
            "average_net_excess_60d": float(record["average_net_excess_60d"]),
            "max_drawdown_60d": float(record["max_drawdown_60d"]),
        }
        for record in records
    ]


def summarize_resonance_samples(sample_frame: pd.DataFrame) -> dict[str, Any]:
    """Summarize held-out outcomes by vote count, band, and vote direction."""
    empty = {
        "version": RESONANCE_VERSION,
        "status": "NO_RESONANCE_SAMPLES",
        "by_count": [],
        "by_band": [],
        "by_transition": [],
    }
    if (
        sample_frame is None
        or sample_frame.empty
        or "resonance_count" not in sample_frame
    ):
        return empty

    frame = sample_frame.copy()
    frame["resonance_count"] = pd.to_numeric(
        frame["resonance_count"], errors="coerce"
    )
    frame = frame.loc[
        frame["resonance_count"].between(0, 5, inclusive="both")
    ].copy()
    if frame.empty:
        return {**empty, "status": "NO_FULL_COVERAGE_SAMPLES"}

    count_values = frame["resonance_count"].astype(np.int8)
    count_labels = {value: f"{value}/5" for value in range(6)}
    by_count = _aggregate_groups(
        frame,
        count_values,
        group_labels=count_labels,
    )

    band = pd.cut(
        frame["resonance_count"],
        bins=[-0.5, 1.5, 3.5, 5.5],
        labels=["0-1/5", "2-3/5", "4-5/5"],
        ordered=True,
    )
    by_band = _aggregate_groups(frame, pd.Series(band, index=frame.index))

    delta3 = pd.to_numeric(
        frame.get("resonance_delta_3d", pd.Series(np.nan, index=frame.index)),
        errors="coerce",
    )
    strong = frame["resonance_count"].ge(4)
    transition_values = np.select(
        [
            (strong & delta3.gt(0.0)).to_numpy(),
            (strong & delta3.le(0.0)).to_numpy(),
            (~strong & delta3.gt(0.0)).to_numpy(),
        ],
        ["RISING_TO_4PLUS", "4PLUS_NOT_RISING", "BELOW4_RISING"],
        default="BELOW4_NOT_RISING",
    )
    transition = pd.Categorical(
        transition_values,
        categories=[
            "RISING_TO_4PLUS",
            "4PLUS_NOT_RISING",
            "BELOW4_RISING",
            "BELOW4_NOT_RISING",
        ],
        ordered=True,
    )
    by_transition = _aggregate_groups(
        frame, pd.Series(transition, index=frame.index)
    )

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
