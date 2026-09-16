"""Behavioural coverage for ``validate_decision_integrity`` (912 lines, 44% of ``report_core``).

Why this function first
-----------------------
``tests/test_report_core_provenance.py`` established that all 24 top-level
definitions in ``report_core`` are alive -- none of the module is dead -- so
behavioural tests here are not wasted.  Within it, ``validate_decision_integrity``
(``report_core.py:527-1436``) is the largest single block and the highest-risk
one, because it **fails closed**::

    if violations:
        raise ValueError("Decision integrity violation: " + " | ".join(violations))

A false positive aborts publication, not just a log line.  It had no direct test.

How a 912-line function is tested without a 447-column fixture
--------------------------------------------------------------
Every check sits behind a column-presence guard::

    score_columns = {"BaseScore", ...}
    if score_columns.issubset(frame.columns):
        ...

A frame that supplies *only* one block's columns therefore skips every other
block deterministically.  That turns 912 lines into independently addressable
checks, and it is why the fixtures below are six columns wide rather than 447.

``validate_ranking_input`` (``result_contract.py:279``), which runs first at
``report_core.py:535``, behaves the same way -- it only inspects columns that
are present -- so it does not interfere with these narrow frames.

Scope
-----
This covers the function's contract (empty frames, error rows, no side effects)
and its model-weight block.  The other twenty-odd violation exits are not
covered here; this is a start, not a claim of completion.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from report_core import validate_decision_integrity

#: setup / trigger / execution weights that sum to 1.0, as the signature parser
#: requires (:664-667).
GOOD_SIGNATURE = "0.4:0.3:0.3"
COMPONENT_SCORES = {"BaseScore": 50.0, "TriggerScore": 60.0, "ExecutionScore": 70.0}
#: 50*0.4 + 60*0.3 + 70*0.3
EXPECTED_FINAL = 59.0


def _frame(**overrides: object) -> pd.DataFrame:
    """A one-row frame carrying only the model-weight block's columns."""
    payload: dict[str, object] = {
        "Ticker": ["600000.SH"],
        **COMPONENT_SCORES,
        "ModelWeightSignature": [GOOD_SIGNATURE],
        "FinalScore": [EXPECTED_FINAL],
    }
    payload.update(overrides)
    return pd.DataFrame(payload)


# ---------------------------------------------------------------------------
# contract
# ---------------------------------------------------------------------------


def test_an_empty_frame_is_accepted() -> None:
    """The first guard (:529-530), before any column is touched."""
    validate_decision_integrity(pd.DataFrame())


def test_a_frame_of_only_failed_rows_is_accepted() -> None:
    """Provider failures are filtered out, then the remainder is empty (:541-545).

    The comment at :537-540 explains why: a failed download leaves a placeholder
    row with empty fields, and letting those abort publication would make one
    bad provider response fail an otherwise healthy run.  So an all-error frame
    is not a violation -- it is simply nothing to validate.

    The row below carries a score that *would* be a violation (see
    ``test_a_final_score_that_disagrees_with_the_weights_is_rejected``).  That
    is deliberate: with a clean row this test would pass whether or not the
    filter ran, and would prove nothing.
    """
    frame = _frame(FinalScore=[EXPECTED_FINAL + 30.0])
    frame["Error"] = ["connection reset"]
    validate_decision_integrity(frame)


def test_a_failed_row_is_excluded_from_validation() -> None:
    """The filter is per row, not per frame.

    A bad row that is marked as a provider error must not raise, even though
    the same values on a successful row do.  This is the mechanism above,
    observed rather than assumed.
    """
    frame = pd.concat(
        [
            _frame(),
            _frame(
                Ticker=["000001.SZ"],
                FinalScore=[EXPECTED_FINAL + 30.0],
                Error=["timeout"],
            ),
        ],
        ignore_index=True,
    )
    validate_decision_integrity(frame)


def test_the_caller_frame_is_not_modified() -> None:
    """``-> None`` but no in-place mutation.

    :543 rebinds ``frame`` to a filtered **copy**; every later read is off that
    copy.  Worth pinning because a 912-line validator that quietly normalised
    its input would change the frame the caller is about to publish.
    """
    frame = _frame()
    frame["Error"] = [""]
    before = frame.copy()
    validate_decision_integrity(frame)
    pd.testing.assert_frame_equal(frame, before)


# ---------------------------------------------------------------------------
# model-weight block (:656-710)
# ---------------------------------------------------------------------------


def test_a_consistent_weight_signature_and_final_score_pass() -> None:
    """The happy path: signed weights reproduce the published final score."""
    validate_decision_integrity(_frame())


@pytest.mark.parametrize(
    "signature",
    ["0.5:0.5", "0.4:0.3:0.3:0.1", "0.4:0.3:-0.1", "not-a-number", ""],
    ids=["too_few", "too_many", "negative", "unparseable", "blank"],
)
def test_an_invalid_weight_signature_is_rejected(signature: str) -> None:
    """Rejected at :672 -- three finite non-negative weights summing to 1.

    The signature is what makes a published score *auditable*: it records which
    model produced the number.  A signature the validator cannot re-derive from
    leaves the score unverifiable, which is why this fails closed.
    """
    with pytest.raises(ValueError, match="invalid model-weight signature"):
        validate_decision_integrity(_frame(ModelWeightSignature=[signature]))


def test_a_final_score_that_disagrees_with_the_weights_is_rejected() -> None:
    """Rejected at :707-710.

    The whole point of the block: the published ``FinalScore`` must be
    reconstructible from the components and the signed weights.
    """
    with pytest.raises(ValueError, match="final score disagrees with signed model weights"):
        validate_decision_integrity(_frame(FinalScore=[EXPECTED_FINAL + 5.0]))


def test_the_v48_tolerance_is_stricter_than_the_default() -> None:
    """Two tolerances, selected by model version (:703-706).

    ``-v48-`` in ``ModelVersion`` tightens the allowed reconstruction error from
    0.02 to 0.006.  A drift of 0.01 therefore sits *between* the two: accepted
    for a current model, rejected for a v48 one.

    Pinning the boundary from both sides is what makes this a tolerance test
    rather than a restatement of the constant -- either side alone would also
    pass if the branch were removed entirely.
    """
    drifted = _frame(FinalScore=[EXPECTED_FINAL + 0.01])

    validate_decision_integrity(drifted)

    v48 = _frame(FinalScore=[EXPECTED_FINAL + 0.01], ModelVersion=["model-x-v48-y"])
    with pytest.raises(ValueError, match="final score disagrees"):
        validate_decision_integrity(v48)


def test_a_non_finite_component_score_is_not_a_false_positive() -> None:
    """NaN/inf components are excluded via ``complete``, not flagged (:682-688).

    ``numeric()`` (:566) coerces infinities to NaN, and the completeness mask
    requires all four scores to be present before the arithmetic check runs.
    Without that, every partially-scored row would abort publication.
    """
    frame = _frame(BaseScore=[np.nan], FinalScore=[np.nan])
    validate_decision_integrity(frame)

    infinite = _frame(TriggerScore=[np.inf])
    validate_decision_integrity(infinite)


def test_every_offending_row_is_named_in_the_message() -> None:
    """``record`` appends up to five tickers (:562-564).

    The message is the only diagnostic a failed publication produces, so it has
    to say *which* rows are wrong -- a bare "integrity violation" would leave an
    operator with 447 columns and no starting point.
    """
    frame = pd.DataFrame(
        {
            "Ticker": ["BAD1", "BAD2"],
            **{key: [value, value] for key, value in COMPONENT_SCORES.items()},
            "ModelWeightSignature": [GOOD_SIGNATURE, GOOD_SIGNATURE],
            "FinalScore": [EXPECTED_FINAL + 5.0, EXPECTED_FINAL + 6.0],
        }
    )
    with pytest.raises(ValueError) as caught:
        validate_decision_integrity(frame)
    message = str(caught.value)
    assert "BAD1" in message and "BAD2" in message, message
