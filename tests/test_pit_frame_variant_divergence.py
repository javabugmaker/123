"""Where the two ``_verified_point_in_time_frame`` implementations disagree.

The open question from §10.9
----------------------------
The S4 reconnaissance found that one thing now has two implementations:

* ``analytics_core._verified_point_in_time_frame`` (``analytics_core:320``) --
  the native one, reachable in a static probe;
* ``backtest_production_activation_v93._production_point_in_time_frame``
  (``v93:78``) -- swapped in for the duration of one ``run_historical_backtest``.

§10.9 left unresolved whether they can produce different sample sets.  They
can, and the disagreement is not marginal -- it is total in every case except
two:

| input                                    | native | v93                  |
|------------------------------------------|--------|----------------------|
| status ``ELIGIBLE``                      | keep   | keep, weight 1.0     |
| unavailable + a recognised missing reason| **drop** | keep, weight 0.25  |
| unavailable + any other reason           | drop   | drop                 |
| **status column absent**                 | **empty frame** | keep all, 0.25 |

The fourth row is the dangerous one: the native builder returns an *empty*
frame when the column is missing, while v93 treats every row as a missing
snapshot and keeps the lot.  Same input, opposite answers.

Why this is not merely academic: on the 2026-09-15 run every sample was
unavailable (snapshots only cover the final 18 sessions of an 11-year window),
so the native builder would have produced **zero** rows and v93 produced all
156,547 at a quarter weight.  Whichever one is "authoritative" decides whether
calibration is switched off or running degraded -- and production currently
runs the v93 reading.

Which one *should* be authoritative is still undecided; this file only makes
the disagreement impossible to overlook.
"""

from __future__ import annotations

import pandas as pd
import pytest

import analytics_core as _core
from backtest_production_activation_v93 import _production_point_in_time_frame

ROWS_PER_CASE = 3


def _frame(status: str | None, reason: str | None) -> pd.DataFrame:
    data: dict[str, object] = {
        "ticker": ["000001.SZ", "000002.SZ", "600000.SS"],
        "split": ["train", "validation", "test"],
        "sample_weight": [1.0, 1.0, 1.0],
    }
    if status is not None:
        data["universe_snapshot_status"] = [status] * ROWS_PER_CASE
    if reason is not None:
        data["universe_snapshot_reason"] = [reason] * ROWS_PER_CASE
    return pd.DataFrame(data)


# (label, status, reason, native keeps, v93 keeps)
CASES: list[tuple[str, str | None, str | None, int, int]] = [
    ("eligible", "ELIGIBLE", "eligible", 3, 3),
    ("unavailable_recognised_reason", "UNAVAILABLE", "no_point_in_time_snapshot", 0, 3),
    ("unavailable_unrecognised_reason", "UNAVAILABLE", "delisted_before_entry", 0, 0),
    ("status_column_absent", None, None, 0, 3),
    ("blank_status_and_reason", "", "", 0, 3),
    ("already_provisional", "PROVISIONAL", "no_point_in_time_snapshot", 0, 3),
]


@pytest.mark.parametrize(
    ("label", "status", "reason", "expected_native", "expected_production"),
    CASES,
    ids=[case[0] for case in CASES],
)
def test_the_two_builders_disagree_exactly_here(
    label: str,
    status: str | None,
    reason: str | None,
    expected_native: int,
    expected_production: int,
) -> None:
    """Freeze the divergence case by case.

    Six inputs, and the two implementations agree on two of them.  Any change
    to either builder shifts one of these counts and names the case.
    """
    native = _core._verified_point_in_time_frame(_frame(status, reason))
    production = _production_point_in_time_frame(_frame(status, reason))
    assert len(native) == expected_native, (
        f"[{label}] the native builder now keeps {len(native)}, was {expected_native}"
    )
    assert len(production) == expected_production, (
        f"[{label}] the v93 builder now keeps {len(production)}, was {expected_production}"
    )


def test_a_missing_status_column_is_the_sharpest_divergence() -> None:
    """The one case where the answers are opposites rather than degrees.

    Native: no column means no evidence, so the frame is emptied outright.
    v93: no column means every row is a missing snapshot, so everything is
    retained.  Callers downstream cannot tell these two apart.
    """
    frame = pd.DataFrame(
        {"ticker": ["000001.SZ"], "split": ["train"], "sample_weight": [1.0]}
    )
    native = _core._verified_point_in_time_frame(frame.copy())
    production = _production_point_in_time_frame(frame.copy())
    assert native.empty, "the native builder no longer empties the frame"
    assert len(production) == 1, "v93 no longer retains column-less frames"
    assert float(production["sample_weight"].iloc[0]) == 0.25


def test_v93_relabels_and_discounts_what_it_retains() -> None:
    """What "kept provisionally" actually means, so it cannot drift silently.

    The row is not kept as-is: its status is rewritten to ``PROVISIONAL`` and a
    blank reason becomes ``no_point_in_time_snapshot``, which is itself a
    recognised reason -- retained rows therefore re-qualify on a second pass.
    """
    production = _production_point_in_time_frame(_frame("", ""))
    assert production["universe_snapshot_status"].eq("PROVISIONAL").all()
    assert production["universe_snapshot_reason"].eq("no_point_in_time_snapshot").all()
    assert production["sample_weight"].round(6).eq(0.25).all()
    assert production["universe_evidence_weight"].round(6).eq(0.25).all()


def test_the_native_builder_is_strictly_an_eligibility_filter() -> None:
    """The native side, stated positively: it is ``ELIGIBLE`` or nothing.

    Worth pinning on its own because it is the reading a static probe reports,
    and a static probe is what most tooling here does.
    """
    mixed = pd.DataFrame(
        {
            "ticker": ["000001.SZ", "000002.SZ", "600000.SS"],
            "split": ["train", "validation", "test"],
            "sample_weight": [1.0, 1.0, 1.0],
            "universe_snapshot_status": ["eligible", "ELIGIBLE", "UNAVAILABLE"],
            "universe_snapshot_reason": ["x", "y", "no_point_in_time_snapshot"],
        }
    )
    native = _core._verified_point_in_time_frame(mixed)
    assert native["ticker"].tolist() == ["000001.SZ", "000002.SZ"], (
        "the native filter stopped being case-insensitive / ELIGIBLE-only"
    )
