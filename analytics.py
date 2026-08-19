"""v57 analytics facade with fresh benchmarks and calibration governance.

The historical analytics implementation lives in ``analytics_core``.  This
public boundary keeps the v51 T+1-open alignment contract and the v56 benchmark
refresh/evidence provenance fixes, while adding one fail-soft governance rule
for peer calibration discovered from real post-close output:

* benchmark frames are refreshed through the normal TickFlow Free downloader
  before falling back to an existing cache;
* ticker-specific backtest insufficiency is described separately from peer/global
  calibration;
* when walk-forward peer calibration is explicitly classified ``UNSTABLE``, its
  confidence multiplier is shrunk a second time by the observed stable-fold
  ratio.  Stable calibrations retain the legacy weight and insufficient-fold
  histories retain the legacy neutral treatment.

The technical score formula, train/validation/test split, execution model and
candidate eligibility rules are unchanged.
"""

from __future__ import annotations

import sys

import pandas as pd

import analytics_core as _core
from analytics_core import *  # noqa: F403
from backtest_alignment import install_analytics_alignment

install_analytics_alignment(_core)

_LEGACY_APPLY_BACKTEST_PROVENANCE = _core._apply_backtest_provenance
_LEGACY_CALIBRATION_STABILITY_STATS = _core.calibration_stability_stats


def _load_benchmark_frames(source: str) -> dict[str, pd.DataFrame]:
    """Refresh each benchmark first; use cache only as a resilient fallback.

    The old implementation loaded a benchmark cache first and contacted the
    provider only when that cache was missing.  Once created, a benchmark could
    therefore remain several sessions behind even though the stock universe had
    already advanced.  ``download_ticker`` already performs the canonical
    incremental TickFlow Free update, so use that path before accepting cache.
    """
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


def _apply_backtest_provenance(
    frame: pd.DataFrame,
    summary: BacktestSummary,
    observed: pd.Series,
) -> pd.DataFrame:
    """Clarify that ticker evidence and peer calibration are separate channels."""
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
    """Govern peer-calibration confidence using observed walk-forward stability.

    The core calibration already scales confidence by ``stable_fold_ratio`` once.
    A real v56 run produced 11 valid folds but only 54.55% directionally stable
    folds, which correctly classified the calibration as ``UNSTABLE`` while
    still allowing roughly eight percent peer weight.  For an explicitly
    unstable model, applying the same empirical support ratio once more is a
    conservative shrinkage rule: no evidence is discarded, but unstable peer
    evidence cannot retain near-normal influence.

    ``STABLE`` and ``INSUFFICIENT_FOLDS`` keep the legacy behavior so this is not
    a blanket penalty for short histories.
    """
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


_core._load_benchmark_frames = _load_benchmark_frames
_core._apply_backtest_provenance = _apply_backtest_provenance
_core.calibration_stability_stats = calibration_stability_stats

sys.modules[__name__] = _core