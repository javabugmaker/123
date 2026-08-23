"""Canonical score-runtime composition for the v95+ model.

Older acceleration modules remain import-compatible implementation kernels, but
public scoring semantics must have one deterministic owner. Installing this
module composes the runtime in one order:

1. v79 exact-formula/cache acceleration kernels;
2. v79 endpoint and weight-cache accelerators;
3. v95 nominal component-scale / diagnostic-only HVN overlay.

``ensure()`` is intentionally cheap. It performs identity checks only and
re-composes the runtime if a legacy compatibility import has rebound a public
score function after bootstrap. This removes import-order dependence without
paying repeated installer work in the normal hot path.
"""

from __future__ import annotations

import score_acceleration_v79 as _raw_score
import score_core as _core
import score_endpoint_acceleration_v79 as _endpoint
import score_scale_migration_v95 as _scale
import score_weight_cache_v79 as _weight_cache

SCORE_RUNTIME_COMPOSITION_VERSION = (
    "2026-08-23-v97-canonical-score-runtime-composition-v2"
)


def install() -> None:
    """Install raw accelerators first and the canonical score policy last."""
    _raw_score.install()
    _endpoint.install()
    _weight_cache.install()
    _scale.install()
    _core.SCORE_RUNTIME_COMPOSITION_VERSION = SCORE_RUNTIME_COMPOSITION_VERSION


def ensure() -> None:
    """Repair public score bindings only when a legacy installer has drifted."""
    if (
        _core._series is not _raw_score._series
        or _core.score_volume is not _scale.score_volume
        or _core.score_accumulation is not _scale.score_accumulation
        or _core.score_structure is not _scale.score_structure
        or _core.entry_point is not _endpoint.entry_point
    ):
        install()


install()
