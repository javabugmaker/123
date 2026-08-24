"""v102 fail-closed governance for historical calibration evidence.

The statistical calibration layer is useful evidence, but evidence that fails
held-out ordering or walk-forward stability must never influence a production
ranking. This overlay runs after v97 calibration math, preserves all raw
calibration diagnostics, and zeroes only the applied peer weight when the
validation contract is not satisfied.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

CALIBRATION_GOVERNANCE_VERSION = (
    "2026-08-24-v102-heldout-monotonicity-walkforward-fail-closed-v1"
)

_INSTALLED = False
_ORIGINAL_APPLY: Any = None


def _number(value: Any, default: float = np.nan) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if np.isfinite(parsed) else default


def _summary_payload(summary: Any) -> dict[str, Any]:
    serializer = getattr(summary, "to_dict", None)
    if callable(serializer):
        try:
            value = serializer()
        except (ArithmeticError, KeyError, TypeError, ValueError):
            value = {}
        if isinstance(value, dict):
            return value
    return {}


def calibration_governance_state(summary: Any) -> tuple[bool, str, dict[str, Any]]:
    """Return whether peer calibration may affect production ranking."""
    payload = _summary_payload(summary)
    stability_raw = payload.get("calibration_stability", {})
    stability = stability_raw if isinstance(stability_raw, dict) else {}
    status = str(stability.get("status", "") or "").strip().upper()

    high_low_20 = _number(payload.get("monotonicity_high_low_20d"))
    high_low_60 = _number(payload.get("monotonicity_high_low_60d"))
    rank_ic_20 = _number(payload.get("rank_ic_20d"))
    rank_ic_60 = _number(payload.get("rank_ic_60d"))

    checks = {
        "walk_forward_stable": status == "STABLE",
        "heldout_20d_positive": bool(np.isfinite(high_low_20) and high_low_20 > 0.0),
        "heldout_60d_positive": bool(np.isfinite(high_low_60) and high_low_60 > 0.0),
        "rank_ic_20d_positive": bool(np.isfinite(rank_ic_20) and rank_ic_20 > 0.0),
        "rank_ic_60d_positive": bool(np.isfinite(rank_ic_60) and rank_ic_60 > 0.0),
    }
    active = all(checks.values())
    failed = [name for name, passed in checks.items() if not passed]
    reason = (
        "peer calibration validated: held-out ordering and walk-forward stability passed"
        if active
        else "peer calibration diagnostic-only: " + ", ".join(failed)
    )
    diagnostics = {
        "status": status or "MISSING",
        "monotonicity_high_low_20d": high_low_20,
        "monotonicity_high_low_60d": high_low_60,
        "rank_ic_20d": rank_ic_20,
        "rank_ic_60d": rank_ic_60,
        "checks": checks,
    }
    return active, reason, diagnostics


def _numeric(frame: pd.DataFrame, column: str, default: float = np.nan) -> pd.Series:
    source = frame.get(column, pd.Series(default, index=frame.index, dtype=float))
    return pd.to_numeric(source, errors="coerce").replace([np.inf, -np.inf], np.nan)


def _rewrite_governed_output(core: Any, summary: Any) -> None:
    path = core.OUTPUT_DIR / "AllResults.csv"
    if not path.exists():
        return

    frame = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
    if frame.empty or "CompositeScore" not in frame.columns:
        return

    active, global_reason, diagnostics = calibration_governance_state(summary)

    local_weight = _numeric(frame, "BacktestLocalEvidenceWeight", 0.0).fillna(0.0).clip(0.0, 1.0)
    raw_peer_weight = _numeric(frame, "BacktestPeerEvidenceWeight", 0.0).fillna(0.0).clip(0.0, 1.0)
    row_stability = frame.get(
        "GlobalCalibrationStability", pd.Series("", index=frame.index, dtype=object)
    ).fillna("").astype(str).str.upper()
    row_peer_allowed = row_stability.eq("STABLE") if active else pd.Series(False, index=frame.index)
    peer_weight = raw_peer_weight.where(row_peer_allowed, 0.0)

    local_score = _numeric(frame, "BacktestEvidenceScoreRaw", 50.0).fillna(50.0).clip(0.0, 100.0)
    peer_score = _numeric(frame, "GlobalCalibrationScore", 50.0).fillna(50.0).clip(0.0, 100.0)
    evidence_total = local_weight + peer_weight
    evidence_score = pd.Series(50.0, index=frame.index, dtype=float)
    has_evidence = evidence_total.gt(0.0)
    evidence_score.loc[has_evidence] = (
        local_score.loc[has_evidence] * local_weight.loc[has_evidence]
        + peer_score.loc[has_evidence] * peer_weight.loc[has_evidence]
    ) / evidence_total.loc[has_evidence]

    effective_weight = pd.Series(
        np.maximum(local_weight.to_numpy(dtype=float), peer_weight.to_numpy(dtype=float)),
        index=frame.index,
        dtype=float,
    ).clip(0.0, 1.0)

    final_score = _numeric(frame, "FinalScore")
    raw_score = final_score.where(final_score.notna(), _numeric(frame, "Score", 0.0))
    raw_score = raw_score.fillna(0.0).clip(0.0, 100.0)
    old_composite = _numeric(frame, "CompositeScore").fillna(raw_score)
    composite = (
        raw_score * (1.0 - effective_weight)
        + evidence_score * effective_weight
    ).clip(0.0, 100.0)

    frame["BacktestPeerEvidenceWeightRaw"] = raw_peer_weight.round(4)
    frame["BacktestPeerEvidenceWeight"] = peer_weight.round(4)
    frame["BacktestEffectiveWeight"] = effective_weight.round(4)
    frame["BacktestAdjustedScore"] = evidence_score.round(4)
    frame["CompositeScore"] = composite.round(4)
    frame["FailureAdjustedScore"] = composite.round(4)
    frame["GlobalCalibrationApplied"] = peer_weight.gt(0.0)
    frame["GlobalCalibrationGovernanceStatus"] = np.where(
        peer_weight.gt(0.0), "ACTIVE", "DIAGNOSTIC_ONLY"
    )
    frame["GlobalCalibrationGovernanceReason"] = global_reason
    frame["CalibrationGovernanceVersion"] = CALIBRATION_GOVERNANCE_VERSION
    frame["HeldOutWalkForwardStatus"] = str(diagnostics["status"])

    denominator = old_composite.replace(0.0, np.nan)
    correction = (composite / denominator).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    correction = correction.clip(0.70 / 1.30, 1.30 / 0.70)
    for column in ("TechnicalInstitutionalScore", "InstitutionalScore"):
        if column in frame.columns:
            current = _numeric(frame, column)
            frame[column] = (current * correction).clip(0.0, 100.0).round(4)

    frame = core.finalize_signal_ranking(frame)

    from report import _atomic_write_csv, _atomic_write_parquet, refresh_candidate_exports

    _atomic_write_csv(frame, path)
    refresh_candidate_exports(frame, output_dir=core.OUTPUT_DIR)
    _atomic_write_parquet(frame, core.OUTPUT_DIR / "AllResults.parquet")


def install(core: Any) -> None:
    """Install after v97 calibration math and before the analytics facade seals."""
    global _INSTALLED, _ORIGINAL_APPLY
    if _INSTALLED or getattr(core, "_CALIBRATION_GOVERNANCE_V102_INSTALLED", False):
        return
    original = getattr(core, "_legacy_apply_backtest_ranking", None)
    if not callable(original):
        return
    _ORIGINAL_APPLY = original

    def governed_apply_backtest_ranking(summary: Any, top_n: int = 50) -> None:
        _ORIGINAL_APPLY(summary, top_n=top_n)
        _rewrite_governed_output(core, summary)

    core._legacy_apply_backtest_ranking = governed_apply_backtest_ranking
    core.CALIBRATION_GOVERNANCE_VERSION = CALIBRATION_GOVERNANCE_VERSION
    core._CALIBRATION_GOVERNANCE_V102_INSTALLED = True
    _INSTALLED = True
