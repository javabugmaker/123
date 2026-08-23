"""v98 vectorised backtest integrity layer.

This module closes the remaining dense-history FAST/EXACT drift and moves the
historical execution hot path from per-sample pandas/numpy reductions to batched
arrays. It deliberately leaves point-in-time universe evidence and final dict
serialization scalar because those are provenance/I/O boundaries rather than
numeric kernels.

It also replaces the v96 WAIT_PULLBACK second full backtest pass with one
signal-only pass plus a five-day vectorised order resolver. A suspended T+1 no
longer destroys a valid T+2..T+5 conditional order.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import analytics_core as _core
import backtest_fastscore_v80 as _fast
import backtest_sample_acceleration_v80 as _sample
import conditional_fill_v96 as _conditional
import execution_costs as _costs
import tradeability_acceleration_v80 as _tradefast
from backtest_alignment import align_benchmark_returns

BACKTEST_VECTORIZATION_VERSION = "2026-08-23-v98-vectorized-execution-integrity-v1"
CONDITIONAL_FILL_VERSION = "2026-08-23-v98-vectorized-wait-pullback-v1"

_ORIGINAL_FAST_SCORE_MATRIX = _fast._fast_score_matrix
_ORIGINAL_SAMPLE_BACKTEST = _core._backtest_one_ticker
_ORIGINAL_DATE_ARRAY = _tradefast._date_array
_INSTALLED = False


def _numeric(
    frame: pd.DataFrame,
    column: str,
    *,
    fallback: np.ndarray | None = None,
) -> np.ndarray:
    if column not in frame.columns:
        if fallback is not None:
            return fallback.copy()
        return np.full(len(frame), np.nan, dtype=np.float64)
    return pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=np.float64)


def _dense_trend_score(frame: pd.DataFrame, *, peak_window: int) -> np.ndarray:
    """Vectorised scalar TrendScore semantics for dense mature history."""
    n = len(frame)
    close_s = pd.to_numeric(frame["Close"], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    ma200_s = pd.to_numeric(frame["MA200"], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    close = close_s.to_numpy(dtype=np.float64)
    ma200 = ma200_s.to_numpy(dtype=np.float64)
    pair = np.isfinite(close) & np.isfinite(ma200)
    available = (
        pair
        & (np.arange(n) >= 251)
        & (_fast._rolling_count(pair, 504) >= 60)
    )

    score = np.zeros(n, dtype=np.float64)
    old_ma = ma200_s.shift(59).to_numpy(dtype=np.float64)
    slope = np.divide(
        ma200,
        old_ma,
        out=np.full(n, np.nan, dtype=np.float64),
        where=np.isfinite(ma200) & np.isfinite(old_ma) & (old_ma != 0.0),
    ) - 1.0
    score += np.where(
        available & np.isfinite(slope) & (slope < 0.0),
        np.clip(np.abs(slope) / 0.12, 0.0, 1.0) * 5.0,
        0.0,
    )

    below_pct = np.divide(
        ma200 - close,
        ma200,
        out=np.full(n, np.nan, dtype=np.float64),
        where=np.isfinite(ma200) & (ma200 > 0.0),
    )
    score += np.where(
        available & np.isfinite(below_pct) & (below_pct > 0.0),
        np.clip(below_pct / 0.30, 0.0, 1.0) * 6.0
        - np.clip(np.maximum(below_pct - 0.45, 0.0) / 0.30, 0.0, 1.0) * 3.0,
        0.0,
    )

    below = pair & (close < ma200)
    days_below = np.minimum(
        _fast._trailing_run(below),
        _fast._rolling_count(pair, 504),
    )
    score += np.where(
        available,
        np.clip(days_below.astype(np.float64) / 250.0, 0.0, 1.0) * 3.0,
        0.0,
    )

    valid_close = pd.Series(np.where(pair, close, np.nan), index=frame.index)
    peak = (
        valid_close.rolling(int(peak_window), min_periods=1)
        .max()
        .to_numpy(dtype=np.float64)
    )
    depth = np.abs(
        np.divide(
            close - peak,
            peak,
            out=np.zeros(n, dtype=np.float64),
            where=np.isfinite(peak) & (peak > 0.0),
        )
    )
    depth_mask = available & (depth >= 0.15) & (depth <= 0.50)
    score += np.where(
        depth_mask,
        np.clip(1.0 - np.abs(depth - 0.32) / 0.25, 0.0, 1.0) * 3.0,
        0.0,
    )

    old_close = close_s.shift(19).to_numpy(dtype=np.float64)
    recovery = np.divide(
        close,
        old_close,
        out=np.full(n, np.nan, dtype=np.float64),
        where=np.isfinite(close) & np.isfinite(old_close) & (old_close != 0.0),
    ) - 1.0
    score += np.where(
        available & np.isfinite(recovery) & (recovery > 0.0),
        np.clip(recovery / 0.12, 0.0, 1.0) * 3.0,
        0.0,
    )
    score = np.clip(score, 0.0, 20.0)
    score[~available] = 0.0
    return score


def _fast_score_matrix(frame: pd.DataFrame, *, is_etf: bool):
    """Correct the last v80 252-bar Trend peak to the canonical 504 bars."""
    matrix = _ORIGINAL_FAST_SCORE_MATRIX(frame, is_etf=is_etf)
    if matrix is None:
        return None

    # The v80 matrix is used only after _dense_history_supported succeeds.
    # At FAST_VECTOR_START and later all five dimensions are available, so
    # BaseScore is the unrenormalised component sum and FinalScore is linear.
    old_trend = _dense_trend_score(frame, peak_window=252)
    new_trend = _dense_trend_score(frame, peak_window=504)
    delta = new_trend - old_trend
    delta[: int(_fast._FAST_VECTOR_START)] = 0.0
    if not np.any(np.abs(delta) > 1e-12):
        return matrix

    base = np.clip(matrix.base_score + delta, 0.0, 100.0)
    setup_weight, trigger_weight, execution_weight = _core._model_component_weights()
    final = np.clip(
        base * float(setup_weight)
        + matrix.trigger_score * float(trigger_weight)
        + matrix.execution_score * float(execution_weight),
        0.0,
        100.0,
    )
    return _fast.FastScoreMatrix(
        base_score=base,
        trigger_score=matrix.trigger_score,
        execution_score=matrix.execution_score,
        final_score=final,
        breakout_score=matrix.breakout_score,
        value_trap_risk=matrix.value_trap_risk,
        entry_signal=matrix.entry_signal,
    )


def _date_array(frame: pd.DataFrame) -> np.ndarray:
    """Fast path for DatetimeIndex while preserving numeric-index semantics."""
    index = frame.index
    if isinstance(index, pd.DatetimeIndex):
        values = index
        if values.tz is not None:
            values = values.tz_localize(None)
        return values.normalize().to_numpy(dtype="datetime64[ns]")
    return _ORIGINAL_DATE_ARRAY(frame)


def _selected_drawdown_curves(
    entry_indices: np.ndarray,
    entry_prices: np.ndarray,
    closes: np.ndarray,
    lows: np.ndarray,
    *,
    max_forward: int,
) -> np.ndarray:
    """Return cumulative drawdown curves for all selected entries in one pass."""
    starts = np.asarray(entry_indices, dtype=np.int64)
    prices = np.asarray(entry_prices, dtype=np.float64)
    width = max(0, int(max_forward)) + 1
    if starts.size == 0:
        return np.empty((0, width), dtype=np.float64)

    offsets = np.arange(width, dtype=np.int64)
    positions = starts[:, None] + offsets[None, :]
    in_range = (positions >= 0) & (positions < len(closes))
    safe = np.clip(positions, 0, max(0, len(closes) - 1))
    close_window = closes[safe].astype(np.float64, copy=True)
    low_window = lows[safe].astype(np.float64, copy=True)
    close_window[~in_range] = np.nan
    low_window[~in_range] = np.nan

    running_peak = np.maximum.accumulate(close_window, axis=1)
    running_peak = np.maximum(running_peak, prices[:, None])
    ratios = low_window / running_peak - 1.0
    cumulative_min = np.minimum.accumulate(ratios, axis=1)
    return np.minimum(cumulative_min, 0.0) * 100.0


def _resolve_exit_batch(
    ticker: str,
    frame: pd.DataFrame,
    intended: np.ndarray,
    *,
    is_etf: bool,
    max_delay_days: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    intended = np.asarray(intended, dtype=np.int64)
    target = np.full(len(intended), -1, dtype=np.int64)
    delay = np.zeros(len(intended), dtype=np.int16)
    reason = np.full(len(intended), "out_of_range", dtype=object)

    state = _tradefast._build_state(ticker, frame, is_etf)
    if state is None:
        for position, raw in enumerate(intended):
            resolved, resolved_delay, resolved_reason = _core.resolve_exit_index(
                ticker,
                frame,
                int(raw),
                is_etf=is_etf,
                max_delay_days=max_delay_days,
            )
            if resolved is not None:
                target[position] = int(resolved)
            delay[position] = int(resolved_delay)
            reason[position] = str(resolved_reason)
        return target, delay, reason

    resolved, delays = _tradefast._exit_resolution(state, max_delay_days)
    valid_intended = (intended >= 1) & (intended < len(frame))
    if not valid_intended.any():
        return target, delay, reason

    slots = np.flatnonzero(valid_intended)
    starts = intended[slots]
    resolved_targets = resolved[starts].astype(np.int64, copy=False)
    resolved_delays = delays[starts].astype(np.int16, copy=False)
    target[slots] = resolved_targets
    delay[slots] = np.maximum(resolved_delays, 0)

    success = resolved_targets >= 0
    success_slots = slots[success]
    success_targets = resolved_targets[success]
    success_delays = resolved_delays[success]
    immediate = success_delays == 0
    reason[success_slots[immediate]] = "tradeable"
    delayed_slots = success_slots[~immediate]
    delayed_targets = success_targets[~immediate]
    if delayed_slots.size:
        reason[delayed_slots] = state.exit_reason[np.maximum(delayed_targets - 1, 0)]

    failed_slots = slots[~success]
    if failed_slots.size:
        stop = np.minimum(
            len(frame),
            intended[failed_slots] + max(0, int(max_delay_days)) + 1,
        )
        has_reason = stop > intended[failed_slots]
        if has_reason.any():
            reason[failed_slots[has_reason]] = state.exit_reason[stop[has_reason] - 1]
            delay[failed_slots[has_reason]] = (
                stop[has_reason] - intended[failed_slots[has_reason]]
            ).astype(np.int16)
    return target, delay, reason


def _evaluation_maps(
    enriched: pd.DataFrame,
    signal_points: Any,
    *,
    is_etf: bool,
) -> tuple[dict[int, tuple[float, str]], dict[int, tuple[float, float, float]]]:
    attached = getattr(signal_points, "evaluations", None)
    components = dict(getattr(signal_points, "components", {}) or {})
    if attached is not None:
        return (
            {
                int(index): (float(score), str(signal))
                for index, score, signal in attached
            },
            components,
        )

    evaluation_map: dict[int, tuple[float, str]] = {}
    for raw_index in signal_points:
        index = int(raw_index)
        historical = _core._backtest_scoring_window(enriched, index)
        score = _core.score_ticker(historical, is_etf=is_etf)
        final = _core._finite_float(getattr(score, "final_score", np.nan), np.nan)
        if not np.isfinite(final):
            final = _core._finite_float(getattr(score, "total", np.nan), 0.0)
        evaluation_map[index] = (
            float(final),
            _core._historical_entry_signal(historical, score, is_etf=is_etf),
        )
        components[index] = (
            _core._finite_float(getattr(score, "base_score", np.nan), 0.0),
            _core._finite_float(getattr(score, "trigger_score", np.nan), 0.0),
            _core._finite_float(
                getattr(score, "execution_score", np.nan),
                _core._finite_float(getattr(score, "entry_score", np.nan), 0.0),
            ),
        )
    return evaluation_map, components


def _backtest_one_ticker(
    ticker: str,
    source: str,
    benchmark_frame: pd.DataFrame | None = None,
    commission: float = _core.BACKTEST_STOCK_COMMISSION_RATE,
    stamp_duty: float = 0.0005,
    slippage: float = 0.001,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None] = (None, None),
    *,
    profile: Any | None = None,
    signal_start_index: int | None = None,
    sample_min_signal_index: int | None = None,
    frame: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    """Batched immediate execution; PIT/provenance and dict output stay scalar."""
    if (
        _core.is_entry_tradeable is not _tradefast.is_entry_tradeable
        or _core.resolve_exit_index is not _tradefast.resolve_exit_index
    ):
        return _ORIGINAL_SAMPLE_BACKTEST(
            ticker,
            source,
            benchmark_frame,
            commission,
            stamp_duty,
            slippage,
            split_dates,
            profile=profile,
            signal_start_index=signal_start_index,
            sample_min_signal_index=sample_min_signal_index,
            frame=frame,
        )

    if frame is None:
        frame = _core._load_cache(ticker, source)
    if frame is None or len(frame) < 300:
        return []
    raw_path = _core._cache_path(ticker, source)
    enriched, _cache_hit = _core.load_or_compute_indicators(
        ticker,
        frame,
        _core.compute_all_indicators,
        source_path=raw_path if raw_path.exists() else None,
        enabled=_core.INDICATOR_CACHE_ENABLED,
    )
    is_etf = _core.is_etf_ticker(str(ticker))

    if profile is None and signal_start_index is None:
        signal_points = _core._signal_points(enriched, is_etf=is_etf)
    else:
        active_profile = profile or _core._resolve_backtest_profile("exact", 1)
        profile_components: dict[int, tuple[float, float, float]] = {}
        signal_points = _core._SignalPointList(
            _core._signal_evaluations(
                enriched,
                is_etf=is_etf,
                profile=active_profile,
                start_index=signal_start_index,
                component_sink=profile_components,
            ),
            profile_components,
        )
    if not signal_points:
        return []

    evaluation_map, component_map = _evaluation_maps(
        enriched, signal_points, is_etf=is_etf
    )
    closes = _numeric(enriched, "Close")
    opens = _numeric(enriched, "Open")
    lows = _numeric(enriched, "Low")
    highs = _numeric(enriched, "High", fallback=closes)
    volumes = _numeric(enriched, "Volume")
    outcome_horizon = max(60, int(_core.BACKTEST_OUTCOME_HORIZON_DAYS))
    min_sample = (
        int(sample_min_signal_index) if sample_min_signal_index is not None else 0
    )

    indices = np.asarray([int(value) for value in signal_points], dtype=np.int64)
    entry = indices + 1
    final = entry + outcome_horizon
    safe_entry = np.clip(entry, 0, len(enriched) - 1)
    safe20 = np.clip(entry + 20, 0, len(enriched) - 1)
    safe_final = np.clip(final, 0, len(enriched) - 1)

    bad_high_low = (
        ~np.isfinite(highs)
        | ~np.isfinite(lows)
        | (highs <= 0.0)
        | (lows <= 0.0)
    ).astype(np.int32)
    bad_prefix = np.empty(len(enriched) + 1, dtype=np.int64)
    bad_prefix[0] = 0
    np.cumsum(bad_high_low, out=bad_prefix[1:])

    state = _tradefast._build_state(ticker, enriched, is_etf)
    if state is None:
        return _ORIGINAL_SAMPLE_BACKTEST(
            ticker,
            source,
            benchmark_frame,
            commission,
            stamp_duty,
            slippage,
            split_dates,
            profile=profile,
            signal_start_index=signal_start_index,
            sample_min_signal_index=sample_min_signal_index,
            frame=frame,
        )

    in_range = (entry > 0) & (entry < len(enriched)) & (final < len(enriched))
    valid = (
        (indices >= min_sample)
        & in_range
        & np.isfinite(opens[safe_entry])
        & (opens[safe_entry] > 0.0)
        & state.entry_tradeable[safe_entry]
        & np.isfinite(closes[safe20])
        & np.isfinite(closes[safe_final])
    )
    slots = np.flatnonzero(valid)
    if slots.size:
        valid[slots] &= (
            bad_prefix[final[slots] + 1] - bad_prefix[entry[slots]]
        ) == 0
    indices = indices[valid]
    entry = entry[valid]
    if indices.size == 0:
        return []

    pit_keep = np.ones(len(indices), dtype=bool)
    pit_status = np.full(len(indices), "UNAVAILABLE", dtype=object)
    pit_reason = np.full(len(indices), "", dtype=object)
    for position, index in enumerate(indices):
        signal_date = pd.Timestamp(enriched.index[int(index)])
        eligible, reason = _core.point_in_time_eligibility(ticker, signal_date)
        if eligible is False:
            pit_keep[position] = False
        elif eligible is True:
            pit_status[position] = "ELIGIBLE"
        pit_reason[position] = str(reason)

    indices = indices[pit_keep]
    entry = entry[pit_keep]
    pit_status = pit_status[pit_keep]
    pit_reason = pit_reason[pit_keep]
    if indices.size == 0:
        return []

    intended20 = entry + 20
    intended60 = entry + outcome_horizon
    exit20, delay20, reason20 = _resolve_exit_batch(
        ticker,
        enriched,
        intended20,
        is_etf=is_etf,
        max_delay_days=_core.BACKTEST_MAX_EXIT_DELAY_DAYS,
    )
    exit60, delay60, reason60 = _resolve_exit_batch(
        ticker,
        enriched,
        intended60,
        is_etf=is_etf,
        max_delay_days=_core.BACKTEST_MAX_EXIT_DELAY_DAYS,
    )
    exit_keep = (exit20 >= 0) & (exit60 >= 0)
    indices = indices[exit_keep]
    entry = entry[exit_keep]
    pit_status = pit_status[exit_keep]
    pit_reason = pit_reason[exit_keep]
    exit20 = exit20[exit_keep]
    exit60 = exit60[exit_keep]
    delay20 = delay20[exit_keep]
    delay60 = delay60[exit_keep]
    reason20 = reason20[exit_keep]
    reason60 = reason60[exit_keep]
    if indices.size == 0:
        return []

    entry_prices = opens[entry]
    fee_schedule = _costs.BrokerFeeSchedule(stock_commission_rate=float(commission))
    notional = float(fee_schedule.assumed_trade_notional)
    commission_rate = _costs.effective_commission_rate(
        is_etf=is_etf, schedule=fee_schedule, notional=notional
    )
    entry_slip = _sample._liquidity_slippage_vector(
        opens, volumes, base_slippage=slippage, notional=notional
    )
    exit_slip = _sample._liquidity_slippage_vector(
        closes, volumes, base_slippage=slippage, notional=notional
    )
    statutory = 0.0 if is_etf else max(0.0, float(stamp_duty))
    cost20 = (
        commission_rate * 2.0
        + entry_slip[entry]
        + exit_slip[exit20]
        + statutory
    ) * 100.0
    cost60 = (
        commission_rate * 2.0
        + entry_slip[entry]
        + exit_slip[exit60]
        + statutory
    ) * 100.0

    max_forward = outcome_horizon + max(0, int(_core.BACKTEST_MAX_EXIT_DELAY_DAYS))
    drawdown = _selected_drawdown_curves(
        entry, entry_prices, closes, lows, max_forward=max_forward
    )
    offset20 = exit20 - entry
    offset60 = exit60 - entry
    dd20 = drawdown[np.arange(len(entry)), offset20]
    dd60 = drawdown[np.arange(len(entry)), offset60]

    regime = _sample._benchmark_regime_by_stock_row(benchmark_frame, enriched.index)
    spacings = np.empty(len(indices), dtype=np.float64)
    spacings[0] = float(outcome_horizon)
    if len(indices) > 1:
        spacings[1:] = np.maximum(1, np.diff(indices))
    weights = np.minimum(1.0, spacings / float(outcome_horizon))

    validation_end, test_start = split_dates
    samples: list[dict[str, Any]] = []
    for position, index in enumerate(indices):
        index_i = int(index)
        entry_i = int(entry[position])
        exit20_i = int(exit20[position])
        exit60_i = int(exit60[position])
        entry_price = float(entry_prices[position])
        future20 = float(closes[exit20_i])
        future60 = float(closes[exit60_i])
        historical_score, historical_signal = evaluation_map[index_i]
        setup, trigger, execution = component_map.get(
            index_i, (historical_score, 0.0, 0.0)
        )
        entry_date = pd.Timestamp(enriched.index[entry_i])
        exit60_date = pd.Timestamp(enriched.index[exit60_i])
        samples.append(
            {
                "ticker": ticker,
                "asset_type": "etf" if is_etf else "stock",
                "entry_signal": historical_signal,
                "market_regime": str(regime[index_i]),
                "universe_snapshot_status": str(pit_status[position]),
                "universe_snapshot_reason": str(pit_reason[position]),
                "signal_date": pd.Timestamp(enriched.index[index_i]).strftime("%Y-%m-%d"),
                "entry_date": entry_date.strftime("%Y-%m-%d"),
                "entry_price": entry_price,
                "exit20_date": pd.Timestamp(enriched.index[exit20_i]).strftime("%Y-%m-%d"),
                "exit60_date": exit60_date.strftime("%Y-%m-%d"),
                "exit20_delay_days": int(delay20[position]),
                "exit60_delay_days": int(delay60[position]),
                "exit20_delay_reason": str(reason20[position]),
                "exit60_delay_reason": str(reason60[position]),
                "round_trip_cost20_pct": round(float(cost20[position]), 6),
                "round_trip_cost60_pct": round(float(cost60[position]), 6),
                "return20": (future20 / entry_price - 1.0) * 100.0,
                "return60": (future60 / entry_price - 1.0) * 100.0,
                "benchmark_return20": np.nan,
                "benchmark_return60": np.nan,
                "net_return20": (future20 / entry_price - 1.0) * 100.0
                - float(cost20[position]),
                "net_return60": (future60 / entry_price - 1.0) * 100.0
                - float(cost60[position]),
                "drawdown20": float(dd20[position]),
                "drawdown60": float(dd60[position]),
                "score": historical_score,
                "setup_score": float(setup),
                "trigger_score": float(trigger),
                "execution_score": float(execution),
                "split": _core._purged_split_label(
                    entry_date,
                    exit60_date,
                    (validation_end, test_start),
                ),
                "sample_weight": round(float(weights[position]), 4),
            }
        )
    return samples


def _entry_zone_arrays(
    enriched: pd.DataFrame,
    *,
    is_etf: bool,
) -> tuple[np.ndarray, np.ndarray]:
    close_s = pd.to_numeric(enriched["Close"], errors="coerce")
    high_s = pd.to_numeric(enriched["High"], errors="coerce")
    low_s = pd.to_numeric(enriched["Low"], errors="coerce")
    ma20_s = pd.to_numeric(enriched["MA20"], errors="coerce")
    atr_s = pd.to_numeric(enriched["ATR14"], errors="coerce")

    close = close_s.to_numpy(dtype=np.float64)
    ma20 = ma20_s.to_numpy(dtype=np.float64)
    atr = atr_s.to_numpy(dtype=np.float64)
    support = low_s.rolling(20, min_periods=1).min().to_numpy(dtype=np.float64)
    resistance = (
        high_s.shift(1).rolling(20, min_periods=1).max().to_numpy(dtype=np.float64)
    )
    resistance = np.where(np.isfinite(resistance), resistance, close)
    effective_atr = np.where(np.isfinite(atr) & (atr > 0.0), atr, close * 0.03)
    anchor = support + effective_atr * 0.55
    use_ma = np.isfinite(ma20) & np.isfinite(close) & (ma20 <= close)
    anchor = np.where(use_ma, np.maximum(anchor, ma20), anchor)
    anchor = np.minimum(anchor, close)
    low_zone = np.maximum(support, anchor - effective_atr * 0.35)
    high_zone = np.minimum(resistance, anchor + effective_atr * 0.35)
    decimals = int(_core.tradable_price_decimals(is_etf))
    low_zone = np.round(low_zone, decimals)
    high_zone = np.round(high_zone, decimals)
    high_zone = np.where(high_zone < low_zone, low_zone, high_zone)
    return low_zone, high_zone


def _wait_fill_batch(
    ticker: str,
    enriched: pd.DataFrame,
    signal_indices: np.ndarray,
    zone_low: np.ndarray,
    zone_high: np.ndarray,
    *,
    is_etf: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    signals = np.asarray(signal_indices, dtype=np.int64)
    count = len(signals)
    fill_index = np.full(count, -1, dtype=np.int64)
    fill_price = np.full(count, np.nan, dtype=np.float64)
    fill_delay = np.zeros(count, dtype=np.int16)
    fill_basis = np.full(count, "", dtype=object)
    if count == 0:
        return fill_index, fill_price, fill_delay, fill_basis

    state = _tradefast._build_state(ticker, enriched, is_etf)
    if state is None:
        for position, signal in enumerate(signals):
            resolved = _conditional._conditional_fill(
                ticker,
                enriched,
                int(signal),
                float(zone_low[position]),
                float(zone_high[position]),
                is_etf=is_etf,
            )
            if resolved is None:
                continue
            target, price, delay, basis = resolved
            fill_index[position] = int(target)
            fill_price[position] = float(price)
            fill_delay[position] = int(delay)
            fill_basis[position] = str(basis)
        return fill_index, fill_price, fill_delay, fill_basis

    opens = _numeric(enriched, "Open")
    highs = _numeric(enriched, "High")
    lows = _numeric(enriched, "Low")
    offsets = np.arange(
        1,
        int(_conditional.WAIT_PULLBACK_VALIDITY_TRADING_DAYS) + 1,
        dtype=np.int64,
    )
    candidates = signals[:, None] + offsets[None, :]
    in_range = candidates < len(enriched)
    safe = np.clip(candidates, 0, len(enriched) - 1)
    open_matrix = opens[safe]
    high_matrix = highs[safe]
    low_matrix = lows[safe]
    tradeable = state.entry_tradeable[safe] & in_range
    valid_price = (
        np.isfinite(open_matrix)
        & np.isfinite(high_matrix)
        & np.isfinite(low_matrix)
        & (open_matrix > 0.0)
    )
    gap_below = valid_price & (open_matrix < zone_low[:, None])
    touches = (
        valid_price
        & (high_matrix >= zone_low[:, None])
        & (low_matrix <= zone_high[:, None])
    )
    event = tradeable & (gap_below | touches)
    has_event = event.any(axis=1)
    first = np.argmax(event, axis=1)
    rows = np.arange(count)
    chosen_gap = gap_below[rows, first]
    fillable = has_event & ~chosen_gap
    if not fillable.any():
        return fill_index, fill_price, fill_delay, fill_basis

    chosen_candidates = candidates[rows, first]
    chosen_open = open_matrix[rows, first]
    inside = (chosen_open >= zone_low) & (chosen_open <= zone_high)
    chosen_price = np.where(inside, chosen_open, zone_high)
    decimals = int(_core.tradable_price_decimals(is_etf))
    chosen_price = np.round(chosen_price, decimals)

    slots = np.flatnonzero(fillable)
    fill_index[slots] = chosen_candidates[slots]
    fill_price[slots] = chosen_price[slots]
    fill_delay[slots] = offsets[first[slots]].astype(np.int16)
    fill_basis[slots] = np.where(inside[slots], "OPEN_IN_ZONE", "LIMIT_AT_ZONE_HIGH")
    return fill_index, fill_price, fill_delay, fill_basis


def _wait_samples(
    ticker: str,
    enriched: pd.DataFrame,
    benchmark_frame: pd.DataFrame | None,
    commission: float,
    stamp_duty: float,
    slippage: float,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None],
    *,
    profile: Any,
    signal_start_index: int | None,
    sample_min_signal_index: int | None,
) -> list[dict[str, Any]]:
    is_etf = _core.is_etf_ticker(str(ticker))
    components: dict[int, tuple[float, float, float]] = {}
    previous_actions = _core._BACKTEST_ACTIONABLE_SIGNALS
    try:
        _core._BACKTEST_ACTIONABLE_SIGNALS = _conditional._WAIT_ONLY
        evaluations = _core._signal_evaluations(
            enriched,
            is_etf=is_etf,
            profile=profile,
            start_index=signal_start_index,
            component_sink=components,
        )
    finally:
        _core._BACKTEST_ACTIONABLE_SIGNALS = previous_actions

    if not evaluations:
        return []
    min_sample = (
        int(sample_min_signal_index) if sample_min_signal_index is not None else 0
    )
    evaluations = [
        (int(index), float(score), str(signal))
        for index, score, signal in evaluations
        if int(index) >= min_sample and str(signal).upper() == "WAIT_PULLBACK"
    ]
    if not evaluations:
        return []

    signal_indices = np.asarray([item[0] for item in evaluations], dtype=np.int64)
    scores = np.asarray([item[1] for item in evaluations], dtype=np.float64)
    pit_keep = np.ones(len(signal_indices), dtype=bool)
    pit_status = np.full(len(signal_indices), "UNAVAILABLE", dtype=object)
    pit_reason = np.full(len(signal_indices), "", dtype=object)
    for position, signal in enumerate(signal_indices):
        eligible, reason = _core.point_in_time_eligibility(
            ticker, pd.Timestamp(enriched.index[int(signal)])
        )
        if eligible is False:
            pit_keep[position] = False
        elif eligible is True:
            pit_status[position] = "ELIGIBLE"
        pit_reason[position] = str(reason)

    signal_indices = signal_indices[pit_keep]
    scores = scores[pit_keep]
    pit_status = pit_status[pit_keep]
    pit_reason = pit_reason[pit_keep]
    if signal_indices.size == 0:
        return []

    spacings = np.empty(len(signal_indices), dtype=np.float64)
    outcome_horizon = max(60, int(_core.BACKTEST_OUTCOME_HORIZON_DAYS))
    spacings[0] = float(outcome_horizon)
    if len(signal_indices) > 1:
        spacings[1:] = np.maximum(1, np.diff(signal_indices))
    weights = np.minimum(1.0, spacings / float(outcome_horizon))

    all_low, all_high = _entry_zone_arrays(enriched, is_etf=is_etf)
    zone_low = all_low[signal_indices]
    zone_high = all_high[signal_indices]
    valid_zone = (
        np.isfinite(zone_low)
        & np.isfinite(zone_high)
        & (zone_low > 0.0)
        & (zone_high >= zone_low)
    )
    signal_indices = signal_indices[valid_zone]
    scores = scores[valid_zone]
    pit_status = pit_status[valid_zone]
    pit_reason = pit_reason[valid_zone]
    weights = weights[valid_zone]
    zone_low = zone_low[valid_zone]
    zone_high = zone_high[valid_zone]
    if signal_indices.size == 0:
        return []

    fill_index, fill_price, fill_delay, fill_basis = _wait_fill_batch(
        ticker,
        enriched,
        signal_indices,
        zone_low,
        zone_high,
        is_etf=is_etf,
    )
    filled = (fill_index >= 0) & np.isfinite(fill_price) & (fill_price > 0.0)
    signal_indices = signal_indices[filled]
    scores = scores[filled]
    pit_status = pit_status[filled]
    pit_reason = pit_reason[filled]
    weights = weights[filled]
    zone_low = zone_low[filled]
    zone_high = zone_high[filled]
    fill_index = fill_index[filled]
    fill_price = fill_price[filled]
    fill_delay = fill_delay[filled]
    fill_basis = fill_basis[filled]
    if signal_indices.size == 0:
        return []

    closes = _numeric(enriched, "Close")
    highs = _numeric(enriched, "High", fallback=closes)
    lows = _numeric(enriched, "Low")
    volumes = _numeric(enriched, "Volume")
    intended20 = fill_index + 20
    intended60 = fill_index + outcome_horizon
    mature = intended60 < len(enriched)
    signal_indices = signal_indices[mature]
    scores = scores[mature]
    pit_status = pit_status[mature]
    pit_reason = pit_reason[mature]
    weights = weights[mature]
    zone_low = zone_low[mature]
    zone_high = zone_high[mature]
    fill_index = fill_index[mature]
    fill_price = fill_price[mature]
    fill_delay = fill_delay[mature]
    fill_basis = fill_basis[mature]
    intended20 = intended20[mature]
    intended60 = intended60[mature]
    if signal_indices.size == 0:
        return []

    exit20, delay20, reason20 = _resolve_exit_batch(
        ticker,
        enriched,
        intended20,
        is_etf=is_etf,
        max_delay_days=_core.BACKTEST_MAX_EXIT_DELAY_DAYS,
    )
    exit60, delay60, reason60 = _resolve_exit_batch(
        ticker,
        enriched,
        intended60,
        is_etf=is_etf,
        max_delay_days=_core.BACKTEST_MAX_EXIT_DELAY_DAYS,
    )
    safe_exit20 = np.clip(exit20, 0, len(closes) - 1)
    safe_exit60 = np.clip(exit60, 0, len(closes) - 1)
    valid_exit = (
        (exit20 >= 0)
        & (exit60 >= 0)
        & np.isfinite(closes[safe_exit20])
        & np.isfinite(closes[safe_exit60])
    )
    if valid_exit.any():
        bad = (
            ~np.isfinite(highs)
            | ~np.isfinite(lows)
            | (highs <= 0.0)
            | (lows <= 0.0)
        ).astype(np.int32)
        prefix = np.empty(len(enriched) + 1, dtype=np.int64)
        prefix[0] = 0
        np.cumsum(bad, out=prefix[1:])
        slots = np.flatnonzero(valid_exit)
        valid_exit[slots] &= (
            prefix[exit60[slots] + 1] - prefix[fill_index[slots]]
        ) == 0

    signal_indices = signal_indices[valid_exit]
    scores = scores[valid_exit]
    pit_status = pit_status[valid_exit]
    pit_reason = pit_reason[valid_exit]
    weights = weights[valid_exit]
    zone_low = zone_low[valid_exit]
    zone_high = zone_high[valid_exit]
    fill_index = fill_index[valid_exit]
    fill_price = fill_price[valid_exit]
    fill_delay = fill_delay[valid_exit]
    fill_basis = fill_basis[valid_exit]
    exit20 = exit20[valid_exit]
    exit60 = exit60[valid_exit]
    delay20 = delay20[valid_exit]
    delay60 = delay60[valid_exit]
    reason20 = reason20[valid_exit]
    reason60 = reason60[valid_exit]
    if signal_indices.size == 0:
        return []

    fee_schedule = _costs.BrokerFeeSchedule(stock_commission_rate=float(commission))
    notional = float(fee_schedule.assumed_trade_notional)
    commission_rate = _costs.effective_commission_rate(
        is_etf=is_etf, schedule=fee_schedule, notional=notional
    )
    entry_slip = _sample._liquidity_slippage_vector(
        fill_price,
        volumes[fill_index],
        base_slippage=slippage,
        notional=notional,
    )
    exit_slip = _sample._liquidity_slippage_vector(
        closes, volumes, base_slippage=slippage, notional=notional
    )
    statutory = 0.0 if is_etf else max(0.0, float(stamp_duty))
    cost20 = (
        commission_rate * 2.0 + entry_slip + exit_slip[exit20] + statutory
    ) * 100.0
    cost60 = (
        commission_rate * 2.0 + entry_slip + exit_slip[exit60] + statutory
    ) * 100.0

    max_forward = outcome_horizon + max(0, int(_core.BACKTEST_MAX_EXIT_DELAY_DAYS))
    curves = _selected_drawdown_curves(
        fill_index, fill_price, closes, lows, max_forward=max_forward
    )
    dd20 = curves[np.arange(len(fill_index)), exit20 - fill_index]
    dd60 = curves[np.arange(len(fill_index)), exit60 - fill_index]
    regime = _sample._benchmark_regime_by_stock_row(benchmark_frame, enriched.index)

    validation_end, test_start = split_dates
    samples: list[dict[str, Any]] = []
    for position, signal in enumerate(signal_indices):
        signal_i = int(signal)
        entry_i = int(fill_index[position])
        exit20_i = int(exit20[position])
        exit60_i = int(exit60[position])
        price = float(fill_price[position])
        future20 = float(closes[exit20_i])
        future60 = float(closes[exit60_i])
        setup, trigger, execution = components.get(
            signal_i, (float(scores[position]), 0.0, 0.0)
        )
        entry_date = pd.Timestamp(enriched.index[entry_i])
        exit60_date = pd.Timestamp(enriched.index[exit60_i])
        samples.append(
            {
                "ticker": ticker,
                "asset_type": "etf" if is_etf else "stock",
                "entry_signal": "WAIT_PULLBACK",
                "market_regime": str(regime[signal_i]),
                "universe_snapshot_status": str(pit_status[position]),
                "universe_snapshot_reason": str(pit_reason[position]),
                "signal_date": pd.Timestamp(enriched.index[signal_i]).strftime("%Y-%m-%d"),
                "entry_date": entry_date.strftime("%Y-%m-%d"),
                "entry_price": price,
                "entry_fill_type": "WAIT_PULLBACK_ZONE_TOUCH",
                "entry_fill_basis": str(fill_basis[position]),
                "entry_fill_delay_days": int(fill_delay[position]),
                "entry_zone_low": float(zone_low[position]),
                "entry_zone_high": float(zone_high[position]),
                "entry_zone_validity_days": int(
                    _conditional.WAIT_PULLBACK_VALIDITY_TRADING_DAYS
                ),
                "conditional_fill_version": CONDITIONAL_FILL_VERSION,
                "exit20_date": pd.Timestamp(enriched.index[exit20_i]).strftime("%Y-%m-%d"),
                "exit60_date": exit60_date.strftime("%Y-%m-%d"),
                "exit20_delay_days": int(delay20[position]),
                "exit60_delay_days": int(delay60[position]),
                "exit20_delay_reason": str(reason20[position]),
                "exit60_delay_reason": str(reason60[position]),
                "round_trip_cost20_pct": round(float(cost20[position]), 6),
                "round_trip_cost60_pct": round(float(cost60[position]), 6),
                "return20": (future20 / price - 1.0) * 100.0,
                "return60": (future60 / price - 1.0) * 100.0,
                "benchmark_return20": np.nan,
                "benchmark_return60": np.nan,
                "net_return20": (future20 / price - 1.0) * 100.0
                - float(cost20[position]),
                "net_return60": (future60 / price - 1.0) * 100.0
                - float(cost60[position]),
                "drawdown20": float(dd20[position]),
                "drawdown60": float(dd60[position]),
                "score": float(scores[position]),
                "setup_score": float(setup),
                "trigger_score": float(trigger),
                "execution_score": float(execution),
                "split": _core._purged_split_label(
                    entry_date,
                    exit60_date,
                    (validation_end, test_start),
                ),
                "sample_weight": round(float(weights[position]), 4),
            }
        )
    return align_benchmark_returns(samples, benchmark_frame)


def _conditional_backtest_one_ticker(
    ticker: str,
    source: str,
    benchmark_frame: pd.DataFrame | None = None,
    commission: float = _core.BACKTEST_STOCK_COMMISSION_RATE,
    stamp_duty: float = 0.0005,
    slippage: float = 0.001,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None] = (None, None),
    *,
    profile: Any | None = None,
    signal_start_index: int | None = None,
    sample_min_signal_index: int | None = None,
    frame: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    """One immediate execution pass plus one signal-only WAIT_PULLBACK pass."""
    active_profile = profile or _core._resolve_backtest_profile("exact", 1)
    original = _conditional._ORIGINAL_BACKTEST_ONE_TICKER
    if original is None:
        return []

    with _conditional._EXECUTION_LOCK:
        previous_actions = _core._BACKTEST_ACTIONABLE_SIGNALS
        try:
            _core._BACKTEST_ACTIONABLE_SIGNALS = _conditional._IMMEDIATE_SIGNALS
            immediate = original(
                ticker,
                source,
                benchmark_frame,
                commission,
                stamp_duty,
                slippage,
                split_dates,
                profile=active_profile,
                signal_start_index=signal_start_index,
                sample_min_signal_index=sample_min_signal_index,
                frame=frame,
            )
        finally:
            _core._BACKTEST_ACTIONABLE_SIGNALS = previous_actions

    enriched = _conditional._load_enriched(ticker, source, frame=frame)
    if enriched is None:
        for item in immediate:
            item.setdefault("entry_fill_type", "IMMEDIATE_NEXT_OPEN")
            item.setdefault("entry_fill_delay_days", 1)
            item.setdefault("conditional_fill_version", CONDITIONAL_FILL_VERSION)
        return immediate

    with _conditional._EXECUTION_LOCK:
        conditional = _wait_samples(
            ticker,
            enriched,
            benchmark_frame,
            commission,
            stamp_duty,
            slippage,
            split_dates,
            profile=active_profile,
            signal_start_index=signal_start_index,
            sample_min_signal_index=sample_min_signal_index,
        )

    for item in immediate:
        item.setdefault("entry_fill_type", "IMMEDIATE_NEXT_OPEN")
        item.setdefault("entry_fill_delay_days", 1)
        item.setdefault("conditional_fill_version", CONDITIONAL_FILL_VERSION)
    return _core._merge_backtest_samples(immediate, conditional, enriched)


def install() -> None:
    global _INSTALLED
    if not _INSTALLED:
        _tradefast._date_array = _date_array
        _fast._fast_score_matrix = _fast_score_matrix
        _conditional._backtest_one_ticker = _conditional_backtest_one_ticker
        _conditional.CONDITIONAL_FILL_VERSION = CONDITIONAL_FILL_VERSION
        _INSTALLED = True

    # v80 bundles re-assert worker bindings on repeated install calls. Re-assert
    # only the execution owner here; do not overwrite scoring_consistency's later
    # wrapper around the corrected FAST kernel.
    _sample._backtest_one_ticker = _backtest_one_ticker
    _core._backtest_one_ticker = _backtest_one_ticker
    _core.BACKTEST_VECTORIZATION_VERSION = BACKTEST_VECTORIZATION_VERSION
