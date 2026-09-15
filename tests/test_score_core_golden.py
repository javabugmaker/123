"""Golden equivalence test for ``score_core``'s scoring helpers.

``score_core`` is 1183 lines / 29 functions and produces the numbers that reach
the published scan.  Before this file it had one test, and that test covered the
composition kernel rather than any feature function.

The failure mode being guarded against is a silent re-scale: the v95/v79
overlays and the v113 facade already rebind most of these helpers at import
time, so a change can move every score in the product without raising anything.

Meta-checks that keep the net honest:

* ``function_provenance`` records *where each callable actually lives*.  Only 9
  of the 25 helpers under test still resolve to ``score_core``; the rest are
  supplied by ``score_acceleration_v79``, ``score_scale_migration_v95``,
  ``score_endpoint_acceleration_v79`` and the ``score`` facade.  A further
  rebinding must fail here instead of silently redefining the contract;
* every helper with at least three cases must produce more than one distinct
  output — a corpus where a helper always says the same thing cannot see it
  change.

Recapture with::

    python tests/golden_score_core.py
"""

from __future__ import annotations

import json
from collections import defaultdict

from golden_match import same
from golden_score_core import build_cases, capture, load

GOLDEN = load()
_MIN_CASES_PER_FUNCTION = 3


def test_golden_output_matches_for_every_case() -> None:
    actual = capture()
    assert actual["cases"].keys() == GOLDEN["cases"].keys(), (
        "Case set changed — recapture deliberately with `python "
        f"tests/golden_score_core.py`. Missing: "
        f"{sorted(set(GOLDEN['cases']) - set(actual['cases']))}; new: "
        f"{sorted(set(actual['cases']) - set(GOLDEN['cases']))}"
    )

    mismatches: list[str] = []
    for key, expected in GOLDEN["cases"].items():
        got = actual["cases"][key]
        if "raised" in expected or "raised" in got:
            if expected.get("raised") != got.get("raised"):
                mismatches.append(f"{key}: raised {expected.get('raised')} -> {got.get('raised')}")
            continue
        if not same(got.get("value"), expected.get("value")):
            mismatches.append(
                f"{key}: {json.dumps(expected.get('value'), ensure_ascii=False)[:120]} -> "
                f"{json.dumps(got.get('value'), ensure_ascii=False)[:120]}"
            )

    assert not mismatches, (
        "score_core behaviour drifted from the golden file. If intended, recapture "
        "with `python tests/golden_score_core.py` and review the fixture diff:\n  "
        + "\n  ".join(mismatches[:25])
    )


def test_function_provenance_is_unchanged() -> None:
    """Guard the overlays: if a helper gets rebound, say so loudly.

    Most helpers under test are already supplied by an overlay rather than by
    ``score_core`` itself.  That is the assembly this file is meant to freeze,
    but a *new* rebinding would silently change what the test means.
    """
    expected = GOLDEN["function_provenance"]
    actual = capture()["function_provenance"]
    drifted = {
        name: (expected[name], actual.get(name))
        for name in expected
        if actual.get(name) != expected[name]
    }
    assert not drifted, (
        "A helper under test is now bound somewhere else — the golden file would "
        "keep passing while measuring a different function. Recapture with "
        f"`python tests/golden_score_core.py` after reviewing: {drifted}"
    )


def test_every_helper_produces_more_than_one_output() -> None:
    """A corpus where a helper never varies cannot detect that helper changing."""
    outputs: dict[str, set[str]] = defaultdict(set)
    case_counts: dict[str, int] = defaultdict(int)
    for label, case in GOLDEN["cases"].items():
        name = label.split("[", 1)[0]
        case_counts[name] += 1
        outputs[name].add(json.dumps(case.get("value"), sort_keys=True, ensure_ascii=False))

    flat = [
        name
        for name, seen in outputs.items()
        if len(seen) == 1 and case_counts[name] >= _MIN_CASES_PER_FUNCTION
    ]
    assert not flat, (
        f"These helpers return the same value in every case ({sorted(flat)}); "
        "add inputs that separate them, otherwise the golden file cannot detect "
        "a change in their logic"
    )


def test_input_scenarios_are_deterministic() -> None:
    """The fixture is worthless if the inputs are not reproducible."""
    first = build_cases()
    second = build_cases()
    assert len(first) == len(second)
    for (label_a, fn_a, args_a, kwargs_a), (label_b, fn_b, args_b, kwargs_b) in zip(first, second):
        assert label_a == label_b and fn_a == fn_b, "Case order or naming drifted"
        assert kwargs_a == kwargs_b
        assert len(args_a) == len(args_b), f"{label_a}: arity drifted"
