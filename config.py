"""InstitutionScanner v52 configuration facade.

v52 keeps the v51 numerical score chain intact while tightening three runtime
integrity contracts discovered from a real 2026-08-17 full-universe scan:
security price-limit rules, setup-backed breakout overrides, and ETF market-cap
provenance.  The prior v51 defaults remain in ``config_v51``.
"""

from __future__ import annotations

import config_v51 as _v51
from config_v51 import *  # noqa: F403

SCORING_VERSION: str = (
    "2026-08-17-v52-setup-backed-breakout-" + _v51.SCORING_VERSION
)
PIPELINE_VERSION: str = (
    "2026-08-17-v52-price-limit-marketcap-contract-" + _v51.PIPELINE_VERSION
)
DECISION_INTEGRITY_VERSION: str = (
    "2026-08-17-v52-setup-backed-filter-override-"
    + _v51.DECISION_INTEGRITY_VERSION
)
OUTPUT_CONTRACT_VERSION: str = (
    "2026-08-17-v52-price-limit-source-marketcap-applicability-"
    + _v51.OUTPUT_CONTRACT_VERSION
)
MARKET_DATA_VERSION: str = (
    "2026-08-17-v52-explicit-limit-rules-" + _v51.MARKET_DATA_VERSION
)
BACKTEST_PROVENANCE_VERSION: str = (
    "2026-08-17-v52-date-aware-limit-rules-" + _v51.BACKTEST_PROVENANCE_VERSION
)

# A breakout may bypass the normal setup gate only when it still carries at
# least one independent accumulation/structure clue and at least three total
# diagnostics.  Current-day CMF + AD alone are event confirmation, not a setup.
FILTER_OVERRIDE_MIN_SIGNAL_COUNT: int = 3

# Explicit provenance for output/backtest audit trails.
PRICE_LIMIT_RULE_VERSION: str = "2026-08-17-v52-exchange-rule"
