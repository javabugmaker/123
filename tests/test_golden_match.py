"""Pin the tolerance that keeps the golden gates both honest and portable.

The three golden gates freeze thousands of numbers produced by numpy/scipy
kernels.  Compared bit-exactly they are a *portability* gate, not a behaviour
gate: GitHub Actions (ubuntu-24.04, numpy 2.4.6, scipy 1.17.1) reported three
"behaviour drifts" that were the last one or two ULPs of BLAS noise, and the
resulting red light is what stopped GitHub Pages from updating.

So the tolerance has to be defended from both directions, and this file is that
defence:

* it must be **loose enough** to absorb real cross-platform noise — pinned below
  with the exact pair of numbers CI produced;
* it must stay **tight enough** that a genuine semantic change still fails —
  pinned with a perturbation five orders of magnitude above the noise, which is
  still two orders of magnitude below the smallest change these fixtures exist
  to catch;
* the gates must actually **route** their comparison through ``golden_match``,
  otherwise a future edit can quietly restore ``==`` and the red light returns.
"""

from __future__ import annotations

import importlib
import inspect
import math

from golden_match import ABS_TOL, REL_TOL, floats_match, same

# Measured on 2026-09-15 from the failing Static Quality run: the same source
# that is bit-identical on Windows differs on ubuntu-24.04 by ~7e-15 relative.
CI_OBSERVED_EXPECTED = 3.494920656917694
CI_OBSERVED_ACTUAL = 3.4949206569177194

# Five orders of magnitude above that noise, and still far below any change in
# model weights, filter thresholds or formula terms.
SEMANTIC_PERTURBATION = 1e-6

GOLDEN_GATE_MODULES = (
    "test_analytics_core_golden",
    "test_filters_core_golden",
    "test_score_core_golden",
)


def test_absorbs_the_noise_that_turned_ci_red() -> None:
    """The measured CI drift must read as unchanged behaviour."""
    assert CI_OBSERVED_EXPECTED != CI_OBSERVED_ACTUAL, (
        "the recorded CI numbers are bit-identical; re-capture a real drift pair "
        "so this test keeps proving the tolerance absorbs it"
    )
    assert same(CI_OBSERVED_EXPECTED, CI_OBSERVED_ACTUAL)
    assert floats_match(23.41711159456133, 23.417111594561334)


def test_rejects_a_change_five_orders_above_the_noise() -> None:
    """A 1e-6 relative move is a behaviour change and must still fail."""
    assert not same(1.0, 1.0 + 1e-6)
    assert not same(CI_OBSERVED_EXPECTED, CI_OBSERVED_EXPECTED * (1 + 1e-6))


def test_tolerance_stays_far_from_both_ends() -> None:
    """Guard the constant itself: neither bit-exact nor 'close enough'.

    Setting this to 0 brings the portability red light back; setting it to 1e-2
    would let a real re-weighting of the model ship unnoticed.
    """
    assert 1e-13 < REL_TOL < 1e-7, f"REL_TOL={REL_TOL} left the safe band"
    assert 0 < ABS_TOL < 1e-9, f"ABS_TOL={ABS_TOL} left the safe band"


def test_nan_matches_only_nan() -> None:
    """Two runs that both produce NaN agree; NaN vs a number does not."""
    nan = float("nan")
    assert same(nan, nan)
    assert not same(nan, 1.0)
    assert not same(1.0, nan)


def test_infinities_are_compared_exactly() -> None:
    inf = float("inf")
    assert same(inf, inf)
    assert not same(inf, 1e308)
    assert not same(-inf, inf)


def test_a_verdict_is_never_a_count() -> None:
    """``True == 1`` in Python; a boolean flip must not pass as a number match."""
    assert not same(True, 1)
    assert not same(False, 0)
    assert same(True, True)


def test_comparison_recurses_through_structure() -> None:
    left = {"a": [1.0, {"b": 2.0}], "c": "x", "d": (1.5, 2.5)}
    right = {"a": [1.0, {"b": 2.0 * (1 + 1e-15)}], "c": "x", "d": (1.5, 2.5)}
    assert same(left, right)
    assert not same(left, {"a": [1.0, {"b": 2.0 * (1 + 1e-6)}], "c": "x", "d": (1.5, 2.5)})
    assert not same(left, {"a": [1.0, {"b": 2.0}], "c": "y", "d": (1.5, 2.5)})
    assert not same([1.0, 2.0], (1.0, 2.0)), "a list is not a tuple"


def test_values_near_zero_use_the_absolute_tolerance() -> None:
    """A relative test alone is meaningless at zero, so ABS_TOL covers it."""
    assert same(0.0, 1e-13)
    assert not same(0.0, 1e-9)
    assert math.isclose(0.0, 1e-13, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def test_every_golden_gate_routes_comparison_through_golden_match() -> None:
    """Stop the red light from coming back through the side door.

    Each gate is free to write its own ``_same`` helper again; if it does, the
    portability failure returns and this time nothing will point at it.
    """
    unwired = [
        name
        for name in GOLDEN_GATE_MODULES
        if "golden_match" not in inspect.getsource(importlib.import_module(name))
    ]
    assert not unwired, (
        "These golden gates no longer compare through golden_match, so their "
        "floats are being compared bit-exactly again: " + ", ".join(unwired)
    )
