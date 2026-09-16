"""Which layer of data is allowed to *shape* calibration.

Why this file exists
--------------------
``model_calibration`` is fed from three production sites, and each one is
supposed to see a different slice of the backtest sample frame:

| site                                                    | may be fitted on        |
|---------------------------------------------------------|-------------------------|
| ``build_global_calibration``  (``analytics_core:3199``)  | train + validation      |
| ``calibrate_component_weights`` (``analytics_core:3206``)| validation only         |
| ``walk_forward_stats``        (``analytics_core:3202``)  | per-fold train only     |

Everything else -- notably ``calibration_details_for_frame`` at
``analytics_core:2504`` -- is *inference*: it applies an already-fitted
calibration to the live ranking frame and must not refit anything.

``tests/test_calibration_governance.py`` (5 tests) locks the *activation* gates
around calibration.  This file locks the *input* boundary, which had no
coverage at all: before this file, nothing would have complained if the test
split started being folded into a fit.

The one thing this file deliberately does **not** do
--------------------------------------------------
``build_global_calibration`` itself only drops ``split == "purged"``
(``model_calibration:207-208``); excluding ``test`` is entirely the caller's
job.  Its docstring says "without using the held-out test set", which reads
like a promise the function keeps -- it does not, it is a caller contract.
Production honours it at ``analytics_core:3196-3198``.  Whether the filter
should move *into* the function is a design decision, not a bug fix, so it is
listed as open rather than changed here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from model_calibration import build_global_calibration, calibrate_component_weights

ROOT = Path(__file__).resolve().parents[1]


def _rows(
    split: str,
    count: int,
    *,
    first_date: str,
    driver: str,
    excess_scale: float = 1.0,
    excess_shift: float = 0.0,
) -> list[dict[str, object]]:
    """Deterministic sample rows where *driver* alone predicts the outcome.

    ``driver`` is one of ``setup`` / ``trigger`` / ``execution``.  Whoever
    controls the outcome is exactly what a weight search is supposed to find,
    so making the *test* split disagree with the *validation* split is what
    tells the two apart.
    """
    out: list[dict[str, object]] = []
    offsets = pd.to_timedelta(range(count), unit="D")
    exit_offsets = pd.to_timedelta(range(60, 60 + count), unit="D")
    for index in range(count):
        # Spread scores deterministically across 30..95 so buckets differ.
        setup = 30.0 + (index * 7 % 60)
        trigger = 30.0 + (index * 11 % 60)
        execution = 30.0 + (index * 13 % 60)
        signal_value = {"setup": setup, "trigger": trigger, "execution": execution}[driver]
        noise = ((index * 37) % 17 - 8) * 0.05
        out.append(
            {
                "ticker": f"{split[:2].upper()}{index % 5:02d}.SZ",
                "asset_type": "stock",
                "entry_signal": "BREAKOUT_CONFIRM",
                "market_regime": "BULL",
                "score": round(0.6 * setup + 0.25 * trigger + 0.15 * execution, 4),
                "setup_score": setup,
                "trigger_score": trigger,
                "execution_score": execution,
                "entry_date": pd.Timestamp(first_date) + offsets[index],
                "exit60_date": pd.Timestamp(first_date) + exit_offsets[index],
                # benchmark is 0 so net_excess20 == net_return20
                "net_return20": (signal_value - 50.0) / 5.0 * excess_scale + excess_shift + noise,
                "benchmark_return20": 0.0,
                "net_return60": (signal_value - 50.0) / 5.0 * excess_scale + excess_shift,
                "benchmark_return60": 0.0,
                "sample_weight": 1.0,
                "split": split,
            }
        )
    return out


def _frame(*blocks: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame([row for block in blocks for row in block])


@pytest.fixture(scope="module")
def validation_driven() -> pd.DataFrame:
    """Validation prefers setup; that is what the weights should follow."""
    return _frame(
        _rows("train", 16, first_date="2020-01-01", driver="setup"),
        _rows("validation", 34, first_date="2020-03-01", driver="setup"),
        _rows("test", 34, first_date="2020-06-01", driver="setup"),
    )


def _global_row(frame: pd.DataFrame) -> dict[str, object]:
    rows = build_global_calibration(frame)
    globals_ = [row for row in rows if row["level"] == "global"]
    assert globals_, "no global level emitted; the fixture is too small to fit"
    return globals_[0]


def test_the_test_split_moves_global_calibration_if_it_gets_in() -> None:
    """The boundary is load-bearing: test rows are not inert.

    If including ``test`` changed nothing, the caller-side filter at
    ``analytics_core:3196`` would be decoration and this whole file pointless.
    """
    train_val = _frame(
        _rows("train", 8, first_date="2020-01-01", driver="setup"),
        _rows("validation", 8, first_date="2020-02-01", driver="setup"),
    )
    # Same rows, plus a test split whose outcomes point the other way.
    with_test = _frame(
        _rows("train", 8, first_date="2020-01-01", driver="setup"),
        _rows("validation", 8, first_date="2020-02-01", driver="setup"),
        _rows("test", 16, first_date="2020-04-01", driver="setup", excess_scale=-3.0),
    )

    clean = _global_row(train_val)
    leaky = _global_row(with_test)

    assert leaky["samples"] == clean["samples"] + 16, (
        "the test rows did not reach the fit at all; the fixture is not "
        "measuring what it claims"
    )
    assert leaky["calibration_score"] != clean["calibration_score"], (
        "adding a disagreeing test split did not move the calibration score -- "
        "either the fit already ignores test (then the caller filter is dead "
        "code, delete it) or this fixture is not actually reaching the fit"
    )
    assert float(leaky["calibration_score"]) < float(clean["calibration_score"]), (
        "a strongly negative test split made the calibration score go *up*"
    )


def test_purged_rows_are_dropped_by_the_fit_itself() -> None:
    """The one slice ``build_global_calibration`` excludes on its own."""
    clean = _frame(
        _rows("train", 8, first_date="2020-01-01", driver="setup"),
        _rows("validation", 8, first_date="2020-02-01", driver="setup"),
    )
    purged = _rows("purged", 16, first_date="2020-04-01", driver="setup", excess_scale=-3.0)
    assert _global_row(_frame(*[clean.to_dict("records")] + [purged])) == _global_row(clean)


def test_component_weights_are_chosen_on_validation_alone(
    validation_driven: pd.DataFrame,
) -> None:
    """``test`` is reported, never optimised on.

    Two frames that differ *only* in their test split must produce identical
    selected weights.  If the search ever touched test, the two would diverge.
    """
    disagreeing = _frame(
        _rows("train", 16, first_date="2020-01-01", driver="setup"),
        _rows("validation", 34, first_date="2020-03-01", driver="setup"),
        # A test split that would beg for weight on execution instead.
        _rows("test", 34, first_date="2020-06-01", driver="execution", excess_scale=3.0),
    )

    baseline = calibrate_component_weights(validation_driven)
    swapped = calibrate_component_weights(disagreeing)

    assert baseline.accepted, (
        "the search did not move off the defaults on either frame, so this "
        "test proves nothing about which split drove the choice"
    )
    assert (
        baseline.setup_weight,
        baseline.trigger_weight,
        baseline.execution_weight,
    ) == (
        swapped.setup_weight,
        swapped.trigger_weight,
        swapped.execution_weight,
    ), (
        "changing only the test split changed the selected weights -- the "
        "search is reading the held-out set"
    )
    assert baseline.test_ic != swapped.test_ic, (
        "the reported test IC did not move, so the two frames are not actually "
        "different and the assertion above is vacuous"
    )
    assert baseline.validation_ic == swapped.validation_ic


def test_component_weights_never_beat_the_default_on_test_evidence(
    validation_driven: pd.DataFrame,
) -> None:
    """Negative half: a test-only edge must not flip ``accepted``.

    Same validation data as above, but the test split is engineered so that a
    different weight vector looks far better there.  ``accepted`` has to stay a
    validation decision.
    """
    lopsided = _frame(
        _rows("train", 16, first_date="2020-01-01", driver="setup"),
        _rows("validation", 34, first_date="2020-03-01", driver="setup"),
        _rows("test", 60, first_date="2020-06-01", driver="execution", excess_scale=8.0),
    )
    baseline = calibrate_component_weights(validation_driven)
    tempted = calibrate_component_weights(lopsided)
    assert tempted.accepted == baseline.accepted
    assert tempted.validation_samples == baseline.validation_samples
    assert tempted.test_samples != baseline.test_samples


def test_the_production_fit_is_handed_only_train_and_validation() -> None:
    """Structural lock on ``analytics_core:3196-3199``.

    The two behavioural tests above cannot see *which* frame production passes;
    this one can.  It reads the call site rather than trusting the comment.
    """
    tree = ast.parse((ROOT / "analytics_core.py").read_text(encoding="utf-8"))

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "build_global_calibration"
    ]
    assert len(calls) == 1, f"expected exactly one production fit site, found {len(calls)}"
    argument = calls[0].args[0]
    assert isinstance(argument, ast.Name), "the fit is no longer handed a named frame"
    name = argument.id

    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(target, "id", None) == name for target in node.targets)
    ]
    assert len(assignments) == 1, f"expected one assignment to {name}, found {len(assignments)}"
    source = ast.get_source_segment(
        (ROOT / "analytics_core.py").read_text(encoding="utf-8"), assignments[0]
    ) or ""
    assert '"train"' in source and '"validation"' in source, (
        f"{name} is no longer filtered to the fitting splits: {source!r}"
    )
    assert '"test"' not in source, (
        f"{name} now admits the test split into the fit: {source!r}"
    )
