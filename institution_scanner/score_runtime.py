"""Canonical score-runtime composition for the v95+ production model."""

from __future__ import annotations

from typing import Any, Final

SCORE_RUNTIME_COMPOSITION_VERSION: Final = (
    "2026-09-04-v113-canonical-score-runtime-composition-v1"
)


def install(
    core: Any,
    raw_score: Any,
    endpoint: Any,
    weight_cache: Any,
    scale: Any,
) -> None:
    """Install raw accelerators first and the canonical score policy last."""
    raw_score.install()
    endpoint.install()
    weight_cache.install()
    scale.install()
    core.SCORE_RUNTIME_COMPOSITION_VERSION = SCORE_RUNTIME_COMPOSITION_VERSION


def ensure(core: Any, raw_score: Any, scale: Any) -> None:
    """Repair only recognized pre-v95 score-policy drift."""
    if (
        core.score_volume is raw_score.score_volume
        or core.score_accumulation is raw_score.score_accumulation
        or core.score_structure is raw_score.score_structure
    ):
        scale.install()
    core.SCORE_RUNTIME_COMPOSITION_VERSION = SCORE_RUNTIME_COMPOSITION_VERSION
