"""Compatibility bootstrap routed through the canonical reliability package.

The legacy calibration overlays remain stable compatibility layers. New model
contracts, shadow challengers and hierarchical evidence live under the canonical
``institution_scanner`` package so future work does not add another root vXX
monkey-patch module.
"""

from __future__ import annotations

import analytics_core as _core
import calibration_governance_v102 as _calibration_governance
import calibration_semantics_v102_1 as _calibration_semantics
from institution_scanner import reliability as _reliability

ANALYTICS_COMPAT_VERSION = "2026-08-24-v105-canonical-reliability-bootstrap-v1"
LEGACY_PERFORMANCE_ENGINE_VERSION = (
    "2026-08-20-v80-vectorized-backtest-workstation-v1"
)


def install() -> None:
    """Install stable governance, narrative alignment and shadow diagnostics."""
    _core.PERFORMANCE_ENGINE_VERSION = LEGACY_PERFORMANCE_ENGINE_VERSION
    _core.ANALYTICS_COMPAT_VERSION = ANALYTICS_COMPAT_VERSION
    _calibration_governance.install(_core)
    _calibration_semantics.install(_core)
    _reliability.install(_core)
