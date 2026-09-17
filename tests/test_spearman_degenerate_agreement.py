"""Degenerate-input agreement between the project's ``_spearman`` copies.

Three modules define their own ``_spearman``.  They agreed on ordinary input
but not on degenerate input: ``model_audit`` returned NaN where
``model_calibration`` returned 0.0, and the two even used different length
thresholds (2 vs 3).

That mattered because 0.0 is not a measurement -- it is the absence of one.
A constant run has no rank agreement to report, and 0.0 reads as "measured,
no signal": ``calibration_stability_stats`` already guarded itself with
``np.isfinite``, so a 0.0 slipped past that guard and was counted as an
*unstable* fold (``rank_ics > 0.0`` is false for 0.0), dragging down
``stable_fold_ratio`` for a fold that carried no evidence either way.

These tests pin the shared rule: undefined is NaN, never 0.0.

The length threshold is deliberately *not* unified (2 vs 3).  The two call
sites cannot meet: ``model_calibration`` requires at least 30 rows before it
calls this at all, so the return value was the only reachable disagreement.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from model_audit import _spearman as audit_spearman
from model_calibration import _spearman as calibration_spearman
from model_calibration import calibration_stability_stats

_CONSTANT = pd.Series([1.0, 1.0, 1.0, 1.0, 1.0])
_VARYING = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])


def test_calibration_returns_nan_for_a_constant_target() -> None:
    assert np.isnan(calibration_spearman(_VARYING, _CONSTANT))


def test_calibration_returns_nan_for_a_constant_score() -> None:
    assert np.isnan(calibration_spearman(_CONSTANT, _VARYING))


def test_all_zero_weights_leave_no_rows_and_degenerate_to_nan() -> None:
    """All-zero weights are dropped by ``data["weight"].gt(0.0)`` before the
    sum is ever taken, so this reaches the same degenerate branch rather than
    the ``total <= 0.0`` guard below it.  That guard is unreachable: every
    surviving row has a positive weight, so a non-empty ``data`` always sums
    above zero.  It is kept as defence, not as a reachable case."""
    zero = pd.Series([0.0, 0.0, 0.0, 0.0, 0.0])
    assert np.isnan(calibration_spearman(_VARYING, _VARYING, zero))


def test_the_two_copies_agree_that_degenerate_input_is_nan() -> None:
    """The whole point of the change: they used to disagree here."""
    assert np.isnan(audit_spearman(_VARYING, _CONSTANT))
    assert np.isnan(calibration_spearman(_VARYING, _CONSTANT))
    assert np.isnan(audit_spearman(_CONSTANT, _VARYING))
    assert np.isnan(calibration_spearman(_CONSTANT, _VARYING))


def test_a_degenerate_value_is_never_mistaken_for_a_zero_measurement() -> None:
    """0.0 would pass ``np.isfinite`` and be scored as an unstable fold."""
    value = calibration_spearman(_VARYING, _CONSTANT)
    assert not np.isfinite(value)
    assert value != 0.0


def test_a_degenerate_fold_is_excluded_from_stability_stats() -> None:
    """The behaviour this unlocks: NaN folds drop out instead of counting
    against stability."""
    rows = [
        {"year": 2024, "rank_ic": float("nan"), "top_bottom_spread20": 0.1},
        {"year": 2025, "rank_ic": 0.5, "top_bottom_spread20": 0.2},
        {"year": 2026, "rank_ic": 0.7, "top_bottom_spread20": 0.3},
    ]
    stats = calibration_stability_stats(rows, minimum_folds=2)
    assert stats["fold_count"] == 2
    assert stats["stable_fold_ratio"] == pytest.approx(1.0)


def test_ordinary_input_still_agrees_and_is_finite() -> None:
    left = pd.Series([3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0, 6.0])
    right = pd.Series([2.0, 7.0, 1.0, 8.0, 2.0, 8.0, 1.0, 8.0])
    assert np.isfinite(calibration_spearman(left, right))
    assert np.isfinite(audit_spearman(left, right))
