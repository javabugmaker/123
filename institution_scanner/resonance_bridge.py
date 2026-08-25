"""Canonical import surface for the legacy five-factor resonance kernel."""
from __future__ import annotations

from technical_resonance_v90 import (
    RESONANCE_VERSION,
    attach_resonance_to_samples,
    summarize_resonance_samples,
)

__all__ = [
    "RESONANCE_VERSION",
    "attach_resonance_to_samples",
    "summarize_resonance_samples",
]
