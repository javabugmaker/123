"""v102.1 narrative alignment after fail-closed calibration governance.

v102 correctly zeroes invalid peer calibration weights. This adapter updates only
human-readable evidence/decision explanations so exported CSVs do not claim that
peer evidence is used when governance has made it diagnostic-only.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

CALIBRATION_NARRATIVE_VERSION = (
    "2026-08-24-v102.1-calibration-narrative-alignment-v1"
)

_INSTALLED = False
_ORIGINAL_APPLY: Any = None

_REPLACEMENTS = {
    "本票回测样本不足，仅使用同类证据，不参与本票校准": (
        "本票回测样本不足；同类校准未通过生产治理，仅保留诊断，"
        "不使用回测证据调整生产评分"
    ),
    "本票回测样本不足，仅参考同类证据，不参与本票校准": (
        "本票回测样本不足；同类校准未通过生产治理，仅保留诊断，"
        "不使用回测证据调整生产评分"
    ),
    "同类全局校准可独立参与综合分": (
        "同类全局校准本轮未通过生产治理，仅保留诊断，不参与综合分"
    ),
    "同类全局校准若有有效置信度，可通过综合分的受限校准权重参与模型。": (
        "同类全局校准还需通过 held-out 与 walk-forward 生产治理；"
        "本轮未通过，仅保留诊断，不参与生产评分。"
    ),
}

_TEXT_COLUMNS = (
    "DecisionReason",
    "TradeReadinessReason",
    "OperationAdvice",
    "EvidenceReason",
    "BacktestSkipReason",
)


def _align_narratives(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "GlobalCalibrationGovernanceStatus" not in frame.columns:
        return frame

    result = frame.copy()
    status = (
        result["GlobalCalibrationGovernanceStatus"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )
    diagnostic_only = status.ne("ACTIVE")
    if not diagnostic_only.any():
        return result

    for column in _TEXT_COLUMNS:
        if column not in result.columns:
            continue
        values = result.loc[diagnostic_only, column].fillna("").astype(str)
        for old, new in _REPLACEMENTS.items():
            values = values.str.replace(old, new, regex=False)
        result.loc[diagnostic_only, column] = values
    return result


def install(core: Any) -> None:
    """Run after v102 governance without changing any numeric score or rank."""
    global _INSTALLED, _ORIGINAL_APPLY
    if _INSTALLED or getattr(core, "_CALIBRATION_NARRATIVE_V1021_INSTALLED", False):
        return

    original = getattr(core, "_legacy_apply_backtest_ranking", None)
    if not callable(original):
        return
    _ORIGINAL_APPLY = original

    def narrative_aligned_apply_backtest_ranking(summary: Any, top_n: int = 50) -> None:
        _ORIGINAL_APPLY(summary, top_n=top_n)

        path = core.OUTPUT_DIR / "AllResults.csv"
        if not path.exists():
            return
        frame = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
        aligned = _align_narratives(frame)
        if aligned.equals(frame):
            return

        from report import (
            _atomic_write_csv,
            _atomic_write_parquet,
            refresh_candidate_exports,
        )

        _atomic_write_csv(aligned, path)
        refresh_candidate_exports(aligned, output_dir=core.OUTPUT_DIR)
        _atomic_write_parquet(aligned, core.OUTPUT_DIR / "AllResults.parquet")

    core._legacy_apply_backtest_ranking = narrative_aligned_apply_backtest_ranking
    core.CALIBRATION_NARRATIVE_VERSION = CALIBRATION_NARRATIVE_VERSION
    core._CALIBRATION_NARRATIVE_V1021_INSTALLED = True
    _INSTALLED = True
