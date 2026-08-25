"""Two-phase canonical bootstrap for analytics compatibility kernels.

The legacy installers remain behavior-preserving kernels, but production
``analytics.py`` no longer imports them individually. Installation order is
centralized here and split around the analytics facade's own wrappers so moving
an installer cannot silently change which callable is captured as the legacy
baseline.
"""
from __future__ import annotations

from typing import Any, Final

import analytics_acceleration_v77 as _analytics_acceleration
import analytics_compat_v97 as _analytics_compat
import backtest_acceleration_v77 as _backtest_acceleration
import backtest_fastpath_v78 as _backtest_fastpath
import backtest_fastscore_v80 as _backtest_fastscore
import backtest_math_integrity_v94 as _math_integrity
import backtest_profile_alignment_v95 as _profile_alignment
import cache_acceleration_v77 as _cache_acceleration
import calibration_math_v96 as _calibration_math
import calibration_weight_cache_v79 as _calibration_weight_cache
import indicator_acceleration_v77 as _indicator_acceleration
import model_calibration as _model_calibration
import score_runtime_v97 as _score_runtime
import scoring_consistency_v94 as _scoring_consistency
import universe_cache_acceleration_v78 as _universe_cache_acceleration
from backtest_alignment import install_analytics_alignment
from backtest_rank_integrity_v82 import (
    BACKTEST_RECENCY_NORMALIZATION_VERSION,
    install_single_recency_ranking_guard,
    single_recency_ranking_context,
)

ANALYTICS_RUNTIME_FACADE_VERSION: Final = (
    "2026-08-25-v109.5-two-phase-canonical-analytics-bootstrap-v1"
)

_PRE_INSTALLED = False
_POST_INSTALLED = False


def install_pre_facade(core: Any) -> None:
    """Install accelerators/policy guards before analytics captures baselines."""
    global _PRE_INSTALLED
    if _PRE_INSTALLED:
        return
    _indicator_acceleration.install()
    _cache_acceleration.install()
    _universe_cache_acceleration.install()
    _backtest_acceleration.install()
    _analytics_acceleration.install()
    _score_runtime.install()
    _calibration_weight_cache.install()
    _backtest_fastpath.install()
    _backtest_fastscore.install()
    _profile_alignment.install()
    _scoring_consistency.install()
    install_analytics_alignment(core)
    install_single_recency_ranking_guard(core)
    core.ANALYTICS_RUNTIME_COMPOSITION_VERSION = ANALYTICS_RUNTIME_FACADE_VERSION
    _PRE_INSTALLED = True


def install_post_facade(core: Any) -> None:
    """Install math/PIT/reliability layers after facade wrappers are bound."""
    global _POST_INSTALLED
    if _POST_INSTALLED:
        return
    if not _PRE_INSTALLED:
        install_pre_facade(core)
    _math_integrity.install(core, _model_calibration)
    _calibration_math.install(core)
    _analytics_compat.install()
    _POST_INSTALLED = True


__all__ = [
    "ANALYTICS_RUNTIME_FACADE_VERSION",
    "BACKTEST_RECENCY_NORMALIZATION_VERSION",
    "install_post_facade",
    "install_pre_facade",
    "single_recency_ranking_context",
]
