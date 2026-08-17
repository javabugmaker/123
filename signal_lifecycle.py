"""v52 lifecycle policy facade.

The v51 ranking/decision engine remains in ``signal_lifecycle_v51``.  v52
narrows the breakout filter override after real-output validation showed that
current-day CMF + AD confirmation alone could bypass every accumulation and
structure setup filter.  A strict override now needs at least one independent
setup clue and three total scanner signals.

Legacy synthetic/archived rows that predate the v52 setup-evidence schema stay
readable under the previous override contract.  Current scanner output always
carries the setup fields, so production v52 decisions remain fail-closed.
"""

from __future__ import annotations

import sys

import pandas as pd

import config as _config
import signal_lifecycle_v51 as _core
from signal_lifecycle_v51 import *  # noqa: F403

_SETUP_COLUMNS = (
    "VolAccum",
    "volume_accumulation",
    "OBV_Div",
    "obv_divergence",
    "Consolidation",
    "consolidation",
    "VolContract",
    "volatility_contraction",
)


def _has_v52_setup_schema(frame: pd.DataFrame) -> bool:
    return "SignalCount" in frame.columns and any(
        column in frame.columns for column in _SETUP_COLUMNS
    )


def _setup_support_mask(frame: pd.DataFrame) -> pd.Series:
    # Historical result files and older unit fixtures legitimately do not have
    # the v52 setup columns.  Preserve their old interpretation instead of
    # manufacturing a failure from missing future-schema fields.
    if not _has_v52_setup_schema(frame):
        return pd.Series(True, index=frame.index, dtype=bool)

    accumulation = (
        _core._bool_series(frame, "VolAccum", False)
        | _core._bool_series(frame, "volume_accumulation", False)
        | _core._bool_series(frame, "OBV_Div", False)
        | _core._bool_series(frame, "obv_divergence", False)
    )
    structure = (
        _core._bool_series(frame, "Consolidation", False)
        | _core._bool_series(frame, "consolidation", False)
        | _core._bool_series(frame, "VolContract", False)
        | _core._bool_series(frame, "volatility_contraction", False)
    )
    signal_count = _core._number(
        frame.get("SignalCount", pd.Series(0.0, index=frame.index)),
        0.0,
    )
    minimum = int(getattr(_config, "FILTER_OVERRIDE_MIN_SIGNAL_COUNT", 3))
    return (accumulation | structure) & signal_count.ge(minimum)


def strict_filter_override_mask(
    frame: pd.DataFrame,
    signal: pd.Series | None = None,
    passed_filters: pd.Series | None = None,
    universe_eligible: pd.Series | None = None,
) -> pd.Series:
    """Allow only setup-backed, fully confirmed current-schema overrides."""
    normalized_signal = (
        signal.fillna("AVOID").astype(str).str.strip().str.upper()
        if signal is not None
        else _core._text_series(frame, "EntrySignal", "AVOID").str.upper()
    )
    passed = (
        passed_filters.map(_core._bool)
        if passed_filters is not None
        else _core._bool_series(frame, "PassedFilters", True)
    )
    eligible = (
        universe_eligible.map(_core._bool)
        if universe_eligible is not None
        else _core._bool_series(frame, "UniverseEligible", True)
    )
    terminal, weakening = _core._lifecycle_risk_masks(frame)
    return (
        ~passed
        & eligible
        & normalized_signal.eq("BREAKOUT_CONFIRM")
        & _core._bool_series(frame, "BreakoutVolumeConfirmed", False)
        & _core._bool_series(frame, "BreakoutFlowConfirmed", False)
        & _core._breakout_confirmation_ok(frame, normalized_signal)
        & _setup_support_mask(frame)
        & ~terminal
        & ~weakening
    )


def _is_active(frame: pd.DataFrame) -> pd.Series:
    """Lifecycle activity must use the same canonical override as ranking."""
    score = _core._number(
        frame.get("Score", pd.Series(0.0, index=frame.index)),
        0.0,
    )
    signals = _core._number(
        frame.get("SignalCount", pd.Series(0.0, index=frame.index)),
        0.0,
    )
    passed = _core._bool_series(frame, "PassedFilters", False)
    eligible = _core._bool_series(frame, "UniverseEligible", True)
    entry_signal = _core._text_series(frame, "EntrySignal", "AVOID").str.upper()
    override = strict_filter_override_mask(
        frame,
        signal=entry_signal,
        passed_filters=passed,
        universe_eligible=eligible,
    )
    return passed | ((score >= 35.0) & (signals >= 3.0)) | override


_core.strict_filter_override_mask = strict_filter_override_mask
_core._is_active = _is_active
sys.modules[__name__] = _core
