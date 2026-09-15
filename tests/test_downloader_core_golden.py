"""Golden equivalence test for ``downloader_core``'s pure helpers.

``downloader_core`` is 877 lines with 43 functions, production responsibility
for how tickers, sources, cached frames and corporate-action rebases are
interpreted — and no test coverage.  The nine network-bound functions are left
out (they need a live provider); everything exercised here is offline and
deterministic.

The failure mode this guards against is quiet corruption: a change in
``_validate_ohlcv`` or ``_requires_full_rebase`` does not raise, it just lets
slightly different data into every downstream stage.

Two meta-checks keep the net honest:

* ``function_provenance`` records *where each callable actually lives*.  Several
  overlays rebind symbols on ``downloader_core`` at import time, so this file
  would otherwise keep passing while measuring a different function than the
  one it was captured from;
* every helper must produce more than one distinct output across the corpus —
  a corpus where a helper always says the same thing cannot see it change.

Recapture with::

    python tests/golden_downloader_core.py
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from golden_downloader_core import build_cases, capture, load

GOLDEN = load()
_MIN_CASES_PER_FUNCTION = 3


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same(left[k], right[k]) for k in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, float) and isinstance(right, float):
        return left == right
    return left == right


def test_golden_output_matches_for_every_case() -> None:
    actual = capture()
    assert actual["cases"].keys() == GOLDEN["cases"].keys(), (
        "Case set changed — recapture deliberately with `python "
        f"tests/golden_downloader_core.py`. Missing: "
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
        if not _same(got.get("value"), expected.get("value")):
            mismatches.append(
                f"{key}: {json.dumps(expected.get('value'), ensure_ascii=False)[:120]} -> "
                f"{json.dumps(got.get('value'), ensure_ascii=False)[:120]}"
            )

    assert not mismatches, (
        "downloader_core behaviour drifted from the golden file. If intended, "
        "recapture with `python tests/golden_downloader_core.py` and review the "
        "fixture diff:\n  " + "\n  ".join(mismatches[:25])
    )


def test_function_provenance_is_unchanged() -> None:
    """Guard the overlays: if a helper gets rebound, say so loudly.

    ``_validate_ohlcv`` is already replaced by
    ``institution_scanner.market_cache_performance`` and
    ``_normalize_cn_share_count`` by ``downloader_v51_base``.  That is fine —
    the golden file measures the production assembly.  But a *new* rebinding
    would silently change what this test means, so it has to fail here first.
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
        f"`python tests/golden_downloader_core.py` after reviewing: {drifted}"
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
