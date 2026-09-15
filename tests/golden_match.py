"""Float-tolerant structural comparison shared by the golden equivalence gates.

Why this exists
---------------
The three golden gates (``analytics_core``, ``filters_core``, ``score_core``)
each freeze a few thousand captured numbers and compare them with ``==``.
That is bit-exact equality, and bit-exactness is not a property of this
repository's code -- it is a property of the machine it ran on: BLAS kernel,
CPU vector width, and the numpy/scipy build all reorder floating-point sums.

The Static Quality gate proved this.  It runs on ubuntu-24.04 / Python 3.11
with ``constraints-ci.txt`` (numpy 2.4.6, scipy 1.17.1) and has been red since
2026-09-10 with diffs of exactly this shape::

    score_volatility[etf_like]:  3.494920656917694  -> 3.4949206569177194
    total:                      23.41711159456133   -> 23.417111594561334

Those differ by ~7e-15 relative -- the last one or two ULPs.  Nothing about the
strategy changed; the arithmetic underneath it did.  With bit-exact comparison
the gate reported three "behaviour drifts" that were pure numerical noise, and
because the gate is what gates publication, GitHub Pages stopped updating.

Choosing the tolerance
----------------------
``REL_TOL = 1e-9`` sits deliberately far from both ends:

* it is ~6 orders of magnitude *looser* than the observed cross-platform noise
  (7e-15), so it absorbs BLAS/CPU/build variation with a wide margin;
* it is ~5 orders of magnitude *tighter* than the smallest semantic change these
  fixtures exist to catch.  Re-weighting the model, moving a filter threshold,
  or reordering a formula term moves these numbers by 1e-4 or more.  A change
  that only moves the ninth significant digit is not a behaviour change.

``ABS_TOL`` covers values at or near zero, where a relative test alone would be
meaningless.

NaN is treated as equal to itself so that "both runs produced NaN" reads as
matching behaviour rather than as drift; infinities must still match exactly.
"""

from __future__ import annotations

import math
from typing import Any

REL_TOL = 1e-9
ABS_TOL = 1e-12


def floats_match(left: float, right: float) -> bool:
    """True when two floats agree to within the golden tolerance."""
    if left == right:  # also covers +inf/+inf and -inf/-inf
        return True
    if math.isnan(left) and math.isnan(right):
        return True
    if math.isinf(left) or math.isinf(right):
        return False
    return math.isclose(left, right, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def same(left: Any, right: Any) -> bool:
    """Structural equality that tolerates last-digit float noise.

    Booleans are compared by identity because ``True == 1`` would let a verdict
    silently turn into a count.
    """
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, float) and isinstance(right, float):
        return floats_match(left, right)
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            same(left[key], right[key]) for key in left
        )
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return (
            type(left) is type(right)
            and len(left) == len(right)
            and all(same(a, b) for a, b in zip(left, right))
        )
    return left == right
