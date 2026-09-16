"""Convergence lock for the "enough cached history" judgement (§9.3 #6).

Why this file exists
--------------------
Seven call sites --- ``analytics_core`` twice, plus five root overlays
(``backtest_cache_acceleration_v80``, ``backtest_incremental_v78``,
``backtest_sample_acceleration_v80``, ``backtest_vectorization_v98``,
``conditional_fill_v96``) --- each spelled the same rule independently::

    if frame is None or len(frame) < 300:

They all meant one thing: below 300 bars the indicators a backtest reads are
not fully defined, so the frame is skipped instead of being scored on partial
history.  Nothing forced the copies to agree.  Changing six of them and
forgetting the seventh would have moved the effective threshold for one code
path only, and every existing test would have stayed green while a slice of the
universe silently started (or stopped) being backtested at a different cut-off.

They now all call ``analytics_core._has_backtest_history``, and the number
itself is ``config_core.BACKTEST_MIN_HISTORY_BARS``.

Two gates, because one is not enough:

* the *static* gate stops anyone re-introducing a literal (the failure mode is
  duplication, and behaviour tests cannot see a second copy of a rule);
* the *behaviour* gate pins the boundary and the single definition, so the
  constant cannot drift without a deliberate recapture.

Both were reverse-validated by breaking the corresponding source line.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

import analytics_core
import config_core

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Every module that used to carry its own copy of the rule.  The helper name is
#: spelled without the ``_core.`` prefix so the same assertion works for
#: analytics_core (which calls it unqualified) and the overlays.
CALL_SITES = (
    "analytics_core.py",
    "backtest_cache_acceleration_v80.py",
    "backtest_incremental_v78.py",
    "backtest_sample_acceleration_v80.py",
    "backtest_vectorization_v98.py",
    "conditional_fill_v96.py",
)

HELPER = "_has_backtest_history"


def production_modules() -> list[Path]:
    """Every production module, excluding tests, caches and vendored copies."""
    skip = {"tests", "__pycache__", ".workbuddy-ai", "cache", ".git", "node_modules"}
    return [
        path
        for path in REPO_ROOT.rglob("*.py")
        if not any(part in skip for part in path.relative_to(REPO_ROOT).parts)
    ]


def literal_history_guards() -> list[str]:
    """Find ``len(<something>) < 300`` left behind outside comments.

    An AST scan rather than a text search: the phrase survives in two docstrings
    and comments on purpose, and a regex would flag those.
    """
    found: list[str] = []
    for path in production_modules():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            left = node.left
            if not (isinstance(left, ast.Call) and isinstance(left.func, ast.Name)):
                continue
            if left.func.id != "len":
                continue
            for op, comparator in zip(node.ops, node.comparators):
                if not isinstance(op, ast.Lt):
                    continue
                if (
                    isinstance(comparator, ast.Constant)
                    and comparator.value == config_core.BACKTEST_MIN_HISTORY_BARS
                ):
                    found.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    return sorted(found)


def test_no_module_spells_the_threshold_literal_any_more() -> None:
    """The duplication itself is the defect; only a static scan can see it."""
    stragglers = literal_history_guards()
    assert not stragglers, (
        "len(...) < "
        f"{config_core.BACKTEST_MIN_HISTORY_BARS} is still written out at "
        + ", ".join(stragglers)
        + f"; it must go through analytics_core.{HELPER} instead"
    )


def test_every_former_call_site_goes_through_the_helper() -> None:
    """Convergence, asserted per site rather than in aggregate.

    A single ``_has_backtest_history`` somewhere in the tree would satisfy a
    global check while five of the six overlays kept their private copy.
    """
    missing = [
        name
        for name in CALL_SITES
        if HELPER not in (REPO_ROOT / name).read_text(encoding="utf-8")
    ]
    assert not missing, (
        f"{HELPER} is not referenced in: " + ", ".join(missing)
    )


def test_the_threshold_has_exactly_one_definition() -> None:
    """The number lives in config_core; analytics_core must read it, not shadow it."""
    assert config_core.BACKTEST_MIN_HISTORY_BARS == 300
    assert analytics_core.BACKTEST_MIN_HISTORY_BARS is (
        config_core.BACKTEST_MIN_HISTORY_BARS
    ), "analytics_core redefined the threshold instead of importing it"


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        (0, False),
        (1, False),
        (299, False),
        (300, True),
        (301, True),
    ],
)
def test_the_boundary_is_at_the_constant(rows: int, expected: bool) -> None:
    """299 bars is not enough, 300 is -- the cut-off is inclusive."""
    frame = pd.DataFrame({"Close": [1.0] * rows}) if rows else pd.DataFrame()
    assert analytics_core._has_backtest_history(frame) is expected


def test_a_missing_frame_is_not_history() -> None:
    assert analytics_core._has_backtest_history(None) is False


def test_overlays_reach_the_same_rule() -> None:
    """The overlays must resolve the helper off analytics_core, not import a copy.

    A ``from analytics_core import _has_backtest_history`` in an overlay would
    freeze the function at import time; the overlays are meant to see whatever
    the rule is *now*.
    """
    for name in CALL_SITES[1:]:
        source = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert f"_core.{HELPER}" in source, (
            f"{name} does not call _core.{HELPER}; a by-value import would "
            "detach it from later changes to the rule"
        )
