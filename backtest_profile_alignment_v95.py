"""v95/v97 FAST/EXACT historical scoring-profile alignment.

FAST remains a sparser scheduler, but every evaluated endpoint consumes the same
504-bar point-in-time score history as EXACT. Volume-profile/HVN state is
observability-only, so neither historical scoring profile recomputes it.

v97 makes the installer re-entrant: older acceleration/test facades may restore
the stable profile resolver later in process lifetime. Each install call now
re-asserts the canonical resolver without wrapping an already wrapped function.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import analytics_core as _core

BACKTEST_PROFILE_ALIGNMENT_VERSION = (
    "2026-08-23-v97-504-window-hvn-diagnostic-reentrant-profile-v2"
)
CANONICAL_SCORE_WINDOW_BARS = 504

_INSTALLED = False
_ORIGINAL_RESOLVE_PROFILE: Any = None


def _resolve_backtest_profile(mode: str, ticker_count: int):
    if _ORIGINAL_RESOLVE_PROFILE is None:
        raise RuntimeError("canonical backtest profile resolver is not initialized")
    profile = _ORIGINAL_RESOLVE_PROFILE(mode, ticker_count)
    return replace(
        profile,
        score_window=CANONICAL_SCORE_WINDOW_BARS,
        historical_volume_profile=False,
    )


def install() -> None:
    global _INSTALLED, _ORIGINAL_RESOLVE_PROFILE
    if _ORIGINAL_RESOLVE_PROFILE is None:
        current = _core._resolve_backtest_profile
        if current is not _resolve_backtest_profile:
            _ORIGINAL_RESOLVE_PROFILE = current
    _core._resolve_backtest_profile = _resolve_backtest_profile
    _core.BACKTEST_PROFILE_ALIGNMENT_VERSION = BACKTEST_PROFILE_ALIGNMENT_VERSION
    _INSTALLED = True
