"""Which rows a walk-forward fold is allowed to fit on.

Why this file exists
--------------------
``tests/test_calibration_input_boundary.py`` freezes the input boundary of
``build_global_calibration`` and ``calibrate_component_weights``.  It says
about ``walk_forward_stats`` only that each fold is fitted on "per-fold
train", which reads as if the global ``split`` label decided membership.

It does not.  ``walk_forward_stats`` slices folds **by date, never by ``split``
label** (``model_calibration:619-625``)::

    train = sample.loc[sample["entry_date"].lt(start) & sample["exit60_date"].lt(start)]
    test  = sample.loc[sample["entry_date"].ge(start) & sample["entry_date"].lt(end)
                       & sample["exit60_date"].lt(end)]

That is deliberate -- the comment above it explains the exit-date guard was
added to stop late-December outcomes leaking into the following year.  But it
has a consequence nobody had written down: a row labelled ``split == "test"``
whose entry and 60-day exit both land before a fold boundary **is part of that
fold's training set**.  With the production split (test begins 2024-06-28) the
2025 and 2026 folds therefore fit on test-labelled rows.

This file locks that as-built behaviour so the two candidate readings cannot
silently swap.  Whether the label *should* be respected is a design decision,
listed as open rather than changed here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from model_calibration import walk_forward_stats

ROOT = Path(__file__).resolve().parents[1]

# Group A: 100 rows in early 2020, labelled train.
# Group B: 30 rows in Jan 2021, deliberately labelled "test".
# Group C: 40 rows in early 2022, labelled test.
#
# Fold 2021 sees train = A (100), test = B (30).
# Fold 2022 sees train = A + B (130), test = C (40).
# The point of the fixture is that fold 2022's train count exceeds the number
# of rows labelled "train" anywhere in the frame.
GROUP_A = ("train", 100, "2020-01-01", 1)
GROUP_B = ("test", 30, "2021-01-01", 1)
GROUP_C = ("test", 40, "2022-01-01", 3)


def _rows(
    split: str,
    count: int,
    first_date: str,
    day_step: int,
    *,
    driver: str = "setup",
) -> list[dict[str, object]]:
    """Deterministic sample rows on distinct entry dates.

    One row per entry date keeps ``calibration_weight`` at 1.0 each
    (``_prepare_samples`` divides by the per-date weight sum), so effective
    sample counts track row counts and the ``min_train_samples`` /
    ``min_test_samples`` gates behave predictably.
    """
    out: list[dict[str, object]] = []
    for index in range(count):
        setup = 30.0 + (index * 7 % 60)
        trigger = 30.0 + (index * 11 % 60)
        execution = 30.0 + (index * 13 % 60)
        signal_value = {"setup": setup, "trigger": trigger, "execution": execution}[driver]
        entry = pd.Timestamp(first_date) + pd.to_timedelta(index * day_step, unit="D")
        noise = ((index * 37) % 17 - 8) * 0.05
        out.append(
            {
                "ticker": f"{index % 250:06d}.SZ",
                "asset_type": "stock",
                "entry_signal": "BREAKOUT_CONFIRM",
                "market_regime": "BULL",
                "score": round(0.6 * setup + 0.25 * trigger + 0.15 * execution, 4),
                "setup_score": setup,
                "trigger_score": trigger,
                "execution_score": execution,
                "entry_date": entry,
                "exit60_date": entry + pd.to_timedelta(60, unit="D"),
                "net_return20": (signal_value - 50.0) / 5.0 + noise,
                "benchmark_return20": 0.0,
                "net_return60": (signal_value - 50.0) / 5.0,
                "benchmark_return60": 0.0,
                "sample_weight": 1.0,
                "split": split,
            }
        )
    return out


def _frame(*blocks: tuple[str, int, str, int]) -> pd.DataFrame:
    return pd.DataFrame([row for block in blocks for row in _rows(*block)])


@pytest.fixture(scope="module")
def folds() -> dict[int, dict[str, object]]:
    rows = walk_forward_stats(_frame(GROUP_A, GROUP_B, GROUP_C))
    return {int(row["year"]): row for row in rows}


def test_fold_train_is_sliced_by_date_and_not_by_split_label(folds: dict[int, dict[str, object]]) -> None:
    """A fold's training set contains rows the global split calls ``test``.

    130 is A + B.  Only 100 rows in the whole frame carry ``split == "train"``,
    so a train set of 130 is only possible if the label is ignored.  Respecting
    the label would drop this to 100 and the assertion fails.
    """
    assert 2022 in folds, "the 2022 fold was not emitted; the fixture is too small"
    assert folds[2022]["train_samples"] == 100 + 30, (
        "fold 2022 no longer fits on the test-labelled 2021 rows -- the slice "
        "has changed from date-based to split-label-based"
    )


def test_the_mislabelled_rows_are_countable_from_the_fixture(folds: dict[int, dict[str, object]]) -> None:
    """Non-vacuity guard for the assertion above.

    ``train_samples == 130`` proves nothing on its own unless the frame really
    does hold exactly 100 train-labelled rows.  Pin that separately so a change
    to the fixture cannot quietly turn the first test into a tautology.
    """
    frame = _frame(GROUP_A, GROUP_B, GROUP_C)
    train_labelled = int(frame["split"].astype(str).eq("train").sum())
    assert train_labelled == 100
    assert int(folds[2022]["train_samples"]) > train_labelled, (
        "the fold train is no longer larger than the train-labelled population, "
        "so the boundary this file locks is gone"
    )
    assert int(folds[2022]["test_samples"]) == 40, (
        "the 2022 fold's test side is not the 40 expected rows"
    )


def test_relabelling_those_rows_changes_nothing(folds: dict[int, dict[str, object]]) -> None:
    """Negative half: the label is genuinely inert, not merely tolerated.

    Two frames identical except for the label on group B must produce the same
    fold.  If a split filter is ever added, this one goes red alongside the
    first test -- the pair pins both directions.
    """
    relabelled = walk_forward_stats(
        _frame(GROUP_A, ("train", *GROUP_B[1:]), GROUP_C)
    )
    assert {int(row["year"]): row for row in relabelled} == folds


def test_the_production_walk_forward_sees_the_whole_verified_frame() -> None:
    """Structural lock on ``analytics_core:3202``.

    The behavioural tests above cannot see which frame production passes.  This
    one reads the call site: ``walk_forward_stats`` is handed
    ``verified_model_frame``, which is train + validation + test, so folds after
    the test boundary really can draw on test-labelled rows.
    """
    source = (ROOT / "analytics_core.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "walk_forward_stats"
    ]
    assert len(calls) == 1, f"expected one production walk-forward site, found {len(calls)}"
    argument = calls[0].args[0]
    assert isinstance(argument, ast.Name), "the walk-forward is no longer handed a named frame"
    assert argument.id == "verified_model_frame", (
        f"the walk-forward is now handed {argument.id!r} instead of the full "
        "verified frame; the boundary above no longer describes production"
    )
