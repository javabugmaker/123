"""v96 conditional historical execution for WAIT_PULLBACK.

WAIT_PULLBACK is not a next-session market order. v96 evaluates it as a
point-in-time conditional limit setup:

* lock the EntryZone on the signal date;
* search at most five following trading bars;
* require the fill session to be tradeable and its OHLC range to touch the
  original zone;
* fill at the session open when it opens inside the zone, otherwise at the
  upper zone boundary when price trades down into it;
* invalidate a gap below the zone rather than treating a breakdown as a
  favourable pullback fill;
* anchor costs, exits, drawdown, benchmark and outcome horizons to the actual
  fill date; no touch means no trade sample.

The module is deliberately installable/uninstallable. Production backtest
transactions install it while direct scalar research APIs keep the historical
immediate-signal contract. Spawned workers install it in their isolated process.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import analytics_core as _core
from backtest_alignment import align_benchmark_returns

CONDITIONAL_FILL_VERSION = (
    "2026-08-23-v97-wait-pullback-zone-touch-5d-transaction-v2"
)
WAIT_PULLBACK_VALIDITY_TRADING_DAYS = 5
_IMMEDIATE_SIGNALS = frozenset({"BUY_NOW", "BREAKOUT_CONFIRM"})
_WAIT_ONLY = frozenset({"WAIT_PULLBACK"})

_INSTALLED = False
_ORIGINAL_BACKTEST_ONE_TICKER: Any = None
_EXECUTION_LOCK = threading.RLock()


def _finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return np.nan
    return number if np.isfinite(number) else np.nan


def _score_entry_zone(
    enriched: pd.DataFrame,
    signal_index: int,
    *,
    is_etf: bool,
    score_window: int,
) -> tuple[float, float] | None:
    historical = _core._backtest_scoring_window(
        enriched,
        int(signal_index),
        score_window=max(252, int(score_window)),
        include_volume_profile=False,
    )
    score = _core.score_ticker(historical, is_etf=is_etf)
    entry = _core.entry_point(
        historical,
        breakout=_core._finite_float(
            getattr(score, "breakout_score", np.nan), np.nan
        ),
        volume_score=_core._finite_float(getattr(score, "volume", np.nan), np.nan),
        value_trap_risk_value=_core._finite_float(
            getattr(score, "value_trap_risk", np.nan), np.nan
        ),
        price_decimals=_core.tradable_price_decimals(is_etf),
    )
    if str(entry.get("signal", "AVOID")).upper() != "WAIT_PULLBACK":
        return None
    low = _finite(entry.get("low"))
    high = _finite(entry.get("high"))
    if not np.isfinite(low) or not np.isfinite(high) or low <= 0.0 or high < low:
        return None
    return float(low), float(high)


def _conditional_fill(
    ticker: str,
    enriched: pd.DataFrame,
    signal_index: int,
    zone_low: float,
    zone_high: float,
    *,
    is_etf: bool,
) -> tuple[int, float, int, str] | None:
    """Resolve a conservative daily-bar zone-touch fill.

    OHLC eligibility is prefiltered as NumPy arrays. Only the at-most-five bars
    that can change the order state reach the scalar tradeability resolver.
    """
    start = int(signal_index) + 1
    stop = min(
        len(enriched),
        int(signal_index) + int(WAIT_PULLBACK_VALIDITY_TRADING_DAYS) + 1,
    )
    if start >= stop:
        return None

    window = enriched.iloc[start:stop]
    open_values = pd.to_numeric(window.get("Open"), errors="coerce").to_numpy(
        dtype=float
    )
    high_values = pd.to_numeric(window.get("High"), errors="coerce").to_numpy(
        dtype=float
    )
    low_values = pd.to_numeric(window.get("Low"), errors="coerce").to_numpy(
        dtype=float
    )
    valid = (
        np.isfinite(open_values)
        & np.isfinite(high_values)
        & np.isfinite(low_values)
        & (open_values > 0.0)
        & (high_values > 0.0)
        & (low_values > 0.0)
    )
    gap_below = valid & (open_values < float(zone_low))
    touches = valid & (low_values <= float(zone_high)) & (high_values >= float(zone_low))
    event_offsets = np.flatnonzero(gap_below | touches)

    for offset in event_offsets:
        fill_index = start + int(offset)
        tradeable, _reason = _core.is_entry_tradeable(
            ticker, enriched, fill_index, is_etf=is_etf
        )
        if not tradeable:
            continue

        open_price = float(open_values[offset])
        low_price = float(low_values[offset])
        if bool(gap_below[offset]):
            return None
        if float(zone_low) <= open_price <= float(zone_high):
            fill_price = open_price
            basis = "OPEN_INSIDE_ZONE"
        elif open_price > float(zone_high) and low_price <= float(zone_high):
            fill_price = float(zone_high)
            basis = "LIMIT_AT_ZONE_HIGH"
        else:
            continue
        decimals = _core.tradable_price_decimals(is_etf)
        fill_price = round(float(fill_price), int(decimals))
        if fill_price <= 0.0:
            continue
        return (
            fill_index,
            fill_price,
            int(fill_index - signal_index),
            basis,
        )
    return None


def _rewrite_wait_sample(
    source: dict[str, Any],
    *,
    ticker: str,
    enriched: pd.DataFrame,
    benchmark_frame: pd.DataFrame | None,
    commission: float,
    stamp_duty: float,
    slippage: float,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None],
    profile: Any,
    is_etf: bool,
    date_positions: dict[str, int],
) -> dict[str, Any] | None:
    signal_date_text = str(source.get("signal_date", "") or "")
    signal_index = date_positions.get(signal_date_text)
    if signal_index is None:
        return None

    zone = _score_entry_zone(
        enriched,
        signal_index,
        is_etf=is_etf,
        score_window=int(getattr(profile, "score_window", 504)),
    )
    if zone is None:
        return None
    zone_low, zone_high = zone
    fill = _conditional_fill(
        ticker,
        enriched,
        signal_index,
        zone_low,
        zone_high,
        is_etf=is_etf,
    )
    if fill is None:
        return None
    entry_index, entry_price, fill_delay, fill_basis = fill

    outcome_horizon = max(60, int(_core.BACKTEST_OUTCOME_HORIZON_DAYS))
    if entry_index + outcome_horizon >= len(enriched):
        return None

    closes = pd.to_numeric(enriched.get("Close"), errors="coerce").to_numpy(dtype=float)
    highs = pd.to_numeric(enriched.get("High"), errors="coerce").to_numpy(dtype=float)
    lows = pd.to_numeric(enriched.get("Low"), errors="coerce").to_numpy(dtype=float)
    volumes = pd.to_numeric(enriched.get("Volume"), errors="coerce").to_numpy(dtype=float)

    exit20_index, exit20_delay, exit20_reason = _core.resolve_exit_index(
        ticker,
        enriched,
        entry_index + 20,
        is_etf=is_etf,
        max_delay_days=_core.BACKTEST_MAX_EXIT_DELAY_DAYS,
    )
    exit60_index, exit60_delay, exit60_reason = _core.resolve_exit_index(
        ticker,
        enriched,
        entry_index + outcome_horizon,
        is_etf=is_etf,
        max_delay_days=_core.BACKTEST_MAX_EXIT_DELAY_DAYS,
    )
    if exit20_index is None or exit60_index is None:
        return None
    if (
        not np.isfinite(closes[exit20_index])
        or not np.isfinite(closes[exit60_index])
        or np.any(~np.isfinite(highs[entry_index : exit60_index + 1]))
        or np.any(~np.isfinite(lows[entry_index : exit60_index + 1]))
        or np.any(highs[entry_index : exit60_index + 1] <= 0.0)
        or np.any(lows[entry_index : exit60_index + 1] <= 0.0)
    ):
        return None

    future20 = float(closes[exit20_index])
    future60 = float(closes[exit60_index])
    fee_schedule = _core.BrokerFeeSchedule(
        stock_commission_rate=float(commission)
    )
    cost20 = _core.round_trip_cost_percent(
        is_etf=is_etf,
        entry_price=float(entry_price),
        entry_volume=float(volumes[entry_index]),
        exit_price=future20,
        exit_volume=float(volumes[exit20_index]),
        base_slippage=float(slippage),
        stamp_duty=float(stamp_duty),
        schedule=fee_schedule,
    )
    cost60 = _core.round_trip_cost_percent(
        is_etf=is_etf,
        entry_price=float(entry_price),
        entry_volume=float(volumes[entry_index]),
        exit_price=future60,
        exit_volume=float(volumes[exit60_index]),
        base_slippage=float(slippage),
        stamp_duty=float(stamp_duty),
        schedule=fee_schedule,
    )

    prices20 = np.concatenate(
        ([entry_price], closes[entry_index : exit20_index + 1])
    )
    prices60 = np.concatenate(
        ([entry_price], closes[entry_index : exit60_index + 1])
    )
    lows20 = np.concatenate(([entry_price], lows[entry_index : exit20_index + 1]))
    lows60 = np.concatenate(([entry_price], lows[entry_index : exit60_index + 1]))
    drawdown20 = float(
        ((lows20 / np.maximum.accumulate(prices20) - 1.0).min()) * 100.0
    )
    drawdown60 = float(
        ((lows60 / np.maximum.accumulate(prices60) - 1.0).min()) * 100.0
    )

    entry_date = pd.Timestamp(enriched.index[entry_index])
    exit20_date = pd.Timestamp(enriched.index[exit20_index])
    exit60_date = pd.Timestamp(enriched.index[exit60_index])
    result = dict(source)
    result.update(
        {
            "entry_signal": "WAIT_PULLBACK",
            "entry_date": entry_date.strftime("%Y-%m-%d"),
            "entry_price": float(entry_price),
            "entry_fill_type": "WAIT_PULLBACK_ZONE_TOUCH",
            "entry_fill_basis": fill_basis,
            "entry_fill_delay_days": int(fill_delay),
            "entry_zone_low": float(zone_low),
            "entry_zone_high": float(zone_high),
            "entry_zone_validity_days": int(WAIT_PULLBACK_VALIDITY_TRADING_DAYS),
            "conditional_fill_version": CONDITIONAL_FILL_VERSION,
            "exit20_date": exit20_date.strftime("%Y-%m-%d"),
            "exit60_date": exit60_date.strftime("%Y-%m-%d"),
            "exit20_delay_days": int(exit20_delay),
            "exit60_delay_days": int(exit60_delay),
            "exit20_delay_reason": str(exit20_reason),
            "exit60_delay_reason": str(exit60_reason),
            "round_trip_cost20_pct": round(float(cost20), 6),
            "round_trip_cost60_pct": round(float(cost60), 6),
            "return20": (future20 / entry_price - 1.0) * 100.0,
            "return60": (future60 / entry_price - 1.0) * 100.0,
            "net_return20": (future20 / entry_price - 1.0) * 100.0 - cost20,
            "net_return60": (future60 / entry_price - 1.0) * 100.0 - cost60,
            "drawdown20": drawdown20,
            "drawdown60": drawdown60,
            "split": _core._purged_split_label(
                entry_date, exit60_date, split_dates
            ),
        }
    )
    return align_benchmark_returns([result], benchmark_frame)[0]


def _load_enriched(
    ticker: str,
    source: str,
    frame: pd.DataFrame | None = None,
) -> pd.DataFrame | None:
    market = frame if frame is not None else _core._load_cache(ticker, source)
    if market is None or len(market) < 300:
        return None
    raw_path = _core._cache_path(ticker, source)
    enriched, _hit = _core.load_or_compute_indicators(
        ticker,
        market,
        _core.compute_all_indicators,
        source_path=raw_path if Path(raw_path).exists() else None,
        enabled=_core.INDICATOR_CACHE_ENABLED,
    )
    return enriched if enriched is not None and not enriched.empty else None


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
    """Generate immediate samples plus independently scheduled conditional fills."""
    active_profile = profile or _core._resolve_backtest_profile("exact", 1)
    original = _ORIGINAL_BACKTEST_ONE_TICKER
    if original is None:
        return []

    with _EXECUTION_LOCK:
        previous_signals = _core._BACKTEST_ACTIONABLE_SIGNALS
        try:
            _core._BACKTEST_ACTIONABLE_SIGNALS = _IMMEDIATE_SIGNALS
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
            _core._BACKTEST_ACTIONABLE_SIGNALS = _WAIT_ONLY
            pending = original(
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
            _core._BACKTEST_ACTIONABLE_SIGNALS = previous_signals

    enriched = _load_enriched(ticker, source, frame=frame)
    if enriched is None or not pending:
        for item in immediate:
            item.setdefault("entry_fill_type", "IMMEDIATE_NEXT_OPEN")
            item.setdefault("entry_fill_delay_days", 1)
            item.setdefault("conditional_fill_version", CONDITIONAL_FILL_VERSION)
        return immediate

    is_etf = _core.is_etf_ticker(str(ticker))
    normalized_dates = pd.DatetimeIndex(enriched.index).strftime("%Y-%m-%d")
    date_positions = dict(zip(normalized_dates, range(len(normalized_dates)), strict=False))
    conditional = [
        rewritten
        for source_sample in pending
        if (
            rewritten := _rewrite_wait_sample(
                source_sample,
                ticker=ticker,
                enriched=enriched,
                benchmark_frame=benchmark_frame,
                commission=commission,
                stamp_duty=stamp_duty,
                slippage=slippage,
                split_dates=split_dates,
                profile=active_profile,
                is_etf=is_etf,
                date_positions=date_positions,
            )
        )
        is not None
    ]

    for item in immediate:
        item.setdefault("entry_fill_type", "IMMEDIATE_NEXT_OPEN")
        item.setdefault("entry_fill_delay_days", 1)
        item.setdefault("conditional_fill_version", CONDITIONAL_FILL_VERSION)

    merged = _core._merge_backtest_samples(immediate, conditional, enriched)
    return align_benchmark_returns(merged, benchmark_frame)


def install() -> None:
    """Install conditional execution in the current process."""
    global _INSTALLED, _ORIGINAL_BACKTEST_ONE_TICKER
    with _EXECUTION_LOCK:
        if _INSTALLED:
            return
        _ORIGINAL_BACKTEST_ONE_TICKER = _core._backtest_one_ticker
        _core._backtest_one_ticker = _backtest_one_ticker
        _core.BACKTEST_SIGNAL_EXECUTION_VERSION = CONDITIONAL_FILL_VERSION
        _core.CONDITIONAL_FILL_VERSION = CONDITIONAL_FILL_VERSION
        _core.WAIT_PULLBACK_VALIDITY_TRADING_DAYS = WAIT_PULLBACK_VALIDITY_TRADING_DAYS
        _INSTALLED = True


def uninstall() -> None:
    """Restore the immediate-only scalar executor after a parent transaction."""
    global _INSTALLED, _ORIGINAL_BACKTEST_ONE_TICKER
    with _EXECUTION_LOCK:
        if not _INSTALLED:
            return
        if _core._backtest_one_ticker is _backtest_one_ticker:
            _core._backtest_one_ticker = _ORIGINAL_BACKTEST_ONE_TICKER
        _ORIGINAL_BACKTEST_ONE_TICKER = None
        _INSTALLED = False
