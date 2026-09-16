"""Semantic lock for ``backtest_math_integrity_v94``.

Why this file exists
--------------------
``backtest_math_integrity_v94.install()`` rebinds five things across three
modules.  Two of them are *math*, not plumbing:

* ``analytics_core._weighted_profit_factor`` — the raw implementation returns
  ``+inf`` when a held-out sample has wins but no losses, and ``+inf`` does not
  survive JSON serialisation.  The overlay caps it at ``PROFIT_FACTOR_SCORE_CAP``
  so an all-winning sample saturates at the same 3.0 the ranking path uses.
* ``analytics_core.BacktestSummary.to_dict`` — gains a ``split_policy`` key that
  discloses the train/validation/test purge rule.

Neither had a gate.  The analytics golden freezes the 16 backtest *statistics*
helpers and does not include ``_weighted_profit_factor`` or ``BacktestSummary``,
so the cap could be dropped (or raised) and every existing test would stay green
while published profit factors turned into ``null``.

These locks pin the *rules*, and each one was reverse-validated by breaking the
corresponding line in ``backtest_math_integrity_v94.py``.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

import analytics
import analytics_core
import backtest_math_integrity_v94 as v94
import model_calibration


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
