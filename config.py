"""InstitutionScanner v72 data/recovery/publication integrity facade.

TickFlow Free remains the sole market-data client. v55-v70 harden settlement,
execution freshness, resume state, fundamental freshness and derived-cache
validity; v64-v66 harden GUI cancellation and ordinary result publication; v71
extends all-or-rollback publication to backtest ranking postprocessing; v72
prevents the standalone cache-only report command from replacing newer results
with materially stale or mixed-date cache state.

Technical scoring, backtest split policy and research ranking weights are
unchanged.
"""

from __future__ import annotations

import sys

import config_v51 as _v51
from config_v51 import *  # noqa: F403

SCORING_VERSION: str = (
    "2026-08-17-v52-setup-backed-breakout-" + _v51.SCORING_VERSION
)
PIPELINE_VERSION: str = (
    "2026-08-19-v72-standalone-cache-report-gate-"
    "2026-08-19-v71-transactional-backtest-publication-"
    "2026-08-19-v70-hard-financial-refresh-coverage-"
    "2026-08-19-v69-same-length-history-content-check-"
    "2026-08-19-v68-pinned-resume-publication-cleanup-"
    "2026-08-19-v67-partial-fundamental-refresh-stays-stale-"
    "2026-08-19-v66-transactional-report-publication-"
    "2026-08-19-v65-cache-first-publication-gate-"
    "2026-08-19-v64-gui-process-tree-cancel-"
    "2026-08-19-v63-benchmark-cache-fail-closed-"
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
    "2026-08-19-v72-cache-report-market-date-gate-"
    "2026-08-19-v71-atomic-backtest-result-set-"
    "2026-08-19-v68-publication-coupled-checkpoint-"
    "2026-08-19-v66-atomic-result-set-rollback-"
    "2026-08-19-v65-cache-first-market-date-contract-"
    "2026-08-19-v58-trade-freshness-diagnostics-"
    "2026-08-19-v57-calibration-stability-evidence-"
    "2026-08-18-v54-trade-liquidity-board-diagnostics-"
    "2026-08-18-v53-effective-market-date-provenance-"
    "2026-08-17-v52-price-limit-source-marketcap-applicability-"
    + _v51.OUTPUT_CONTRACT_VERSION
)
MARKET_DATA_VERSION: str = (
    "2026-08-19-v69-same-length-history-content-check-"
    "2026-08-19-v62-full-history-revision-fingerprint-"
    "2026-08-19-v58-bounded-provider-lag-"
    "2026-08-19-v55-free-eod-settlement-retry-"
    "2026-08-17-v52-explicit-limit-rules-"
    + _v51.MARKET_DATA_VERSION
)
BACKTEST_PROVENANCE_VERSION: str = (
    "2026-08-19-v71-transactional-ranking-publication-"
    "2026-08-19-v69-full-history-cache-verification-"
    "2026-08-19-v63-benchmark-unavailable-no-cache-reuse-"
    "2026-08-19-v62-history-revision-cache-v10-"
    "2026-08-19-v57-unstable-peer-confidence-shrink-"
    "2026-08-19-v56-benchmark-refresh-peer-evidence-audit-"
    "2026-08-17-v52-date-aware-limit-rules-"
    + _v51.BACKTEST_PROVENANCE_VERSION
)


def setup_logging(*args, **kwargs):
    logger = _v51.setup_logging(*args, **kwargs)
    logger.propagate = False
    return logger


def _install_gui_runtime_contract_if_ready() -> None:
    gui_core = sys.modules.get("gui_core")
    if gui_core is None or not hasattr(gui_core, "ScannerGUI"):
        return
    try:
        import gui_process_v64

        gui_process_v64.install()
    except (ImportError, AttributeError, RuntimeError):
        return


FILTER_OVERRIDE_MIN_SIGNAL_COUNT: int = 3
DAILY_MAX_PROVIDER_LAG_TRADING_DAYS: int = 1
DAILY_MIN_COHERENT_DATA_DATE_RATIO: float = 0.90

TRADE_READY_MIN_MEDIAN_TURNOVER_60D: float = 5_000_000.0
TRADE_READY_MAX_ASSUMED_PARTICIPATION_RATE: float = 0.01
TRADE_LIQUIDITY_RULE_VERSION: str = "2026-08-18-v54-order-participation"

TRADE_READY_MAX_DATA_AGE_TRADING_DAYS: int = 0
TRADE_FRESHNESS_RULE_VERSION: str = "2026-08-19-v60-completed-session-only"

CHECKPOINT_RESUME_VERSION: str = "2026-08-19-v68-pinned-frame-publish-clear-v3"
FUNDAMENTAL_REFRESH_INTEGRITY_VERSION: str = "2026-08-19-v70-hard-financial-coverage-v3"
CACHE_HISTORY_INTEGRITY_VERSION: str = "2026-08-19-v69-full-ohlcv-content-check-v2"
BENCHMARK_CACHE_INTEGRITY_VERSION: str = "2026-08-19-v63-current-benchmark-required-v1"
GUI_PROCESS_INTEGRITY_VERSION: str = "2026-08-19-v64-process-tree-cancel-v1"
CACHE_FIRST_PUBLICATION_VERSION: str = "2026-08-19-v65-coherent-market-date-v1"
REPORT_PUBLICATION_INTEGRITY_VERSION: str = "2026-08-19-v66-stateful-staging-rollback-v1"
BACKTEST_PUBLICATION_INTEGRITY_VERSION: str = "2026-08-19-v71-transactional-ranking-output-v1"
CACHE_REPORT_PUBLICATION_VERSION: str = "2026-08-19-v72-coherent-market-date-v1"

PRICE_LIMIT_RULE_VERSION: str = "2026-08-17-v52-exchange-rule"
CALIBRATION_GOVERNANCE_VERSION: str = "2026-08-19-v57-unstable-stable-ratio-shrink-v1"

_install_gui_runtime_contract_if_ready()
