"""Compatibility bootstrap routed through the canonical reliability package.

The legacy calibration overlays remain stable compatibility layers. New model
contracts, point-in-time held-out scoping, shadow challengers and hierarchical
evidence live under the canonical ``institution_scanner`` package so future
work does not add another root vXX monkey-patch module.
"""

from __future__ import annotations

import analytics_core as _core
import calibration_governance_v102 as _calibration_governance
import calibration_semantics_v102_1 as _calibration_semantics
from institution_scanner import pit_counts as _pit_counts
from institution_scanner import point_in_time_backtest as _pit_backtest
from institution_scanner import reliability as _reliability

ANALYTICS_COMPAT_VERSION = "2026-08-25-v106.5-pit-count-provenance-bootstrap-v1"
LEGACY_PERFORMANCE_ENGINE_VERSION = (
    "2026-08-20-v80-vectorized-backtest-workstation-v1"
)


def install() -> None:
    """Install PIT scope, count repair, governance, narrative and shadow diagnostics."""
    _core.PERFORMANCE_ENGINE_VERSION = LEGACY_PERFORMANCE_ENGINE_VERSION
    _core.ANALYTICS_COMPAT_VERSION = ANALYTICS_COMPAT_VERSION
    _pit_backtest.install(_core)
    _pit_counts.install()
    _calibration_governance.install(_core)
    _calibration_semantics.install(_core)
    _reliability.install(_core)
