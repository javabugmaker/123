"""Backtest scoring must reproduce the live scoring model.

``score_core.score_structure`` reads the volume-profile columns
``Above_HVN`` / ``DistToHVN_Pct`` when they are present and adds up to 2 of its
15 points on that evidence (``score_core.py:394``).  The live scan passes a full
indicator frame that carries those columns (``scanner_core.py:470`` ->
``scanner_core.py:581``), while the historical path builds its frame through
``institution_scanner.backtest_statistics._backtest_scoring_window``.

That window helper *drops* the precomputed columns and only recomputes them when
``include_volume_profile`` is true.  ``backtest_profile_alignment_v95`` forced
the flag to ``False`` for both FAST and EXACT, so the backtest scored a model
that was missing the HVN term while live kept it.  Measured on random walks the
branch fires for roughly 23% of samples and shifts ``structure`` by +0.03 to
+1.70 points (mean +0.94).

These tests pin the two properties that matter:

1. when the historical window recomputes the volume profile, it reproduces the
   live frame exactly — the 252-bar lookback fits inside the 504-bar window, so
   both see the same trailing slice;
2. when it does *not*, the score genuinely diverges.  This is the regression
   lock: without it, silently dropping the flag again would pass unnoticed.

A meta-check asserts the corpus really triggers the HVN branch, so the second
property cannot degrade into a vacuous ``0 != 0`` comparison.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import indicators
from institution_scanner.backtest_statistics import _backtest_scoring_window

_ROOT = Path(__file__).resolve().parents[1]


def _load_canonical_score_core():
    """Load ``score_core`` without the process-global rebinding overlays.

    Mirrors ``test_backtest_score_vectorized_alignment``: the v113 facade and
    the v79/v95 accelerations rebind helpers at import time, and this test is
    about the canonical arithmetic rather than whichever object won the race.
    """
    name = "_test_equivalence_score_core"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    path = _ROOT / "score_core.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("cannot load scalar scorer: %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SCORE_CORE = _load_canonical_score_core()


def _hvn_branch_fires(frame: pd.DataFrame) -> bool:
    """Return whether the terminal bar satisfies the HVN scoring condition."""
    if "Above_HVN" not in frame.columns or "DistToHVN_Pct" not in frame.columns:
        return False
    above = frame["Above_HVN"].iloc[-1]
    dist = pd.to_numeric(frame["DistToHVN_Pct"].iloc[-1], errors="coerce")
    if not np.isfinite(dist):
        return False
    return bool(above) and 0.0 < float(dist) < 10.0


def _build_frame(seed: int, rows: int = 900) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 20.0 * np.exp(np.cumsum(rng.normal(0.0004, 0.018, rows)))
    high = close * (1.0 + np.abs(rng.normal(0.0, 0.010, rows)))
    low = close * (1.0 - np.abs(rng.normal(0.0, 0.010, rows)))
    volume = rng.lognormal(12.0, 0.4, rows)
    frame = pd.DataFrame(
        {
            "Open": close * (1.0 + rng.normal(0.0, 0.004, rows)),
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        },
        index=pd.bdate_range("2019-01-02", periods=rows),
    )
    indicators.compute_all_indicators(frame)
    return frame


def _frame_where_hvn_fires() -> pd.DataFrame:
    for seed in range(400):
        frame = _build_frame(seed)
        if _hvn_branch_fires(frame):
            return frame
    raise AssertionError("no deterministic sample triggered the HVN branch")


@pytest.fixture(scope="module")
def live_frame() -> pd.DataFrame:
    """A full indicator frame shaped exactly like what the live scan scores."""
    return _frame_where_hvn_fires()


def test_corpus_actually_triggers_the_hvn_branch(live_frame: pd.DataFrame) -> None:
    """Meta-check: the corpus below would be vacuous without a firing sample."""
    assert _hvn_branch_fires(live_frame)


def test_historical_window_with_volume_profile_matches_live(
    live_frame: pd.DataFrame,
) -> None:
    """Recomputing the volume profile must reproduce the live structure score.

    ``compute_volume_profile`` uses a 252-bar trailing lookback, which fits
    inside the 504-bar historical window, so the truncated window and the full
    live frame see byte-identical input.
    """
    last = len(live_frame) - 1
    window = _backtest_scoring_window(
        live_frame, last, include_volume_profile=True
    )
    assert "Above_HVN" in window.columns, "window must carry the VP columns"
    assert _hvn_branch_fires(window), "recomputed window must still fire HVN"

    live_structure = SCORE_CORE.score_structure(live_frame)
    window_structure = SCORE_CORE.score_structure(window)
    assert window_structure == pytest.approx(live_structure, abs=1e-9)


def test_backtest_profiles_request_the_volume_profile(monkeypatch) -> None:
    """Pin the *configuration*, not just the mechanism.

    The window helper only recomputes when it is asked to.  ``backtest_profile_
    alignment_v95`` used to force ``historical_volume_profile=False`` for both
    FAST and EXACT, and the fast paths hardcoded ``include_volume_profile=False``
    at three call sites.  Without this pin, flipping the switch back would pass
    every window-level test above while silently reintroducing the divergence.
    """
    import analytics_core
    import backtest_profile_alignment_v95 as alignment_v95
    import config

    original = analytics_core._resolve_backtest_profile
    try:
        alignment_v95.install()

        # Assert the *as-shipped* default.  Do not monkeypatch the switch to
        # True first: doing so would make this pass even when
        # BACKTEST_HISTORICAL_VOLUME_PROFILE=0, which is exactly the vacuous
        # gate this file exists to prevent.
        for mode in ("exact", "fast"):
            profile = analytics_core._resolve_backtest_profile(mode, 1)
            assert profile.historical_volume_profile is True, (
                f"{mode} profile must request the volume profile; "
                "BACKTEST_HISTORICAL_VOLUME_PROFILE is off"
            )
            # The canonical 504-bar window contract must survive the override.
            assert profile.score_window == 504, mode

        # Reverse validation: the switch must actually control the outcome.
        monkeypatch.setattr(
            config, "BACKTEST_HISTORICAL_VOLUME_PROFILE", False, raising=False
        )
        for mode in ("exact", "fast"):
            profile = analytics_core._resolve_backtest_profile(mode, 1)
            assert profile.historical_volume_profile is False, mode
    finally:
        analytics_core._resolve_backtest_profile = original


def test_historical_window_without_volume_profile_diverges_from_live(
    live_frame: pd.DataFrame,
) -> None:
    """Regression lock: dropping the VP recompute must visibly change the score.

    If this ever stops failing, either the HVN term was removed from
    ``score_structure`` or the corpus stopped firing — both need a deliberate
    decision, not a silent pass.
    """
    last = len(live_frame) - 1
    stripped = _backtest_scoring_window(
        live_frame, last, include_volume_profile=False
    )
    assert "Above_HVN" not in stripped.columns

    live_structure = SCORE_CORE.score_structure(live_frame)
    stripped_structure = SCORE_CORE.score_structure(stripped)
    assert stripped_structure != pytest.approx(live_structure, abs=1e-9)
    # The HVN term only ever adds points, so the stripped frame cannot score
    # higher than the live one.
    assert stripped_structure < live_structure
