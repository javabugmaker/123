"""InstitutionScanner v56 configuration facade.

v56 keeps the v52 scoring and v54 execution-liquidity contracts intact while
making TickFlow authenticated credentials a first-class local GUI setting.  The
v55 post-close quote repair remains the settlement fallback.
"""

from __future__ import annotations

import config_v51 as _v51
from config_v51 import *  # noqa: F403

SCORING_VERSION: str = (
    "2026-08-17-v52-setup-backed-breakout-" + _v51.SCORING_VERSION
)
PIPELINE_VERSION: str = (
    "2026-08-19-v56-gui-tickflow-auth-"
    "2026-08-19-v55-authenticated-eod-close-fallback-"
    "2026-08-18-v54-execution-liquidity-readiness-"
    "2026-08-18-v53-provider-settlement-date-gate-"
    "2026-08-17-v52-price-limit-marketcap-contract-"
    + _v51.PIPELINE_VERSION
)
DECISION_INTEGRITY_VERSION: str = (
    "2026-08-18-v54-trade-ready-liquidity-gate-"
    "2026-08-17-v52-setup-backed-filter-override-"
    + _v51.DECISION_INTEGRITY_VERSION
)
OUTPUT_CONTRACT_VERSION: str = (
    "2026-08-18-v54-trade-liquidity-board-diagnostics-"
    "2026-08-18-v53-effective-market-date-provenance-"
    "2026-08-17-v52-price-limit-source-marketcap-applicability-"
    + _v51.OUTPUT_CONTRACT_VERSION
)
MARKET_DATA_VERSION: str = (
    "2026-08-19-v56-local-api-credentials-"
    "2026-08-19-v55-authenticated-eod-quotes-"
    "2026-08-17-v52-explicit-limit-rules-"
    + _v51.MARKET_DATA_VERSION
)
BACKTEST_PROVENANCE_VERSION: str = (
    "2026-08-17-v52-date-aware-limit-rules-" + _v51.BACKTEST_PROVENANCE_VERSION
)

# TickFlow credential contract.  The actual API key is never defined here;
# GUI-local credentials live in the gitignored .env.local file.
TICKFLOW_AUTH_ENV_VAR: str = "TICKFLOW_API_KEY"
TICKFLOW_LOCAL_SETTINGS_FILE: str = ".env.local"
TICKFLOW_AUTH_MODE_VERSION: str = "2026-08-19-v56-gui-local-precedence"

# A breakout may bypass the normal setup gate only when it still carries at
# least one independent accumulation/structure clue and at least three total
# diagnostics. Current-day CMF + AD alone are event confirmation, not a setup.
FILTER_OVERRIDE_MIN_SIGNAL_COUNT: int = 3

# DAILY EOD settlement contract. The historical provider may be uniformly one
# trading day behind the exchange calendar. v55/v56 first try an authenticated
# post-close quote repair; if that is unavailable, the existing coherent-lag
# policy remains the fail-safe publication contract.
DAILY_MAX_PROVIDER_LAG_TRADING_DAYS: int = 1
DAILY_MIN_COHERENT_DATA_DATE_RATIO: float = 0.90

# v54 execution-only liquidity contract. The broad research universe keeps its
# existing 2.5m CNY turnover floor from v51; a READY/CAUTIOUS signal needs at
# least 5m CNY median 60-day turnover and must keep the assumed 50k order at or
# below 1% participation. Neither condition changes RankingScore.
TRADE_READY_MIN_MEDIAN_TURNOVER_60D: float = 5_000_000.0
TRADE_READY_MAX_ASSUMED_PARTICIPATION_RATE: float = 0.01
TRADE_LIQUIDITY_RULE_VERSION: str = "2026-08-18-v54-order-participation"

# Explicit provenance for output/backtest audit trails.
PRICE_LIMIT_RULE_VERSION: str = "2026-08-17-v52-exchange-rule"
