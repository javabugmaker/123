"""Canonical score-runtime composition for the v95+ model.

Older acceleration modules remain import-compatible implementation kernels, but
public scoring semantics must have one deterministic owner. Installing this
module composes the runtime in one order:

1. v79 exact-formula/cache acceleration kernels;
2. v79 endpoint and weight-cache accelerators;
3. v95 nominal component-scale / diagnostic-only HVN overlay.

``ensure()`` is intentionally cheap. It repairs only *known* legacy raw-kernel
rebinding. Arbitrary third-party/test hooks are left alone, so runtime
composition no longer destroys legitimate instrumentation or compatibility
patches merely because their callable identity differs from the canonical one.
"""

from __future__ import annotations

import score_acceleration_v79 as _raw_score
import score_core as _core
import score_endpoint_acceleration_v79 as _endpoint
import score_scale_migration_v95 as _scale
import score_weight_cache_v79 as _weight_cache

SCORE_RUNTIME_COMPOSITION_VERSION = (
    "2026-08-23-v97-canonical-score-runtime-composition-v4"
)


def install() -> None:
    """Install raw accelerators first and the canonical score policy last."""
    _raw_score.install()
    _endpoint.install()
    _weight_cache.install()
    _scale.install()
    _core.SCORE_RUNTIME_COMPOSITION_VERSION = SCORE_RUNTIME_COMPOSITION_VERSION


def ensure() -> None:
    """Repair only recognized pre-v95 score-policy drift.

    ``score_acceleration_v79.install`` is the compatibility installer that can
    legitimately run late in spawned workers or old integrations. When that
    happens it exposes its raw Volume/Accumulation/Structure kernels publicly.
    Those exact identities are safe evidence of drift. A Mock, profiler,
    extension hook or other callable is not evidence of legacy drift and must
    not be overwritten here.
    """
    if (
        _core.score_volume is _raw_score.score_volume
        or _core.score_accumulation is _raw_score.score_accumulation
        or _core.score_structure is _raw_score.score_structure
    ):
        _scale.install()
    _core.SCORE_RUNTIME_COMPOSITION_VERSION = SCORE_RUNTIME_COMPOSITION_VERSION


install()
