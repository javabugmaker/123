"""v83 runtime installer for layered ranking and lifecycle acceleration.

The project uses compatibility facades that retain imported function references.
This installer patches both the canonical lifecycle module and the already-loaded
analytics module, while remaining idempotent under repeated imports and worker
initialization.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pandas as pd

from lifecycle_acceleration_v83 import install as install_lifecycle_acceleration
from ranking_architecture_v83 import (
    RANKING_ARCHITECTURE_VERSION,
    stamp_layered_ranking,
)

_WRAPPED_ATTR = "_v83_layered_ranking_installed"
_ORIGINAL_ATTR = "_v83_layered_ranking_original"


def _install_ranking_wrapper(module: Any) -> None:
    if bool(getattr(module, _WRAPPED_ATTR, False)):
        return
    original: Callable[[pd.DataFrame], pd.DataFrame] = module.finalize_signal_ranking

    def layered_finalize(frame: pd.DataFrame) -> pd.DataFrame:
        return stamp_layered_ranking(original(frame))

    setattr(module, _ORIGINAL_ATTR, original)
    module.finalize_signal_ranking = layered_finalize
    setattr(module, "RANKING_ARCHITECTURE_VERSION", RANKING_ARCHITECTURE_VERSION)
    setattr(module, _WRAPPED_ATTR, True)


def install() -> None:
    """Patch live/report and analytics references once after analytics_core loads."""
    import analytics_core
    import signal_lifecycle

    _install_ranking_wrapper(signal_lifecycle)
    install_lifecycle_acceleration(signal_lifecycle)

    # analytics_core imports finalize_signal_ranking by value before this module
    # is loaded, so it needs its own idempotent wrapper for post-backtest paths.
    _install_ranking_wrapper(analytics_core)


install()
