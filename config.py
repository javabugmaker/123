"""InstitutionScanner v60 execution-date integrity configuration facade.

TickFlow Free remains the sole market-data client. v55 added bounded post-close
daily-bar settlement retry; v56 refreshes benchmark market data before backtest
freshness is evaluated; v57 governs unstable peer calibration; v58 closes stale
market-data execution gaps and duplicate logger propagation; v59 makes scan
resume crash-safe and binds it to the market/fundamental/universe input state;
v60 rejects any data date later than the latest completed A-share session from
execution freshness.

Technical scoring, backtest split policy and research ranking weights are
unchanged.
"""

from __future__ import annotations

import config_v51 as _v51
from config_v51 import *  # noqa: F403

SCORING_VERSION: str = (
    "2026-08-17-v52-setup-backed-breakout-" + _v51.SCORING_VERSION
)
PIPELINE_VERSION: str = (
    "2026-08-19-v60-future-eod-fail-closed-"
    "2026-08-19-v59-snapshot-safe-resume-"
    "2026-08-19-v58-stale-data-fail-closed-"
    "2026-08-19-v57-unstable-calibration-governance-"
    "2026-08-19-v56-fresh-benchmark-audit-"
    "2026-08-19-v55-free-eod-settlement-retry-"
    "2026-08-18-v54-execution-liquidity-readiness-"
    "2026-08-18-v53-provider-settlement-date-gate-"
    "2026-08-17-v52-price-limit-marketcap-contract-"
    + _v51.PIPELINE_VERSION
)
DECISION_INTEGRITY_VERSION: str = (
    "2026-08-19-v60-future-date-execution-gate-"
    "2026-08-19-v58-current-data-execution-gate-"
    "2026-08-18-v54-trade-ready-liquidity-gate-"
    "2026-08-17-v52-setup-backed-filter-override-"
    + _v51.DECISION_INTEGRITY_VERSION
)
OUTPUT_CONTRACT_VERSION: str = (
    "2026-08-19-v58-trade-freshness-diagnostics-"
    "2026-08-19-v57-calibration-stability-evidence-"
    "2026-08-18-v54-trade-liquidity-board-diagnostics-"
    "2026-08-18-v53-effective-market-date-provenance-"
    "2026-08-17-v52-price-limit-source-marketcap-applicability-"
    + _v51.OUTPUT_CONTRACT_VERSION
)
MARKET_DATA_VERSION: str = (
    "2026-08-19-v58-bounded-provider-lag-"
    "2026-08-19-v55-free-eod-settlement-retry-"
    "2026-08-17-v52-explicit-limit-rules-"
    + _v51.MARKET_DATA_VERSION
)
BACKTEST_PROVENANCE_VERSION: str = (
    "2026-08-19-v57-unstable-peer-confidence-shrink-"
    "2026-08-19-v56-benchmark-refresh-peer-evidence-audit-"
    "2026-08-17-v52-date-aware-limit-rules-"
    + _v51.BACKTEST_PROVENANCE_VERSION
)


def setup_logging(*args, **kwargs):
    """Return the shared logger with duplicate parent propagation disabled."""
    logger = _v51.setup_logging(*args, **kwargs)
    # setup_logging gives each scanner module its own console/file handlers.
    # Letting such a child also propagate to ``institution_scanner`` emits the
    # same record again when the parent logger is configured by CLI/GUI.
    logger.propagate = False
    return logger


# A breakout may bypass the normal setup gate only when it still carries at
# least one independent accumulation/structure clue and at least three total
# diagnostics. Current-day CMF + AD alone are event confirmation, not a setup.
FILTER_OVERRIDE_MIN_SIGNAL_COUNT: int = 3

# DAILY EOD settlement contract. TickFlow Free may be uniformly one trading day
# behind during its post-close settlement window, but partial/mixed settlement
# or a coherent lag beyond this bound is never accepted for canonical analysis.
DAILY_MAX_PROVIDER_LAG_TRADING_DAYS: int = 1
DAILY_MIN_COHERENT_DATA_DATE_RATIO: float = 0.90

# v54 execution-only liquidity contract. The broad research universe keeps its
# existing 2.5m CNY turnover floor from v51; a READY/CAUTIOUS signal needs at
# least 5m CNY median 60-day turnover and must keep the assumed 50k order at or
# below 1% participation. Neither condition changes RankingScore.
TRADE_READY_MIN_MEDIAN_TURNOVER_60D: float = 5_000_000.0
TRADE_READY_MAX_ASSUMED_PARTICIPATION_RATE: float = 0.01
TRADE_LIQUIDITY_RULE_VERSION: str = "2026-08-18-v54-order-participation"

# v58/v60 execution freshness contract. Research rows may remain visible when
# provider data is delayed, but an immediate trading recommendation must be
# based exactly on the latest completed session. Future-dated/intraday evidence
# is invalid rather than equivalent to age zero.
TRADE_READY_MAX_DATA_AGE_TRADING_DAYS: int = 0
TRADE_FRESHNESS_RULE_VERSION: str = "2026-08-19-v60-completed-session-only"

# v59 resume contract. Checkpoint files are reusable only when runtime versions,
# completed session, per-ticker OHLCV, AkShare fundamental cache and TickFlow
# universe metadata are unchanged. Legacy ticker-only checkpoints fail closed.
CHECKPOINT_RESUME_VERSION: str = "2026-08-19-v59-snapshot-input-fingerprint-v2"

# Explicit provenance for output/backtest audit trails.
PRICE_LIMIT_RULE_VERSION: str = "2026-08-17-v52-exchange-rule"
CALIBRATION_GOVERNANCE_VERSION: str = "2026-08-19-v57-unstable-stable-ratio-shrink-v1"
