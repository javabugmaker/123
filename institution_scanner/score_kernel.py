"""Pure final-score composition shared by live and vectorized scoring."""

from __future__ import annotations

import numpy as np
from numpy.typing import ArrayLike, NDArray


def combine_score_components(
    setup: ArrayLike,
    trigger: ArrayLike,
    execution: ArrayLike,
    coverage: ArrayLike,
    *,
    weights: tuple[float, float, float],
) -> NDArray[np.float64]:
    """Apply the locked component weights and indicator-coverage ceiling."""
    setup_array, trigger_array, execution_array, coverage_array = np.broadcast_arrays(
        np.asarray(setup, dtype=np.float64),
        np.asarray(trigger, dtype=np.float64),
        np.asarray(execution, dtype=np.float64),
        np.asarray(coverage, dtype=np.float64),
    )
    setup_weight, trigger_weight, execution_weight = weights
    weighted = (
        setup_array * setup_weight
        + trigger_array * trigger_weight
        + execution_array * execution_weight
    )
    weighted = np.clip(
        np.where(np.isfinite(weighted), weighted, 0.0),
        0.0,
        100.0,
    )
    coverage_array = np.clip(
        np.where(np.isfinite(coverage_array), coverage_array, 0.0),
        0.0,
        1.0,
    )
    return np.asarray(
        np.minimum(weighted, 40.0 + 60.0 * coverage_array),
        dtype=np.float64,
    )


def combine_scalar_score(
    setup: float,
    trigger: float,
    execution: float,
    coverage: float,
    *,
    weights: tuple[float, float, float],
) -> float:
    """Scalar convenience wrapper used by the live score facade."""
    combined = combine_score_components(
        setup,
        trigger,
        execution,
        coverage,
        weights=weights,
    )
    return float(combined.item())
