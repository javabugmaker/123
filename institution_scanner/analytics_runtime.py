"""Two-phase canonical bootstrap for analytics compatibility kernels.

Installation order is centralized here. Small wrapper-only legacy overlays are
retired from the production path by calling their canonical services directly;
large implementation kernels remain until golden-equivalence allows extraction.
"""

from __future__ import annotations

from typing import Any, Final

import analytics_acceleration_v77 as _analytics_acceleration
import backtest_acceleration_v77 as _backtest_acceleration
import backtest_fastpath_v78 as _backtest_fastpath
import backtest_fastscore_v80 as _backtest_fastscore
import backtest_math_integrity_v94 as _math_integrity
import cache_acceleration_v77 as _cache_acceleration
import calibration_governance_v102 as _calibration_governance
import calibration_math_v96 as _calibration_math
import calibration_semantics_v102_1 as _calibration_semantics
import calibration_weight_cache_v79 as _calibration_weight_cache
import indicator_acceleration_v77 as _indicator_acceleration
import model_calibration as _model_calibration
import score_acceleration_v79 as _raw_score
import score_core as _score_core
import score_endpoint_acceleration_v79 as _score_endpoint
import score_scale_migration_v95 as _score_scale
import score_weight_cache_v79 as _score_weight_cache
import scoring_consistency_v94 as _scoring_consistency
import universe_cache_acceleration_v78 as _universe_cache_acceleration
from backtest_alignment import install_analytics_alignment
from backtest_rank_integrity_v82 import (
    BACKTEST_RECENCY_NORMALIZATION_VERSION,
    install_single_recency_ranking_guard,
    single_recency_ranking_context,
)
from institution_scanner import backtest_profile as _profile_alignment
from institution_scanner import pit_counts as _pit_counts
from institution_scanner import pit_maturity as _pit_maturity
from institution_scanner import point_in_time_backtest as _pit_backtest
from institution_scanner import postprocess_performance as _postprocess_performance
from institution_scanner import ranking_determinism as _ranking_determinism
from institution_scanner import reliability as _reliability
from institution_scanner import score_runtime as _score_runtime

ANALYTICS_RUNTIME_FACADE_VERSION: Final = (
    "2026-09-04-v113-canonical-analytics-bootstrap-v2"
)
LEGACY_PERFORMANCE_ENGINE_VERSION: Final = (
    "2026-08-20-v80-vectorized-backtest-workstation-v1"
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
    _score_runtime.install(
        _score_core,
        _raw_score,
        _score_endpoint,
        _score_weight_cache,
        _score_scale,
    )
    _calibration_weight_cache.install()
    _backtest_fastpath.install()
    _backtest_fastscore.install()
    _profile_alignment.install(core)
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

    # Former analytics_compat_v97 wrapper, now owned directly by the canonical
    # runtime composition so there is no extra monkey-patch bootstrap layer.
    core.PERFORMANCE_ENGINE_VERSION = LEGACY_PERFORMANCE_ENGINE_VERSION
    _pit_backtest.install(core)
    _pit_counts.install()
    _pit_maturity.install(core)
    _calibration_governance.install(core)
    _calibration_semantics.install(core)
    _reliability.install(core)
    _ranking_determinism.install_reliability(_reliability)
    _postprocess_performance.install()

    _POST_INSTALLED = True


__all__ = [
    "ANALYTICS_RUNTIME_FACADE_VERSION",
    "BACKTEST_RECENCY_NORMALIZATION_VERSION",
    "install_post_facade",
    "install_pre_facade",
    "single_recency_ranking_context",
]
