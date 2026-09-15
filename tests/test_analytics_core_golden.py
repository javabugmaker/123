"""Equivalence gate for ``analytics_core``'s backtest statistics helpers.

The fixture in ``fixtures/analytics_core_golden.json`` freezes the behaviour of
16 helpers that are candidates for extraction out of the 3837-line
``analytics_core`` (which has 5 lines of byte budget left).  This test proves an
extraction is behaviour-preserving: while the helpers live in ``analytics_core``
it passes trivially; once they move, ``function_provenance`` becomes the record
of where they went and every captured value must still match.

Recapture the fixture with ``python tests/golden_analytics_core.py`` — and only
after reading the diff.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import golden_analytics_core as golden


def test_fixture_matches_live_behaviour() -> None:
    expected = golden.load()
    with Path(golden.FIXTURE_PATH).open(encoding="utf-8") as handle:
        assert json.load(handle) == expected

    actual = golden.capture()

    assert actual["functions"] == expected["functions"]
    # Provenance is part of the record: where each helper resolved to is as much
    # a behaviour as the number it returned.  An intentional move must be
    # accompanied by a deliberate recapture, not drift in silently.
    assert actual["function_provenance"] == expected["function_provenance"]
    assert set(actual["cases"]) == set(expected["cases"]), (
        "Case set drifted: "
        + ", ".join(sorted(set(actual["cases"]) ^ set(expected["cases"])))
    )
    mismatched = {
        label: (expected["cases"][label], actual["cases"][label])
        for label in sorted(expected["cases"])
        if actual["cases"][label] != expected["cases"][label]
    }
    assert not mismatched, "Behaviour changed for: " + ", ".join(sorted(mismatched))


def test_every_captured_case_produced_a_value() -> None:
    """A fixture full of ``raised`` entries would be a gate that cannot fail.

    Every case is expected to exercise a real branch; if the corpus stops
    feeding a function correctly, this fails loudly instead of silently
    freezing an exception name.
    """
    payload = golden.load()
    raised = {
        label: case["raised"]
        for label, case in payload["cases"].items()
        if "raised" in case
    }
    assert not raised, f"Cases raised instead of producing a value: {raised}"


def test_confidence_tiers_are_all_exercised() -> None:
    """Guard the corpus, not the code: all four evidence tiers must be hit.

    ``_backtest_evidence`` branches on sample-count thresholds.  If the corpus
    drifts so every case lands in one tier, the gate above still passes while
    protecting nothing about the tier boundaries.
    """
    payload = golden.load()
    tiers = {
        tuple(case["value"])[2]
        for label, case in payload["cases"].items()
        if label.startswith("backtest_evidence/") and "value" in case
    }
    assert tiers == {"样本不足", "低可信度", "中可信度", "高可信度"}, f"tiers={tiers}"


def test_spearman_is_not_degenerate() -> None:
    """A perfectly rank-correlated corpus makes ``_spearman`` unable to fail.

    If score and target are both monotonic the estimator returns 1.0 no matter
    how its arithmetic is rearranged, so pin that the corpus is non-trivial.
    """
    payload = golden.load()
    values: list[float] = []
    for label, case in payload["cases"].items():
        if label.startswith("spearman/") and "value" in case:
            values.append(float(case["value"]))
    assert values, "no spearman cases captured"
    assert any(abs(value) < 0.95 for value in values), (
        f"spearman corpus is degenerate (all |rho| >= 0.95): {values}"
    )


def test_move_candidates_are_not_patched_by_any_overlay() -> None:
    """The precondition for extracting them, re-checked on every run.

    A helper that some overlay rebinds cannot be moved: the overlay would keep
    writing to ``analytics_core`` while the extracted copy kept its own
    original, and nothing would raise.  Post-move the assertion flips -- every
    candidate must now resolve to ``MOVE_HOME``, so an overlay that rebinds
    ``analytics_core.<name>`` resolves to the overlay's own module and fails.
    """
    provenance = golden.live_provenance()
    off_home = {
        name: provenance[name]
        for name in golden.MOVE_CANDIDATES
        if not provenance[name].startswith(golden.MOVE_HOME + ".")
    }
    assert not off_home, (
        f"These helpers no longer resolve to {golden.MOVE_HOME} -- most likely "
        "an overlay rebound them on analytics_core, which would silently "
        "detach production from the extracted copy: "
        + ", ".join(f"{k} -> {v}" for k, v in sorted(off_home.items()))
    )


def test_patched_canaries_stay_outside_analytics_core() -> None:
    """Pins *why* ``_bucket_rows`` is excluded from the extraction.

    ``_date_balanced_weights`` is rebound by ``backtest_math_integrity_v94``
    through its ``analytics_module`` parameter -- invisible to a module-alias
    scan, and the reason the §22 free/patched table was wrong.  ``_bucket_rows``
    calls it, so both must stay put.
    """
    payload = golden.load()
    provenance: dict[str, str] = payload["function_provenance"]
    assert not provenance["_date_balanced_weights"].startswith("analytics_core.")
    # _bucket_rows itself is unpatched; it stays only because of its dependency.
    assert provenance["_bucket_rows"].startswith("analytics_core.")


def test_extracted_module_shares_overlay_patched_bindings() -> None:
    """Catch the regression value fixtures are structurally unable to see.

    ``indicator_acceleration_v77`` swaps ``compute_volume_profile`` for an
    accelerated implementation *after* import.  Both implementations return the
    same numbers, so every captured value matches either way -- a pure
    performance regression that ``test_fixture_matches_live_behaviour`` waves
    through.  Only an identity check against what analytics_core resolves to can
    detect it, which is why the extracted module imports ``indicators`` as a
    module and resolves the attribute at call time.
    """
    core = importlib.import_module("analytics_core")
    mismatched = {
        name: (
            f"{golden.resolved_global(name).__module__}."
            f"{golden.resolved_global(name).__qualname__}",
            f"{getattr(core, name).__module__}.{getattr(core, name).__qualname__}",
        )
        for name in golden.LATE_BOUND_NAMES
        if golden.resolved_global(name) is not getattr(core, name)
    }
    assert not mismatched, (
        "Extracted helpers would use a different implementation than "
        "analytics_core -- the overlay patch no longer reaches them: "
        + ", ".join(f"{k}: extracted={v[0]} vs core={v[1]}" for k, v in sorted(mismatched.items()))
    )


def test_canaries_never_enter_the_extracted_module() -> None:
    """Same pin as above, but asserted live.

    The fixture only records where things were; this records where they are.  If
    someone later moves a canary into the extracted module, the frozen assertion
    above stays green while production silently detaches from the patch.

    Note the two canaries are pinned *differently*, because they are excluded
    for different reasons:

    * ``_date_balanced_weights`` is rebound by an overlay, so live it resolves
      to the overlay's module -- asserting it sits in ``analytics_core`` would
      be wrong.  The invariant is only that it must not be extracted.
    * ``_bucket_rows`` is not patched at all; it is excluded purely because it
      *calls* the patched helper, so it must keep resolving to analytics_core.
    """
    provenance = golden.live_provenance()
    extracted = {
        name: provenance[name]
        for name in golden.PATCHED_CANARIES
        if provenance[name].startswith(golden.MOVE_HOME + ".")
    }
    assert not extracted, (
        "A canary was moved into "
        f"{golden.MOVE_HOME}; production would stop seeing the "
        "backtest_math_integrity_v94 patch: "
        + ", ".join(f"{k} -> {v}" for k, v in sorted(extracted.items()))
    )

    bucket = provenance["_bucket_rows"]
    assert bucket.startswith(golden.STAY_HOME + "."), (
        f"_bucket_rows resolves to {bucket}, not {golden.STAY_HOME}"
    )
