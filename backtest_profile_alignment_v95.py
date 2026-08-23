"""v95 FAST/EXACT historical scoring-profile alignment.

FAST remains a sparser scheduler, but an evaluated endpoint must consume the
same amount of point-in-time score history as EXACT.  Both modes therefore use
a 504-bar score window.  Volume-profile/HVN state is diagnostic-only under the
v95 scalar score, so historical profile recomputation is disabled in both modes
rather than making one engine depend on an unavailable feature.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import analytics_core as _core

BACKTEST_PROFILE_ALIGNMENT_VERSION = (
    "2026-08-23-v95-504-window-hvn-diagnostic-profile-v1"
)
CANONICAL_SCORE_WINDOW_BARS = 504

_INSTALLED = False
_ORIGINAL_RESOLVE_PROFILE: Any = None


def _resolve_backtest_profile(mode: str, ticker_count: int):
    profile = _ORIGINAL_RESOLVE_PROFILE(mode, ticker_count)
    return replace(
        profile,
        score_window=CANONICAL_SCORE_WINDOW_BARS,
        historical_volume_profile=False,
    )


def install() -> None:
    global _INSTALLED, _ORIGINAL_RESOLVE_PROFILE
    if _INSTALLED:
        return
    _ORIGINAL_RESOLVE_PROFILE = _core._resolve_backtest_profile
    _core._resolve_backtest_profile = _resolve_backtest_profile
    _core.BACKTEST_PROFILE_ALIGNMENT_VERSION = BACKTEST_PROFILE_ALIGNMENT_VERSION
    _INSTALLED = True
