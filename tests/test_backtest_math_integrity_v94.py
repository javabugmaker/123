"""Semantic lock for ``backtest_math_integrity_v94``.

Why this file exists
--------------------
``backtest_math_integrity_v94.install()`` rebinds five things across three
modules.  Two of them used to be *math*, not plumbing:

* ``analytics_core._weighted_profit_factor`` — the raw implementation returns
  ``+inf`` when a held-out sample has wins but no losses, and ``+inf`` does not
  survive JSON serialisation.  The overlay capped it at
  ``PROFIT_FACTOR_SCORE_CAP`` so an all-winning sample saturates at the same 3.0
  the ranking path uses.
* ``analytics_core.BacktestSummary.to_dict`` — gains a ``split_policy`` key that
  discloses the train/validation/test purge rule.

Neither had a gate.  The analytics golden freezes the 16 backtest *statistics*
helpers and does not include ``_weighted_profit_factor`` or ``BacktestSummary``,
so the cap could be dropped (or raised) and every existing test would stay green
while published profit factors turned into ``null``.

§9.3 #2a afterwards sank both rules into ``analytics_core`` itself, so the
numbers are correct with or without ``install()``.  The behaviour locks below
therefore also pin *where the rule lives*: if anybody re-introduces an overlay
wrapper, ``__module__`` stops being ``analytics_core`` and the suite goes red.
That is the point — an overlay copy is exactly the failure mode this file was
written to prevent.

These locks pin the *rules*, and each one was reverse-validated by breaking the
corresponding line in the implementation.
"""

from __future__ import annotations

import functools
import json
import pathlib
import subprocess
import sys

import numpy as np
import pandas as pd
import pytest

import analytics
import analytics_core
import backtest_math_integrity_v94 as v94
import model_calibration

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


@functools.lru_cache(maxsize=1)
def _source_state() -> dict[str, object]:
    """Read ``analytics_core`` in a process that installs no overlay at all.

    In-process this is unanswerable: ``analytics.py`` rebinds
    ``BacktestSummary.to_dict`` to add resonance output and then swaps itself
    into ``sys.modules`` as ``analytics_core``, so every live attribute resolves
    to the facade.  A fresh interpreter importing ``analytics_core`` alone is
    the only view of the un-overridden source — the state §9.3 #2a is about.
    """
    probe = (
        "import json, pandas as pd, analytics_core as c;"
        "print('@@@' + json.dumps({"
        "'to_dict_module': c.BacktestSummary.to_dict.__module__,"
        "'split_policy': c.BacktestSummary().to_dict().get('split_policy'),"
        "'pf_module': c._weighted_profit_factor.__module__,"
        "'pf_all_win': c._weighted_profit_factor("
        "pd.Series([1.0, 2.0, 3.0]), pd.Series([1.0, 1.0, 1.0])),"
        "}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    # A probe that produced nothing must fail loudly; silently defaulting here
    # would turn this gate into a no-op.
    assert result.returncode == 0, (
        "source probe crashed:\n" + (result.stderr or "")[-800:]
    )
    marker = next(
        (line for line in result.stdout.splitlines() if line.startswith("@@@")),
        None,
    )
    assert marker is not None, (
        "source probe printed no result:\n" + (result.stdout or "")[-800:]
    )
    return json.loads(marker[3:])


@pytest.fixture(scope="module")
def installed():
    """Install the overlay once; production installs it at import time too.

    There is deliberately no teardown: the module has no ``uninstall()``, and
    every production entry point leaves it installed.  Tearing it down would
    make the test process less like production, not more.
    """
    v94.install(analytics, model_calibration)
    return v94


def test_all_winning_sample_caps_instead_of_returning_infinity(installed) -> None:
    """Wins with no losses must saturate at the cap, not become ``+inf``.

    ``+inf`` is what breaks: it is not valid JSON, so a perfect held-out sample
    serialises to ``null`` in the published report.
    """
    values = pd.Series([1.0, 2.0, 3.0])
    weights = pd.Series([1.0, 1.0, 1.0])
    result = analytics_core._weighted_profit_factor(values, weights)
    assert np.isfinite(result), f"profit factor is not finite: {result}"
    assert result == installed.PROFIT_FACTOR_SCORE_CAP
    # Pin the number itself, not just the wiring.  ``result == THE_CONSTANT``
    # keeps passing no matter what the constant is set to, so raising the cap
    # would silently change every published profit factor.  Treat 3.0 the way
    # the golden fixtures treat a value: changing it is a deliberate act.
    assert installed.PROFIT_FACTOR_SCORE_CAP == 3.0, (
        "the profit-factor cap moved; ranking saturates at 3.0, so a different "
        "cap here would disagree with the ranking path"
    )
    # The whole point of the cap: it has to survive a JSON round-trip.
    assert json.loads(json.dumps({"pf": result}))["pf"] == result


def test_a_finite_profit_factor_is_passed_through_unchanged(installed) -> None:
    """The cap is an edge-case guard, not a rescale of normal samples."""
    values = pd.Series([2.0, -1.0])
    weights = pd.Series([1.0, 1.0])
    assert analytics_core._weighted_profit_factor(values, weights) == 2.0


def test_an_empty_sample_stays_not_a_number(installed) -> None:
    """Neither profit nor loss: the cap must not invent a number."""
    values = pd.Series([0.0, 0.0])
    weights = pd.Series([1.0, 1.0])
    assert np.isnan(analytics_core._weighted_profit_factor(values, weights))


def test_summary_discloses_the_split_policy(installed) -> None:
    """Every published summary must say how the splits were purged."""
    payload = analytics_core.BacktestSummary().to_dict()
    assert payload["split_policy"] == installed.BACKTEST_SPLIT_POLICY
    assert "purged" in installed.BACKTEST_SPLIT_POLICY


def test_the_cap_belongs_to_analytics_core_not_to_an_overlay() -> None:
    """§9.3 #2a: the cap must not be re-applied by an overlay.

    Sinking it into ``analytics_core`` means the number is right even when
    nothing is installed.  Asserting the resolved function's ``__module__``
    catches the reverse move: under the old overlay the resolved object was a
    closure defined in ``backtest_math_integrity_v94``.
    """
    resolved = analytics_core._weighted_profit_factor
    assert resolved.__module__ == "analytics_core", (
        f"_weighted_profit_factor resolves to {resolved.__module__}; the cap has "
        "been moved back into an overlay, so published profit factors would "
        "depend on install order again"
    )
    assert analytics_core.PROFIT_FACTOR_SCORE_CAP == 3.0


def test_the_cap_holds_in_a_process_that_installs_nothing() -> None:
    """The number itself, read off un-overridden source."""
    state = _source_state()
    assert state["pf_module"] == "analytics_core"
    assert state["pf_all_win"] == 3.0, (
        f"un-overridden _weighted_profit_factor returns {state['pf_all_win']!r} "
        "for an all-winning sample; it must saturate at 3.0"
    )


def test_the_split_policy_survives_without_any_overlay() -> None:
    """Same pin for ``split_policy``, probed in a process that installs nothing."""
    state = _source_state()
    assert state["to_dict_module"] == "analytics_core", (
        f"un-overridden to_dict resolves to {state['to_dict_module']}; "
        "split_policy is being injected by an overlay rather than by the model"
    )
    assert state["split_policy"] == analytics_core.BACKTEST_SPLIT_POLICY, (
        f"un-overridden to_dict reports split_policy={state['split_policy']!r}"
    )


def test_the_two_constants_have_exactly_one_definition() -> None:
    """v94 re-exports them; it must not grow a second, drifting copy."""
    assert v94.PROFIT_FACTOR_SCORE_CAP == analytics_core.PROFIT_FACTOR_SCORE_CAP
    assert v94.BACKTEST_SPLIT_POLICY == analytics_core.BACKTEST_SPLIT_POLICY


def test_install_publishes_the_math_contract(installed) -> None:
    """The cap and the split policy must be readable off the analytics module."""
    assert analytics.PROFIT_FACTOR_SCORE_CAP == installed.PROFIT_FACTOR_SCORE_CAP
    assert analytics.BACKTEST_SPLIT_POLICY == installed.BACKTEST_SPLIT_POLICY
    assert (
        analytics.PRODUCTION_BACKTEST_MATH_VERSION
        == installed.PRODUCTION_BACKTEST_MATH_VERSION
    )
    assert analytics._date_balanced_weights is installed.date_balanced_evidence_weights


def test_universe_evidence_weight_haircuts_provisional_membership(installed) -> None:
    """Point-in-time membership uncertainty is a 0.25 haircut, not a pass."""
    frame = pd.DataFrame(
        {
            "universe_snapshot_status": [
                "ELIGIBLE",
                "PROVISIONAL",
                "INELIGIBLE",
                "EXCLUDED",
                "eligible",
            ]
        }
    )
    weights = installed.universe_evidence_weight(frame).tolist()
    assert weights == [1.0, 0.25, 0.0, 0.0, 1.0]


def test_a_crowded_day_gets_at_most_one_unit_of_influence(installed) -> None:
    """Overlap independence: same-day signals share a single unit, not N."""
    frame = pd.DataFrame(
        {
            "entry_date": ["2024-01-02"] * 4 + ["2024-01-03"],
            "sample_weight": [1.0] * 5,
            "universe_snapshot_status": ["ELIGIBLE"] * 5,
        }
    )
    balanced = installed.date_balanced_evidence_weights(frame)
    per_day = balanced.groupby(frame["entry_date"]).sum()
    assert per_day.loc["2024-01-02"] == pytest.approx(1.0)
    assert per_day.loc["2024-01-03"] == pytest.approx(1.0)


def test_signal_semantic_rows_keep_only_signal_levels_with_an_entry_signal(
    installed,
) -> None:
    """Peer priors may only fall back through levels that keep entry-signal."""
    rows = [
        {"level": "asset_signal", "entry_signal": "BUY_NOW"},
        {"level": "global", "entry_signal": "BUY_NOW"},
        {"level": "asset_signal", "entry_signal": ""},
        {"level": "asset_signal"},
    ]
    kept = installed.signal_semantic_calibration_rows(rows)
    assert kept == [{"level": "asset_signal", "entry_signal": "BUY_NOW"}]
