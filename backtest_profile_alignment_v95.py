"""v95/v97 FAST/EXACT historical scoring-profile alignment.

FAST remains a sparser scheduler, but every evaluated endpoint consumes the same
504-bar point-in-time score history as EXACT.

v97 makes the installer re-entrant: older acceleration/test facades may restore
the stable profile resolver later in process lifetime. Each install call now
re-asserts the canonical resolver without wrapping an already wrapped function.

v97.1 keeps the volume profile in the historical frame.  An earlier revision
described the volume profile as "observability-only" and forced
``historical_volume_profile`` to ``False``; that claim was wrong.
``score_core.score_structure`` reads ``Above_HVN`` / ``DistToHVN_Pct`` and adds
up to 2 of its 15 points on them, and the live scan passes a frame that carries
them.  Dropping the columns therefore made the backtest score a model that was
missing a term live still applied -- measured at +0.03 to +1.70 structure points
on roughly 23% of samples.

Both profiles now request the recompute, which keeps FAST == EXACT and makes
both equal to live.  ``BACKTEST_HISTORICAL_VOLUME_PROFILE`` restores the old
behaviour without a code change.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import analytics_core as _core
import config as _config

BACKTEST_PROFILE_ALIGNMENT_VERSION = (
    "2026-09-15-v97.1-504-window-hvn-recomputed-reentrant-profile-v3"
)
CANONICAL_SCORE_WINDOW_BARS = 504


def _historical_volume_profile_enabled() -> bool:
    """Read the switch late so tests can flip ``config`` after import."""
    return bool(getattr(_config, "BACKTEST_HISTORICAL_VOLUME_PROFILE", True))

_INSTALLED = False
_ORIGINAL_RESOLVE_PROFILE: Any = None


def _resolve_backtest_profile(mode: str, ticker_count: int):
    if _ORIGINAL_RESOLVE_PROFILE is None:
        raise RuntimeError("canonical backtest profile resolver is not initialized")
    profile = _ORIGINAL_RESOLVE_PROFILE(mode, ticker_count)
    return replace(
        profile,
        score_window=CANONICAL_SCORE_WINDOW_BARS,
        historical_volume_profile=_historical_volume_profile_enabled(),
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
