"""InstitutionScanner production configuration facade.

The production Champion weights remain unchanged. v113 only repairs financial
quality semantics, separates market/portfolio execution capacity, and adds gate
distribution observability. Scoring changes must still follow Champion/Challenger
governance in ``institution_scanner.contracts``.
"""

from __future__ import annotations

import os
import sys

import config_v51 as _v51
from config_v51 import *  # noqa: F403
from workstation_runtime_v77 import runtime_profile

_RUNTIME = runtime_profile()

SCAN_THREADS: int = _RUNTIME.scan_threads
BACKTEST_MAX_PROCESSES: int = _RUNTIME.backtest_processes
BACKTEST_CHUNK_SIZE: int = _RUNTIME.backtest_chunk_size
BACKTEST_FAST_CHUNK_SIZE: int = _RUNTIME.backtest_fast_chunk_size
FUNDAMENTAL_DOWNLOAD_THREADS: int = 1
FUNDAMENTAL_DOWNLOAD_TIMEOUT: int = 300
FUNDAMENTAL_CHECKPOINT_EVERY: int = 100
FUNDAMENTAL_MAX_IN_FLIGHT_FACTOR: int = 2
BACKTEST_INCREMENTAL_TAIL_BARS: int = _RUNTIME.backtest_incremental_tail_bars

# The Champion score signature is intentionally unchanged. These version bumps
# describe data/decision semantics and engineering, not a new alpha model.
SCORING_VERSION: str = (
    "2026-09-01-v112-akshare-financial-quality-"
    "2026-08-22-v89-continuous-breakout-trigger-"
    "2026-08-21-v88-missing-evidence-no-renormalization-"
    "2026-08-21-v82-single-recency-ranking-"
    "2026-08-17-v52-setup-backed-breakout-"
    + _v51.SCORING_VERSION
)
PIPELINE_VERSION: str = (
    "2026-09-04-v113-canonical-quality-capacity-gate-health-"
    "2026-09-01-v112-akshare-batch-resumable-refresh-"
    "2026-09-01-v112-akshare-point-in-time-fundamentals-"
    "2026-08-22-v89-executable-backtest-signal-semantics-"
    "2026-08-21-v88-purged-date-balanced-point-in-time-research-"
    "2026-08-21-v87-directional-execution-backtest-integrity-"
    "2026-08-21-v86-cache-safe-vectorized-policy-alignment-"
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
    "2026-09-04-v113-annual-roe-tristate-capacity-"
    "2026-09-01-v110-financial-only-quality-evidence-"
    "2026-08-21-v87-directional-price-cost-gates-"
    "2026-08-19-v60-future-date-execution-gate-"
    "2026-08-19-v58-current-data-execution-gate-"
    "2026-08-18-v54-trade-ready-liquidity-gate-"
    "2026-08-17-v52-setup-backed-filter-override-"
    + _v51.DECISION_INTEGRITY_VERSION
)
OUTPUT_CONTRACT_VERSION: str = (
    "2026-09-04-v113-quality-status-execution-capacity-"
    "2026-09-01-v110-financial-report-provenance-columns-"
    "2026-08-21-v88-research-integrity-audit-provenance-"
    "2026-08-21-v87-execution-economics-backtest-evidence-"
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
FUNDAMENTAL_GATE_VERSION: str = (
    "2026-09-04-v113-annual-roe-interim-diagnostic-tristate-"
    "2026-09-01-v112-akshare-announcement-date-provenance-"
    "financial-only-quality-evidence-"
    + _v51.FUNDAMENTAL_GATE_VERSION
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
    "2026-08-22-v89-immediate-executable-signal-samples-"
    "2026-08-21-v88-purged-overlap-point-in-time-calibration-"
    "2026-08-21-v87-purged-weighted-net-excess-"
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


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except ValueError:
        return float(default)
    return value if value == value and abs(value) != float("inf") else float(default)


FILTER_OVERRIDE_MIN_SIGNAL_COUNT: int = 3
DAILY_MAX_PROVIDER_LAG_TRADING_DAYS: int = 1
DAILY_MIN_COHERENT_DATA_DATE_RATIO: float = 0.90

# Market capacity is a property of the security. Portfolio capacity is a
# property of the configured order. The compatibility v54 gate still consumes
# the same effective threshold, but the new canonical diagnostics expose both.
TRADE_LIQUIDITY_MARKET_FLOOR_CNY: float = float(VOLUME_MIN_MEDIAN_TURNOVER_60D)  # noqa: F405
TRADE_READY_MIN_MEDIAN_TURNOVER_60D: float = TRADE_LIQUIDITY_MARKET_FLOOR_CNY
TRADE_READY_MAX_ASSUMED_PARTICIPATION_RATE: float = 0.01
LIVE_EXECUTION_ASSUMED_NOTIONAL_CNY: float = max(
    0.0,
    _env_float(
        "INSTITUTION_SCANNER_ORDER_NOTIONAL_CNY",
        float(BACKTEST_ASSUMED_TRADE_NOTIONAL),  # noqa: F405
    ),
)
# Legacy execution modules read BACKTEST_ASSUMED_TRADE_NOTIONAL directly.
# Point it at the explicit live-capacity setting so old and canonical gates
# cannot disagree. With no environment override, the historical 50k default is
# unchanged.
BACKTEST_ASSUMED_TRADE_NOTIONAL: float = LIVE_EXECUTION_ASSUMED_NOTIONAL_CNY
TRADE_LIQUIDITY_RULE_VERSION: str = (
    "2026-09-04-v113-market-portfolio-capacity-v1"
)

TRADE_READY_MAX_DATA_AGE_TRADING_DAYS: int = 0
TRADE_FRESHNESS_RULE_VERSION: str = "2026-08-19-v60-completed-session-only"

TRADE_READY_MIN_BREAKOUT_PRICE_CONFIRMATION_SCORE: float = 60.0
TRADE_READY_BASE_SLIPPAGE_RATE: float = 0.001
TRADE_READY_STOCK_STAMP_DUTY_RATE: float = 0.0005
TRADE_READY_MIN_TARGET_COST_MULTIPLE: float = 1.50
TRADE_ECONOMICS_RULE_VERSION: str = "2026-08-21-v87-round-trip-cost-coverage-v1"

ETF_CASH_EQUIVALENT_MAX_ATR_PCT: float = 0.20
ETF_CASH_EQUIVALENT_MAX_ABS_RETURN_20D_PCT: float = 0.50
ETF_DIRECTIONAL_RESEARCH_RULE_VERSION: str = (
    "2026-08-21-v87-name-and-behaviour-cash-equivalent-v1"
)

CHECKPOINT_RESUME_VERSION: str = "2026-08-19-v68-pinned-frame-publish-clear-v3"
FUNDAMENTAL_REFRESH_INTEGRITY_VERSION: str = (
    "2026-09-04-v113-annual-roe-summary-v1-"
    "2026-09-01-v112-akshare-report-period-batch-resume-v1"
)
CACHE_HISTORY_INTEGRITY_VERSION: str = "2026-08-19-v69-full-ohlcv-content-check-v2"
BENCHMARK_CACHE_INTEGRITY_VERSION: str = "2026-08-19-v63-current-benchmark-required-v1"
GUI_PROCESS_INTEGRITY_VERSION: str = "2026-08-19-v64-process-tree-cancel-v1"
CACHE_FIRST_PUBLICATION_VERSION: str = "2026-08-19-v65-coherent-market-date-v1"
REPORT_PUBLICATION_INTEGRITY_VERSION: str = "2026-08-19-v75-idempotent-journal-recovery-v3"
BACKTEST_PUBLICATION_INTEGRITY_VERSION: str = "2026-08-19-v75-idempotent-journal-recovery-v3"
CACHE_REPORT_PUBLICATION_VERSION: str = "2026-08-19-v72-coherent-market-date-v1"
DAILY_RECOVERY_INTEGRITY_VERSION: str = (
    "2026-09-04-v113-gate-health-v1-"
    "2026-08-19-v74-pid-aware-outer-transaction-recovery-v1"
)
BACKTEST_COMMAND_INTEGRITY_VERSION: str = (
    "2026-08-19-v76-whole-command-transaction-v1"
)
PERFORMANCE_ENGINE_VERSION: str = (
    "2026-08-21-v87-vectorized-integrity-"
    "2026-08-21-v86-vectorized-policy-alignment-workstation-v1"
)
SCORE_PIPELINE_ACCELERATION_VERSION: str = (
    "2026-08-21-v86-cache-safe-score-transaction-v1"
)
BACKTEST_FASTPATH_VERSION: str = "2026-08-20-v80-whole-ticker-fastscore-v1"
BACKTEST_IO_CACHE_VERSION: str = "2026-08-21-v86-vectorized-benchmark-alignment-v1"
RESEARCH_POLICY_ACCELERATION_VERSION: str = (
    "2026-08-21-v87-vectorized-name-behaviour-policy-v1"
)
BACKTEST_INCREMENTAL_ENGINE_VERSION: str = "2026-08-20-v78-cache-maturity-rewind-v1"
BACKTEST_EXECUTION_ACCELERATION_VERSION: str = (
    "2026-08-20-v80-tradeability-sample-array-v1"
)
BACKTEST_WORKSTATION_TUNING_VERSION: str = (
    "2026-08-20-v80-physical-core-chunk-v1"
)
BACKTEST_RANKING_INTEGRITY_VERSION: str = (
    "2026-08-21-v88-verified-point-in-time-ranking-v1"
)
FULL_UNIVERSE_AUDIT_VERSION: str = "2026-08-21-v82-full-universe-perturbation-v1"
UNIVERSE_SNAPSHOT_VERSION: str = "2026-08-21-v88-stock-etf-universe-snapshot-v1"
PRICE_LIMIT_RULE_VERSION: str = "2026-08-17-v52-exchange-rule"
CALIBRATION_GOVERNANCE_VERSION: str = "2026-08-19-v57-unstable-stable-ratio-shrink-v1"

_install_gui_runtime_contract_if_ready()
