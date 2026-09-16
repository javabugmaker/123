"""Semantic lock for ``conditional_fill_v96`` — the conditional-fill executor.

Why this file exists
--------------------
``conditional_fill_v96.install()`` replaces ``analytics_core._backtest_one_ticker``
wholesale and publishes three constants onto it.  That is not an acceleration --
it is a *different* fill model (a WAIT order fills only if price touches the
entry zone inside the validity window, instead of filling immediately).  Nothing
in the golden fixtures covered it: the analytics golden freezes the backtest
*statistics* helpers, and the 16 functions it captures do not include the
executor.  So a change to the fill model could move every backtest number
without a single gate noticing.

The locks below pin the decisions that define the model rather than a frozen
dump of outputs, because the model is a set of rules and the rules are what a
refactor would break:

* the validity window is exactly ``WAIT_PULLBACK_VALIDITY_TRADING_DAYS`` bars —
  a touch on the last legal day fills, one day later it does not;
* a bar that opens below the zone is a gap-down and rejects the fill;
* an open inside the zone fills at the open, an open above it that trades down
  into the zone fills at the zone high;
* ``install()`` really is what production runs (identity, not equality).

Every one of these was reverse-validated by breaking the corresponding line in
``conditional_fill_v96.py`` and watching the test go red.
"""

from __future__ import annotations

import pandas as pd
import pytest

import conditional_fill_v96 as v96

ROWS = 60


@pytest.fixture
def installed():
    """Install the overlay and restore the prior state afterwards.

    ``install()`` mutates ``analytics_core`` process-wide, and this suite runs
    every test in one process.  Leaving the overlay installed (or removing it
    when something else had already installed it) is exactly the cross-module
    pollution that ``cd63ffd`` fixed for recon.
    """
    was_installed = v96._INSTALLED
    v96.install()
    try:
        yield v96
    finally:
        if not was_installed:
            v96.uninstall()


def _frame() -> pd.DataFrame:
    """Flat OHLCV sitting *above* the entry zone, waiting for a pullback.

    A WAIT order only makes sense while price is away from the zone, so the
    baseline bar must not touch it: ``touches`` requires ``low <= zone_high``,
    and this bar's low (100.8) stays above the zone high (100.5).  Getting this
    backwards makes every test below fill on the first bar and prove nothing.
    """
    return pd.DataFrame(
        {
            "Open": [101.0] * ROWS,
            "High": [101.3] * ROWS,
            "Low": [100.8] * ROWS,
            "Close": [101.0] * ROWS,
            "Volume": [1_000_000.0] * ROWS,
        },
        index=pd.date_range("2024-01-02", periods=ROWS, freq="D"),
    )


def _touch(frame: pd.DataFrame, index: int, low: float = 99.9) -> None:
    """Make one bar pull back through the zone used by the tests."""
    frame.iloc[index, frame.columns.get_loc("Low")] = low
    frame.iloc[index, frame.columns.get_loc("Open")] = 100.0
    frame.iloc[index, frame.columns.get_loc("High")] = 100.4


ZONE_LOW = 99.5
ZONE_HIGH = 100.5
SIGNAL_INDEX = 10


def test_install_publishes_the_conditional_fill_contract(installed) -> None:
    """Production must actually run the conditional executor, plus its knobs."""
    import analytics_core

    assert analytics_core._backtest_one_ticker is installed._backtest_one_ticker, (
        "analytics_core still runs the immediate-fill executor; the overlay is "
        "installed but not attached, so the backtest silently regressed"
    )
    assert analytics_core.CONDITIONAL_FILL_VERSION == installed.CONDITIONAL_FILL_VERSION
    assert (
        analytics_core.WAIT_PULLBACK_VALIDITY_TRADING_DAYS
        == installed.WAIT_PULLBACK_VALIDITY_TRADING_DAYS
    )


def test_validity_window_ends_after_the_configured_number_of_bars(installed) -> None:
    """A touch on the last legal day fills; one bar later it must not.

    The window is ``iloc[start:stop]`` with
    ``stop = signal_index + WAIT_PULLBACK_VALIDITY_TRADING_DAYS + 1``, i.e. the
    last examinable bar is ``signal_index + N``.  An off-by-one here changes
    every backtest fill rate and is invisible in any aggregate metric.
    """
    window = int(installed.WAIT_PULLBACK_VALIDITY_TRADING_DAYS)
    assert window >= 2, "the boundary cases below need a window of at least 2 bars"

    inside = _frame()
    last_legal = SIGNAL_INDEX + window
    _touch(inside, last_legal)
    assert installed._conditional_fill(
        "600000.SH", inside, SIGNAL_INDEX, ZONE_LOW, ZONE_HIGH, is_etf=False
    ) is not None, f"a touch on day +{window} must still fill"

    outside = _frame()
    _touch(outside, last_legal + 1)
    assert (
        installed._conditional_fill(
            "600000.SH", outside, SIGNAL_INDEX, ZONE_LOW, ZONE_HIGH, is_etf=False
        )
        is None
    ), f"a touch on day +{window + 1} is past the validity window and must not fill"


def test_a_bar_opening_below_the_zone_rejects_the_fill(installed) -> None:
    """Gap-down: the limit would have filled worse, so the model walks away.

    The rejection has to be *terminal*, not a skip.  That is why a perfectly
    valid touch is planted on the next bar: if the gap-down bar were merely
    skipped over, the order would fill one bar later and every backtest would
    quietly become more optimistic.  Verified by turning the ``return None``
    into a ``continue`` -- this test is the one that catches it.
    """
    frame = _frame()
    frame.iloc[SIGNAL_INDEX + 1, frame.columns.get_loc("Open")] = 90.0
    frame.iloc[SIGNAL_INDEX + 1, frame.columns.get_loc("Low")] = 89.0
    _touch(frame, SIGNAL_INDEX + 2)
    assert (
        installed._conditional_fill(
            "600000.SH", frame, SIGNAL_INDEX, ZONE_LOW, ZONE_HIGH, is_etf=False
        )
        is None
    )


def test_an_open_inside_the_zone_fills_at_the_open(installed) -> None:
    frame = _frame()
    frame.iloc[SIGNAL_INDEX + 1, frame.columns.get_loc("Open")] = 100.0
    frame.iloc[SIGNAL_INDEX + 1, frame.columns.get_loc("Low")] = 99.9
    frame.iloc[SIGNAL_INDEX + 1, frame.columns.get_loc("High")] = 100.4
    result = installed._conditional_fill(
        "600000.SH", frame, SIGNAL_INDEX, ZONE_LOW, ZONE_HIGH, is_etf=False
    )
    assert result is not None
    _fill_index, fill_price, delay, basis = result
    assert basis == "OPEN_INSIDE_ZONE"
    assert fill_price == 100.0
    assert delay == 1


def test_an_open_above_the_zone_fills_at_the_zone_high(installed) -> None:
    frame = _frame()
    frame.iloc[SIGNAL_INDEX + 1, frame.columns.get_loc("Open")] = 102.0
    frame.iloc[SIGNAL_INDEX + 1, frame.columns.get_loc("Low")] = 99.0
    frame.iloc[SIGNAL_INDEX + 1, frame.columns.get_loc("High")] = 102.5
    result = installed._conditional_fill(
        "600000.SH", frame, SIGNAL_INDEX, ZONE_LOW, ZONE_HIGH, is_etf=False
    )
    assert result is not None
    _fill_index, fill_price, _delay, basis = result
    assert basis == "LIMIT_AT_ZONE_HIGH"
    assert fill_price == ZONE_HIGH


def test_no_touch_inside_the_window_means_no_fill(installed) -> None:
    """The flat frame never trades into the zone, so nothing may fill."""
    assert (
        installed._conditional_fill(
            "600000.SH", _frame(), SIGNAL_INDEX, ZONE_LOW, ZONE_HIGH, is_etf=False
        )
        is None
    )
