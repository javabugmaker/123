"""Equivalence gate for ``signal_lifecycle_core``'s per-signal attribute cluster.

The fixture in ``fixtures/signal_lifecycle_golden.json`` freezes the behaviour of
the 17-function / 407-line dependency closure that
``tests/recon_extraction_targets.py`` reports as self-consistent.  Those
functions now live in ``institution_scanner/signal_attributes``; this gate is
what makes saying so safe.

Three hazards in this cluster are invisible to a naive value fixture:

* ``signal_lifecycle.py:90/95`` do ``passed_filters.map(_core._bool)`` -- an
  overlay fetching a symbol *off the module object* instead of importing it.
  recon compares ``__module__``, so it cannot see the coupling; renaming
  ``_bool`` looked safe and silently broke both canaries on the first attempt at
  this extraction.  ``test_module_level_attribute_reachin_is_still_satisfied``
  is the only gate that can see it.
* The cluster reads eleven ``config`` constants by value.  That is safe *today*
  because none of them is migrated, unlike the ``INSTITUTIONAL_TIER_*`` scores
  that T3' had to late-bind.  ``test_constant_probe_would_catch_a_migration``
  keeps that claim falsifiable by checking a constant that really does move.
* ``_is_active``, ``finalize_signal_ranking`` and
  ``strict_filter_override_mask`` resolve to overlays at runtime.  Moving any of
  them would leave the overlay writing to ``signal_lifecycle_core`` while
  production read the extracted copy -- and nothing would raise.  They are
  captured as canaries instead.

Recapture with ``python tests/golden_signal_lifecycle.py`` -- and only after
reading the diff.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import golden_signal_lifecycle as golden

#: Every branch ``_status`` can take.  ``_status`` has seven exits and the corpus
#: is small enough that drifting into one of them is a real risk; a value gate
#: that only ever saw "NEW" would still pass while protecting nothing.
EXPECTED_STATUSES = {"", "CONFIRMED", "FAILED", "NEW", "STRENGTHEN", "WATCH", "WEAKEN"}

#: The reach-in call sites that no reflection-based tool can see.
REACHIN_SOURCE = Path(__file__).resolve().parents[1] / "signal_lifecycle.py"


def test_fixture_matches_live_behaviour() -> None:
    expected = golden.load()
    with Path(golden.FIXTURE_PATH).open(encoding="utf-8") as handle:
        assert json.load(handle) == expected

    actual = golden.capture()

    assert actual["functions"] == expected["functions"]
    # Provenance is part of the record: where each function resolved to is as
    # much a behaviour as the value it returned.  An intentional move must come
    # with a deliberate recapture, not drift in silently.
    assert actual["function_provenance"] == expected["function_provenance"]
    assert set(actual["cases"]) == set(expected["cases"]), (
        "Case set drifted: " + ", ".join(sorted(set(actual["cases"]) ^ set(expected["cases"])))
    )
    mismatched = {
        label: (expected["cases"][label], actual["cases"][label])
        for label in sorted(expected["cases"])
        if actual["cases"][label] != expected["cases"][label]
    }
    assert not mismatched, "Behaviour changed for: " + ", ".join(sorted(mismatched))


def test_every_captured_case_produced_a_value() -> None:
    """A fixture full of ``raised`` entries would be a gate that cannot fail."""
    payload = golden.load()
    raised = {
        label: case["raised"] for label, case in payload["cases"].items() if "raised" in case
    }
    assert not raised, f"Cases raised instead of producing a value: {raised}"


def test_move_candidates_resolve_to_the_extracted_module() -> None:
    """Every candidate must resolve to ``MOVE_HOME`` -- asserted live.

    Pre-move this read "must resolve to ``signal_lifecycle_core``"; post-move it
    reads "must resolve to the extracted module".  Either way the point is the
    same: any *third* answer is an overlay that rebound the function on
    ``signal_lifecycle_core``, which would mean the overlay keeps patching a
    module production no longer reads.
    """
    provenance = golden.live_provenance()
    off_home = {
        name: provenance[name]
        for name in golden.MOVE_CANDIDATES
        if not provenance[name].startswith(golden.MOVE_HOME + ".")
    }
    assert not off_home, (
        f"These functions no longer resolve to {golden.MOVE_HOME} -- most likely "
        "an overlay rebound them on signal_lifecycle_core, which would silently "
        "detach production from the extracted copy: "
        + ", ".join(f"{k} -> {v}" for k, v in sorted(off_home.items()))
    )


def test_patched_canaries_stay_out_of_the_extracted_module() -> None:
    """Pins *why* the three canaries are excluded from the extraction.

    All three resolve to overlays at runtime.  None may land in
    ``signal_attributes``, and none may stop being patched either -- if
    ``runtime_v83`` stops installing, the fixture's meaning changes rather than
    the code's, and that is worth failing loudly about.
    """
    provenance = golden.live_provenance()
    for name in golden.PATCHED_CANARIES:
        where = provenance[name]
        assert not where.startswith(golden.MOVE_HOME + "."), (
            f"{name} was moved into {golden.MOVE_HOME}; production would stop "
            "seeing the overlay that rebinds it."
        )
        assert not where.startswith(golden.STAY_HOME + "."), (
            f"{name} now resolves to {where}; the overlay that used to rebind it "
            "appears to have stopped installing, which changes the meaning of "
            "the fixture rather than of this test."
        )


def test_extracted_constants_match_live_config() -> None:
    """The cluster's eleven constants must equal what ``config`` says right now.

    The extracted module imports them by value, so it holds whatever ``config``
    held at first import.  That is only safe while nothing rewrites ``config``
    afterwards -- which is why this compares against ``config`` itself rather
    than against ``signal_lifecycle_core``: the old module bound the same
    constants its own way, so agreement between the two proved nothing about
    whether either was stale.

    Note this gate is only meaningful because the constants *can* move; see
    ``test_constant_probe_would_catch_a_migration``.
    """
    import config

    mismatched = {
        name: (golden.resolved_constant(name), getattr(config, name))
        for name in golden.CONSTANT_NAMES
        if golden.resolved_constant(name) != getattr(config, name)
    }
    assert not mismatched, (
        "The extracted cluster is reading stale constants -- it bound them by "
        "value before config settled: "
        + ", ".join(f"{k}: extracted={a!r} vs config={b!r}" for k, (a, b) in sorted(mismatched.items()))
    )


def test_constant_probe_would_catch_a_migration() -> None:
    """Guard the claim above, not just the values.

    "None of these constants is migrated" is only worth asserting if the probe
    can tell a migrated constant from an unmigrated one.  ``config`` under the
    production assembly really does carry 36.0825 where the source says 35.0 for
    ``INSTITUTIONAL_TIER_A_SCORE``, so the probe compares live values that are
    capable of moving.  If a future refactor imports ``config`` in a way that
    snapshots it early, this fails first.
    """
    import config

    assert config.INSTITUTIONAL_TIER_A_SCORE != 35.0, (
        "INSTITUTIONAL_TIER_A_SCORE still reads its pre-migration value of 35.0; "
        "score_threshold_migration_v95 is no longer installing under this "
        "import order, so test_extracted_constants_match_the_module_they_left "
        "would pass even if the extracted module had snapshotted config early."
    )


def test_module_level_attribute_reachin_is_still_satisfied() -> None:
    """The one coupling no reflection-based tool can see.

    ``signal_lifecycle.py`` reaches into the module with ``_core._bool`` rather
    than importing the name.  The first attempt at this extraction merged
    ``_bool`` into ``institution_scanner._common._truthy`` -- the bodies were
    byte-identical -- and every recon check still reported the cluster clean.
    Two canaries went from real values to ``null`` because the module no longer
    had a ``_bool`` attribute to fetch.

    This test asserts the thing that actually broke, plus that the call site
    still exists, so "cleaning up" the reach-in has to be a deliberate edit here
    rather than a silent one.
    """
    core = importlib.import_module(golden.STAY_HOME)
    extracted = importlib.import_module(golden.MOVE_HOME)

    assert getattr(core, "_bool", None) is getattr(extracted, "_bool"), (
        f"{golden.STAY_HOME}._bool is not the extracted function; an overlay "
        "that fetches it by attribute (signal_lifecycle.py:90/95) would either "
        "raise or silently get a different predicate."
    )
    assert "_core._bool" in REACHIN_SOURCE.read_text(encoding="utf-8"), (
        "signal_lifecycle.py no longer reaches in via _core._bool. If that was "
        "an intentional cleanup, delete this assertion -- but do it in the same "
        "commit that removes the re-export, or the two drift apart."
    )


def test_bool_corpus_covers_both_outcomes() -> None:
    """An all-true corpus makes ``_bool`` unable to detect a broken predicate."""
    payload = golden.load()
    outcomes = {
        case["value"] for label, case in payload["cases"].items() if label.startswith("bool/")
    }
    assert outcomes == {True, False}, (
        f"_bool cases produced only {sorted(outcomes)}; a change to the accepted "
        "spellings would pass unnoticed"
    )


def test_every_status_branch_is_exercised() -> None:
    """All seven ``_status`` exits must appear in the corpus."""
    payload = golden.load()
    statuses = {
        case["value"]
        for label, case in payload["cases"].items()
        if label.startswith("status/") and "value" in case
    }
    missing = EXPECTED_STATUSES - statuses
    assert not missing, f"status branches never produced: {sorted(missing)}"


def test_number_handles_non_finite_values() -> None:
    """``_number`` exists to normalise inf/-inf/NaN; the corpus must include them."""
    payload = golden.load()
    values = payload["cases"]["number/inf"]["value"]
    series = values.get("__series__") if isinstance(values, dict) else values
    assert series is not None, "number/inf produced no series"
    assert all(value is not None for value in series), (
        f"number/inf still carries non-finite values ({series}); the "
        "replace/fillna pipeline is not being exercised"
    )
