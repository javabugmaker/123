"""InstitutionScanner configuration facade.

The stable runtime defaults remain in ``config_core``.  This facade carries the
current model/pipeline provenance across v36 market-data normalization, v37
project-integrity/evidence UX, and v38 industry-adaptive Fundamental Gate 2.0.
"""

from __future__ import annotations

from config_core import *  # noqa: F403

SCORING_VERSION: str = "2026-08-12-v38-fundamental-gate2"
PIPELINE_VERSION: str = "2026-08-12-v38-fundamental-gate2-v37-integrity"
FUNDAMENTAL_GATE_VERSION: str = "2026-08-12-v38-industry-adaptive"
GUI_VERSION: str = "2026-08-12-v37-evidence-ux"
EVIDENCE_POLICY_VERSION: str = "2026-08-12-v37-peer-plus-ticker"
MARKET_DATA_VERSION: str = "2026-08-12-v36-tickflow-volume-shares"
BACKTEST_PROVENANCE_VERSION: str = "2026-08-12-v36-volume-shares"

# Relative asset rank is only a comparability correction.  It must not replace
# the absolute institutional score as the model anchor.
CROSS_ASSET_PERCENTILE_MAX_ADJUSTMENT: float = 5.0

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
