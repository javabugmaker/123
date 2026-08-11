"""v35 configuration facade.

The v34 configuration implementation is kept in ``config_core`` so the public
``config`` module can advance model/cache versions while preserving the stable
runtime, path and logging configuration.
"""

from __future__ import annotations

from config_core import *  # noqa: F403

SCORING_VERSION: str = "2026-08-11-v35-orthogonal-decision"
PIPELINE_VERSION: str = "2026-08-11-v35-model-integrity"

# Relative asset rank is only a comparability correction.  It must not replace
# the absolute institutional score as the model anchor.
CROSS_ASSET_PERCENTILE_MAX_ADJUSTMENT: float = 5.0

# Rapidly weakening signals stay visible for research but lose trade-ready
# status and receive a bounded ranking penalty until strength recovers.
LIFECYCLE_WEAKEN_RANKING_FACTOR: float = 0.82
