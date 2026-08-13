"""InstitutionScanner configuration facade.

The stable runtime defaults remain in ``config_core``.  This facade carries the
current model/pipeline provenance across v36 market-data normalization, v38
Fundamental Gate 2.0, v41 output integrity, v42 diversity performance, v43
core logic integrity, v44 output reproducibility, v45 fail-closed execution,
v46 decision explainability, v47 canonical eligibility reconciliation and v48
numeric provenance validation.
"""

from __future__ import annotations

from config_core import *  # noqa: F403

SCORING_VERSION: str = "2026-08-13-v48-precise-score-chain-v47-canonical-filter-override-v46-explainable-eligibility-v45-fail-closed-execution-v44-tradable-risk-v43-wilder-risk-integrity-v39-decision"
PIPELINE_VERSION: str = "2026-08-13-v48-numeric-preflight-v47-preflight-integrity-v46-auditable-decisions-v45-view-contract-v44-reproducibility-v43-core-v42-diversity-v41-output-v40-semantics-v39-decision-v38-fundamental"
FUNDAMENTAL_GATE_VERSION: str = "2026-08-12-v43-hard-data-completeness-v38-industry-adaptive"
DECISION_INTEGRITY_VERSION: str = "2026-08-13-v48-numeric-provenance-v47-single-override-authority-v46-multi-blocker-audit-v45-mandatory-risk-geometry-v44-reproducible-risk-v43-risk-eligibility-v41-action-v40-explanations-v39-lifecycle"
OUTPUT_CONTRACT_VERSION: str = "2026-08-13-v48-precise-risk-audit-v47-filter-override-audit-v46-backtest-provenance-v45-gui-price-rank-v44-price-audit-v43-risk-audit-v41-unified-v40-candidate-views"
GUI_VERSION: str = "2026-08-13-v48-risk-geometry-detail-v47-filter-override-audit-v46-applicability-freshness-v45-signal-view-clarity-v41-decision-clarity-v37-evidence-ux"
EVIDENCE_POLICY_VERSION: str = "2026-08-12-v37-peer-plus-ticker"
MARKET_DATA_VERSION: str = "2026-08-12-v36-tickflow-volume-shares"
BACKTEST_PROVENANCE_VERSION: str = "2026-08-13-v46-cutoff-freshness-v36-volume-shares"

# Backtest freshness is an audit-only status.  It never changes model scores;
# it distinguishes a normal one-session vendor lag from a delayed/stale
# benchmark cutoff shown in exports and the GUI.
BACKTEST_FRESHNESS_DELAYED_TRADING_DAYS: int = 1
BACKTEST_FRESHNESS_STALE_TRADING_DAYS: int = 5

# Relative asset rank is only a comparability correction.  It must not replace
# the absolute institutional score as the model anchor.
CROSS_ASSET_PERCENTILE_MAX_ADJUSTMENT: float = 5.0

# Execution eligibility uses the same risk geometry exposed in result files.
# Missing metrics stay compatible with legacy exports, while current scans are
# required to satisfy both bounds before becoming trade-ready.
TRADE_READY_MAX_STOP_DISTANCE_PCT: float = 12.0
TRADE_READY_MIN_REWARD_RISK: float = 1.0

# Rapidly weakening signals stay visible for research but lose trade-ready
# status and receive a bounded ranking penalty until strength recovers.
LIFECYCLE_WEAKEN_RANKING_FACTOR: float = 0.82

# Fundamental Gate 2.0 keeps GENERAL strict and only adapts sectors whose
# accounting/economic cycles make the universal v24 gate inappropriate.
QUALITY_GENERAL_ROE_THRESHOLD: float = 10.0
QUALITY_FINANCIAL_ROE_THRESHOLD: float = 6.0
QUALITY_CYCLICAL_ROE_THRESHOLD: float = 5.0
QUALITY_DEFENSIVE_ROE_THRESHOLD: float = 6.0
QUALITY_GENERAL_MARGIN_MAX_PERCENTILE: float = 0.30
QUALITY_CYCLICAL_MARGIN_MAX_PERCENTILE: float = 0.50
QUALITY_RECOVERY_MIN_GROWTH: float = 0.15
QUALITY_RESILIENT_MIN_LATEST_RATIO: float = 0.90
