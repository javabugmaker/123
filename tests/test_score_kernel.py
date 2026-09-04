from __future__ import annotations

import numpy as np

from institution_scanner.score_kernel import (
    combine_scalar_score,
    combine_score_components,
)


def test_scalar_and_vector_score_composition_are_identical() -> None:
    setup = np.array([80.0, 60.0, 20.0])
    trigger = np.array([40.0, 90.0, 10.0])
    execution = np.array([70.0, 50.0, 30.0])
    coverage = np.array([1.0, 0.5, 0.0])
    weights = (0.60, 0.25, 0.15)

    vector = combine_score_components(
        setup,
        trigger,
        execution,
        coverage,
        weights=weights,
    )
    scalar = np.array(
        [
            combine_scalar_score(s, t, e, c, weights=weights)
            for s, t, e, c in zip(setup, trigger, execution, coverage, strict=True)
        ]
    )

    np.testing.assert_array_equal(vector, scalar)
    np.testing.assert_array_equal(vector, np.array([68.5, 66.0, 19.0]))


def test_coverage_cap_and_nonfinite_values_fail_closed() -> None:
    result = combine_score_components(
        [100.0, np.nan],
        [100.0, 100.0],
        [100.0, 100.0],
        [0.25, np.nan],
        weights=(0.60, 0.25, 0.15),
    )

    np.testing.assert_array_equal(result, np.array([55.0, 0.0]))
