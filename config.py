"""InstitutionScanner v82 ranking-integrity configuration facade.

v82 preserves v80 scoring weights and workstation acceleration while fixing
post-backtest recency semantics, adding full-universe perturbation auditing and
prospective point-in-time universe snapshots.  No setup/trigger/execution
weight or trade threshold is changed here.
"""

from __future__ import annotations

import sys

import config_v51 as _v51
from config_v51 import *  # noqa: F403
from workstation_runtime_v77 import runtime_profile

_RUNTIME = runtime_profile()

SCAN_THREADS: int = _RUNTIME.scan_threads
BACKTEST_MAX_PROCESSES: int = _RUNTIME.backtest_processes
BACKTEST_CHUNK_SIZE: int = _RUNTIME.backtest_chunk_size
BACKTEST_FAST_CHUNK_SIZE: int = _RUNTIME.backtest_fast_chunk_size
# Retained as the legacy/fallback bound. The v78 cache-aware path derives the
# normal append-only recomputation window from the previous cached row count.
BACKTEST_INCREMENTAL_TAIL_BARS: int = _RUNTIME.backtest_incremental_tail_bars

SCORING_VERSION: str = (
    "2026-08-21-v82-single-recency-ranking-"
    "2026-08-17-v52-setup-backed-breakout-"
    + _v51.SCORING_VERSION
)
PIPELINE_VERSION: str = (
    "2026-08-21-v82-ranking-integrity-audit-"
    "2026-08-20-v80-vectorized-backtest-workstation-engine-"
    "2026-08-20-v79-vectorized-score-endpoint-cache-"
    "2026-08-20-v78-vectorized-fast-backtest-io-cache-"
    "2026-08-20-v77-vectorized-workstation-runtime-"
    "2026-08-19-v76-whole-backtest-command-transaction-"
    "2026-08-19-v75-idempotent-publication-recovery-"
    "2026-08-19-v74-daily-hard-crash-recovery-"
    "2026-08-19-v73-journaled-publication-recovery-"
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
    "2026-08-21-v82-ranking-audit-provenance-"
    "2026-08-19-v76-whole-backtest-result-set-"
    "2026-08-19-v75-idempotent-crash-recovery-"
    "2026-08-19-v74-daily-outer-transaction-recovery-"
    "2026-08-19-v73-journaled-crash-recovery-"
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
    "2026-08-21-v82-single-recency-ranking-"
    "2026-08-20-v80-vectorized-fastscore-execution-cache-"
    "2026-08-20-v78-cache-aware-maturity-tail-equivalent-"
    "2026-08-20-v77-incremental-tail-360-runtime-only-"
    "2026-08-19-v76-whole-command-publication-"
    "2026-08-19-v75-idempotent-ranking-recovery-"
    "2026-08-19-v73-journaled-backtest-publication-"
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
REPORT_PUBLICATION_INTEGRITY_VERSION: str = "2026-08-19-v75-idempotent-journal-recovery-v3"
BACKTEST_PUBLICATION_INTEGRITY_VERSION: str = "2026-08-19-v75-idempotent-journal-recovery-v3"
CACHE_REPORT_PUBLICATION_VERSION: str = "2026-08-19-v72-coherent-market-date-v1"
DAILY_RECOVERY_INTEGRITY_VERSION: str = (
    "2026-08-19-v74-pid-aware-outer-transaction-recovery-v1"
)
BACKTEST_COMMAND_INTEGRITY_VERSION: str = (
    "2026-08-19-v76-whole-command-transaction-v1"
)
PERFORMANCE_ENGINE_VERSION: str = "2026-08-20-v80-vectorized-backtest-workstation-v1"
SCORE_PIPELINE_ACCELERATION_VERSION: str = (
    "2026-08-20-v79-threadlocal-series-endpoint-cache-v1"
)
BACKTEST_FASTPATH_VERSION: str = "2026-08-20-v80-whole-ticker-fastscore-v1"
BACKTEST_IO_CACHE_VERSION: str = "2026-08-20-v80-one-hash-benchmark-lookup-v1"
BACKTEST_INCREMENTAL_ENGINE_VERSION: str = "2026-08-20-v78-cache-maturity-rewind-v1"
BACKTEST_EXECUTION_ACCELERATION_VERSION: str = (
    "2026-08-20-v80-tradeability-sample-array-v1"
)
BACKTEST_WORKSTATION_TUNING_VERSION: str = (
    "2026-08-20-v80-physical-core-chunk-v1"
)

BACKTEST_RANKING_INTEGRITY_VERSION: str = (
    "2026-08-21-v82-single-recency-ranking-v1"
)
FULL_UNIVERSE_AUDIT_VERSION: str = (
    "2026-08-21-v82-full-universe-perturbation-v1"
)
UNIVERSE_SNAPSHOT_VERSION: str = (
    "2026-08-21-v82-prospective-universe-snapshot-v1"
)

PRICE_LIMIT_RULE_VERSION: str = "2026-08-17-v52-exchange-rule"
CALIBRATION_GOVERNANCE_VERSION: str = "2026-08-19-v57-unstable-stable-ratio-shrink-v1"

_install_gui_runtime_contract_if_ready()
