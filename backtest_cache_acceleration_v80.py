"""v80 warm-cache validation acceleration without weakening history integrity.

v69 requires a deterministic full-history OHLCV fingerprint before a backtest
cache can be reused. The v78 incremental wrapper computed the current full
fingerprint, then called market_prefix_matches which hashed the same full
history again for an unchanged ticker. v80 compares the already-computed full
fingerprints directly when row count/date bounds match, while append/revision
paths retain the exact prefix verification and maturity rewind contract.

FAST samples are namespaced once for the v80 whole-ticker scorer because v80
also fixes the ETF value-trap quick-gate semantics. EXACT caches keep their
existing identity.

v97 keeps the production profile-aware executor but detects old positional-only
research/test integrations at the actual cache-kernel call boundary. This makes
compatibility independent of whichever facade currently owns the public cached
function.
"""

from __future__ import annotations

import inspect
from typing import Any

import pandas as pd

import analytics_core as _core

_INSTALLED = False
_FAST_SCORING_ENGINE = "v80-whole-ticker-exact-equivalent-v1"


def _maturity_rewind_bars() -> int:
    horizon = max(60, int(_core.BACKTEST_OUTCOME_HORIZON_DAYS))
    exit_delay = max(0, int(_core.BACKTEST_MAX_EXIT_DELAY_DAYS))
    return horizon + exit_delay + 2


def _same_state_by_full_fingerprint(
    current: dict[str, Any],
    cached: dict[str, Any],
) -> bool | None:
    """Return exact same-state result, or None when legacy state needs fallback."""
    try:
        current_rows = int(current.get("rows", 0) or 0)
        cached_rows = int(cached.get("rows", 0) or 0)
    except (TypeError, ValueError):
        return False
    if current_rows != cached_rows:
        return False
    if str(current.get("first", "") or "") != str(cached.get("first", "") or ""):
        return False
    if str(current.get("last", "") or "") != str(cached.get("last", "") or ""):
        return False
    expected = str(cached.get("history_fingerprint", "") or "").strip()
    observed = str(current.get("history_fingerprint", "") or "").strip()
    if not expected or not observed:
        return None
    return bool(expected == observed)


def _state_prefix_ok(
    frame: pd.DataFrame | None,
    current: dict[str, Any],
    cached: dict[str, Any],
) -> tuple[bool, bool]:
    """Return (valid-prefix, exact-same-state) with one full hash on hot hits."""
    if not cached:
        return True, False
    if frame is None or frame.empty:
        return False, False
    same = _same_state_by_full_fingerprint(current, cached)
    if same is True:
        return True, True
    if same is None:
        matched = bool(_core.market_prefix_matches(frame, cached))
        exact = matched and int(cached.get("rows", 0) or 0) == len(frame) and str(
            cached.get("last", "") or ""
        ) == str(current.get("last", "") or "")
        return matched, exact
    # Row/date bounds differ: this may be a valid append. Full prefix checking
    # remains mandatory and catches any historical provider revision.
    return bool(_core.market_prefix_matches(frame, cached)), False


def _cache_identity(
    ticker: str,
    source: str,
    benchmark_name: str,
    commission: float,
    stamp_duty: float,
    slippage: float,
    active_profile: Any,
) -> dict[str, Any]:
    identity: dict[str, Any] = {
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
    if bool(active_profile.fast_prefilter):
        identity["fast_scoring_engine"] = _FAST_SCORING_ENGINE
    return identity


def _supports_profile_contract(callable_obj: Any) -> bool:
    """Return whether an executor accepts the modern keyword profile contract."""
    probe = getattr(callable_obj, "side_effect", None)
    if callable(probe):
        callable_obj = probe
    try:
        parameters = inspect.signature(callable_obj).parameters.values()
    except (TypeError, ValueError):
        return True
    return any(
        parameter.name == "profile"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _invoke_backtest_one_ticker(
    ticker: str,
    source: str,
    benchmark_frame: pd.DataFrame | None,
    commission: float,
    stamp_duty: float,
    slippage: float,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None],
    *,
    active_profile: Any | None = None,
    signal_start_index: int | None = None,
    sample_min_signal_index: int | None = None,
    frame: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    """Invoke modern production executors without breaking legacy positional hooks."""
    executor = _core._backtest_one_ticker
    if not _supports_profile_contract(executor):
        return executor(
            ticker,
            source,
            benchmark_frame,
            commission,
            stamp_duty,
            slippage,
            split_dates,
        )
    return executor(
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
            _invoke_backtest_one_ticker(
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
        _cache_identity(
            ticker,
            source,
            benchmark_name,
            commission,
            stamp_duty,
            slippage,
            active_profile,
        )
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

        market_ok, same_market = _state_prefix_ok(frame, current_market, cached_market)
        benchmark_ok, _same_benchmark = _state_prefix_ok(
            benchmark_frame,
            current_benchmark,
            cached_benchmark,
        )
        if market_ok and benchmark_ok:
            if same_market:
                return _core._relabel_sample_splits(cached_samples, split_dates), True

            old_rows = max(0, int(cached_market.get("rows", 0) or 0))
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
            tail_samples = _invoke_backtest_one_ticker(
                ticker,
                source,
                benchmark_frame,
                commission,
                stamp_duty,
                slippage,
                split_dates,
                active_profile=active_profile,
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
                context["_v80_last_recompute_bars"] = max(0, len(frame) - warmup)
            return samples, True

    samples = _invoke_backtest_one_ticker(
        ticker,
        source,
        benchmark_frame,
        commission,
        stamp_duty,
        slippage,
        split_dates,
        active_profile=active_profile,
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
    _core._backtest_one_ticker_cached = _backtest_one_ticker_cached
    _INSTALLED = True


install()
