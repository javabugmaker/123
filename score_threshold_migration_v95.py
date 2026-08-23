"""v95 absolute-threshold migration for the restored score scale.

The legacy setup dimensions could contribute about 95 positive points although
the model documented a 100-point setup scale.  v95 restores the missing five
points.  Absolute thresholds therefore need a deterministic scale migration,
otherwise merely fixing the ruler would make more rows cross old numeric gates.

Two domains are kept distinct:

* raw Setup/``Score`` gates scale by 100/95;
* institutional/final-score gates scale by 100/97 because the default model
  uses 60% Setup + 25% Trigger + 15% Execution, making the old attainable
  composite maximum roughly 0.60*95 + 0.25*100 + 0.15*100 = 97.

Percentile thresholds are intentionally unchanged.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

SETUP_THRESHOLD_MIGRATION_FACTOR = 100.0 / 95.0
FINAL_THRESHOLD_MIGRATION_FACTOR = 100.0 / 97.0
LEGACY_SETUP_ACTIVE_THRESHOLD = 35.0
SETUP_ACTIVE_THRESHOLD = round(
    LEGACY_SETUP_ACTIVE_THRESHOLD * SETUP_THRESHOLD_MIGRATION_FACTOR, 4
)

LEGACY_TIER_A = 35.0
LEGACY_TIER_B = 30.0
LEGACY_TIER_C = 25.0
TIER_A = round(LEGACY_TIER_A * FINAL_THRESHOLD_MIGRATION_FACTOR, 4)
TIER_B = round(LEGACY_TIER_B * FINAL_THRESHOLD_MIGRATION_FACTOR, 4)
TIER_C = round(LEGACY_TIER_C * FINAL_THRESHOLD_MIGRATION_FACTOR, 4)

SCORE_THRESHOLD_MIGRATION_VERSION = (
    "2026-08-23-v95-attainable-score-space-threshold-migration-v1"
)

_INSTALLED = False


def install(config_module: Any, lifecycle_module: Any | None = None) -> None:
    """Install migrated constants and, when available, the raw setup gate."""
    global _INSTALLED
    config_module.SETUP_THRESHOLD_MIGRATION_FACTOR = SETUP_THRESHOLD_MIGRATION_FACTOR
    config_module.FINAL_THRESHOLD_MIGRATION_FACTOR = FINAL_THRESHOLD_MIGRATION_FACTOR
    config_module.SIGNAL_LIFECYCLE_MIN_SETUP_SCORE = SETUP_ACTIVE_THRESHOLD
    config_module.INSTITUTIONAL_TIER_A_SCORE = TIER_A
    config_module.INSTITUTIONAL_TIER_B_SCORE = TIER_B
    config_module.INSTITUTIONAL_TIER_C_SCORE = TIER_C
    config_module.TRADE_READY_MIN_INSTITUTIONAL_SCORE = TIER_C
    config_module.INSTITUTIONAL_SCORE_TIERS = (
        ("A级机构启动", TIER_A),
        ("B级观察", TIER_B),
        ("C级价值观察", TIER_C),
    )
    config_module.SCORE_THRESHOLD_MIGRATION_VERSION = SCORE_THRESHOLD_MIGRATION_VERSION

    if lifecycle_module is None:
        return
    lifecycle_module.INSTITUTIONAL_TIER_A_SCORE = TIER_A
    lifecycle_module.INSTITUTIONAL_TIER_B_SCORE = TIER_B
    lifecycle_module.INSTITUTIONAL_TIER_C_SCORE = TIER_C
    lifecycle_module.TRADE_READY_MIN_INSTITUTIONAL_SCORE = TIER_C

    def is_active(frame: pd.DataFrame) -> pd.Series:
        score = lifecycle_module._number(
            frame.get("Score", pd.Series(0.0, index=frame.index)),
            0.0,
        )
        signals = lifecycle_module._number(
            frame.get("SignalCount", pd.Series(0.0, index=frame.index)),
            0.0,
        )
        passed = lifecycle_module._bool_series(frame, "PassedFilters", False)
        eligible = lifecycle_module._bool_series(frame, "UniverseEligible", True)
        entry_signal = lifecycle_module._text_series(
            frame, "EntrySignal", "AVOID"
        ).str.upper()
        override = lifecycle_module.strict_filter_override_mask(
            frame,
            signal=entry_signal,
            passed_filters=passed,
            universe_eligible=eligible,
        )
        return passed | (
            score.ge(float(SETUP_ACTIVE_THRESHOLD)) & signals.ge(3.0)
        ) | override

    lifecycle_module._is_active = is_active
    lifecycle_module.SCORE_THRESHOLD_MIGRATION_VERSION = (
        SCORE_THRESHOLD_MIGRATION_VERSION
    )
    _INSTALLED = True
