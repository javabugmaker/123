"""Explicit inventory of legacy runtime overlays still on the production path.

This is observability, not an installer. Canonicalization is complete only when
an overlay can be removed after golden-equivalence tests; keeping the inventory
explicit prevents compatibility debt from becoming invisible again.
"""
from __future__ import annotations

from typing import Final

RUNTIME_INVENTORY_VERSION: Final = "2026-09-01-v110-runtime-overlay-inventory-v2"

LEGACY_ANALYTICS_OVERLAYS: Final[tuple[str, ...]] = (
    "indicator_acceleration_v77",
    "cache_acceleration_v77",
    "universe_cache_acceleration_v78",
    "backtest_acceleration_v77",
    "analytics_acceleration_v77",
    "score_runtime_v97",
    "calibration_weight_cache_v79",
    "backtest_fastpath_v78",
    "backtest_fastscore_v80",
    "backtest_profile_alignment_v95",
    "scoring_consistency_v94",
    "backtest_rank_integrity_v82",
    "backtest_math_integrity_v94",
    "calibration_math_v96",
    "analytics_compat_v97",
)

LEGACY_SCAN_OVERLAYS: Final[tuple[str, ...]] = (
    "checkpoint_inputs_v59",
    "scanner_resume_v59",
    "scanner_resume_v68",
)


def runtime_inventory() -> dict[str, object]:
    analytics = list(LEGACY_ANALYTICS_OVERLAYS)
    scan = list(LEGACY_SCAN_OVERLAYS)
    return {
        "version": RUNTIME_INVENTORY_VERSION,
        "legacy_overlay_count": len(analytics) + len(scan),
        "analytics_overlays": analytics,
        "scan_overlays": scan,
        "migration_policy": "GOLDEN_EQUIVALENCE_BEFORE_REMOVAL",
        "new_root_overlays_allowed": False,
    }
