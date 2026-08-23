"""Production backtest activation v93.

Historical point-in-time universe snapshots remain the preferred calibration
evidence. When an old signal predates the locally recorded universe snapshots,
the sample is not promoted to fully verified evidence: it enters a provisional
lane with a reduced sample weight. Samples that are explicitly known to have
been outside the historical universe remain excluded.

v94 hardens the degraded lane by separating historical-universe evidence
quality from overlap weight and by requiring both an unknown status and a
missing-snapshot reason before a row can be provisional. Explicit INELIGIBLE or
EXCLUDED states therefore remain fail-closed even when their reason text is
blank.
"""

from __future__ import annotations

import threading
from typing import Any

import pandas as pd

import analytics_core as _core

PRODUCTION_BACKTEST_ACTIVATION_VERSION = (
    "2026-08-23-v94-provisional-universe-evidence-weight-v2"
)
PROVISIONAL_SAMPLE_WEIGHT_SCALE = 0.25
_MISSING_UNIVERSE_REASONS = frozenset(
    {
        "",
        "no_point_in_time_snapshot",
        "snapshot_starts_after_signal",
    }
)
_MISSING_UNIVERSE_STATUSES = frozenset(
    {
        "",
        "UNAVAILABLE",
        "UNKNOWN",
        "MISSING",
        "UNVERIFIED",
        "PROVISIONAL",
    }
)

_LOCK = threading.RLock()
_INSTALLED = False
_ORIGINAL_VERIFIED_POINT_IN_TIME_FRAME = _core._verified_point_in_time_frame
_ORIGINAL_RUN_HISTORICAL_BACKTEST: Any = None
_RUN_STATE: dict[str, int] = {
    "verified_model_samples": 0,
    "provisional_model_samples": 0,
    "known_excluded_model_samples": 0,
}


def _reset_run_state() -> None:
    with _LOCK:
        _RUN_STATE.update(
            {
                "verified_model_samples": 0,
                "provisional_model_samples": 0,
                "known_excluded_model_samples": 0,
            }
        )


def _record_run_state(
    *,
    verified_model_samples: int,
    provisional_model_samples: int,
    known_excluded_model_samples: int,
) -> None:
    with _LOCK:
        _RUN_STATE.update(
            {
                "verified_model_samples": int(verified_model_samples),
                "provisional_model_samples": int(provisional_model_samples),
                "known_excluded_model_samples": int(known_excluded_model_samples),
            }
        )


def _run_state_snapshot() -> dict[str, int]:
    with _LOCK:
        return dict(_RUN_STATE)


def _production_point_in_time_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return verified evidence plus conservatively weighted missing-PIT rows.

    Explicit historical exclusions are never admitted. Only rows whose
    membership could not be observed because the local PIT snapshot is missing
    (or begins after the signal) may enter the provisional lane.
    """
    if frame.empty:
        _record_run_state(
            verified_model_samples=0,
            provisional_model_samples=0,
            known_excluded_model_samples=0,
        )
        return frame.copy()

    if "universe_snapshot_status" in frame.columns:
        status = (
            frame["universe_snapshot_status"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
        )
    else:
        status = pd.Series("", index=frame.index, dtype=object)

    if "universe_snapshot_reason" in frame.columns:
        reason = (
            frame["universe_snapshot_reason"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
        )
    else:
        reason = pd.Series("", index=frame.index, dtype=object)

    verified_mask = status.eq("ELIGIBLE")
    missing_snapshot_mask = (
        ~verified_mask
        & status.isin(_MISSING_UNIVERSE_STATUSES)
        & reason.isin(_MISSING_UNIVERSE_REASONS)
    )
    known_excluded_mask = (~verified_mask) & (~missing_snapshot_mask)

    if "split" in frame.columns:
        model_mask = frame["split"].astype(str).isin(
            ["train", "validation", "test"]
        )
    else:
        model_mask = pd.Series(True, index=frame.index, dtype=bool)

    _record_run_state(
        verified_model_samples=int((verified_mask & model_mask).sum()),
        provisional_model_samples=int((missing_snapshot_mask & model_mask).sum()),
        known_excluded_model_samples=int((known_excluded_mask & model_mask).sum()),
    )

    verified = frame.loc[verified_mask].copy()
    provisional = frame.loc[missing_snapshot_mask].copy()
    if not verified.empty:
        verified["universe_evidence_weight"] = 1.0
    if not provisional.empty:
        weights = pd.to_numeric(
            provisional.get(
                "sample_weight",
                pd.Series(1.0, index=provisional.index, dtype=float),
            ),
            errors="coerce",
        ).fillna(1.0).clip(lower=0.0, upper=1.0)
        provisional["universe_evidence_weight"] = float(
            PROVISIONAL_SAMPLE_WEIGHT_SCALE
        )
        provisional["sample_weight"] = (
            weights * float(PROVISIONAL_SAMPLE_WEIGHT_SCALE)
        ).clip(lower=0.0, upper=1.0)
        provisional["universe_snapshot_status"] = "PROVISIONAL"
        blank_reason = (
            provisional.get(
                "universe_snapshot_reason",
                pd.Series("", index=provisional.index, dtype=object),
            )
            .fillna("")
            .astype(str)
            .str.strip()
            .eq("")
        )
        if blank_reason.any():
            provisional.loc[
                blank_reason, "universe_snapshot_reason"
            ] = "no_point_in_time_snapshot"

    if verified.empty:
        return provisional.sort_index(kind="mergesort")
    if provisional.empty:
        return verified.sort_index(kind="mergesort")
    return pd.concat([verified, provisional], axis=0).sort_index(
        kind="mergesort"
    )


def _decorate_summary(summary: Any, state: dict[str, int]) -> Any:
    verified = int(state.get("verified_model_samples", 0) or 0)
    provisional = int(state.get("provisional_model_samples", 0) or 0)
    excluded = int(state.get("known_excluded_model_samples", 0) or 0)

    setattr(
        summary,
        "production_backtest_activation_version",
        PRODUCTION_BACKTEST_ACTIVATION_VERSION,
    )
    setattr(summary, "provisional_universe_samples", provisional)
    setattr(
        summary,
        "provisional_universe_weight_scale",
        float(PROVISIONAL_SAMPLE_WEIGHT_SCALE),
    )
    setattr(summary, "historical_universe_excluded_samples", excluded)

    if provisional <= 0:
        return summary

    summary.universe_verified_samples = verified
    summary.universe_unverified_samples = provisional
    summary.survivorship_bias_warning = True
    if bool(getattr(summary, "by_ticker", [])):
        summary.ranking_calibration_status = (
            "ENABLED_MIXED_POINT_IN_TIME_PROVISIONAL"
            if verified > 0
            else "ENABLED_PROVISIONAL_UNVERIFIED_UNIVERSE"
        )
    else:
        summary.ranking_calibration_status = (
            "DISABLED_INSUFFICIENT_PROVISIONAL_TEST_SAMPLES"
        )

    summary.universe_type = (
        "point_in_time_plus_provisional_missing_snapshot"
        if verified > 0
        else "provisional_missing_point_in_time_snapshot"
    )
    summary.current_pool_selection_warning = (
        "历史时点股票池证据缺失的样本仅以25% evidence weight参与校准；"
        "该折扣在交易日聚类去重之后保留，不会被横截面归一化抵消。"
        "已明确历史不合格的样本继续排除，并保留幸存者偏差警告。"
    )
    return summary


def install(analytics_module: Any, main_module: Any) -> None:
    """Install the production calibration lane into analytics and main runtime."""
    global _INSTALLED, _ORIGINAL_RUN_HISTORICAL_BACKTEST
    if _INSTALLED:
        main_module.run_historical_backtest = analytics_module.run_historical_backtest
        return

    _ORIGINAL_RUN_HISTORICAL_BACKTEST = analytics_module.run_historical_backtest
    _core._verified_point_in_time_frame = _production_point_in_time_frame

    def run_historical_backtest(*args: Any, **kwargs: Any) -> Any:
        _reset_run_state()
        summary = _ORIGINAL_RUN_HISTORICAL_BACKTEST(*args, **kwargs)
        return _decorate_summary(summary, _run_state_snapshot())

    run_historical_backtest.__name__ = "run_historical_backtest"
    run_historical_backtest.__doc__ = (
        "Run canonical historical backtest with v94 provisional PIT calibration."
    )

    analytics_module.run_historical_backtest = run_historical_backtest
    main_module.run_historical_backtest = run_historical_backtest
    analytics_module.PRODUCTION_BACKTEST_ACTIVATION_VERSION = (
        PRODUCTION_BACKTEST_ACTIVATION_VERSION
    )
    main_module.PRODUCTION_BACKTEST_ACTIVATION_VERSION = (
        PRODUCTION_BACKTEST_ACTIVATION_VERSION
    )
    _core.PRODUCTION_BACKTEST_ACTIVATION_VERSION = (
        PRODUCTION_BACKTEST_ACTIVATION_VERSION
    )
    _INSTALLED = True
