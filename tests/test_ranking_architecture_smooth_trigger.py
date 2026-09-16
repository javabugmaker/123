"""Pin the current behaviour of ``SmoothTriggerApproximate`` -- and the gap in it.

What the column is
------------------
``ranking_architecture_v83.py:240``::

    result["SmoothTriggerApproximate"] = trigger.ge(99.999)

It sits in the *shadow* layer.  ``stamp_layered_ranking``'s own docstring says it
adds fields "without changing production decisions", and the commit that
introduced it (``93777ca``) describes "a smooth breakout shadow".  Nothing in
the code reads it -- see ``test_the_column_has_no_code_consumer`` -- it exists
only in the exported CSV, for a human to look at.

What this file found, and does *not* change
-------------------------------------------
The column reads as "this ``SmoothTriggerScore`` is an approximation".  The
observable cause of that would be clipping in ``ranking_architecture_v83.py:227``::

    smooth_trigger = np.clip(trigger + trigger_delta, 0.0, 100.0)

Clipping therefore happens when ``trigger + trigger_delta`` leaves ``[0, 100]``
-- but the flag tests ``trigger >= 99.999`` instead.  Those are not the same
condition, and the two diverge in **both** directions, on inputs that occur:

* **false positive** -- ``trigger`` at the ceiling but ``trigger_delta``
  negative: nothing is clipped (the sum moves *down*, into range), yet the flag
  is set.  Measured: ``trigger=100``, ``delta=-12.21`` -> score ``87.79``,
  flagged ``True``.
* **false negative** -- ``trigger`` below the ceiling but ``trigger_delta``
  large enough to overshoot: the sum is clipped and the excess is lost, yet the
  flag is clear.  Measured: ``trigger=95``, ``delta=+10.71`` -> sum ``105.71``,
  clipped to ``100``, flagged ``False``.

One subtlety that makes the false negative invisible from the output alone:
``SmoothTriggerDelta`` (line 237) is ``smooth_trigger - trigger``, i.e. the
delta *after* clipping, so it can never reveal that clipping happened -- the
``trigger=95`` row reports a delta of ``+5.0``, having actually been trimmed by
10.71.  The test therefore recomputes the unclipped delta by calling the two
production component functions directly, rather than copying their formulas.

Not "fixed" here: the correct condition depends on what ``Approximate`` was
meant to mean, there is no consumer and no spec, and changing it changes a
published CSV column.  These tests pin the current behaviour, so that either
fixing it or wiring the column into a decision will have to come back here.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import ranking_architecture_v83 as ranking

ROOT = Path(__file__).resolve().parents[1]

#: Directories that hold data or notes rather than importable source.
_SCAN_EXCLUDED = {"cache", "output", ".git", "__pycache__", ".workbuddy-ai", "tests"}


def _row(trigger: float, price_ratio: float, coverage: float = 1.0) -> pd.Series:
    """One row through ``stamp_layered_ranking``.

    ``price_ratio`` is ``Close / BreakoutBuyPrice``; the clearance it produces
    ((ratio - 1) * 100) is what selects the sign and size of the trigger delta.
    """
    frame = pd.DataFrame(
        [
            {
                "Close": 10.0,
                "BreakoutBuyPrice": 10.0 / price_ratio,
                "BaseScore": 50.0,
                "TriggerScore": trigger,
                "ExecutionScore": 50.0,
                "ScoreCoverage": coverage,
                "FinalScore": 50.0,
            }
        ]
    )
    return ranking.stamp_layered_ranking(frame).iloc[0]


def _unclipped_delta(price_ratio: float, coverage: float = 1.0) -> float:
    """The delta *before* ``np.clip``, via the two production components.

    Reusing the real functions rather than restating their formulas: the point
    of this helper is to recover a value the published columns cannot show.
    """
    clearance = np.asarray([(price_ratio - 1.0) * 100.0], dtype=np.float64)
    legacy = ranking._legacy_breakout_price_component(clearance)[0]
    smooth, _confirmation = ranking._smooth_breakout_price_component(clearance)
    return float(smooth[0] - legacy) * (0.75 + 0.25 * coverage)


def test_a_row_far_below_the_ceiling_is_still_flagged_approximate() -> None:
    """False positive: flagged, but nothing was clipped.

    ``trigger`` is at the ceiling and the delta is negative, so the sum moves
    *into* range -- no clipping, and the published score is 12 points below the
    cap.  The flag is still set, purely because ``trigger >= 99.999``.

    Asserted from the published columns alone, with no recomputation, so this
    stays true however the component functions later change.
    """
    row = _row(trigger=100.0, price_ratio=1.00001)

    assert float(row["SmoothTriggerScore"]) == pytest.approx(87.7892, abs=1e-3)
    assert float(row["SmoothTriggerScore"]) < 100.0, "the score did not reach the cap"
    assert bool(row["SmoothTriggerApproximate"]) is True, (
        "expected the current (questionable) behaviour to be pinned: a row 12 "
        "points below the cap is still flagged approximate"
    )


def test_a_row_that_was_actually_clipped_is_not_flagged_approximate() -> None:
    """False negative: clipped, but not flagged.

    The unclipped sum is 105.71, so 5.71 points of the smoothing are lost -- yet
    ``trigger`` is 95, below the 99.999 threshold, so the flag stays clear.
    """
    trigger, ratio = 95.0, 0.99999
    delta = _unclipped_delta(ratio)

    assert trigger + delta > 100.0, (
        f"this fixture no longer demonstrates clipping: trigger={trigger}, "
        f"unclipped delta={delta}"
    )

    row = _row(trigger=trigger, price_ratio=ratio)
    assert float(row["SmoothTriggerScore"]) == pytest.approx(100.0)
    assert bool(row["SmoothTriggerApproximate"]) is False, (
        "expected the current (questionable) behaviour to be pinned: a row that "
        "was clipped is not flagged approximate"
    )


def test_a_row_with_no_clipping_is_left_unmarked() -> None:
    """The control case: the flag is not simply always wrong.

    Zero delta, no clipping, no flag.  Without this the two tests above would
    read as "the column is broken everywhere" rather than "it diverges in two
    specific directions".
    """
    trigger, ratio = 92.0, 0.995
    assert _unclipped_delta(ratio) == pytest.approx(0.0, abs=1e-9)

    row = _row(trigger=trigger, price_ratio=ratio)
    assert float(row["SmoothTriggerScore"]) == pytest.approx(trigger)
    assert bool(row["SmoothTriggerApproximate"]) is False


def test_the_column_has_no_code_consumer() -> None:
    """It is write-only: defined once, never read.

    That is what keeps the two divergences above diagnostic rather than
    behavioural -- nothing downstream acts on the flag, it only reaches a CSV a
    human may read.  It is also why the divergence has survived: with no
    consumer there is nothing to notice it.

    Fails if anyone starts reading the column, at which point the divergence
    stops being cosmetic and this file's findings need revisiting.
    """
    holders = [
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*.py")
        if not (_SCAN_EXCLUDED & set(path.relative_to(ROOT).parts))
        and "SmoothTriggerApproximate" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert holders == ["ranking_architecture_v83.py"], (
        "SmoothTriggerApproximate is now referenced outside its defining module; "
        f"its accuracy is no longer cosmetic: {holders}"
    )
