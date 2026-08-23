"""v90 analytics facade with executable-signal backtest semantics.

All v89 point-in-time, publication and ranking-integrity semantics remain intact.
A ``WAIT_PULLBACK`` row is a pending conditional order, not an instruction to
buy the next session open. Until the historical engine models zone-touch fills,
only immediately executable ``BUY_NOW`` and ``BREAKOUT_CONFIRM`` states may
create return samples used to calibrate the live ranking.

v90 additionally attaches an *independent* five-factor technical-resonance
snapshot (MACD/KDJ/RSI/OBV/BOLL) to each historical signal date. The resonance
layer is diagnostic only: it does not change entry eligibility, component
weights, FinalScore, RankingScore, or calibration. This lets repeated backtests
measure whether 4/5 confirmation and rising vote counts add genuine OOS value
before any production gate is changed.
"""

from __future__ import annotations

import shutil
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import analytics_acceleration_v77 as _analytics_acceleration
import analytics_core as _core
import backtest_acceleration_v77 as _backtest_acceleration
import backtest_fastpath_v78 as _backtest_fastpath
import cache_acceleration_v77 as _cache_acceleration
import calibration_weight_cache_v79 as _calibration_weight_cache
import indicator_acceleration_v77 as _indicator_acceleration
import score_acceleration_v79 as _score_acceleration_v79
import universe_cache_acceleration_v78 as _universe_cache_acceleration
from analytics_core import *  # noqa: F403
from backtest_alignment import install_analytics_alignment
from backtest_rank_integrity_v82 import (
    BACKTEST_RECENCY_NORMALIZATION_VERSION,
    install_single_recency_ranking_guard,
    single_recency_ranking_context,
)
from model_audit import run_audit
from technical_resonance_v90 import (
    RESONANCE_VERSION,
    attach_resonance_to_samples,
    summarize_resonance_samples,
)

_indicator_acceleration.install()
_cache_acceleration.install()
_universe_cache_acceleration.install()
_backtest_acceleration.install()
_analytics_acceleration.install()
# analytics_acceleration_v77 installs its older score kernels; re-assert v79
# afterwards so every spawned worker runs the newest exact-formula fast path.
_score_acceleration_v79.install()
_calibration_weight_cache.install()
_backtest_fastpath.install()
install_analytics_alignment(_core)
install_single_recency_ranking_guard(_core)

# A WAIT_PULLBACK signal means "place no trade until price returns to the entry
# zone". The stable historical engine currently executes accepted signals at
# the next session open, so admitting WAIT_PULLBACK would manufacture fills that
# the live policy explicitly told the user not to take. Fail closed until a
# point-in-time zone-touch fill engine exists. Both the exact evaluator and the
# vectorised FAST prefilter consult this canonical runtime set before a sample is
# emitted, preserving one execution meaning across modes.
_BACKTEST_EXECUTABLE_SIGNALS = frozenset({"BUY_NOW", "BREAKOUT_CONFIRM"})
_core._BACKTEST_ACTIONABLE_SIGNALS = _BACKTEST_EXECUTABLE_SIGNALS

# signal_lifecycle_v51 intentionally aliases its module entry to the stable
# lifecycle core. Preserve the historical private reference for callers that
# imported it before later lifecycle facades were installed; this is an API
# compatibility alias only and does not introduce another ranking pass.
_lifecycle_v51_compat = sys.modules.get("signal_lifecycle_v51")
if _lifecycle_v51_compat is not None and not hasattr(
    _lifecycle_v51_compat, "_legacy_finalize_signal_ranking"
):
    setattr(
        _lifecycle_v51_compat,
        "_legacy_finalize_signal_ranking",
        getattr(_lifecycle_v51_compat, "finalize_signal_ranking"),
    )

_LEGACY_APPLY_BACKTEST_PROVENANCE = _core._apply_backtest_provenance
_LEGACY_CALIBRATION_STABILITY_STATS = _core.calibration_stability_stats
_LEGACY_BACKTEST_ONE_TICKER_CACHED = _core._backtest_one_ticker_cached
_LEGACY_TICKER_BACKTEST_ROWS = _core._ticker_backtest_rows
_LEGACY_INIT_BACKTEST_WORKER = _core._init_backtest_worker
_LEGACY_SUMMARY_TO_DICT = _core.BacktestSummary.to_dict
_LEGACY_APPLY_BACKTEST_RANKING = _core.apply_backtest_ranking
_core._legacy_apply_backtest_ranking = _LEGACY_APPLY_BACKTEST_RANKING
_BACKTEST_PUBLICATION_LOCK = threading.Lock()
_RESONANCE_ANALYSIS_LOCK = threading.Lock()
_LAST_RESONANCE_ANALYSIS: dict[str, Any] = {
    "version": RESONANCE_VERSION,
    "status": "NOT_EVALUATED",
    "by_count": [],
    "by_band": [],
    "by_transition": [],
}


def _load_benchmark_frames(source: str) -> dict[str, pd.DataFrame]:
    """Refresh each benchmark first; use cache only as a resilient fallback."""
    frames: dict[str, pd.DataFrame] = {}
    for name, ticker in _core.BENCHMARKS.items():
        frame: pd.DataFrame | None = None
        try:
            frame = _core.download_ticker(ticker, source=source)
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            _core.logger.warning(
                "刷新基准 %s (%s) 失败，尝试使用现有缓存: %s",
                name,
                ticker,
                exc,
            )

        if frame is None or frame.empty:
            frame = _core._load_cache(ticker, source)
            if frame is not None and not frame.empty:
                _core.logger.warning(
                    "基准 %s (%s) 使用缓存回退，回测时效字段将继续审计其截止日。",
                    name,
                    ticker,
                )

        if frame is not None and not frame.empty:
            frames[name] = frame
        else:
            _core.logger.warning("无法加载基准 %s (%s)", name, ticker)
    return frames


def _attach_backtest_resonance(
    ticker: str,
    source: str,
    samples: list[dict[str, object]],
    frame: pd.DataFrame | None = None,
) -> list[dict[str, object]]:
    """Attach five-factor state at signal close without changing the signal."""
    if not samples:
        return samples
    market_frame = frame
    if market_frame is None or market_frame.empty:
        market_frame = _core._load_cache(ticker, source)
    try:
        return attach_resonance_to_samples(samples, market_frame)
    except (ArithmeticError, KeyError, TypeError, ValueError) as exc:
        _core.logger.warning(
            "Five-factor resonance diagnostics unavailable for %s: %s",
            ticker,
            exc,
        )
        return samples


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
    profile: _core.BacktestExecutionProfile | None = None,
    benchmark_name: str = "沪深300",
) -> tuple[list[dict[str, object]], bool]:
    """Never reuse excess-return cache when the benchmark is absent now.

    v90 also recomputes resonance from the current OHLCV cache even when the
    historical return sample itself is a cache hit. Old caches therefore do not
    need to be invalidated just to add diagnostic columns.
    """
    if benchmark_frame is not None and not benchmark_frame.empty:
        samples, cache_hit = _LEGACY_BACKTEST_ONE_TICKER_CACHED(
            ticker,
            source,
            benchmark_frame,
            commission,
            stamp_duty,
            slippage,
            split_dates,
            benchmark_signature,
            profile=profile,
            benchmark_name=benchmark_name,
        )
        return _attach_backtest_resonance(ticker, source, samples), cache_hit

    frame = _core._load_cache(ticker, source)
    active_profile = profile or _core._resolve_backtest_profile("exact", 1)
    _core.logger.warning(
        "回测基准 %s 当前不可用：%s 不复用历史 benchmark cache，"
        "本轮样本的基准收益将保持缺失并由测试集完整性门槛处理。",
        benchmark_name,
        ticker,
    )
    samples = _core._backtest_one_ticker(
        ticker,
        source,
        None,
        commission,
        stamp_duty,
        slippage,
        split_dates,
        profile=active_profile,
        frame=frame,
    )
    return _attach_backtest_resonance(ticker, source, samples, frame=frame), False


def _ticker_backtest_rows(
    sample_frame: pd.DataFrame, objective: str = "net_excess_return_20d"
) -> list[dict[str, Any]]:
    """Preserve production calibration and append resonance diagnostics only."""
    rows = _LEGACY_TICKER_BACKTEST_ROWS(sample_frame, objective)
    analysis = summarize_resonance_samples(sample_frame)
    with _RESONANCE_ANALYSIS_LOCK:
        global _LAST_RESONANCE_ANALYSIS
        _LAST_RESONANCE_ANALYSIS = analysis

    if not rows or "resonance_count" not in sample_frame:
        return rows

    working = sample_frame.copy()
    working["entry_signal"] = (
        working.get("entry_signal", pd.Series("UNKNOWN", index=working.index))
        .fillna("UNKNOWN")
        .astype(str)
        .str.upper()
    )
    working["resonance_count"] = pd.to_numeric(
        working["resonance_count"], errors="coerce"
    )
    delta3 = pd.to_numeric(
        working.get("resonance_delta_3d", pd.Series(np.nan, index=working.index)),
        errors="coerce",
    )
    working["_resonance_rising"] = delta3.gt(0.0)
    lookup: dict[tuple[str, str], dict[str, float]] = {}
    for (ticker, signal), group in working.groupby(
        ["ticker", "entry_signal"], sort=False
    ):
        valid = group["resonance_count"].dropna()
        if valid.empty:
            continue
        lookup[(str(ticker), str(signal))] = {
            "resonance_mean_count": round(float(valid.mean()), 4),
            "resonance_strong_bull_share": round(
                float(valid.ge(4.0).mean()), 4
            ),
            "resonance_rising_share": round(
                float(group.loc[valid.index, "_resonance_rising"].mean()), 4
            ),
        }
    for row in rows:
        key = (
            str(row.get("ticker", "")),
            str(row.get("entry_signal", "UNKNOWN")).upper(),
        )
        row.update(lookup.get(key, {}))
    return rows


def _backtest_summary_to_dict(summary: _core.BacktestSummary) -> dict[str, Any]:
    """Expose the held-out resonance experiment in BacktestSummary.json."""
    result = _LEGACY_SUMMARY_TO_DICT(summary)
    with _RESONANCE_ANALYSIS_LOCK:
        result["resonance_analysis"] = dict(_LAST_RESONANCE_ANALYSIS)
    return result


def _init_backtest_worker(
    source: str,
    benchmark: str,
    commission: float,
    stamp_duty: float,
    slippage: float,
    split_dates: tuple[pd.Timestamp | None, pd.Timestamp | None],
    benchmark_signature: str,
    profile: _core.BacktestExecutionProfile,
) -> None:
    """Initialize normal worker context and re-assert v90 facade hooks.

    Windows uses spawned worker processes. Making this facade itself the pool
    initializer ensures each child imports ``analytics`` and therefore sees the
    same resonance attachment policy as the parent process.
    """
    _LEGACY_INIT_BACKTEST_WORKER(
        source,
        benchmark,
        commission,
        stamp_duty,
        slippage,
        split_dates,
        benchmark_signature,
        profile,
    )
    _core._backtest_one_ticker_cached = _backtest_one_ticker_cached


def _apply_backtest_provenance(
    frame: pd.DataFrame,
    summary: _core.BacktestSummary,
    observed: pd.Series,
) -> pd.DataFrame:
    result = _LEGACY_APPLY_BACKTEST_PROVENANCE(frame, summary, observed)
    if "BacktestSkipReason" not in result.columns:
        return result
    reason = result["BacktestSkipReason"].fillna("").astype(str)
    ambiguous = reason.eq("历史样本不足，不参与排名")
    if ambiguous.any():
        result.loc[ambiguous, "BacktestSkipReason"] = (
            "本票历史样本不足，不使用本票回测校准；同类全局校准可独立参与综合分"
        )
    return result


def calibration_stability_stats(
    rows: list[dict[str, object]] | None,
    *,
    minimum_folds: int = 3,
) -> dict[str, object]:
    governed = dict(
        _LEGACY_CALIBRATION_STABILITY_STATS(
            rows,
            minimum_folds=minimum_folds,
        )
    )
    status = str(governed.get("status", "") or "").strip().upper()
    try:
        raw_multiplier = float(governed.get("confidence_multiplier", 1.0) or 0.0)
    except (TypeError, ValueError):
        raw_multiplier = 0.0
    try:
        stable_ratio = float(governed.get("stable_fold_ratio", raw_multiplier) or 0.0)
    except (TypeError, ValueError):
        stable_ratio = raw_multiplier
    raw_multiplier = max(0.0, min(1.0, raw_multiplier))
    stable_ratio = max(0.0, min(1.0, stable_ratio))

    governed["raw_confidence_multiplier"] = round(raw_multiplier, 4)
    if status == "UNSTABLE":
        governed["confidence_multiplier"] = round(
            raw_multiplier * stable_ratio,
            4,
        )
        governed["confidence_governance"] = "unstable-stable-ratio-shrink-v1"
    else:
        governed["confidence_multiplier"] = round(raw_multiplier, 4)
        governed["confidence_governance"] = "legacy-v1"
    return governed


def _transaction_stage_path(path: Path, destination: Path, stage: Path) -> Path:
    candidate = Path(path)
    # refresh_candidate_exports may already be told to write directly into the
    # transaction staging root. Do not remap such a path a second time or the
    # final publication would land under .backtest_publication_txn/.../stage.
    try:
        candidate.relative_to(stage)
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate
    except ValueError:
        pass
    try:
        relative = candidate.relative_to(destination)
    except ValueError:
        relative = Path(candidate.name)
    target = stage / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _refresh_published_ranking_audit(destination: Path) -> None:
    """Refresh diagnostics after publication without changing transaction state."""
    try:
        payload = run_audit(
            destination / "AllResults.csv",
            destination / "audit",
        )
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        _core.logger.warning("Post-backtest ranking audit failed: %s", exc)
        return
    _core.logger.info(
        "Post-backtest ranking audit refreshed: rows=%s, stocks=%s, ETFs=%s.",
        payload.get("rows", 0),
        payload.get("stocks", 0),
        payload.get("etfs", 0),
    )


def apply_backtest_ranking(summary: _core.BacktestSummary, top_n: int = 50) -> None:
    """Run stable backtest postprocess while publishing its result set atomically."""
    import report as report_module

    with _BACKTEST_PUBLICATION_LOCK:
        # Resolve the transaction root through the same canonical path the
        # stable implementation reads. This keeps normal pathlib roots and
        # compatibility path-like wrappers on one concrete destination.
        destination = Path(_core.OUTPUT_DIR / "AllResults.csv").parent
        report_module.recover_publication_transactions(destination)
        transaction_root = destination / ".backtest_publication_txn" / uuid.uuid4().hex
        stage = transaction_root / "stage"
        backup = transaction_root / "backup"
        stage.mkdir(parents=True, exist_ok=True)

        original_csv = report_module._atomic_write_csv
        original_parquet = report_module._atomic_write_parquet
        original_refresh = report_module.refresh_candidate_exports

        def staged_csv(frame: pd.DataFrame, path: Path) -> None:
            original_csv(frame, _transaction_stage_path(Path(path), destination, stage))

        def staged_parquet(frame: pd.DataFrame, path: Path) -> None:
            original_parquet(
                frame,
                _transaction_stage_path(Path(path), destination, stage),
            )

        def staged_refresh(
            frame: pd.DataFrame,
            top_n_csv: int = report_module.TOP_N_REPORT,
            top_n_parquet: int = report_module.TOP_N_PARQUET,
            output_dir: Path | None = None,
            **kwargs: object,
        ):
            del output_dir
            return original_refresh(
                frame,
                top_n_csv=top_n_csv,
                top_n_parquet=top_n_parquet,
                output_dir=stage,
                **kwargs,
            )

        report_module._atomic_write_csv = staged_csv
        report_module._atomic_write_parquet = staged_parquet
        report_module.refresh_candidate_exports = staged_refresh
        try:
            # The legacy postprocess embeds recency in InstitutionalScore before
            # it calls the canonical lifecycle ranker. ContextVar activation is
            # local to this execution context; other callers keep normal rules.
            with single_recency_ranking_context():
                _core._legacy_apply_backtest_ranking(summary, top_n=top_n)
        except BaseException:
            shutil.rmtree(transaction_root, ignore_errors=True)
            raise
        finally:
            report_module._atomic_write_csv = original_csv
            report_module._atomic_write_parquet = original_parquet
            report_module.refresh_candidate_exports = original_refresh

        published = False
        try:
            staged_files = [path for path in stage.rglob("*") if path.is_file()]
            if staged_files:
                report_module._publish_stage(stage, destination, backup)
                published = True
                _core.logger.info(
                    "Backtest ranking publication committed transactionally: %d files.",
                    len(staged_files),
                )
        finally:
            shutil.rmtree(transaction_root, ignore_errors=True)

        if published:
            _refresh_published_ranking_audit(destination)


_core._load_benchmark_frames = _load_benchmark_frames
_core._backtest_one_ticker_cached = _backtest_one_ticker_cached
_core._ticker_backtest_rows = _ticker_backtest_rows
_core._init_backtest_worker = _init_backtest_worker
_core.BacktestSummary.to_dict = _backtest_summary_to_dict
_core._apply_backtest_provenance = _apply_backtest_provenance
_core.calibration_stability_stats = calibration_stability_stats
_core._refresh_published_ranking_audit = _refresh_published_ranking_audit
_core.apply_backtest_ranking = apply_backtest_ranking
_core.BACKTEST_SIGNAL_EXECUTION_VERSION = (
    "2026-08-22-v89-immediate-executable-next-open-v1"
)
_core.BACKTEST_RESONANCE_DIAGNOSTIC_VERSION = RESONANCE_VERSION
_core.BACKTEST_PUBLICATION_INTEGRITY_VERSION = (
    "2026-08-19-v73-journaled-backtest-publication-v2"
)
_core.BACKTEST_RANKING_INTEGRITY_VERSION = (
    "2026-08-21-v88-verified-point-in-time-ranking-"
    + BACKTEST_RECENCY_NORMALIZATION_VERSION
)
_core.PERFORMANCE_ENGINE_VERSION = "2026-08-20-v80-vectorized-backtest-workstation-v1"

sys.modules[__name__] = _core
