"""InstitutionScanner v62 cache-history integrity configuration facade.

TickFlow Free remains the sole market-data client. v55 added bounded post-close
daily-bar settlement retry; v56 refreshes benchmark market data before backtest
freshness is evaluated; v57 governs unstable peer calibration; v58 closes stale
market-data execution gaps and duplicate logger propagation; v59 makes scan
resume crash-safe and binds it to market/fundamental/universe input state; v60
rejects dates later than the latest completed A-share session; v61 prevents a
zero-row AkShare outage from advancing cache freshness; v62 fingerprints the
full OHLCV history so older provider revisions invalidate derived indicators and
backtests instead of being mistaken for a pure append.

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
    "2026-08-19-v62-full-history-cache-integrity-"
    "2026-08-19-v61-fundamental-freshness-integrity-"
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
    "2026-08-19-v62-full-history-revision-fingerprint-"
    "2026-08-19-v58-bounded-provider-lag-"
    "2026-08-19-v55-free-eod-settlement-retry-"
    "2026-08-17-v52-explicit-limit-rules-"
    + _v51.MARKET_DATA_VERSION
)
BACKTEST_PROVENANCE_VERSION: str = (
    "2026-08-19-v62-history-revision-cache-v10-"
    "2026-08-19-v57-unstable-peer-confidence-shrink-"
    "2026-08-19-v56-benchmark-refresh-peer-evidence-audit-"
    "2026-08-17-v52-date-aware-limit-rules-"
    + _v51.BACKTEST_PROVENANCE_VERSION
)


def setup_logging(*args, **kwargs):
    """Return the shared logger with duplicate parent propagation disabled."""
    logger = _v51.setup_logging(*args, **kwargs)
    logger.propagate = False
    return logger


FILTER_OVERRIDE_MIN_SIGNAL_COUNT: int = 3
DAILY_MAX_PROVIDER_LAG_TRADING_DAYS: int = 1
DAILY_MIN_COHERENT_DATA_DATE_RATIO: float = 0.90

TRADE_READY_MIN_MEDIAN_TURNOVER_60D: float = 5_000_000.0
TRADE_READY_MAX_ASSUMED_PARTICIPATION_RATE: float = 0.01
TRADE_LIQUIDITY_RULE_VERSION: str = "2026-08-18-v54-order-participation"

TRADE_READY_MAX_DATA_AGE_TRADING_DAYS: int = 0
TRADE_FRESHNESS_RULE_VERSION: str = "2026-08-19-v60-completed-session-only"

CHECKPOINT_RESUME_VERSION: str = "2026-08-19-v59-snapshot-input-fingerprint-v2"
FUNDAMENTAL_REFRESH_INTEGRITY_VERSION: str = "2026-08-19-v61-zero-row-stays-stale-v1"
CACHE_HISTORY_INTEGRITY_VERSION: str = "2026-08-19-v62-full-ohlcv-fingerprint-v1"

PRICE_LIMIT_RULE_VERSION: str = "2026-08-17-v52-exchange-rule"
CALIBRATION_GOVERNANCE_VERSION: str = "2026-08-19-v57-unstable-stable-ratio-shrink-v1"
