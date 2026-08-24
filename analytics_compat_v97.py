"""Legacy public version markers plus v102 calibration governance bootstrap.

Executor/cache signature compatibility belongs to the actual transaction owners.
This small compatibility module remains the final bootstrap hook in analytics.py.
v102 installs fail-closed calibration governance; v102.1 aligns public narrative
fields with the effective governed weights without changing model mathematics.
"""

from __future__ import annotations

import analytics_core as _core
import calibration_governance_v102 as _calibration_governance
import calibration_semantics_v102_1 as _calibration_semantics

ANALYTICS_COMPAT_VERSION = "2026-08-24-v102.1-calibration-semantics-bootstrap-v1"
LEGACY_PERFORMANCE_ENGINE_VERSION = (
    "2026-08-20-v80-vectorized-backtest-workstation-v1"
)


def install() -> None:
    """Preserve version markers and install governance plus narrative alignment."""
    _core.PERFORMANCE_ENGINE_VERSION = LEGACY_PERFORMANCE_ENGINE_VERSION
    _core.ANALYTICS_COMPAT_VERSION = ANALYTICS_COMPAT_VERSION
    _calibration_governance.install(_core)
    _calibration_semantics.install(_core)
