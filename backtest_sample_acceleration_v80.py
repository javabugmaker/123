"""v80 historical-sample execution acceleration.

Signal generation/scoring remains owned by the stable analytics engine. Once
signal endpoints are known, the legacy backtest repeatedly rebuilt fee objects,
scanned pandas benchmark history, sliced tradeability rows and allocated
concatenated drawdown arrays for every sample. v80 keeps the exact execution
contract but precomputes ticker/benchmark arrays once and makes the sample loop
mostly O(1) indexing plus short drawdown reductions.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import analytics_core as _core
import execution_costs as _costs

_LEGACY_BACKTEST_ONE_TICKER = _core._backtest_one_ticker
_INSTALLED = False


def _numeric(frame: pd.DataFrame, column: str, *, fallback: np.ndarray | None = None) -> np.ndarray:
    if column not in frame.columns:
        if fallback is not None:
            return fallback.copy()
        return np.full(len(frame), np.nan, dtype=np.float64)
    return pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=np.float64)


def _liquidity_slippage_vector(
    price: np.ndarray,
    volume: np.ndarray,
    *,
    base_slippage: float,
    notional: float,
) -> np.ndarray:
    base = max(0.0, float(base_slippage))
    result = np.full(len(price), base, dtype=np.float64)
    valid = (
        np.isfinite(price)
        & np.isfinite(volume)
        & (price > 0.0)
        & (volume > 0.0)
        & np.isfinite(notional)
        & (notional > 0.0)
    )
    if not valid.any():
        return result
    traded_value = price[valid] * volume[valid]
    participation = np.clip(float(notional) / traded_value, 0.0, 1.0)
    impact = float(_costs.BACKTEST_LIQUIDITY_IMPACT_AT_ONE_PERCENT) * np.sqrt(
        participation / 0.01
    )
    result[valid] += np.clip(
        impact,
        0.0,
        float(_costs.BACKTEST_MAX_LIQUIDITY_SLIPPAGE),
    )
    return result


def _benchmark_regime_by_stock_row(
    benchmark_frame: pd.DataFrame | None,
    stock_index: pd.Index,
) -> np.ndarray:
    output = np.full(len(stock_index), "UNKNOWN", dtype=object)
    if benchmark_frame is None or benchmark_frame.empty or "Close" not in benchmark_frame:
        return output
    benchmark = pd.DataFrame(
        {
            "Close": pd.to_numeric(benchmark_frame["Close"], errors="coerce"),
        },
        index=pd.to_datetime(benchmark_frame.index, errors="coerce"),
    )
    benchmark = benchmark.loc[~benchmark.index.isna()].sort_index()
    benchmark = benchmark.loc[np.isfinite(benchmark["Close"].to_numpy(dtype=np.float64))]
    if benchmark.empty:
        return output
    # Keep the last observation on duplicate dates, matching .loc[:date].iloc[-1].
    benchmark = benchmark.loc[~benchmark.index.duplicated(keep="last")]
    values = benchmark["Close"].to_numpy(dtype=np.float64)
    dates = pd.DatetimeIndex(benchmark.index).to_numpy(dtype="datetime64[ns]")
    n = len(values)
    regime = np.full(n, "UNKNOWN", dtype=object)
    if n >= 60:
        series = pd.Series(values)
        ma60 = series.rolling(60, min_periods=60).mean().to_numpy(dtype=np.float64)
        ma200 = series.rolling(200, min_periods=200).mean().to_numpy(dtype=np.float64)
        ma200 = np.where(np.isfinite(ma200), ma200, ma60)
        ret60 = np.zeros(n, dtype=np.float64)
        valid_ret = np.arange(n) >= 60
        prior = np.full(n, np.nan, dtype=np.float64)
        prior[60:] = values[:-60]
        valid_prior = valid_ret & np.isfinite(prior) & (prior > 0.0)
        ret60[valid_prior] = (values[valid_prior] / prior[valid_prior] - 1.0) * 100.0
        risk_on = (
            np.isfinite(ma60)
            & np.isfinite(ma200)
            & (values >= ma60)
            & (values >= ma200)
            & (ret60 > 3.0)
        )
        risk_off = (
            np.isfinite(ma60)
            & np.isfinite(ma200)
            & (values < ma60)
            & (values < ma200)
            & (ret60 < -3.0)
        )
        mature = np.arange(n) >= 59
        regime[mature] = "NEUTRAL"
        regime[risk_on] = "RISK_ON"
        regime[risk_off] = "RISK_OFF"

    stock_dates = pd.to_datetime(stock_index, errors="coerce")
    stock_values = pd.DatetimeIndex(stock_dates).to_numpy(dtype="datetime64[ns]")
    valid_stock = ~np.isnat(stock_values)
    positions = np.searchsorted(dates, stock_values[valid_stock], side="right") - 1
    mapped = np.full(np.count_nonzero(valid_stock), "UNKNOWN", dtype=object)
    usable = positions >= 0
    if usable.any():
        mapped[usable] = regime[positions[usable]]
    output[valid_stock] = mapped
    return output


def _window_has_bad(prefix: np.ndarray, start: int, end: int) -> bool:
    if start < 0 or end < start or end + 1 >= len(prefix):
        return True
    return bool(prefix[end + 1] - prefix[start] > 0)


def _drawdown_percent(
    entry_price: float,
    closes: np.ndarray,
    lows: np.ndarray,
    start: int,
    end: int,
) -> float:
    close_slice = closes[start : end + 1]
    low_slice = lows[start : end + 1]
    if close_slice.size == 0:
        return 0.0
    running_peak = np.maximum.accumulate(close_slice)
    running_peak = np.maximum(running_peak, float(entry_price))
    ratios = low_slice / running_peak - 1.0
    minimum = float(np.min(ratios)) if ratios.size else 0.0
    return float(min(0.0, minimum) * 100.0)


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
    if frame is None:
        frame = _core._load_cache(ticker, source)
    if frame is None or len(frame) < 300:
        return []
    raw_path = _core._cache_path(ticker, source)
    enriched, _indicator_cache_hit = _core.load_or_compute_indicators(
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

    attached_evaluations = getattr(signal_points, "evaluations", None)
    component_map = dict(getattr(signal_points, "components", {}) or {})
    if attached_evaluations is not None:
        evaluation_map = {
            int(index): (float(score), str(signal))
            for index, score, signal in attached_evaluations
        }
    else:
        evaluation_map: dict[int, tuple[float, str]] = {}
        for raw_index in signal_points:
            index = int(raw_index)
            historical = _core._backtest_scoring_window(enriched, index)
            historical_score = _core.score_ticker(historical, is_etf=is_etf)
            final_score = _core._finite_float(
                getattr(historical_score, "final_score", np.nan), np.nan
            )
            if not np.isfinite(final_score):
                final_score = _core._finite_float(
                    getattr(historical_score, "total", np.nan), 0.0
                )
            evaluation_map[index] = (
                float(final_score),
                _core._historical_entry_signal(
                    historical, historical_score, is_etf=is_etf
                ),
            )
            component_map[index] = (
                _core._finite_float(getattr(historical_score, "base_score", np.nan), 0.0),
                _core._finite_float(getattr(historical_score, "trigger_score", np.nan), 0.0),
                _core._finite_float(
                    getattr(historical_score, "execution_score", np.nan),
                    _core._finite_float(getattr(historical_score, "entry_score", np.nan), 0.0),
                ),
            )

    closes = _numeric(enriched, "Close")
    opens = _numeric(enriched, "Open")
    lows = _numeric(enriched, "Low")
    highs = _numeric(enriched, "High", fallback=closes)
    volumes = _numeric(enriched, "Volume")
    outcome_horizon = max(60, int(_core.BACKTEST_OUTCOME_HORIZON_DAYS))
    minimum_sample_index = (
        int(sample_min_signal_index) if sample_min_signal_index is not None else 0
    )

    bad_high_low = (
        ~np.isfinite(highs)
        | ~np.isfinite(lows)
        | (highs <= 0.0)
        | (lows <= 0.0)
    ).astype(np.int32)
    bad_prefix = np.empty(len(enriched) + 1, dtype=np.int64)
    bad_prefix[0] = 0
    np.cumsum(bad_high_low, out=bad_prefix[1:])

    valid_points: list[int] = []
    for raw_index in signal_points:
        index = int(raw_index)
        if index < minimum_sample_index:
            continue
        entry_index = index + 1
        if entry_index >= len(enriched):
            continue
        if not np.isfinite(opens[entry_index]) or opens[entry_index] <= 0.0:
            continue
        tradeable, _tradeability_reason = _core.is_entry_tradeable(
            ticker, enriched, entry_index, is_etf=is_etf
        )
        if not tradeable:
            continue
        final_index = entry_index + outcome_horizon
        if (
            final_index >= len(enriched)
            or not np.isfinite(closes[entry_index + 20])
            or not np.isfinite(closes[final_index])
            or _window_has_bad(bad_prefix, entry_index, final_index)
        ):
            continue
        valid_points.append(index)
    if not valid_points:
        return []

    regime_by_row = _benchmark_regime_by_stock_row(benchmark_frame, enriched.index)
    fee_schedule = _costs.BrokerFeeSchedule(stock_commission_rate=float(commission))
    notional = float(fee_schedule.assumed_trade_notional)
    commission_rate = _costs.effective_commission_rate(
        is_etf=is_etf,
        schedule=fee_schedule,
        notional=notional,
    )
    entry_slippage = _liquidity_slippage_vector(
        opens,
        volumes,
        base_slippage=slippage,
        notional=notional,
    )
    exit_slippage = _liquidity_slippage_vector(
        closes,
        volumes,
        base_slippage=slippage,
        notional=notional,
    )
    statutory = 0.0 if is_etf else max(0.0, float(stamp_duty))

    validation_end, test_start = split_dates
    samples: list[dict[str, Any]] = []
    previous_sample_index: int | None = None
    for index in valid_points:
        signal_date = pd.Timestamp(enriched.index[index])
        historical_eligible, historical_reason = _core.point_in_time_eligibility(
            ticker, signal_date
        )
        if historical_eligible is False:
            continue
        entry_index = index + 1
        entry_date = pd.Timestamp(enriched.index[entry_index])
        entry_price = float(opens[entry_index])
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
            continue
        future20 = float(closes[exit20_index])
        future60 = float(closes[exit60_index])

        cost20_percent = (
            commission_rate * 2.0
            + float(entry_slippage[entry_index])
            + float(exit_slippage[exit20_index])
            + statutory
        ) * 100.0
        cost60_percent = (
            commission_rate * 2.0
            + float(entry_slippage[entry_index])
            + float(exit_slippage[exit60_index])
            + statutory
        ) * 100.0
        drawdown20 = _drawdown_percent(
            entry_price, closes, lows, entry_index, exit20_index
        )
        drawdown60 = _drawdown_percent(
            entry_price, closes, lows, entry_index, exit60_index
        )

        exit60_date = pd.Timestamp(enriched.index[exit60_index])
        split = _core._purged_split_label(
            entry_date,
            exit60_date,
            (validation_end, test_start),
        )
        spacing = (
            outcome_horizon
            if previous_sample_index is None
            else max(1, index - previous_sample_index)
        )
        sample_weight = min(1.0, spacing / float(outcome_horizon))
        historical_score, historical_signal = evaluation_map[index]
        setup_component, trigger_component, execution_component = component_map.get(
            index, (historical_score, 0.0, 0.0)
        )
        samples.append(
            {
                "ticker": ticker,
                "asset_type": "etf" if is_etf else "stock",
                "entry_signal": historical_signal,
                "market_regime": str(regime_by_row[index]),
                "universe_snapshot_status": (
                    "ELIGIBLE" if historical_eligible is True else "UNAVAILABLE"
                ),
                "universe_snapshot_reason": str(historical_reason),
                "signal_date": signal_date.strftime("%Y-%m-%d"),
                "entry_date": entry_date.strftime("%Y-%m-%d"),
                "entry_price": entry_price,
                "exit20_date": pd.Timestamp(enriched.index[exit20_index]).strftime("%Y-%m-%d"),
                "exit60_date": exit60_date.strftime("%Y-%m-%d"),
                "exit20_delay_days": int(exit20_delay),
                "exit60_delay_days": int(exit60_delay),
                "exit20_delay_reason": str(exit20_reason),
                "exit60_delay_reason": str(exit60_reason),
                "round_trip_cost20_pct": round(float(cost20_percent), 6),
                "round_trip_cost60_pct": round(float(cost60_percent), 6),
                "return20": (future20 / entry_price - 1.0) * 100.0,
                "return60": (future60 / entry_price - 1.0) * 100.0,
                # v51 benchmark-open alignment wrapper fills these immediately
                # after this function returns; placeholders avoid duplicate
                # close-basis asof/loc work that is never published.
                "benchmark_return20": np.nan,
                "benchmark_return60": np.nan,
                "net_return20": (future20 / entry_price - 1.0) * 100.0 - cost20_percent,
                "net_return60": (future60 / entry_price - 1.0) * 100.0 - cost60_percent,
                "drawdown20": drawdown20,
                "drawdown60": drawdown60,
                "score": historical_score,
                "setup_score": float(setup_component),
                "trigger_score": float(trigger_component),
                "execution_score": float(execution_component),
                "split": split,
                "sample_weight": round(sample_weight, 4),
            }
        )
        previous_sample_index = index
    return samples


def install() -> None:
    global _INSTALLED
    _core._backtest_one_ticker = _backtest_one_ticker
    _INSTALLED = True


install()
