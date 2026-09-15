"""Equivalence gate for ``report_core``'s candidate-selection cluster.

The fixture in ``fixtures/report_core_golden.json`` freezes the behaviour of the
11-function / 323-line dependency closure around ``_apply_research_policy``,
``_ensure_diversity_columns``, ``_diversify_ranked_candidates`` and
``_institutional_tier`` -- the cluster ``tests/recon_extraction_targets.py``
reports as self-consistent.  ``report_core`` has 91 bytes of budget left, so
this cluster is what makes the next commit possible at all.

The gate exists because two hazards in this cluster are invisible to a value
fixture that was not designed for them:

* ``score_threshold_migration_v95`` rewrites ``config``'s tier thresholds from
  35.0/30.0/25.0 to 36.0825/30.9278/25.7732 at import time.  ``report_core``
  sees the migrated values only because of the position of its ``analytics``
  import relative to its ``config`` import -- an accident of ordering that an
  extracted module would not inherit.  ``MIGRATION_PROBES`` and
  ``test_migration_probes_pin_the_migrated_thresholds`` exist solely to make
  that accident falsifiable.
* ``report_determinism`` rebinds ``_rankable_results``, which is why it is
  captured as a canary and never moved.

Recapture the fixture with ``python tests/golden_report_core.py`` -- and only
after reading the diff.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import golden_report_core as golden

#: Every tier the function can return.  If the corpus drifts so that one of
#: these stops appearing, the value-matching test above still passes while
#: protecting nothing about the branch that produced it.
EXPECTED_TIERS = {"A级机构启动", "B级观察", "C级价值观察", "D级等待确认", "D级陷阱池"}


def test_fixture_matches_live_behaviour() -> None:
    expected = golden.load()
    with Path(golden.FIXTURE_PATH).open(encoding="utf-8") as handle:
        assert json.load(handle) == expected

    actual = golden.capture()

    assert actual["functions"] == expected["functions"]
    # Provenance is part of the record: where each function resolved to is as
    # much a behaviour as the string it returned.  An intentional move must be
    # accompanied by a deliberate recapture, not drift in silently.
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


def test_move_candidates_are_not_patched_by_any_overlay() -> None:
    """Every candidate must resolve to ``MOVE_HOME`` -- asserted live.

    Pre-move this read "must resolve to ``report_core``"; post-move it reads
    "must resolve to the extracted module".  Either way the point is the same:
    any *third* answer is an overlay that rebound the function on
    ``report_core``, which would mean the overlay keeps patching ``report_core``
    while production reads the extracted copy -- and nothing would raise.
    """
    provenance = golden.live_provenance()
    off_home = {
        name: provenance[name]
        for name in golden.MOVE_CANDIDATES
        if not provenance[name].startswith(golden.MOVE_HOME + ".")
    }
    assert not off_home, (
        f"These functions no longer resolve to {golden.MOVE_HOME} -- most likely "
        "an overlay rebound them on report_core, which would silently detach "
        "production from the extracted copy: "
        + ", ".join(f"{k} -> {v}" for k, v in sorted(off_home.items()))
    )


def test_shared_moves_resolve_to_the_common_module() -> None:
    """``_truthy`` lives in ``_common``, not in the extracted module.

    It is part of this cluster -- ``_etf_theme_key``, ``_ensure_diversity_columns``
    and ``_diversify_ranked_candidates`` all call it -- but it also had a
    byte-identical twin in ``publication_renderer``.  Merging them into
    ``_common`` is what ``test_canonical_package_discipline`` demands; the
    duplication had been invisible only because the root-level copy sat outside
    the package the gate scans.  This pins where the merged copy went.
    """
    provenance = golden.live_provenance()
    off_home = {
        name: provenance[name]
        for name in golden.SHARED_MOVES
        if not provenance[name].startswith(golden.SHARED_HOME + ".")
    }
    assert not off_home, (
        f"These no longer resolve to {golden.SHARED_HOME}: "
        + ", ".join(f"{k} -> {v}" for k, v in sorted(off_home.items()))
    )


def test_extracted_module_shares_the_migrated_thresholds() -> None:
    """Catch the regression this cluster is uniquely exposed to.

    ``score_threshold_migration_v95`` rewrites ``config``'s tier thresholds
    after import.  Had the extracted module bound them by value it would hold
    whichever snapshot was current when it was first imported -- 35.0/30.0/25.0
    if that happened before ``score`` was imported, 36.0825/30.9278/25.7732
    after.  Only the three ``MIGRATION_PROBES`` cases would notice; the other 60
    would match either way.

    ``report_core`` still holds its own by-value copy from before the move, so
    comparing the two routes is what proves the late binding in
    ``report_selection`` resolves to the same numbers production has always
    used.
    """
    core = importlib.import_module("report_core")
    mismatched = {
        name: (golden.resolved_global(name), getattr(core, name))
        for name in golden.LATE_BOUND_NAMES
        if golden.resolved_global(name) != getattr(core, name)
    }
    assert not mismatched, (
        "The extracted cluster reads different tier thresholds than "
        "report_core -- the score_threshold_migration_v95 patch no longer "
        "reaches it: "
        + ", ".join(f"{k}: extracted={a!r} vs core={b!r}" for k, (a, b) in sorted(mismatched.items()))
    )


def test_patched_canary_stays_out_of_report_core() -> None:
    """Pins *why* ``_rankable_results`` is excluded from the extraction.

    ``report_determinism`` rebinds it at import time, so live it resolves to
    ``report_determinism``'s own closure rather than to ``report_core``.  It is
    the report-side counterpart of ``_date_balanced_weights`` in the analytics
    extraction.
    """
    provenance = golden.live_provenance()["_rankable_results"]
    assert not provenance.startswith(golden.STAY_HOME + "."), (
        f"_rankable_results now resolves to {provenance}; the report_determinism "
        "patch appears to have stopped installing, which changes the meaning of "
        "the fixture rather than of this test."
    )
    assert not provenance.startswith(golden.MOVE_HOME + "."), (
        "A rebound function was moved into "
        f"{golden.MOVE_HOME}; production would stop seeing the report_determinism patch."
    )


def test_migration_probes_pin_the_migrated_thresholds() -> None:
    """The gate that exists because of how this cluster nearly regressed.

    ``score.py`` calls ``score_threshold_migration_v95.install(config)`` at
    import time, rewriting ``INSTITUTIONAL_TIER_A/B/C_SCORE``.  ``report_core``
    holds the migrated values only because ``from analytics import ...`` (line
    25) pulls in ``score`` before ``from config import ...`` (line 32) runs.
    Extracting the cluster into a module that imports ``config`` by value would
    freeze whichever snapshot happened to be current, and every captured case
    except these three would still match.

    Each probe score sits inside a migration gap, so it returns a different
    tier under each constant set.
    """
    core = importlib.import_module("report_core")
    tier = getattr(core, "_institutional_tier")
    wrong = {}
    for probe in golden.load()["migration_probes"]:
        actual = tier(golden._tier_probe(probe["score"]))
        if actual != probe["migrated"]:
            wrong[probe["score"]] = (actual, probe["migrated"])
    assert not wrong, (
        "Tier thresholds are not the migrated ones -- the extracted cluster "
        "resolved INSTITUTIONAL_TIER_*_SCORE from a stale snapshot: "
        + ", ".join(f"score {s}: got {a!r}, want {b!r}" for s, (a, b) in sorted(wrong.items()))
    )


def test_migration_probes_are_still_differential() -> None:
    """Guard the probes themselves, not the code.

    A probe whose migrated and legacy answers are identical proves nothing.  If
    someone widens or narrows the migration factors until a probe no longer
    straddles the boundary, this fails instead of leaving a gate that looks
    green while protecting nothing.
    """
    for score, migrated, legacy in golden.MIGRATION_PROBES:
        assert migrated != legacy, (
            f"probe score {score} returns {migrated!r} under both constant sets; "
            "it no longer detects a lost migration"
        )


def test_all_institutional_tiers_are_exercised() -> None:
    """All five tier outputs must appear in the corpus.

    ``_institutional_tier`` has four tier branches plus a value-trap override
    that short-circuits all of them.  If the corpus drifts so every case lands
    in one tier, the value gate still passes while protecting nothing about the
    boundaries.
    """
    payload = golden.load()
    tiers = {
        case["value"]
        for label, case in payload["cases"].items()
        if label.startswith("tier/") and "value" in case
    }
    missing = EXPECTED_TIERS - tiers
    assert not missing, f"tiers never produced: {sorted(missing)}"


def test_research_policy_actually_excludes() -> None:
    """An all-eligible corpus makes ``_apply_research_policy`` unable to fail."""
    payload = golden.load()
    frame = payload["cases"]["apply_research_policy/candidates"]["value"]["__frame__"]
    position = frame["columns"].index("ResearchEligible")
    eligible = [row[position] for row in frame["rows"]]
    assert any(not value for value in eligible), (
        "every row is ResearchEligible; the ETF exclusion branch is never exercised"
    )
    assert any(eligible), "no row is ResearchEligible; the corpus is degenerate on the other side"


def test_diversity_penalty_is_not_trivially_one() -> None:
    """The cluster-penalty arithmetic must actually move a number."""
    payload = golden.load()
    frame = payload["cases"]["diversify/full"]["value"]["__frame__"]
    position = frame["columns"].index("ResearchDiversityPenalty")
    penalties = [row[position] for row in frame["rows"]]
    assert any(value < 1.0 for value in penalties), (
        f"every diversity penalty is 1.0 ({penalties}); THEME_CLUSTER_SOFT_PENALTY "
        "has no observable effect and a change to it would pass unnoticed"
    )


def test_industry_cap_changes_the_selection() -> None:
    """``max_per_stock_industry`` must be observable, not merely accepted.

    If the greedy loop ignored the cap the two cases would return the same
    rows and the fixture would freeze a parameter that does nothing.
    """
    payload = golden.load()

    def tickers(label: str) -> list[str]:
        frame = payload["cases"][label]["value"]["__frame__"]
        position = frame["columns"].index("Ticker")
        return [row[position] for row in frame["rows"]]

    assert tickers("diversify/one_per_industry") != tickers("diversify/full"), (
        "tightening max_per_stock_industry produced an identical selection"
    )
