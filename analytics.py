"""Canonical analytics facade for the vectorised v95+ runtime.

The stable implementation remains in :mod:`analytics_core`, while this module
composes acceleration, score, FAST/EXACT consistency, point-in-time diagnostics
and transactional publication exactly once.  Historical version modules remain
import-compatible kernels; import order is no longer allowed to define model
semantics.
"""

from __future__ import annotations

import inspect
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
import backtest_fastscore_v80 as _backtest_fastscore
import backtest_profile_alignment_v95 as _profile_alignment
import cache_acceleration_v77 as _cache_acceleration
import calibration_weight_cache_v79 as _calibration_weight_cache
import indicator_acceleration_v77 as _indicator_acceleration
import score_runtime_v97 as _score_runtime
import scoring_consistency_v94 as _scoring_consistency
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

# One deterministic bootstrap. Raw accelerators install first; canonical policy
# overlays install last so workers, GUI and direct analytics share one runtime.
_indicator_acceleration.install()
_cache_acceleration.install()
_universe_cache_acceleration.install()
_backtest_acceleration.install()
_analytics_acceleration.install()
_score_runtime.install()
_calibration_weight_cache.install()
_backtest_fastpath.install()
_backtest_fastscore.install()
_profile_alignment.install()
_scoring_consistency.install()
install_analytics_alignment(_core)
install_single_recency_ranking_guard(_core)

ANALYTICS_RUNTIME_COMPOSITION_VERSION = (
    "2026-08-23-v97-canonical-vectorized-analytics-runtime-v1"
)
_core.ANALYTICS_RUNTIME_COMPOSITION_VERSION = ANALYTICS_RUNTIME_COMPOSITION_VERSION

# Immediate next-open execution is intentionally limited to signals that are
# already executable. WAIT_PULLBACK is admitted only by the production
# conditional-fill transaction, which models a future zone touch explicitly.
_BACKTEST_EXECUTABLE_SIGNALS = frozenset({"BUY_NOW", "BREAKOUT_CONFIRM"})
_core._BACKTEST_ACTIONABLE_SIGNALS = _BACKTEST_EXECUTABLE_SIGNALS

# Keep the old private lifecycle alias for compatibility only.
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
_LEGACY_BACKTEST_ONE_TICKER_CACHED = _core._backtest_one_ticker_cached
_LEGACY_TICKER_BACKTEST_ROWS = _core._ticker_backtest_rows
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


def _reset_resonance_analysis() -> None:
    global _LAST_RESONANCE_ANALYSIS
    with _RESONANCE_ANALYSIS_LOCK:
        _LAST_RESONANCE_ANALYSIS = {
            "version": RESONANCE_VERSION,
            "status": "NOT_EVALUATED",
            "by_count": [],
            "by_band": [],
            "by_transition": [],
        }


def _load_benchmark_frames(source: str) -> dict[str, pd.DataFrame]:
    """Refresh each benchmark first; use cache only as a resilient fallback."""
    _reset_resonance_analysis()
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
    """Attach five-factor state only to genuine dated historical samples."""
    if not samples:
        return samples
    if not any(str(item.get("signal_date") or "").strip() for item in samples):
        return samples

    market_frame = frame
    if market_frame is None or market_frame.empty:
        market_frame = _core._load_cache(ticker, source)
    if market_frame is None or market_frame.empty:
        return samples
    try:
        return attach_resonance_to_samples(samples, market_frame)
    except (ArithmeticError, KeyError, TypeError, ValueError) as exc:
        _core.logger.warning(
            "Five-factor resonance diagnostics unavailable for %s: %s",
            ticker,
            exc,
        )
        return samples


def _supports_profile_contract(callable_obj: Any) -> bool:
    """Detect legacy patched executors without executing/retrying side effects."""
    probe = getattr(callable_obj, "side_effect", None)
    if callable(probe):
        callable_obj = probe
    try:
        signature = inspect.signature(callable_obj)
    except (TypeError, ValueError):
        return True
    parameters = signature.parameters
    return "profile" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
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
    profile: _core.BacktestExecutionProfile | None = None,
    benchmark_name: str = "沪深300",
) -> tuple[list[dict[str, object]], bool]:
    """Preserve cache semantics and add resonance without signature brittleness."""
    executor = _core._backtest_one_ticker
    if not _supports_profile_contract(executor):
        # Compatibility lane for old research/test integrations that supplied a
        # positional-only executor. Production executors all use the modern
        # profile contract, so this branch has no effect on normal cache paths.
        samples = executor(
            ticker,
            source,
            benchmark_frame,
            commission,
            stamp_duty,
            slippage,
            split_dates,
        )
        return _attach_backtest_resonance(ticker, source, samples), False

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
    samples = executor(
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
    """Preserve calibration rows and attach resonance with one bulk merge."""
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
    working["_resonance_rising"] = pd.to_numeric(
        working.get("resonance_delta_3d", pd.Series(np.nan, index=working.index)),
        errors="coerce",
    ).gt(0.0)
    valid = working["resonance_count"].notna()
    if not valid.any():
        return rows

    resonance = working.loc[
        valid, ["ticker", "entry_signal", "resonance_count", "_resonance_rising"]
    ].copy()
    resonance["_strong"] = resonance["resonance_count"].ge(4.0)
    metrics = (
        resonance.groupby(["ticker", "entry_signal"], sort=False, as_index=False)
        .agg(
            resonance_mean_count=("resonance_count", "mean"),
            resonance_strong_bull_share=("_strong", "mean"),
            resonance_rising_share=("_resonance_rising", "mean"),
        )
    )
    for column in (
        "resonance_mean_count",
        "resonance_strong_bull_share",
        "resonance_rising_share",
    ):
        metrics[column] = metrics[column].round(4)

    base = pd.DataFrame.from_records(rows)
    base["ticker"] = base.get("ticker", pd.Series("", index=base.index)).astype(str)
    base["entry_signal"] = (
        base.get("entry_signal", pd.Series("UNKNOWN", index=base.index))
        .fillna("UNKNOWN")
        .astype(str)
        .str.upper()
    )
    merged = base.merge(
        metrics,
        on=["ticker", "entry_signal"],
        how="left",
        validate="one_to_one",
    )
    return merged.to_dict(orient="records")


def _backtest_summary_to_dict(summary: _core.BacktestSummary) -> dict[str, Any]:
    """Expose the held-out resonance experiment in BacktestSummary.json."""
    result = _LEGACY_SUMMARY_TO_DICT(summary)
    with _RESONANCE_ANALYSIS_LOCK:
        result["resonance_analysis"] = dict(_LAST_RESONANCE_ANALYSIS)
    return result


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


def _transaction_stage_path(path: Path, destination: Path, stage: Path) -> Path:
    candidate = Path(path)
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
    """Run backtest postprocess in one coherent read/write staging root."""
    import report as report_module

    with _BACKTEST_PUBLICATION_LOCK:
        destination = Path(_core.OUTPUT_DIR / "AllResults.csv").parent
        report_module.recover_publication_transactions(destination)
        transaction_root = destination / ".backtest_publication_txn" / uuid.uuid4().hex
        stage = transaction_root / "stage"
        backup = transaction_root / "backup"
        stage.mkdir(parents=True, exist_ok=True)

        # The old transaction redirected writes only. Any nested postprocessor
        # that re-read AllResults therefore saw the pre-transaction file. Seed
        # the stage and point the canonical OUTPUT_DIR at it so every layer reads
        # exactly the data that the previous layer wrote.
        source_csv = destination / "AllResults.csv"
        if source_csv.is_file():
            shutil.copy2(source_csv, stage / "AllResults.csv")

        original_output_dir = _core.OUTPUT_DIR
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

        _core.OUTPUT_DIR = stage
        report_module._atomic_write_csv = staged_csv
        report_module._atomic_write_parquet = staged_parquet
        report_module.refresh_candidate_exports = staged_refresh
        try:
            with single_recency_ranking_context():
                _core._legacy_apply_backtest_ranking(summary, top_n=top_n)
        except BaseException:
            shutil.rmtree(transaction_root, ignore_errors=True)
            raise
        finally:
            _core.OUTPUT_DIR = original_output_dir
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
_core.BacktestSummary.to_dict = _backtest_summary_to_dict
_core._apply_backtest_provenance = _apply_backtest_provenance
_core._refresh_published_ranking_audit = _refresh_published_ranking_audit
_core.apply_backtest_ranking = apply_backtest_ranking
_core.BACKTEST_SIGNAL_EXECUTION_VERSION = (
    "2026-08-22-v89-immediate-executable-next-open-v1"
)
_core.BACKTEST_RESONANCE_DIAGNOSTIC_VERSION = RESONANCE_VERSION
_core.BACKTEST_PUBLICATION_INTEGRITY_VERSION = (
    "2026-08-23-v97-coherent-stage-read-write-publication-v3"
)
_core.BACKTEST_RANKING_INTEGRITY_VERSION = (
    "2026-08-21-v88-verified-point-in-time-ranking-"
    + BACKTEST_RECENCY_NORMALIZATION_VERSION
)
_core.PERFORMANCE_ENGINE_VERSION = "2026-08-23-v97-canonical-vectorized-runtime-v2"

# Production ranking math belongs to analytics itself, not only to the CLI
# command facade. The generic model_calibration API remains generic; only the
# analytics resolver receives signal-semantic policy.
import backtest_math_integrity_v94 as _math_integrity  # noqa: E402
import calibration_math_v96 as _calibration_math  # noqa: E402
import model_calibration as _model_calibration  # noqa: E402

_math_integrity.install(_core, _model_calibration)
_calibration_math.install(_core)

sys.modules[__name__] = _core
