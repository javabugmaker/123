"""Production backtest activation v93/v97.

Historical point-in-time universe snapshots remain the preferred calibration
evidence. When an old signal predates the locally recorded universe snapshots,
the sample is not promoted to fully verified evidence: it enters a provisional
lane with a reduced sample weight. Samples explicitly known outside the
historical universe remain excluded.

The strict point-in-time helper remains a stable research API. Provisional
admission and WAIT_PULLBACK conditional execution are scoped to production
backtest transactions. Spawned workers install conditional execution in their
own process. Legacy/test executors that predate the profile-aware contract are
called unchanged instead of being wrapped twice.
"""

from __future__ import annotations

import inspect
import threading
from typing import Any

import pandas as pd

import analytics_core as _core
import conditional_fill_v96 as _conditional_fill

PRODUCTION_BACKTEST_ACTIVATION_VERSION = (
    "2026-08-23-v97-provisional-pit-conditional-fill-capability-v5"
)
PROVISIONAL_SAMPLE_WEIGHT_SCALE = 0.25
_MISSING_UNIVERSE_REASONS = frozenset(
    {"", "no_point_in_time_snapshot", "snapshot_starts_after_signal"}
)
_MISSING_UNIVERSE_STATUSES = frozenset(
    {"", "UNAVAILABLE", "UNKNOWN", "MISSING", "UNVERIFIED", "PROVISIONAL"}
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
    _RUN_STATE.update(
        {
            "verified_model_samples": int(verified_model_samples),
            "provisional_model_samples": int(provisional_model_samples),
            "known_excluded_model_samples": int(known_excluded_model_samples),
        }
    )


def _run_state_snapshot() -> dict[str, int]:
    return dict(_RUN_STATE)


def _production_point_in_time_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return verified evidence plus conservatively weighted missing-PIT rows."""
    if frame.empty:
        _record_run_state(
            verified_model_samples=0,
            provisional_model_samples=0,
            known_excluded_model_samples=0,
        )
        return frame.copy()

    status = (
        frame.get(
            "universe_snapshot_status",
            pd.Series("", index=frame.index, dtype=object),
        )
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )
    reason = (
        frame.get(
            "universe_snapshot_reason",
            pd.Series("", index=frame.index, dtype=object),
        )
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
    )

    verified_mask = status.eq("ELIGIBLE")
    missing_snapshot_mask = (
        ~verified_mask
        & status.isin(_MISSING_UNIVERSE_STATUSES)
        & reason.isin(_MISSING_UNIVERSE_REASONS)
    )
    known_excluded_mask = (~verified_mask) & (~missing_snapshot_mask)
    model_mask = (
        frame["split"].astype(str).isin(["train", "validation", "test"])
        if "split" in frame.columns
        else pd.Series(True, index=frame.index, dtype=bool)
    )

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
        provisional.loc[
            blank_reason, "universe_snapshot_reason"
        ] = "no_point_in_time_snapshot"

    if verified.empty:
        return provisional.sort_index(kind="mergesort")
    if provisional.empty:
        return verified.sort_index(kind="mergesort")
    return pd.concat([verified, provisional], axis=0).sort_index(kind="mergesort")


def _supports_conditional_executor(function: Any) -> bool:
    """Require the modern profile-aware scalar executor before wrapping it."""
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return False
    parameters = signature.parameters
    return "profile" in parameters or any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
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
    setattr(
        summary,
        "conditional_fill_version",
        _conditional_fill.CONDITIONAL_FILL_VERSION,
    )

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
    """Install the production calibration/execution lane into runtime."""
    global _INSTALLED, _ORIGINAL_RUN_HISTORICAL_BACKTEST
    if _INSTALLED:
        main_module.run_historical_backtest = analytics_module.run_historical_backtest
        return

    _ORIGINAL_RUN_HISTORICAL_BACKTEST = analytics_module.run_historical_backtest

    def run_historical_backtest(*args: Any, **kwargs: Any) -> Any:
        with _LOCK:
            _reset_run_state()
            previous_verified = _core._verified_point_in_time_frame
            _core._verified_point_in_time_frame = _production_point_in_time_frame
            conditional_installed = _supports_conditional_executor(
                _core._backtest_one_ticker
            )
            if conditional_installed:
                _conditional_fill.install()
            try:
                summary = _ORIGINAL_RUN_HISTORICAL_BACKTEST(*args, **kwargs)
            finally:
                if conditional_installed:
                    _conditional_fill.uninstall()
                _core._verified_point_in_time_frame = previous_verified
            return _decorate_summary(summary, _run_state_snapshot())

    run_historical_backtest.__name__ = "run_historical_backtest"
    run_historical_backtest.__doc__ = (
        "Run canonical historical backtest with provisional PIT and conditional fill."
    )
    run_historical_backtest.__module__ = getattr(
        _ORIGINAL_RUN_HISTORICAL_BACKTEST, "__module__", "analytics"
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
