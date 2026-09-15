"""Golden equivalence test for ``filters_core`` — a module with zero coverage.

``filters_core`` holds the production screening semantics that decide whether a
ticker survives into the research pool, and it has no tests at all.  Refactoring
it blind is how a screening rule silently inverts.

This test does not try to know what the filters *should* do.  It freezes what
they *currently* do across a spread of deterministic inputs, so any change —
intended or not — has to be looked at before it ships.

Two meta-checks keep the net honest:

* every filter must still produce both a pass and a reject somewhere in the
  corpus — a corpus where a filter always says the same thing cannot detect a
  change in that filter's logic;
* the config constants the filters read are snapshotted, so if a threshold moves
  the failure message says "config changed, re-capture deliberately" instead of
  looking like a logic regression.

Recapture with::

    python tests/golden_filters_core.py
"""

from __future__ import annotations

import math
from typing import Any

from golden_filters_core import (
    FILTER_FIELDS,
    build_scenarios,
    capture,
    config_snapshot,
    load,
    plain,
)

GOLDEN = load()


def _same(left: Any, right: Any) -> bool:
    """Structural equality that treats NaN as equal to itself."""
    if isinstance(left, float) and isinstance(right, float):
        if math.isnan(left) and math.isnan(right):
            return True
        return left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _describe(value: Any) -> str:
    return repr(value)


def test_config_constants_match_the_capture_snapshot() -> None:
    """Guard against a threshold move masquerading as a logic regression."""
    current = config_snapshot()
    stale = {k: (v, current.get(k)) for k, v in GOLDEN["config_snapshot"].items() if current.get(k) != v}
    assert not stale, (
        "A config constant the filters read has changed since the golden file "
        "was captured. Re-capture deliberately with `python tests/golden_filters_core.py` "
        f"after reviewing the change: {stale}"
    )


def test_golden_output_matches_for_every_scenario() -> None:
    actual = capture()
    assert actual["cases"].keys() == GOLDEN["cases"].keys(), (
        "Scenario set changed. Expected: "
        f"{sorted(GOLDEN['cases'])}  Actual: {sorted(actual['cases'])}"
    )

    mismatches: list[str] = []
    for key, expected_case in GOLDEN["cases"].items():
        actual_case = actual["cases"][key]
        if "raised" in expected_case or "raised" in actual_case:
            if expected_case.get("raised") != actual_case.get("raised"):
                mismatches.append(
                    f"{key}: raised {expected_case.get('raised')} -> {actual_case.get('raised')}"
                )
            continue

        if actual_case["passed_count"] != expected_case["passed_count"]:
            mismatches.append(
                f"{key}: passed_count {expected_case['passed_count']} -> {actual_case['passed_count']}"
            )
        if actual_case["all_passed"] != expected_case["all_passed"]:
            mismatches.append(
                f"{key}: all_passed {expected_case['all_passed']} -> {actual_case['all_passed']}"
            )
        for field, expected_filter in expected_case["filters"].items():
            actual_filter = actual_case["filters"][field]
            for attribute in ("passed", "reason", "details"):
                if not _same(actual_filter[attribute], expected_filter[attribute]):
                    mismatches.append(
                        f"{key}::{field}.{attribute}: "
                        f"{_describe(expected_filter[attribute])} -> "
                        f"{_describe(actual_filter[attribute])}"
                    )

    assert not mismatches, (
        "filters_core behaviour drifted from the golden file. If the change is "
        "intended, re-capture with `python tests/golden_filters_core.py` and "
        "review the fixture diff:\n  " + "\n  ".join(mismatches[:20])
    )


def test_every_filter_produces_both_verdicts() -> None:
    """A corpus where a filter never flips cannot detect that filter changing."""
    verdicts: dict[str, set[bool]] = {field: set() for field in FILTER_FIELDS}
    for case in GOLDEN["cases"].values():
        if "raised" in case:
            continue
        for field in FILTER_FIELDS:
            verdicts[field].add(case["filters"][field]["passed"])

    one_sided = sorted(f for f, seen in verdicts.items() if len(seen) < 2)
    assert not one_sided, (
        f"These filters return the same verdict in every scenario ({one_sided}); "
        "add an input that flips them, otherwise the golden file cannot detect "
        "a change in their logic"
    )


def test_scenarios_are_deterministic() -> None:
    """The whole fixture is worthless if the inputs are not reproducible."""
    first = build_scenarios()
    second = build_scenarios()
    assert list(first) == list(second), "Scenario names or order changed"
    for name in first:
        left, right = first[name], second[name]
        assert list(left.columns) == list(right.columns), f"{name}: columns drifted"
        for column in left.columns:
            assert _same(
                plain(left[column].tolist()), plain(right[column].tolist())
            ), f"{name}.{column} is not deterministic across builds"
