from __future__ import annotations

import ast
import importlib.util
import logging
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd

import indicators
from institution_scanner.backtest_score_vectorized import final_score_series


def _load_unpatched_exact_scorer() -> ModuleType:
    """Load the scalar file without process-global runtime overlays.

    Other test modules intentionally install compatibility overlays into the
    shared ``score_core`` module during collection.  The historical vector
    mirror targets the stable scalar file used by its standalone validator, so
    this test gives that reference an isolated module namespace.
    """
    name = "_test_unpatched_score_core"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().parents[1] / "score_core.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load scalar scorer: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


EXACT_SCORE = _load_unpatched_exact_scorer()

#: Vectorised component key -> the ``ScoreBreakdown`` attribute it must equal.
#:
#: ``score_core`` is the specification (§10.6 decision 3); the vectorised module
#: is an *implementation* of it and answers to it.  Naming them apart here is the
#: only thing tying the two together -- the modules share zero function names, so
#: nothing else in the codebase expresses "these two compute the same thing".
SCALAR_FIELDS: dict[str, str] = {
    "final": "final_score",
    "base": "base_score",
    "trigger": "trigger_score",
    "execution": "execution_score",
    "breakout": "breakout_score",
    "trap": "value_trap_risk",
    "coverage": "indicator_coverage",
}

#: Component keys the vectorised path exposes that have **no** scalar
#: counterpart.  Each entry must say why, and the reason has to be checkable.
VECTOR_ONLY_KEYS: dict[str, str] = {
    "exec_raw": (
        "raw execution before the coverage scale and the 0..100 clamp "
        "(backtest_score_vectorized.py:945 builds ``execution`` from it). The "
        "scalar path only ever exposes the covered+clamped form, so there is "
        "nothing to compare it to. It is locked indirectly: ``execution`` is "
        "asserted bit-exact against ``execution_score``, and on a frame with "
        "missing dimensions the two differ (69.26 vs 78.71 at coverage 0.6), so "
        "a drift confined to the pre-coverage stage cannot hide here."
    ),
}

#: ``score_core`` function -> its vectorised counterpart.  Kept explicit rather
#: than inferred: the two modules share no names, so inference is impossible.
SCALAR_TO_VECTORISED: dict[str, str] = {
    "score_trend": "_trend",
    "score_volume": "_volume",
    "score_accumulation": "_accumulation",
    "score_volatility": "_volatility",
    "score_structure": "_structure",
    "value_trap_risk": "_value_trap",
    "breakout_score": "_breakout",
    "execution_quality_score": "_entry_execution",
    "score_ticker": "final_score_series",
}

#: ``score_core`` functions that ``score_ticker`` calls but which have **no**
#: vectorised counterpart.  The two modules share no names, so the only way to
#: know they are shared rather than duplicated is to say so here.
#:
#: Every other table in this module locks the vectorised side.  Without this
#: one the map is one-way: adding a scalar call that the vectorised path never
#: learns about would not move a single assertion.
SCALAR_ONLY_CALLS: dict[str, str] = {
    "classify_style": (
        "returns the style *label*, a string, not a score. The vectorised path "
        "scores the fast full-market corpus for ranking and never emits a "
        "label, so there is nothing to align; a change here moves reporting, "
        "not the numbers this file compares."
    ),
    "entry_point": (
        "entry timing is *shared*, not duplicated: the FAST path calls "
        "score_core.entry_point too (backtest_fastscore_v80:608, "
        "backtest_fastpath_v78:356, conditional_fill_v96:75). There is no "
        "second implementation, so there is no alignment to lock -- the danger "
        "this entry guards against is someone writing a vectorised entry_point "
        "and forgetting to say so."
    ),
    "tradable_price_decimals": (
        "pure precision helper (3 decimals for ETFs, 2 for stocks). No state, "
        "no history, nothing a second implementation could drift on."
    ),
}

#: Private helpers in the vectorised module that are *not* score components.
VECTOR_HELPERS = frozenset(
    {
        "_component_weights",
        "_col",
        "_ffill",
        "_valid_lag",
        "_valid_rolling",
        "_valid_trailing_run",
        "_ret",
        "_roll",
        "_roll_shift",
        "_clampc",
    }
)


def _enriched_frame(*, seed: int, rows: int = 900) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 10.0 * np.exp(np.cumsum(rng.normal(0.0001, 0.015, rows)))
    open_price = close * np.exp(rng.normal(0.0, 0.004, rows))
    spread = np.abs(rng.normal(0.01, 0.004, rows))
    high = np.maximum(open_price, close) * (1.0 + spread)
    low = np.minimum(open_price, close) * (1.0 - spread)
    volume = rng.lognormal(15.0, 0.8, rows)

    # Suspended/zero-turnover stretches produce legitimate gaps in CMF/MFI.
    # EXACT compacts those series with dropna(); FAST must use the same
    # observation basis instead of treating a row offset as an observation lag.
    for start, stop in ((300, 330), (700, 720)):
        close[start:stop] = close[start - 1]
        open_price[start:stop] = close[start:stop]
        high[start:stop] = close[start:stop]
        low[start:stop] = close[start:stop]
        volume[start:stop] = 0.0

    frame = pd.DataFrame(
        {
            "Open": open_price,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        },
        index=pd.bdate_range("2020-01-02", periods=rows),
    )
    previous = indicators.ENABLE_VOLUME_PROFILE
    try:
        indicators.ENABLE_VOLUME_PROFILE = False
        return indicators.compute_all_indicators(frame)
    finally:
        indicators.ENABLE_VOLUME_PROFILE = previous


def _assert_scalar_parity(frame: pd.DataFrame, *, is_etf: bool) -> None:
    vectorized = final_score_series(
        frame,
        is_etf=is_etf,
        return_components=True,
    )
    positions = sorted(
        set(range(251, len(frame), 10))
        | {329, 330, 331, 339, 340, 341, 719, 720, 721}
    )
    for position in positions:
        scalar = EXACT_SCORE.score_ticker(
            frame.iloc[: position + 1],
            is_etf=is_etf,
        )
        for vector_name, scalar_name in SCALAR_FIELDS.items():
            np.testing.assert_allclose(
                float(vectorized[vector_name][position]),
                float(getattr(scalar, scalar_name)),
                rtol=0.0,
                atol=1e-10,
                err_msg=(
                    f"position={position} field={vector_name} "
                    f"is_etf={is_etf}"
                ),
            )


def _frame_with_missing_dimensions(seed: int = 1) -> pd.DataFrame:
    """A frame where whole indicator dimensions are unavailable.

    Needed because the ordinary corpus is *degenerate* for coverage: every
    indicator is present, so ``indicator_coverage`` is 1.0 everywhere and an
    assertion on it could not fail.  Setting the trend and accumulation inputs
    to NaN drives it to 0.6, which is what makes the coverage lock real -- the
    same reasoning as ``_enriched_frame``'s deliberate zero-turnover gaps.
    """
    frame = _enriched_frame(seed=seed)
    for column in ("MA200", "OBV", "AD", "AD_Slope", "CMF", "MFI"):
        if column in frame.columns:
            frame[column] = float("nan")
    return frame


def test_fast_score_matches_exact_after_zero_turnover_indicator_gaps() -> None:
    logging.getLogger("institution_scanner.score").setLevel(logging.ERROR)
    _assert_scalar_parity(_enriched_frame(seed=1), is_etf=False)


def test_fast_score_applies_exact_etf_style_and_price_precision() -> None:
    logging.getLogger("institution_scanner.score").setLevel(logging.ERROR)
    _assert_scalar_parity(_enriched_frame(seed=2), is_etf=True)


def test_fast_score_matches_exact_with_degraded_indicator_coverage() -> None:
    """Same parity, on a frame where coverage is not the trivial 1.0."""
    logging.getLogger("institution_scanner.score").setLevel(logging.ERROR)
    _assert_scalar_parity(_frame_with_missing_dimensions(seed=1), is_etf=False)


def test_coverage_gate_is_not_vacuous() -> None:
    """Guard the corpus, not the code.

    The whole point of ``_frame_with_missing_dimensions`` is that coverage is
    below 1.  If a future edit to ``indicators`` makes every dimension available
    again, the coverage assertion above would silently become unfalsifiable.
    """
    logging.getLogger("institution_scanner.score").setLevel(logging.ERROR)
    frame = _frame_with_missing_dimensions(seed=1)
    vectorized = final_score_series(frame, is_etf=False, return_components=True)
    assert float(vectorized["coverage"][-1]) < 1.0, (
        "the degraded corpus reports full indicator coverage; the coverage "
        "assertion in the parity test can no longer fail"
    )


def test_every_component_key_is_declared() -> None:
    """A new component key must be declared before it can exist.

    The gap this closes: ``final_score_series`` returned ``coverage`` and
    ``exec_raw`` and the parity test asserted neither, because the fields dict
    listed six names by hand.  A seventh component would have shipped silently.
    """
    logging.getLogger("institution_scanner.score").setLevel(logging.ERROR)
    frame = _enriched_frame(seed=1)
    actual = set(final_score_series(frame, is_etf=False, return_components=True))
    declared = set(SCALAR_FIELDS) | set(VECTOR_ONLY_KEYS)
    assert actual == declared, (
        "component keys changed; declare each new one as either an aligned "
        f"scalar field or an exempted vector-only key: undeclared="
        f"{sorted(actual - declared)} stale={sorted(declared - actual)}"
    )


def test_every_exempted_key_states_a_reason() -> None:
    """An exemption with no written justification is how drift gets laundered."""
    for key, reason in VECTOR_ONLY_KEYS.items():
        assert len(reason) > 60, f"exemption for {key!r} has no real justification"


def test_every_vectorised_function_is_declared() -> None:
    """No private function may appear in the vectorised module unannounced.

    Adding a score component there without declaring it in
    ``SCALAR_TO_VECTORISED`` is exactly the silent-divergence case: the parity
    test only checks the keys it was told about.
    """
    module = importlib.import_module("institution_scanner.backtest_score_vectorized")
    private = {
        name
        for name in vars(module)
        if name.startswith("_") and callable(getattr(module, name))
    }
    declared = set(SCALAR_TO_VECTORISED.values()) | VECTOR_HELPERS
    undeclared = private - declared
    assert not undeclared, (
        "undeclared private callables in the vectorised scoring module; each one "
        "is either a score component (declare its scalar counterpart) or a "
        f"helper (add it to VECTOR_HELPERS): {sorted(undeclared)}"
    )


def _score_ticker_public_calls() -> set[str]:
    """Public ``score_core`` functions that ``score_ticker`` actually calls.

    Read from source rather than by tracing: the point is to see what the
    specification *wires together*, which is exactly what a trace of a patched
    module would hide.
    """
    source = (Path(__file__).resolve().parents[1] / "score_core.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    public = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }
    score_ticker = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "score_ticker"
    )
    called = {
        node.func.id
        for node in ast.walk(score_ticker)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    return called & public


def test_every_scalar_call_in_score_ticker_is_declared() -> None:
    """The scalar half of the map, which was missing.

    ``test_every_component_key_is_declared`` locks the vectorised side: a new
    component key cannot appear unannounced.  This locks the other direction --
    ``score_ticker`` may only call functions this file knows about, so wiring a
    new component into the specification forces a decision here: it either has
    a vectorised counterpart, or it is declared scalar-only with a reason.

    Both directions are needed.  With only the vectorised one, adding a scalar
    component that the FAST path never implements stays silent as long as it
    does not happen to move ``final_score`` on these particular frames.
    """
    called = _score_ticker_public_calls()
    components = set(SCALAR_TO_VECTORISED) - {"score_ticker"}
    declared = components | set(SCALAR_ONLY_CALLS)

    undeclared = sorted(called - declared)
    assert not undeclared, (
        "score_ticker calls score-core functions this file does not know about; "
        "either give each a vectorised counterpart in SCALAR_TO_VECTORISED or "
        f"declare it scalar-only with a reason: {undeclared}"
    )

    stale = sorted(components - called)
    assert not stale, (
        f"declared components that score_ticker no longer calls: {stale}. "
        "Either the specification dropped them (update the map) or they were "
        "rewired somewhere the map does not look."
    )


def test_every_scalar_only_call_states_a_reason() -> None:
    """Same rule as the vectorised exemptions: no reason, no exemption."""
    for name, reason in SCALAR_ONLY_CALLS.items():
        assert len(reason) > 60, f"scalar-only call {name!r} has no real justification"


def test_the_scalar_counterparts_all_exist() -> None:
    """The mapping must point at real functions on both sides."""
    module = importlib.import_module("institution_scanner.backtest_score_vectorized")
    for scalar_name, vector_name in SCALAR_TO_VECTORISED.items():
        assert hasattr(EXACT_SCORE, scalar_name), (
            f"score_core no longer defines {scalar_name}; the mapping is stale"
        )
        assert hasattr(module, vector_name), (
            f"the vectorised module no longer defines {vector_name}"
        )
