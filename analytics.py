"""v56 analytics facade with fresh benchmark and explicit evidence provenance.

The historical analytics implementation lives in ``analytics_core``.  This
public boundary keeps the v51 T+1-open alignment contract, but also fixes two
runtime audit problems found in a real post-close scan:

* benchmark frames are refreshed through the normal TickFlow Free downloader
  before falling back to an existing cache, so an old but non-empty benchmark
  cache cannot make every current result look stale;
* ticker-specific backtest insufficiency is described separately from peer/global
  calibration, which may still contribute a bounded model-calibration weight.

No score formula, split policy, execution model, or candidate eligibility rule is
changed here.
"""

from __future__ import annotations

import sys

import pandas as pd

import analytics_core as _core
from analytics_core import *  # noqa: F403
from backtest_alignment import install_analytics_alignment

install_analytics_alignment(_core)

_LEGACY_APPLY_BACKTEST_PROVENANCE = _core._apply_backtest_provenance


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


_core._load_benchmark_frames = _load_benchmark_frames
_core._apply_backtest_provenance = _apply_backtest_provenance

sys.modules[__name__] = _core
