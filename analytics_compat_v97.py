"""Legacy public version markers plus v102 calibration governance bootstrap.

Executor/cache signature compatibility belongs to the actual transaction owners.
This small compatibility module remains the final bootstrap hook in analytics.py,
so v102 uses it to install fail-closed calibration governance after v97
calibration mathematics has been composed.
"""

from __future__ import annotations

import analytics_core as _core
import calibration_governance_v102 as _calibration_governance

ANALYTICS_COMPAT_VERSION = "2026-08-24-v102-calibration-governance-bootstrap-v1"
LEGACY_PERFORMANCE_ENGINE_VERSION = (
    "2026-08-20-v80-vectorized-backtest-workstation-v1"
)


def install() -> None:
    """Preserve version markers and install the final calibration guard."""
    _core.PERFORMANCE_ENGINE_VERSION = LEGACY_PERFORMANCE_ENGINE_VERSION
    _core.ANALYTICS_COMPAT_VERSION = ANALYTICS_COMPAT_VERSION
    _calibration_governance.install(_core)
