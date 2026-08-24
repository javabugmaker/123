"""Fail-closed governance for historical calibration evidence.

v106 closes two audit gaps that could otherwise let peer calibration receive
production weight:
1. held-out approval metrics must come from a fully point-in-time verified model
   sample set; mixed verified/unverified test evidence is not admissible;
2. peer evidence must explicitly certify leave-one-out construction and complete
   survivorship control. The current prospective snapshot archive is partial, so
   peer calibration remains diagnostic-only until those contracts are satisfied.

Local per-ticker evidence is governed separately and is not weakened here.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

CALIBRATION_GOVERNANCE_VERSION = (
    "2026-08-24-v106-pit-survivorship-loo-fail-closed-v1"
)

_INSTALLED = False
_ORIGINAL_APPLY: Any = None


def _number(value: Any, default: float = np.nan) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if np.isfinite(parsed) else default


def _truth(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return str(value).strip().lower() in {
        "true", "1", "yes", "y", "是", "pass", "complete",
    }


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


def calibration_governance_state(
    summary: Any,
) -> tuple[bool, str, dict[str, Any]]:
    """Return whether peer calibration may affect production ranking.

    Missing integrity provenance always fails closed. This is intentional:
    aggregate peer evidence cannot prove leave-one-out or survivorship
    completeness merely because its return statistics look stable.
    """
    payload = _summary_payload(summary)
    stability_raw = payload.get("calibration_stability", {})
    stability = stability_raw if isinstance(stability_raw, dict) else {}
    status = str(stability.get("status", "") or "").strip().upper()

    high_low_20 = _number(payload.get("monotonicity_high_low_20d"))
    high_low_60 = _number(payload.get("monotonicity_high_low_60d"))
    rank_ic_20 = _number(payload.get("rank_ic_20d"))
    rank_ic_60 = _number(payload.get("rank_ic_60d"))

    pit_raw = payload.get("point_in_time_universe", {})
    pit = pit_raw if isinstance(pit_raw, dict) else {}
    verified_samples = _number(payload.get("universe_verified_samples"), 0.0)
    unverified_samples = _number(
        payload.get("universe_unverified_samples"), np.nan
    )
    ranking_status = str(
        payload.get("ranking_calibration_status", "") or ""
    ).strip().upper()

    pit_available = _truth(pit.get("available"), False)
    survivorship_complete = _truth(
        pit.get(
            "survivorship_complete",
            payload.get("survivorship_control_complete"),
        ),
        False,
    )
    peer_loo_verified = _truth(
        payload.get(
            "peer_leave_one_out_verified",
            payload.get("global_calibration_leave_one_out_verified"),
        ),
        False,
    )
    verified_only = bool(
        np.isfinite(unverified_samples)
        and unverified_samples == 0.0
        and verified_samples > 0.0
    )

    checks = {
        "point_in_time_universe_available": pit_available,
        "verified_model_samples_present": bool(verified_samples > 0.0),
        "no_unverified_model_samples": verified_only,
        "ranking_uses_verified_point_in_time": (
            ranking_status == "ENABLED_VERIFIED_POINT_IN_TIME"
        ),
        "survivorship_control_complete": survivorship_complete,
        "peer_leave_one_out_verified": peer_loo_verified,
        "walk_forward_stable": status == "STABLE",
        "heldout_20d_positive": bool(
            np.isfinite(high_low_20) and high_low_20 > 0.0
        ),
        "heldout_60d_positive": bool(
            np.isfinite(high_low_60) and high_low_60 > 0.0
        ),
        "rank_ic_20d_positive": bool(
            np.isfinite(rank_ic_20) and rank_ic_20 > 0.0
        ),
        "rank_ic_60d_positive": bool(
            np.isfinite(rank_ic_60) and rank_ic_60 > 0.0
        ),
    }
    active = all(checks.values())
    failed = [name for name, passed in checks.items() if not passed]
    reason = (
        "peer calibration validated: PIT, survivorship, leave-one-out, "
        "held-out ordering and walk-forward stability passed"
        if active
        else "peer calibration diagnostic-only: " + ", ".join(failed)
    )
    diagnostics = {
        "status": status or "MISSING",
        "monotonicity_high_low_20d": high_low_20,
        "monotonicity_high_low_60d": high_low_60,
        "rank_ic_20d": rank_ic_20,
        "rank_ic_60d": rank_ic_60,
        "point_in_time_universe_available": pit_available,
        "universe_verified_samples": verified_samples,
        "universe_unverified_samples": unverified_samples,
        "ranking_calibration_status": ranking_status or "MISSING",
        "survivorship_complete": survivorship_complete,
        "peer_leave_one_out_verified": peer_loo_verified,
        "checks": checks,
    }
    return active, reason, diagnostics


def _numeric(
    frame: pd.DataFrame,
    column: str,
    default: float = np.nan,
) -> pd.Series:
    source = frame.get(
        column,
        pd.Series(default, index=frame.index, dtype=float),
    )
    return pd.to_numeric(source, errors="coerce").replace(
        [np.inf, -np.inf],
        np.nan,
    )


def _rewrite_governed_output(core: Any, summary: Any) -> None:
    path = core.OUTPUT_DIR / "AllResults.csv"
    if not path.exists():
        return

    frame = pd.read_csv(
        path,
        encoding="utf-8-sig",
        low_memory=False,
    )
    if frame.empty or "CompositeScore" not in frame.columns:
        return

    active, global_reason, diagnostics = calibration_governance_state(
        summary
    )

    local_weight = (
        _numeric(frame, "BacktestLocalEvidenceWeight", 0.0)
        .fillna(0.0)
        .clip(0.0, 1.0)
    )
    raw_peer_weight = (
        _numeric(frame, "BacktestPeerEvidenceWeight", 0.0)
        .fillna(0.0)
        .clip(0.0, 1.0)
    )
    row_stability = (
        frame.get(
            "GlobalCalibrationStability",
            pd.Series("", index=frame.index, dtype=object),
        )
        .fillna("")
        .astype(str)
        .str.upper()
    )
    row_peer_allowed = (
        row_stability.eq("STABLE")
        if active
        else pd.Series(False, index=frame.index)
    )
    peer_weight = raw_peer_weight.where(row_peer_allowed, 0.0)

    local_score = (
        _numeric(frame, "BacktestEvidenceScoreRaw", 50.0)
        .fillna(50.0)
        .clip(0.0, 100.0)
    )
    peer_score = (
        _numeric(frame, "GlobalCalibrationScore", 50.0)
        .fillna(50.0)
        .clip(0.0, 100.0)
    )
    evidence_total = local_weight + peer_weight
    evidence_score = pd.Series(
        50.0,
        index=frame.index,
        dtype=float,
    )
    has_evidence = evidence_total.gt(0.0)
    evidence_score.loc[has_evidence] = (
        local_score.loc[has_evidence] * local_weight.loc[has_evidence]
        + peer_score.loc[has_evidence] * peer_weight.loc[has_evidence]
    ) / evidence_total.loc[has_evidence]

    effective_weight = pd.Series(
        np.maximum(
            local_weight.to_numpy(dtype=float),
            peer_weight.to_numpy(dtype=float),
        ),
        index=frame.index,
        dtype=float,
    ).clip(0.0, 1.0)

    final_score = _numeric(frame, "FinalScore")
    raw_score = final_score.where(
        final_score.notna(),
        _numeric(frame, "Score", 0.0),
    )
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
        peer_weight.gt(0.0),
        "ACTIVE",
        "DIAGNOSTIC_ONLY",
    )
    frame["GlobalCalibrationGovernanceReason"] = global_reason
    frame["CalibrationGovernanceVersion"] = (
        CALIBRATION_GOVERNANCE_VERSION
    )
    frame["HeldOutWalkForwardStatus"] = str(
        diagnostics["status"]
    )
    frame["GlobalCalibrationPointInTimeVerified"] = bool(
        diagnostics["checks"]["no_unverified_model_samples"]
        and diagnostics["checks"][
            "ranking_uses_verified_point_in_time"
        ]
    )
    frame["GlobalCalibrationSurvivorshipComplete"] = bool(
        diagnostics["survivorship_complete"]
    )
    frame["GlobalCalibrationLeaveOneOutVerified"] = bool(
        diagnostics["peer_leave_one_out_verified"]
    )

    denominator = old_composite.replace(0.0, np.nan)
    correction = (
        (composite / denominator)
        .replace([np.inf, -np.inf], np.nan)
        .fillna(1.0)
    )
    correction = correction.clip(0.70 / 1.30, 1.30 / 0.70)
    for column in (
        "TechnicalInstitutionalScore",
        "InstitutionalScore",
    ):
        if column in frame.columns:
            current = _numeric(frame, column)
            frame[column] = (
                current * correction
            ).clip(0.0, 100.0).round(4)

    frame = core.finalize_signal_ranking(frame)

    from report import (
        _atomic_write_csv,
        _atomic_write_parquet,
        refresh_candidate_exports,
    )

    _atomic_write_csv(frame, path)
    refresh_candidate_exports(
        frame,
        output_dir=core.OUTPUT_DIR,
    )
    _atomic_write_parquet(
        frame,
        core.OUTPUT_DIR / "AllResults.parquet",
    )


def install(core: Any) -> None:
    """Install after calibration math and before the analytics facade seals."""
    global _INSTALLED, _ORIGINAL_APPLY
    if _INSTALLED or getattr(
        core,
        "_CALIBRATION_GOVERNANCE_V102_INSTALLED",
        False,
    ):
        return
    original = getattr(
        core,
        "_legacy_apply_backtest_ranking",
        None,
    )
    if not callable(original):
        return
    _ORIGINAL_APPLY = original

    def governed_apply_backtest_ranking(
        summary: Any,
        top_n: int = 50,
    ) -> None:
        _ORIGINAL_APPLY(summary, top_n=top_n)
        _rewrite_governed_output(core, summary)

    core._legacy_apply_backtest_ranking = (
        governed_apply_backtest_ranking
    )
    core.CALIBRATION_GOVERNANCE_VERSION = (
        CALIBRATION_GOVERNANCE_VERSION
    )
    core._CALIBRATION_GOVERNANCE_V102_INSTALLED = True
    _INSTALLED = True
