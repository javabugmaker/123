"""Semantic lock for the two decision-policy fingerprints.

Why two
-------
``DecisionPolicySignature`` is deliberately *inclusive*: it hashes every policy
name on ``config``, so any change moves it.  That is a feature -- a narrower
digest would let a policy change that never touches a parameter slip through
with an unchanged signature.

The cost is that it cannot tell you *what* changed.  Nine of the 162 names in
the payload are ``*_VERSION`` strings, and those are labels rather than
parameters: each names the patch that last moved it, e.g.
``…-v80-tradeability-sample-array-v1``.  Rewording one moves the signature while
not a single threshold moves.

(Measured, because the tempting story is wrong: the genuinely huge version
ledgers -- ``PIPELINE_VERSION`` is 2022 characters -- never enter the payload at
all, so they cannot move either fingerprint.  The nine that do are 37 to 52
characters; none is a multi-segment historical chain.)

So there are now two columns:

* ``DecisionPolicySignature``        — fail-safe: did *anything* change?
* ``DecisionPolicyParameterDigest``  — discriminating: did a *number* change?

This file locks that split.  Without it the two digests could silently converge
(for instance if someone "simplified" the parameter digest back into the
inclusive one) and the whole distinction would evaporate while every value test
stayed green.
"""

from __future__ import annotations

import pandas as pd
import pytest

import config
from institution_scanner.publication_contract import PUBLIC_CANDIDATE_COLUMNS
from report_core import DECISION_RESULT_COLUMNS, _decision_projection
from result_contract import (
    decision_policy_parameter_digest,
    decision_policy_payload,
    decision_policy_signature,
)


@pytest.fixture(scope="module")
def version_names() -> list[str]:
    """The ``*_VERSION`` labels inside the policy payload."""
    names = sorted(
        name
        for name in decision_policy_payload()
        if name.endswith("_VERSION")
    )
    assert names, (
        "no *_VERSION names in the policy payload; the parameter digest would "
        "be identical to the signature and the split would be pointless"
    )
    return names


@pytest.fixture(scope="module")
def numeric_parameters() -> list[str]:
    return sorted(
        name
        for name, value in decision_policy_payload().items()
        if not name.endswith("_VERSION") and isinstance(value, (int, float))
    )


def test_the_two_fingerprints_disagree_by_construction() -> None:
    """They hash different payloads, so they must not be equal."""
    assert decision_policy_signature() != decision_policy_parameter_digest()


def test_the_parameter_digest_is_strictly_narrower(version_names: list[str]) -> None:
    """The split is exactly the ``*_VERSION`` names -- nothing else, nothing less."""
    full = decision_policy_payload()
    narrow = decision_policy_payload(exclude_version_strings=True)
    missing = sorted(set(full) - set(narrow))
    assert missing == version_names, (
        "the parameter digest drops something other than the version labels: "
        f"{missing}"
    )
    assert not [name for name in narrow if name.endswith("_VERSION")]


def test_rewording_a_version_label_moves_only_the_signature(
    monkeypatch, version_names: list[str]
) -> None:
    """The whole point of the split: a label edit must not look like a policy edit."""
    name = version_names[0]
    before_signature = decision_policy_signature()
    before_digest = decision_policy_parameter_digest()

    monkeypatch.setattr(config, name, f"{getattr(config, name)}-relabelled")

    assert decision_policy_signature() != before_signature, (
        f"{name} changed and the inclusive signature did not move; it is no "
        "longer fail-safe"
    )
    assert decision_policy_parameter_digest() == before_digest, (
        f"{name} is a label, but changing it moved the parameter digest -- the "
        "split is not working"
    )


def test_the_digest_travels_with_the_signature() -> None:
    """A digest nobody exports is decoration.

    ``DecisionPolicySignature`` is carried by three whitelists -- the decision
    projection, the public candidate contract and the report frame.  The digest
    has to ride along on all of them; if any one drops it, the column silently
    becomes NaN downstream while every value test here stays green.
    """
    for name in ("DecisionPolicySignature", "DecisionPolicyParameterDigest"):
        assert name in DECISION_RESULT_COLUMNS, f"{name} missing from decision results"
        assert name in PUBLIC_CANDIDATE_COLUMNS, f"{name} missing from the public contract"

    digest = decision_policy_parameter_digest()
    projected = _decision_projection(
        pd.DataFrame(
            {
                "Ticker": ["A.ST"],
                "DecisionPolicySignature": [decision_policy_signature()],
                "DecisionPolicyParameterDigest": [digest],
            }
        )
    )
    assert projected["DecisionPolicyParameterDigest"].tolist() == [digest]


def test_changing_a_parameter_moves_both(
    monkeypatch, numeric_parameters: list[str]
) -> None:
    """Negative half: a real parameter change must not hide behind the split."""
    assert numeric_parameters, "no numeric policy parameters to perturb"
    name = numeric_parameters[0]
    before_signature = decision_policy_signature()
    before_digest = decision_policy_parameter_digest()

    monkeypatch.setattr(config, name, float(getattr(config, name)) + 1.0)

    assert decision_policy_parameter_digest() != before_digest, (
        f"{name} changed and the parameter digest did not move"
    )
    assert decision_policy_signature() != before_signature
