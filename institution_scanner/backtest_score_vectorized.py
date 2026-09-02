"""Vectorized point-in-time scoring mirror of ``score_core.score_ticker``.

This module reproduces the production Setup/Trigger/Execution score as a full
per-bar series, so a backtest can evaluate the score at every rebalance date in
one pass instead of re-running the scalar scorer ~57 times per ticker.

The scoring SEMANTICS are identical to ``institution_scanner.score_core``; only
the evaluation strategy is different (array/rolling operations over the whole
frame instead of a Python loop over growing sub-frames).  Keep the two in sync
by running ``validate_vectorized.py`` after any change to either module.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from config import (
    AD_SLOPE_LOOKBACK,
    BB_WIDTH_COMPRESSION_LOOKBACK,
    CONSOLIDATION_MAX_RANGE_PCT,
    SCORING_WEIGHTS,
    VOLUME_ACCUM_MIN_DAYS,
    VOLUME_ACCUM_RATIO,
)


def _component_weights() -> tuple[float, float, float]:
    from score_core import _model_component_weights

    return _model_component_weights()


def _col(df: pd.DataFrame, name: str) -> np.ndarray | None:
    if name not in df.columns:
        return None
    return (
        pd.to_numeric(df[name], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .to_numpy(dtype=np.float64)
    )


def _ffill(a: np.ndarray) -> np.ndarray:
    """Last valid value at or before each position (matches ``_latest``)."""
    return pd.Series(a).ffill().to_numpy()


def _valid_lag(a: np.ndarray, periods: int) -> np.ndarray:
    """Lag over finite observations, then project back to the source rows.

    Several scalar scoring rules call ``dropna()`` before selecting an older
    observation.  A fixed row shift is therefore wrong after an indicator gap
    (most commonly a zero-turnover suspension).  The projected result also
    carries forward across an invalid current row, matching ``dropna()`` on a
    prefix ending at that row.
    """
    if periods < 0:
        raise ValueError(periods)
    values = np.asarray(a, dtype=np.float64)
    output = np.full(len(values), np.nan, dtype=np.float64)
    positions = np.flatnonzero(np.isfinite(values))
    if periods == 0:
        output[positions] = values[positions]
    elif positions.size > periods:
        output[positions[periods:]] = values[positions[:-periods]]
    return _ffill(output)


def _valid_rolling(
    a: np.ndarray,
    window: int,
    kind: str,
    *,
    min_periods: int | None = None,
    exclude_recent: int = 0,
) -> np.ndarray:
    """Rolling aggregate over the compact finite-observation sequence."""
    if window <= 0 or exclude_recent < 0:
        raise ValueError((window, exclude_recent))
    values = np.asarray(a, dtype=np.float64)
    output = np.full(len(values), np.nan, dtype=np.float64)
    positions = np.flatnonzero(np.isfinite(values))
    if positions.size == 0:
        return output
    compact = pd.Series(values[positions])
    rolling = compact.rolling(
        int(window),
        min_periods=int(window if min_periods is None else min_periods),
    )
    if kind == "min":
        result = rolling.min()
    elif kind == "max":
        result = rolling.max()
    elif kind == "mean":
        result = rolling.mean()
    elif kind == "median":
        result = rolling.median()
    else:
        raise ValueError(kind)
    if exclude_recent:
        result = result.shift(int(exclude_recent))
    output[positions] = result.to_numpy(dtype=np.float64)
    return _ffill(output)


def _valid_trailing_run(mask: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Trailing true run over valid observations, projected to all rows."""
    valid = np.asarray(valid, dtype=bool)
    compact = np.asarray(mask, dtype=bool)[valid]
    output = np.full(len(valid), np.nan, dtype=np.float64)
    if compact.size == 0:
        return np.zeros(len(valid), dtype=np.float64)
    positions = np.arange(len(compact), dtype=np.int64)
    last_false = np.maximum.accumulate(np.where(compact, -1, positions))
    run = np.where(compact, positions - last_false, 0).astype(np.float64)
    output[np.flatnonzero(valid)] = run
    return np.nan_to_num(_ffill(output), nan=0.0)


def _ret(a: np.ndarray, periods: int) -> np.ndarray:
    """``_safe_return``: (a[t]/a[t-p]-1)*100 with finite/positive guards."""
    if periods <= 0:
        return np.full(len(a), np.nan)
    current = _ffill(a)
    prev = _valid_lag(a, periods)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(
            (current > 0)
            & (prev > 0)
            & np.isfinite(current)
            & np.isfinite(prev),
            current / prev,
            np.nan,
        )
    return (ratio - 1.0) * 100.0


def _roll(a: np.ndarray, k: int, kind: str) -> np.ndarray:
    """Rolling window over the last ``k`` bars ending at each position."""
    s = pd.Series(a)
    if kind == "min":
        return s.rolling(k, min_periods=k).min().to_numpy()
    if kind == "max":
        return s.rolling(k, min_periods=k).max().to_numpy()
    if kind == "mean":
        return s.rolling(k, min_periods=k).mean().to_numpy()
    raise ValueError(kind)


def _roll_shift(a: np.ndarray, k: int, kind: str, offset: int) -> np.ndarray:
    """Rolling window of ``k`` bars ending at ``t - offset`` (inclusive).

    Mirrors the scalar ``series.iloc[-off-k:-off].agg()`` window (e.g. the
    ``resistance = high.iloc[-21:-1].max()`` = 20 bars ending one bar back).
    """
    if offset < 0:
        raise ValueError(offset)
    r = _roll(a, k, kind)
    if offset == 0:
        return r
    out = np.full(len(a), np.nan)
    out[offset:] = r[: len(a) - offset]
    return out


def _clampc(a: np.ndarray, low: float = 0.0, high: float = 1.0) -> np.ndarray:
    """``_clamp`` applied elementwise; non-finite maps to ``low``."""
    fin = np.where(np.isfinite(a), a, np.nan)
    return np.clip(np.where(np.isnan(fin), low, fin), low, high)


def _trend(close: np.ndarray, ma200: np.ndarray) -> np.ndarray:
    n = len(close)
    out = np.zeros(n)
    if ma200 is None:
        return out
    valid = np.isfinite(close) & np.isfinite(ma200)
    vc = np.cumsum(valid.astype(np.int64))
    idx = np.arange(n)

    price = _ffill(close)
    ma200f = _ffill(ma200)

    # last index <= t where close >= ma200 (both valid); else -1
    above = valid & (close >= ma200)
    last_above = np.maximum.accumulate(np.where(above, idx, -1))
    first_valid = int(idx[valid].min()) if valid.any() else n

    # ma200 60-bar slope
    ma0 = ma200f
    ma60 = np.full(n, np.nan)
    ma60[59:] = ma200f[: n - 59]
    slope_pct = np.where((ma0 > 0) & (ma60 > 0), ma0 / ma60 - 1.0, np.nan)

    below_pct = np.where(ma200f > 0, (ma200f - price) / ma200f, np.nan)

    # days below ma200 (only values inside valid region)
    days_below = np.where(
        last_above >= 0, idx - last_above, idx - first_valid + 1
    )

    # drawdown vs the trailing peak over min(504, vc_valid) valid bars
    close_valid = np.where(valid, close, np.nan)
    lookback_peak = pd.Series(close_valid).rolling(504, min_periods=1).max().to_numpy()
    drawdown = np.where(
        (price > 0) & np.isfinite(lookback_peak) & (lookback_peak > 0),
        (price - lookback_peak) / lookback_peak,
        np.nan,
    )
    depth = np.abs(drawdown)

    # 20-bar recovery slope
    close20 = np.full(n, np.nan)
    close20[19:] = close[: n - 19]
    recent_slope = np.where((price > 0) & (close20 > 0), price / close20 - 1.0, np.nan)

    s = np.zeros(n)
    s += np.where(slope_pct < 0, _clampc(np.abs(slope_pct) / 0.12) * 5.0, 0.0)
    s += np.where(below_pct > 0, _clampc(below_pct / 0.30) * 6.0, 0.0)
    s -= np.where(below_pct > 0, _clampc(np.maximum(below_pct - 0.45, 0.0) / 0.30) * 3.0, 0.0)
    s += _clampc(days_below / 250.0) * 3.0
    s += np.where(
        (depth >= 0.15) & (depth <= 0.50),
        _clampc(1.0 - np.abs(depth - 0.32) / 0.25) * 3.0,
        0.0,
    )
    s += np.where(recent_slope > 0, _clampc(recent_slope / 0.12) * 3.0, 0.0)
    s = _clampc(s, 0.0, 20.0)

    ok = (idx + 1 >= 252) & (vc >= 60) & np.isfinite(price) & (price > 0) & (ma200f > 0)
    return np.where(ok, s, 0.0)


def _volume(
    frame: pd.DataFrame,
    volma20: np.ndarray | None,
    volma120: np.ndarray | None,
    volz: np.ndarray | None,
) -> np.ndarray:
    n = len(frame)
    s = np.zeros(n)

    if volma20 is not None and volma120 is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(
                np.isfinite(volma20) & np.isfinite(volma120) & (volma120 != 0),
                volma20 / volma120,
                np.nan,
            )
        ratio_valid = np.isfinite(ratio)
        ratio_ff = _ffill(ratio)
        # trailing run of (ratio >= VOLUME_ACCUM_RATIO) within valid region
        is_true = ratio_valid & (ratio >= VOLUME_ACCUM_RATIO)
        consecutive = _valid_trailing_run(is_true, ratio_valid)

        ratio_now = ratio_ff
        vc = np.cumsum(ratio_valid.astype(np.int64))
        ratio20 = _valid_lag(ratio, 19)
        ratio_change = np.where(np.isfinite(ratio20), ratio_now - ratio20, np.nan)

        s += np.where(
            consecutive >= VOLUME_ACCUM_MIN_DAYS,
            4.0 + _clampc((consecutive - VOLUME_ACCUM_MIN_DAYS) / 80.0) * 6.0,
            0.0,
        )
        s += _clampc((ratio_now - VOLUME_ACCUM_RATIO) / 0.8) * 3.0
        s += np.where(vc >= 20, _clampc(ratio_change / 0.5) * 4.0, 0.0)

    if volz is not None:
        z_valid = np.isfinite(volz)
        z_ff = _ffill(volz)
        # The scalar path drops invalid values and averages up to 30 valid
        # observations once at least ten exist.
        positive = np.where(z_valid, (volz > 0).astype(np.float64), np.nan)
        pos_mean30 = _valid_rolling(
            positive, 30, "mean", min_periods=1
        )
        vc_z = np.cumsum(z_valid.astype(np.int64))
        s += np.where(vc_z >= 10, pos_mean30 * 3.0, 0.0)
        s += np.where(vc_z >= 10, _clampc(z_ff / 2.0) * 2.0, 0.0)

    return _clampc(s, 0.0, 25.0)


def _accumulation(
    frame: pd.DataFrame,
    close: np.ndarray,
    obv: np.ndarray | None,
    ad: np.ndarray | None,
    ad_slope: np.ndarray | None,
    cmf: np.ndarray | None,
    mfi: np.ndarray | None,
) -> np.ndarray:
    n = len(frame)
    s = np.zeros(n)

    if obv is not None:
        close_ff = _ffill(close)
        obv_ff = _ffill(obv)
        valid = np.isfinite(close) & np.isfinite(obv)
        vc = np.cumsum(valid.astype(np.int64))
        # last 60 valid bars split into two halves of 30:
        # first  = [t-59..t-30], second = [t-29..t]
        c30 = _roll(close, 30, "min")
        o30 = _roll(obv, 30, "min")
        sh_c_min = c30
        sh_o_min = o30
        fh_c_min = _roll_shift(close, 30, "min", 30)
        fh_o_min = _roll_shift(obv, 30, "min", 30)

        price_now = close_ff
        obv_now = obv_ff
        near_low = (sh_c_min > 0) & ((price_now - sh_c_min) / sh_c_min <= 0.05)
        price_retest = sh_c_min <= fh_c_min * 1.02
        obv_div = (sh_o_min > fh_o_min) & (obv_now >= sh_o_min)

        s += np.where(
            near_low & price_retest & obv_div, 8.0, np.where(obv_div, 3.0, 0.0)
        )
        s = np.where(vc >= 40, s, 0.0)

    if ad is not None and ad_slope is not None:
        ad_ff = _ffill(ad)
        vc_ad = np.cumsum(np.isfinite(ad).astype(np.int64))
        ad_scale = np.maximum(
            _valid_rolling(
                np.abs(ad), AD_SLOPE_LOOKBACK, "median"
            ),
            1.0,
        )
        active = (
            (vc_ad >= AD_SLOPE_LOOKBACK)
            & np.isfinite(ad_slope)
        )
        with np.errstate(divide="ignore", invalid="ignore"):
            slope_score = _clampc(ad_slope / (ad_scale * 0.03))
        s += np.where(active, slope_score * 5.0, 0.0)
        # Scalar uses max(last min(120, valid_count) AD observations).
        ad120max = _valid_rolling(ad, 120, "max", min_periods=1)
        s += np.where(
            active
            & np.isfinite(ad_ff)
            & np.isfinite(ad120max)
            & (ad_ff >= ad120max * 0.95),
            1.0,
            0.0,
        )

    if cmf is not None:
        cmf_ff = _ffill(cmf)
        vc_cmf = np.cumsum(np.isfinite(cmf).astype(np.int64))
        cmf20 = _valid_lag(cmf, 19)
        cmf_change = np.where(np.isfinite(cmf20), cmf_ff - cmf20, np.nan)
        s += np.where(vc_cmf >= 20, _clampc(cmf_ff / 0.15) * 4.0, 0.0)
        s += np.where(vc_cmf >= 20, _clampc(cmf_change / 0.10) * 2.0, 0.0)

    if mfi is not None:
        fin = np.isfinite(mfi)
        s += np.where(
            fin & (mfi >= 40) & (mfi <= 70), 3.0,
            np.where(fin & (mfi >= 30) & (mfi <= 80), 1.5, 0.0),
        )

    return _clampc(s, 0.0, 25.0)


def _volatility(
    atr14: np.ndarray | None,
    atr50: np.ndarray | None,
    bbw: np.ndarray | None,
    hv20: np.ndarray | None,
    hv60: np.ndarray | None,
) -> np.ndarray:
    n = len(atr14) if atr14 is not None else (
        len(bbw) if bbw is not None else (len(hv20) if hv20 is not None else 0)
    )
    if n == 0:
        return np.zeros(0)
    comps = []
    if atr14 is not None and atr50 is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            comps.append(
                np.where(
                    np.isfinite(atr14) & np.isfinite(atr50) & (atr50 > 0),
                    _clampc((1.0 - atr14 / atr50) / 0.35),
                    np.nan,
                )
            )
    if bbw is not None:
        current = _ffill(bbw)
        bb_vc = np.cumsum(np.isfinite(bbw).astype(np.int64))
        # median over the 50 values bb[-60:-10] -> rolling(50).median() ended at t-10
        base = _valid_rolling(
            bbw,
            BB_WIDTH_COMPRESSION_LOOKBACK - 10,
            "median",
            exclude_recent=10,
        )
        with np.errstate(divide="ignore", invalid="ignore"):
            comps.append(
                np.where(
                    (bb_vc >= BB_WIDTH_COMPRESSION_LOOKBACK)
                    & np.isfinite(base)
                    & (base > 0),
                    _clampc(1.0 - current / base),
                    np.nan,
                )
            )
    if hv20 is not None and hv60 is not None:
        with np.errstate(divide="ignore", invalid="ignore"):
            comps.append(
                np.where(
                    np.isfinite(hv20) & np.isfinite(hv60) & (hv60 > 0),
                    _clampc((1.0 - hv20 / hv60) / 0.5),
                    np.nan,
                )
            )
    if not comps:
        return np.zeros(n)
    stacked = np.stack(comps, axis=0)
    finite_count = np.sum(np.isfinite(stacked), axis=0)
    coverage = finite_count / 3.0
    mean_comp = np.divide(
        np.nansum(stacked, axis=0),
        finite_count,
        out=np.full(n, np.nan, dtype=np.float64),
        where=finite_count > 0,
    )
    return _clampc(mean_comp * coverage * 15.0, 0.0, 15.0)


def _structure(
    frame: pd.DataFrame,
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    low52w: np.ndarray | None,
    distlow52w: np.ndarray | None,
    regslope: np.ndarray | None,
    regr2: np.ndarray | None,
    above_hvn: np.ndarray | None,
    disthvn: np.ndarray | None,
) -> np.ndarray:
    n = len(frame)
    s = np.zeros(n)

    if low52w is not None and distlow52w is not None:
        d = np.where(np.isfinite(distlow52w), distlow52w, np.nan)
        s += np.where(
            (d >= 0) & (d < 8), d / 8 * 5,
            np.where((d >= 8) & (d <= 12), 5.0,
                     np.where((d > 12) & (d <= 20), (20 - d) / 8 * 5, 0.0)),
        )

    # consolidation over last 45 bars
    high45 = _roll(high, 45, "max")
    low45 = _roll(low, 45, "min")
    close45 = _roll(close, 45, "mean")
    with np.errstate(divide="ignore", invalid="ignore"):
        range_pct = np.where(close45 > 0, (high45 - low45) / close45 * 100, np.nan)
    tight = _clampc(1 - range_pct / CONSOLIDATION_MAX_RANGE_PCT, 0, 1)
    s += np.where(range_pct <= CONSOLIDATION_MAX_RANGE_PCT, (0.2 + tight * 0.8) * 5, 0.0)

    if regslope is not None:
        rs = np.where(np.isfinite(regslope), np.abs(regslope), np.nan)
        s += _clampc(1 - rs / 0.05, 0, 1) * 2
        if regr2 is not None:
            r2 = np.where(np.isfinite(regr2), regr2, np.nan)
            s += _clampc(r2, 0, 1) * 1

    if above_hvn is not None and disthvn is not None:
        ah = np.where(np.isfinite(above_hvn), above_hvn, 0.0).astype(bool)
        dh = np.where(np.isfinite(disthvn), disthvn, np.nan)
        s += np.where(ah & (dh > 0) & (dh < 10), _clampc(1 - dh / 10, 0, 1) * 2, 0.0)

    ok = np.arange(n) + 1 >= 252
    ok = ok
    return np.where(ok, np.minimum(s, 15.0), 0.0)


def _value_trap(
    close: np.ndarray,
    volume: np.ndarray,
    ma20: np.ndarray | None,
    ma50: np.ndarray | None,
    cmf: np.ndarray | None,
    ad_slope: np.ndarray | None,
    obv: np.ndarray | None,
    *,
    is_etf: bool = False,
) -> np.ndarray:
    n = len(close)
    risk = np.zeros(n)
    close_ff = _ffill(close)
    ma20f = _ffill(ma20) if ma20 is not None else np.full(n, np.nan)
    ma50f = _ffill(ma50) if ma50 is not None else np.full(n, np.nan)

    clean_vc = np.cumsum(np.isfinite(close).astype(np.int64))

    ret20 = _ret(close, 20)
    ret60 = _ret(close, 60)
    ret120 = _ret(close, 120)

    price = close_ff

    # persistent deterioration
    risk += np.where(np.isfinite(ret120) & (ret120 < 0), _clampc(np.abs(ret120) / 45.0) * 15.0, 0.0)

    if ma50 is not None:
        ma50v = _ffill(ma50)
        old_ma50 = np.full(n, np.nan)
        old_ma50[24:] = ma50[: n - 24]
        risk += np.where(
            np.isfinite(old_ma50) & (old_ma50 > 0) & (ma50v < old_ma50),
            _clampc((old_ma50 - ma50v) / old_ma50 / 0.12) * 12.0,
            0.0,
        )
        risk += np.where(
            np.isfinite(price) & (price < ma50v) & np.isfinite(ret20) & (ret20 < 0),
            8.0,
            0.0,
        )

    # recent low vs prior low
    recent_low = _valid_rolling(close, 40, "min", min_periods=1)
    prior_block = _valid_rolling(
        close, 40, "min", min_periods=1, exclude_recent=40
    )
    prior_low = np.where(clean_vc >= 80, prior_block, recent_low)
    risk += np.where(
        np.isfinite(prior_low) & (prior_low > 0) & (recent_low < prior_low * 0.98),
        _clampc((prior_low - recent_low) / prior_low / 0.12) * 15.0,
        0.0,
    )

    risk += np.where(
        np.isfinite(ret20) & np.isfinite(ret60) & (ret20 < 0) & (ret60 < 0), 10.0, 0.0
    )

    # money-flow evidence
    cmf_f = _ffill(cmf) if cmf is not None else np.full(n, np.nan)
    ads_f = _ffill(ad_slope) if ad_slope is not None else np.full(n, np.nan)
    flow_available = np.zeros(n)
    flow_positive = np.zeros(n)
    for arr in (cmf_f, ads_f):
        fin = np.isfinite(arr)
        flow_available += fin.astype(np.float64)
        flow_positive += (fin & (arr > 0)).astype(np.float64)
    if obv is not None:
        obv_ff = _ffill(obv)
        obv20 = _valid_lag(obv, 19)
        obv_ok = np.isfinite(obv_ff) & np.isfinite(obv20)
        flow_available += obv_ok.astype(np.float64)
        flow_positive += (obv_ok & (obv_ff - obv20 > 0)).astype(np.float64)

    risk += np.where(
        (flow_available > 0) & (flow_positive == 0), 25.0,
        np.where((flow_available > 0) & (flow_positive == 1), 10.0,
                 np.where((flow_available > 0) & (flow_positive >= 2), -8.0, 0.0)),
    )

    # volume contraction
    vol20 = _valid_rolling(volume, 20, "mean")
    vol60_40 = _valid_rolling(
        volume, 40, "mean", exclude_recent=20
    )
    vvc = np.cumsum(np.isfinite(volume).astype(np.int64))
    risk += np.where(
        (vvc >= 60) & np.isfinite(vol20) & (vol60_40 > 0) & (vol20 < vol60_40 * 0.75)
        & np.isfinite(ret20) & (ret20 < 0),
        10.0,
        0.0,
    )
    risk += np.where(
        (vvc >= 60) & np.isfinite(vol20) & (vol60_40 > 0) & (vol20 < vol60_40 * 0.75)
        & np.isfinite(ret20) & (ret20 >= 0),
        -3.0,
        0.0,
    )

    risk = np.where(clean_vc >= 121, risk, 0.0)

    recovery = (
        np.isfinite(price) & np.isfinite(ma20f) & np.isfinite(ma50f)
        & (price >= ma20f) & (ma20f >= ma50f) & np.isfinite(ret20) & (ret20 > 0)
    )
    risk -= np.where(recovery, 15.0, 0.0)
    risk -= np.where(
        ~recovery & np.isfinite(ret20) & (ret20 > 5.0) & (flow_positive >= 2), 8.0, 0.0
    )

    if is_etf:
        risk *= 0.80
    return _clampc(risk, 0.0, 100.0)


def _breakout(
    close: np.ndarray,
    high: np.ndarray,
    volume: np.ndarray,
    ma20: np.ndarray | None,
    ma50: np.ndarray | None,
    ma200: np.ndarray | None,
) -> np.ndarray:
    n = len(close)
    close_ff = _ffill(close)
    vol_ff = _ffill(volume)
    ma20f = _ffill(ma20) if ma20 is not None else np.full(n, np.nan)
    ma50f = _ffill(ma50) if ma50 is not None else np.full(n, np.nan)
    ma200f = _ffill(ma200) if ma200 is not None else np.full(n, np.nan)

    valid3 = np.isfinite(close) & np.isfinite(high) & np.isfinite(volume)
    vc3 = np.cumsum(valid3.astype(np.int64))

    price = _ffill(close)
    pts = np.zeros(n)

    pts += np.where(
        np.isfinite(price) & np.isfinite(ma20f) & np.isfinite(ma50f),
        np.where(
            (price > ma20f) & (ma20f > ma50f), 15.0,
            np.where(price > ma20f, 8.0, 0.0),
        ),
        0.0,
    )
    pts += np.where(np.isfinite(ma200f) & (price > ma200f), 10.0, 0.0)

    # resistance = high.iloc[-21:-1].max(); vol20 = volume.iloc[-21:-1].mean()
    resist = _roll_shift(high, 20, "max", 1)
    vol20 = _roll_shift(volume, 20, "mean", 1)
    vol_now = vol_ff

    cond_br = (vc3 >= 21) & np.isfinite(resist) & (price > resist)
    pts += np.where(cond_br, 25.0, 0.0)
    pts += np.where(cond_br & (vol20 > 0) & (vol_now >= vol20 * 1.5), 15.0, 0.0)

    # up/down volume over last 10 bars (scalar: volume.where(close.diff()>0).iloc[-10:].mean())
    diff_up = np.full(n, False)
    diff_down = np.full(n, False)
    prev_ok = np.isfinite(close[1:]) & np.isfinite(close[:-1])
    diff_up[1:] = prev_ok & (close[1:] > close[:-1])
    diff_down[1:] = prev_ok & (close[1:] <= close[:-1])
    up_vol = pd.Series(np.where(diff_up, volume, np.nan)).rolling(10, min_periods=1).mean().to_numpy()
    down_vol = pd.Series(np.where(diff_down, volume, np.nan)).rolling(10, min_periods=1).mean().to_numpy()
    cond_updown = (
        (vc3 >= 10) & np.isfinite(up_vol) & np.isfinite(down_vol)
        & (down_vol > 0) & (up_vol > down_vol * 1.15)
    )
    pts += np.where(cond_updown, 15.0, 0.0)

    # consolidation range: close[-5:] vs close[-20:-5]
    with np.errstate(divide="ignore", invalid="ignore"):
        recent_range = (
            _roll_shift(close, 5, "max", 0) - _roll_shift(close, 5, "min", 0)
        ) / np.maximum(price, 1e-9)
        prior_range = (
            _roll_shift(close, 15, "max", 5) - _roll_shift(close, 15, "min", 5)
        ) / np.maximum(price, 1e-9)
    pts += np.where(
        (vc3 >= 20) & np.isfinite(prior_range) & (prior_range > 0)
        & (recent_range < prior_range * 0.75),
        10.0,
        0.0,
    )

    # ma20 rising: close[-1] > close[-10] and ma20 > rolling_mean(MA20,10)
    c10 = np.full(n, np.nan); c10[9:] = close_ff[: n - 9]
    ma20_roll10 = _roll_shift(ma20f, 10, "mean", 0)
    pts += np.where(
        np.isfinite(ma20f) & (vc3 >= 10) & (close_ff > c10)
        & np.isfinite(ma20_roll10) & (ma20f > ma20_roll10),
        10.0,
        0.0,
    )

    return _clampc(np.where(vc3 >= 60, pts, 0.0), 0.0, 100.0)


def _entry_execution(
    close: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    volume: np.ndarray,
    atr14: np.ndarray | None,
    rsi: np.ndarray | None,
    ma20: np.ndarray | None,
    cmf: np.ndarray | None,
    ad_slope: np.ndarray | None,
    obv: np.ndarray | None,
    breakout: np.ndarray,
    *,
    price_decimals: int,
) -> np.ndarray:
    n = len(close)
    close_ff = _ffill(close)
    atr = _ffill(atr14) if atr14 is not None else np.full(n, np.nan)
    ma20f = _ffill(ma20) if ma20 is not None else np.full(n, np.nan)
    rsif = _ffill(rsi) if rsi is not None else np.full(n, np.nan)

    price = close_ff
    eff_atr = np.where(np.isfinite(atr) & (atr > 0), atr, price * 0.03)

    # resistance = high.iloc[-21:-1].max(); support = low.iloc[-20:].min()
    res = _roll_shift(high, 20, "max", 1)
    sup_entry = _roll_shift(low, 20, "min", 0)
    sup_exec = sup_entry

    hvc = np.cumsum(np.isfinite(high).astype(np.int64))
    lvc = np.cumsum(np.isfinite(low).astype(np.int64))

    # entry_point rounds resistance to 2dp; execution_quality_score does not.
    res_entry = np.round(res, int(price_decimals))
    resistance_entry = np.where(hvc >= 21, res_entry, price)
    resistance_exec = np.where(hvc >= 21, res, price + eff_atr * 2.0)
    support_entry = np.where(lvc >= 20, sup_entry, price)
    support_exec = np.where(lvc >= 20, sup_exec, price - eff_atr)

    # price_breakout (uses rounded entry resistance)
    price_breakout = (
        (breakout >= 75.0) & np.isfinite(price) & np.isfinite(resistance_entry)
        & (price > resistance_entry)
    )

    # stop and projected_target (entry), rounded to 2 decimals (stocks)
    stop_base = np.where(price_breakout, resistance_entry, support_entry)
    stop = np.maximum(stop_base - eff_atr, 0.0)
    stop = np.round(stop, int(price_decimals))
    projected = np.where(price_breakout, price + eff_atr * 2.5, np.maximum(resistance_entry, price))
    projected = np.round(projected, int(price_decimals))

    # execution_quality_score (uses unrounded resistance)
    exec_support = np.where(price_breakout, resistance_exec, support_exec)
    with np.errstate(divide="ignore", invalid="ignore"):
        dist = np.maximum(0.0, price - exec_support) / np.maximum(eff_atr, 1e-9)
    score = np.zeros(n)
    score += (1.0 - _clampc(dist / 3.0)) * 35.0
    with np.errstate(divide="ignore", invalid="ignore"):
        ma_dist = np.abs(price - ma20f) / np.maximum(eff_atr, 1e-9)
    score += np.where(np.isfinite(ma20f), (1.0 - _clampc(ma_dist / 2.5)) * 20.0, 0.0)

    with np.errstate(divide="ignore", invalid="ignore"):
        risk_dist = np.where((price > 0) & (stop >= 0), (price - stop) / price, np.nan)
    score += np.where(
        np.isfinite(risk_dist) & (risk_dist >= 0.02) & (risk_dist <= 0.08), 20.0,
        np.where(np.isfinite(risk_dist) & (risk_dist >= 0.01) & (risk_dist <= 0.12), 10.0, 0.0),
    )

    projected_t = np.where(np.isfinite(projected), projected, np.where(price_breakout, price + eff_atr * 2.5, resistance_exec))
    reward = np.maximum(0.0, projected_t - price)
    risk_amount = np.maximum(price - stop, eff_atr * 0.25)
    with np.errstate(divide="ignore", invalid="ignore"):
        reward_risk = np.where(risk_amount > 0, reward / risk_amount, 0.0)
    score += _clampc(reward_risk / 2.5) * 15.0

    score += np.where(
        np.isfinite(rsif),
        np.where((rsif >= 40) & (rsif <= 68), 10.0,
                 np.where((rsif >= 30) & (rsif <= 75), 5.0, 0.0)),
        0.0,
    )
    return _clampc(score, 0.0, 100.0)


def final_score_series(
    df: pd.DataFrame, is_etf: bool = False, return_components: bool = False
) -> np.ndarray | dict[str, np.ndarray]:
    n = len(df)
    idx = np.arange(n)

    close = _col(df, "Close")
    if close is None or n < 1:
        return np.zeros(n)
    high = _col(df, "High")
    low = _col(df, "Low")
    volume = _col(df, "Volume")
    ma20 = _col(df, "MA20")
    ma50 = _col(df, "MA50")
    ma200 = _col(df, "MA200")
    atr14 = _col(df, "ATR14")
    atr50 = _col(df, "ATR50")
    rsi = _col(df, "RSI14")
    roc = _col(df, "ROC")
    obv = _col(df, "OBV")
    ad = _col(df, "AD")
    ad_slope = _col(df, "AD_Slope")
    cmf = _col(df, "CMF")
    mfi = _col(df, "MFI")
    volma20 = _col(df, "VolMA20")
    volma120 = _col(df, "VolMA120")
    volz = _col(df, "VolZScore")
    bbw = _col(df, "BB_Width")
    hv20 = _col(df, "HV20")
    hv60 = _col(df, "HV60")
    low52w = _col(df, "Low52W")
    distlow52w = _col(df, "DistToLow52W")
    regslope = _col(df, "RegSlope")
    regr2 = _col(df, "RegR2")
    above_hvn = _col(df, "Above_HVN")
    disthvn = _col(df, "DistToHVN_Pct")

    # ---- dimension availability (mirrors _score_dimensions_available) ----
    def has_finite(cols, minimum):
        arrs = [c for c in cols if c is not None]
        if len(arrs) != len(cols):
            return np.zeros(n, dtype=bool), np.zeros(n, dtype=np.int64)
        all_fin = np.ones(n, dtype=bool)
        for c in arrs:
            all_fin &= np.isfinite(c)
        vc = np.cumsum(all_fin.astype(np.int64))
        return (vc >= minimum) & all_fin, vc

    trend_avail = (idx + 1 >= 252) & has_finite((close, ma200), 60)[0]
    vol_hf, _ = has_finite((volma20, volma120), VOLUME_ACCUM_MIN_DAYS)
    volz_hf, _ = has_finite((volz,), 10)
    volume_avail = (idx + 1 >= 120) & (vol_hf | volz_hf)
    obv_hf, _ = has_finite((obv,), 40)
    ad_hf, _ = has_finite((ad, ad_slope), AD_SLOPE_LOOKBACK)
    cmf_hf, _ = has_finite((cmf,), 20)
    mfi_hf, _ = has_finite((mfi,), 1)
    accum_avail = (idx + 1 >= 60) & (obv_hf | ad_hf | cmf_hf | mfi_hf)
    atr_hf, _ = has_finite((atr14, atr50), 1)
    bb_hf, _ = has_finite((bbw,), BB_WIDTH_COMPRESSION_LOOKBACK)
    hv_hf, _ = has_finite((hv20, hv60), 1)
    volat_avail = (idx + 1 >= BB_WIDTH_COMPRESSION_LOOKBACK) & (atr_hf | bb_hf | hv_hf)
    struct_avail = (idx + 1 >= 252) & has_finite((close, high, low), 1)[0]

    available = np.stack([trend_avail, volume_avail, accum_avail, volat_avail, struct_avail], axis=0)
    available_count = available.sum(axis=0)
    missing = 5 - available_count
    coverage = available_count / 5.0

    # ---- dimension sub-scores ----
    trend = _trend(close, ma200)
    volume_score = _volume(df, volma20, volma120, volz)
    accumulation = _accumulation(df, close, obv, ad, ad_slope, cmf, mfi)
    volatility = _volatility(atr14, atr50, bbw, hv20, hv60)
    structure = _structure(
        df, close, high, low, low52w, distlow52w, regslope, regr2, above_hvn, disthvn
    )

    raw = np.stack([trend, volume_score, accumulation, volatility, structure], axis=0)
    raw = np.where(available, raw, 0.0)

    # style classification + adjustment
    with np.errstate(divide="ignore", invalid="ignore"):
        atr_pct = np.where(
            np.isfinite(atr14) & np.isfinite(close) & (close > 0),
            atr14 / close,
            np.nan,
        )
    roc_arr = np.where(np.isfinite(roc), roc, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        vr = np.where(
            np.isfinite(volma20) & np.isfinite(volma120) & (volma120 > 0),
            volma20 / volma120,
            1.0,
        )

    style_ok = idx + 1 >= 60
    high_growth = style_ok & np.isfinite(atr_pct) & (atr_pct >= 0.045)
    trend_style = style_ok & ~high_growth & (roc_arr >= 12)
    fund_flow = style_ok & ~high_growth & ~trend_style & (vr >= 1.25)
    low_def = style_ok & ~high_growth & ~trend_style & ~fund_flow & np.isfinite(atr_pct) & (atr_pct <= 0.025)

    # default (1,1,1,1,1); apply style weights
    adj_trend = np.ones(n); adj_vol = np.ones(n); adj_acc = np.ones(n)
    adj_volat = np.ones(n); adj_struct = np.ones(n)
    adj_trend = np.where(high_growth, 1.15, adj_trend)
    adj_vol = np.where(high_growth, 1.05, adj_vol)
    adj_acc = np.where(high_growth, 0.90, adj_acc)
    adj_volat = np.where(high_growth, 0.85, adj_volat)
    adj_struct = np.where(high_growth, 0.95, adj_struct)
    adj_trend = np.where(trend_style, 1.25, adj_trend)
    adj_acc = np.where(trend_style, 0.90, adj_acc)
    adj_volat = np.where(trend_style, 0.85, adj_volat)
    adj_struct = np.where(trend_style, 0.95, adj_struct)
    adj_trend = np.where(fund_flow, 0.90, adj_trend)
    adj_vol = np.where(fund_flow, 1.05, adj_vol)
    adj_acc = np.where(fund_flow, 1.25, adj_acc)
    adj_volat = np.where(fund_flow, 1.05, adj_volat)
    adj_trend = np.where(low_def, 0.90, adj_trend)
    adj_vol = np.where(low_def, 0.95, adj_vol)
    adj_acc = np.where(low_def, 1.05, adj_acc)
    adj_volat = np.where(low_def, 1.25, adj_volat)
    adj_struct = np.where(low_def, 1.20, adj_struct)
    if is_etf:
        # ``score_core.classify_style`` always selects the dedicated ETF
        # profile; market-derived stock style masks must not leak into it.
        adj_trend.fill(1.00)
        adj_vol.fill(1.00)
        adj_acc.fill(1.10)
        adj_volat.fill(1.00)
        adj_struct.fill(0.90)

    limits = np.array(
        [
            SCORING_WEIGHTS.trend,
            SCORING_WEIGHTS.volume,
            SCORING_WEIGHTS.accumulation,
            SCORING_WEIGHTS.volatility,
            SCORING_WEIGHTS.structure,
        ],
        dtype=float,
    )
    adj = np.stack([adj_trend, adj_vol, adj_acc, adj_volat, adj_struct], axis=0)
    adjusted = np.clip(raw * adj, 0.0, limits[:, None])

    total = adjusted.sum(axis=0)

    trap = _value_trap(
        close,
        volume,
        ma20,
        ma50,
        cmf,
        ad_slope,
        obv,
        is_etf=is_etf,
    )
    breakout = _breakout(close, high, volume, ma20, ma50, ma200)
    exec_raw = _entry_execution(
        close,
        high,
        low,
        volume,
        atr14,
        rsi,
        ma20,
        cmf,
        ad_slope,
        obv,
        breakout,
        price_decimals=3 if is_etf else 2,
    )

    setup_cov = 0.55 + 0.45 * coverage
    trigger_cov = 0.75 + 0.25 * coverage
    exec_cov = 0.70 + 0.30 * coverage

    base = _clampc(total * setup_cov, 0.0, 100.0)
    trigger = _clampc(breakout * trigger_cov, 0.0, 100.0)
    execution = _clampc(exec_raw * exec_cov, 0.0, 100.0)

    ws, wt, we = _component_weights()
    final = _clampc(base * ws + trigger * wt + execution * we, 0.0, 100.0)
    coverage_cap = 40.0 + 60.0 * coverage
    final = np.minimum(final, coverage_cap)

    final = np.where(missing >= 4, 0.0, final)
    if return_components:
        return {
            "final": final,
            "base": base,
            "trigger": trigger,
            "execution": execution,
            "breakout": breakout,
            "exec_raw": exec_raw,
            "trap": trap,
            "coverage": coverage,
        }
    return final
