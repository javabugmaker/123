"""Canonical FAST/EXACT historical scoring-profile alignment.

FAST may schedule fewer endpoints, but every evaluated endpoint consumes the same
504-bar point-in-time score history as EXACT. Historical volume profile remains
diagnostic-only.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Final

BACKTEST_PROFILE_ALIGNMENT_VERSION: Final = (
    "2026-09-04-v113-canonical-504-window-profile-v1"
)
CANONICAL_SCORE_WINDOW_BARS: Final = 504

_ORIGINAL_RESOLVE_PROFILE: Any = None


def install(core: Any) -> None:
    global _ORIGINAL_RESOLVE_PROFILE
    if _ORIGINAL_RESOLVE_PROFILE is None:
        current = core._resolve_backtest_profile
        if current is not _resolve_backtest_profile:
            _ORIGINAL_RESOLVE_PROFILE = current
    core._resolve_backtest_profile = _resolve_backtest_profile
    core.BACKTEST_PROFILE_ALIGNMENT_VERSION = BACKTEST_PROFILE_ALIGNMENT_VERSION


def _resolve_backtest_profile(mode: str, ticker_count: int):
    if _ORIGINAL_RESOLVE_PROFILE is None:
        raise RuntimeError("canonical backtest profile resolver is not initialized")
    profile = _ORIGINAL_RESOLVE_PROFILE(mode, ticker_count)
    return replace(
        profile,
        score_window=CANONICAL_SCORE_WINDOW_BARS,
        historical_volume_profile=False,
    )
