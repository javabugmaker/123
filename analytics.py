"""v80 analytics facade with vectorised scoring and workstation backtests.

All v73/v76 analytics semantics remain intact. v77 compiled indicator kernels,
single-pass enrichment, fast cache hashing and worker benchmark memoization are
installed on the real analytics runtime. v78 vectorizes the FAST historical
quick gate and caches TickFlow metadata. v79 reuses normalized score columns and
endpoint computations across filters/scoring/scanner calls. v80 moves FAST
historical scoring, execution/tradeability and benchmark alignment to ticker-
level arrays while preserving score, entry, ranking and cache-integrity rules.
"""

from __future__ import annotations

import shutil
import sys
import threading
import uuid
from pathlib import Path

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

_LEGACY_APPLY_BACKTEST_PROVENANCE = _core._apply_backtest_provenance
_LEGACY_CALIBRATION_STABILITY_STATS = _core.calibration_stability_stats
_LEGACY_BACKTEST_ONE_TICKER_CACHED = _core._backtest_one_ticker_cached
_LEGACY_APPLY_BACKTEST_RANKING = _core.apply_backtest_ranking
_core._legacy_apply_backtest_ranking = _LEGACY_APPLY_BACKTEST_RANKING
_BACKTEST_PUBLICATION_LOCK = threading.Lock()


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
    """Never reuse excess-return cache when the benchmark is absent now."""
    if benchmark_frame is not None and not benchmark_frame.empty:
        return _LEGACY_BACKTEST_ONE_TICKER_CACHED(
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
    return samples, False


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
    # transaction staging root.  Do not remap such a path a second time or the
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


def apply_backtest_ranking(summary: _core.BacktestSummary, top_n: int = 50) -> None:
    """Run stable backtest postprocess while publishing its result set atomically."""
    import report as report_module

    with _BACKTEST_PUBLICATION_LOCK:
        destination = Path(_core.OUTPUT_DIR)
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
            _core._legacy_apply_backtest_ranking(summary, top_n=top_n)
        except BaseException:
            shutil.rmtree(transaction_root, ignore_errors=True)
            raise
        finally:
            report_module._atomic_write_csv = original_csv
            report_module._atomic_write_parquet = original_parquet
            report_module.refresh_candidate_exports = original_refresh

        try:
            staged_files = [path for path in stage.rglob("*") if path.is_file()]
            if staged_files:
                report_module._publish_stage(stage, destination, backup)
                _core.logger.info(
                    "Backtest ranking publication committed transactionally: %d files.",
                    len(staged_files),
                )
        finally:
            shutil.rmtree(transaction_root, ignore_errors=True)


_core._load_benchmark_frames = _load_benchmark_frames
_core._backtest_one_ticker_cached = _backtest_one_ticker_cached
_core._apply_backtest_provenance = _apply_backtest_provenance
_core.calibration_stability_stats = calibration_stability_stats
_core.apply_backtest_ranking = apply_backtest_ranking
_core.BACKTEST_PUBLICATION_INTEGRITY_VERSION = (
    "2026-08-19-v73-journaled-backtest-publication-v2"
)
_core.PERFORMANCE_ENGINE_VERSION = "2026-08-20-v80-vectorized-backtest-workstation-v1"

sys.modules[__name__] = _core
