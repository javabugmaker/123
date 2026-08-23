"""Canonical score-runtime composition for the v95+ model.

Older acceleration modules remain import-compatible implementation kernels, but
public scoring semantics must have one deterministic owner.  Installing this
module always composes the runtime in the same order:

1. v79 exact-formula/cache acceleration kernels;
2. v79 endpoint and weight-cache accelerators;
3. v95 nominal component-scale / diagnostic-only HVN overlay.

Keeping this ordering in one place removes the repeated rebind/re-assert chains
that previously made import order part of the model definition.
"""

from __future__ import annotations

import score_acceleration_v79 as _raw_score
import score_core as _core
import score_endpoint_acceleration_v79 as _endpoint
import score_scale_migration_v95 as _scale
import score_weight_cache_v79 as _weight_cache

SCORE_RUNTIME_COMPOSITION_VERSION = (
    "2026-08-23-v97-canonical-score-runtime-composition-v1"
)


def install() -> None:
    """Install raw accelerators first and the canonical score policy last."""
    _raw_score.install()
    _endpoint.install()
    _weight_cache.install()
    _scale.install()
    _core.SCORE_RUNTIME_COMPOSITION_VERSION = SCORE_RUNTIME_COMPOSITION_VERSION


install()
