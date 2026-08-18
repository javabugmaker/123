"""InstitutionScanner v53 configuration facade.

v53 keeps the v52 scoring and execution rules intact while hardening the DAILY
market-data settlement contract. A coherent one-trading-day provider EOD lag
can be published with explicit provenance; mixed-date and materially stale
universes remain fail-closed. The v52 runtime rules remain layered over v51.
"""

from __future__ import annotations

import config_v51 as _v51
from config_v51 import *  # noqa: F403

SCORING_VERSION: str = (
    "2026-08-17-v52-setup-backed-breakout-" + _v51.SCORING_VERSION
)
PIPELINE_VERSION: str = (
    "2026-08-18-v53-provider-settlement-date-gate-"
    "2026-08-17-v52-price-limit-marketcap-contract-"
    + _v51.PIPELINE_VERSION
)
DECISION_INTEGRITY_VERSION: str = (
    "2026-08-17-v52-setup-backed-filter-override-"
    + _v51.DECISION_INTEGRITY_VERSION
)
OUTPUT_CONTRACT_VERSION: str = (
    "2026-08-18-v53-effective-market-date-provenance-"
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
# diagnostics. Current-day CMF + AD alone are event confirmation, not a setup.
FILTER_OVERRIDE_MIN_SIGNAL_COUNT: int = 3

# DAILY EOD settlement contract. The provider may be uniformly one trading day
# behind the exchange calendar, but partial/mixed settlement is never accepted.
DAILY_MAX_PROVIDER_LAG_TRADING_DAYS: int = 1
DAILY_MIN_COHERENT_DATA_DATE_RATIO: float = 0.90

# Explicit provenance for output/backtest audit trails.
PRICE_LIMIT_RULE_VERSION: str = "2026-08-17-v52-exchange-rule"
