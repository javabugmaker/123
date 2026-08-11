"""v36 configuration facade.

The stable runtime configuration remains in ``config_core``.  v36 keeps the
v35 scoring model unchanged while advancing the market-data/pipeline provenance
to canonical TickFlow CN volume-in-shares semantics.
"""

from __future__ import annotations

from config_core import *  # noqa: F403

SCORING_VERSION: str = "2026-08-11-v35-orthogonal-decision"
PIPELINE_VERSION: str = "2026-08-12-v37-project-integrity-evidence"
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
