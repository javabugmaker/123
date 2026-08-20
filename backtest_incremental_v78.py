"""v78 cache-aware incremental backtest recomputation.

When the market cache only appends new daily bars, historical samples whose full
60-day outcome plus maximum exit-delay window already existed in the previous
run cannot change. The stable implementation nevertheless discarded/recomputed
a fixed 300-360 bar tail for every ticker.

Anchor recomputation to the *previous cached row count* instead. We rewind far
enough to cover outcome maturity and delayed exits, then add the existing
cooldown/candidate warmup before signal evaluation. Daily runs therefore score
roughly the last ~130 bars rather than ~420, while a long gap automatically
includes every newly appended bar.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

import analytics_core as _core

_INSTALLED = False


def _maturity_rewind_bars() -> int:
    horizon = max(60, int(_core.BACKTEST_OUTCOME_HORIZON_DAYS))
    exit_delay = max(0, int(_core.BACKTEST_MAX_EXIT_DELAY_DAYS))
    # +2 covers the T+1 entry offset and strict ``>= len(frame)`` maturity gate.
    return horizon + exit_delay + 2


def _backtest_one_ticker_cached(
    ticker: str,
    source: str,
    benchmark_frame: pd.DataFrame | None,
    commission: float,
    stamp_duty: float,
    slippage: float,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None],
    benchmark_signature: str = "",
    *,
    profile: Any | None = None,
    benchmark_name: str = "沪深300",
) -> tuple[list[dict[str, Any]], bool]:
    del benchmark_signature
    frame = _core._load_cache(ticker, source)
    if frame is None or len(frame) < 300:
        return (
            _core._backtest_one_ticker(
                ticker,
                source,
                benchmark_frame,
                commission,
                stamp_duty,
                slippage,
                split_dates,
            ),
            False,
        )

    active_profile = profile or _core._resolve_backtest_profile("exact", 1)
    cache_key = _core.backtest_cache_key(
        {
            "ticker": str(ticker),
            "source": str(source),
            "benchmark": str(benchmark_name),
            "commission": float(commission),
            "etf_commission": float(_core.BACKTEST_ETF_COMMISSION_RATE),
            "stamp_duty": float(stamp_duty),
            "slippage": float(slippage),
            "assumed_trade_notional": float(_core.BACKTEST_ASSUMED_TRADE_NOTIONAL),
            "execution_model": "asset_fees_liquidity_t1_limit_exit_v1",
            "max_exit_delay_days": int(_core.BACKTEST_MAX_EXIT_DELAY_DAYS),
            "cooldown": int(active_profile.cooldown),
            "horizon": int(_core.BACKTEST_OUTCOME_HORIZON_DAYS),
            "score_window": int(active_profile.score_window),
            "mode": active_profile.name,
            "historical_volume_profile": bool(active_profile.historical_volume_profile),
            "candidate_gap": int(active_profile.candidate_gap),
            "fast_prefilter": bool(active_profile.fast_prefilter),
            "model_weight_signature": _core.model_weight_signature(),
        }
    )
    current_market = _core.market_cache_state(frame)
    current_benchmark = (
        _core.market_cache_state(benchmark_frame)
        if benchmark_frame is not None and not benchmark_frame.empty
        else {}
    )
    cached_payload = (
        _core.load_backtest_cache_state(ticker, cache_key)
        if _core.BACKTEST_CACHE_ENABLED
        else None
    )
    if cached_payload is not None:
        cached_samples = list(cached_payload.get("samples", []))
        raw_state = cached_payload.get("state", {})
        cached_state = raw_state if isinstance(raw_state, dict) else {}
        raw_market = cached_state.get("market", {})
        raw_benchmark = cached_state.get("benchmark", {})
        cached_market = raw_market if isinstance(raw_market, dict) else {}
        cached_benchmark = raw_benchmark if isinstance(raw_benchmark, dict) else {}
        market_ok = _core.market_prefix_matches(frame, cached_market)
        benchmark_ok = bool(
            not cached_benchmark
            or (
                benchmark_frame is not None
                and not benchmark_frame.empty
                and _core.market_prefix_matches(benchmark_frame, cached_benchmark)
            )
        )
        if market_ok and benchmark_ok:
            old_rows = max(0, int(cached_market.get("rows", 0) or 0))
            old_last = str(cached_market.get("last", ""))
            same_market = old_rows == len(frame) and old_last == str(
                current_market.get("last", "")
            )
            if same_market:
                return _core._relabel_sample_splits(cached_samples, split_dates), True

            # Recompute from the oldest signal whose outcome could have changed
            # after the previous cache ended. If many bars were appended, using
            # old_rows automatically covers the complete appended interval.
            bounded_old_rows = min(max(old_rows, 252), len(frame))
            cutoff_index = max(251, bounded_old_rows - _maturity_rewind_bars())
            warmup = max(
                251,
                cutoff_index
                - max(
                    int(active_profile.cooldown),
                    int(_core.BACKTEST_OUTCOME_HORIZON_DAYS),
                    int(active_profile.candidate_gap),
                ),
            )
            cutoff_date = pd.Timestamp(frame.index[cutoff_index])
            retained = [
                dict(item)
                for item in cached_samples
                if pd.Timestamp(item.get("signal_date")) < cutoff_date
            ]
            tail_samples = _core._backtest_one_ticker(
                ticker,
                source,
                benchmark_frame,
                commission,
                stamp_duty,
                slippage,
                split_dates,
                profile=active_profile,
                signal_start_index=warmup,
                sample_min_signal_index=cutoff_index,
                frame=frame,
            )
            samples = _core._merge_backtest_samples(retained, tail_samples, frame)
            samples = _core._relabel_sample_splits(samples, split_dates)
            if _core.BACKTEST_CACHE_ENABLED:
                _core.save_backtest_cache(
                    ticker,
                    cache_key,
                    samples,
                    state={"market": current_market, "benchmark": current_benchmark},
                )
            context = getattr(_core, "_BACKTEST_WORKER_CONTEXT", None)
            if isinstance(context, dict):
                context["_v78_last_recompute_bars"] = max(0, len(frame) - warmup)
            return samples, True

    samples = _core._backtest_one_ticker(
        ticker,
        source,
        benchmark_frame,
        commission,
        stamp_duty,
        slippage,
        split_dates,
        profile=active_profile,
        frame=frame,
    )
    if _core.BACKTEST_CACHE_ENABLED:
        _core.save_backtest_cache(
            ticker,
            cache_key,
            samples,
            state={"market": current_market, "benchmark": current_benchmark},
        )
    return samples, False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _core._backtest_one_ticker_cached = _backtest_one_ticker_cached
    _INSTALLED = True


install()
