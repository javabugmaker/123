"""Legacy public version markers for the canonical analytics runtime.

Executor/cache signature compatibility now belongs to the actual transaction
owners (calibration_weight_cache_v79 and production activation). This module is
kept only because historical imports may still reference its name. It performs
no wrapping and adds no runtime call layer.
"""

from __future__ import annotations

import analytics_core as _core

ANALYTICS_COMPAT_VERSION = "2026-08-23-v97-marker-only-compat-v2"
LEGACY_PERFORMANCE_ENGINE_VERSION = (
    "2026-08-20-v80-vectorized-backtest-workstation-v1"
)


def install() -> None:
    """Preserve stable public version identifiers without wrapping execution."""
    _core.PERFORMANCE_ENGINE_VERSION = LEGACY_PERFORMANCE_ENGINE_VERSION
    _core.ANALYTICS_COMPAT_VERSION = ANALYTICS_COMPAT_VERSION
