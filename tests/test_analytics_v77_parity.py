"""Parity between ``analytics_core`` and the ``analytics_acceleration_v77`` replacement.

``analytics_acceleration_v77.install()`` rebinds three ``analytics_core``
functions:

* ``enrich_results``
* ``_select_exact_refinement_pool``
* ``_apply_backtest_freshness``

An import probe shows the rebind is **entry-dependent**: ``main`` and
``daily_pipeline`` resolve all three to ``analytics_acceleration_v77.py``, while
``historical_backtest`` resolves them to ``analytics_core.py``. The daily job
therefore runs the accelerated implementation and the historical backtest runs
the original one, and before this file nothing compared the two.

That is the same shape as the ``lifecycle_acceleration_v83`` divergence, where
an "equivalent" acceleration layer dropped ``BenchmarkReturn20D/60D`` on a same
day re-run. v83 grew a parity probe after the fact; v77 had none.

Scope, stated plainly: this covers two of the three functions.
``enrich_results`` mutates ``ScanResult`` objects in place and needs benchmark
frames from ``_load_benchmark_frames``, so a parity scenario for it has to
stand up real benchmark data first. It is still uncovered.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest


def _load_unpatched_analytics_core() -> ModuleType:
    """Load ``analytics_core.py`` in an isolated namespace.

    Other test modules install compatibility overlays into the shared
    ``analytics_core`` module. Reading the function off that module would
    hand back whichever implementation happened to be installed last, and a
    parity test that compares a function with itself passes no matter what.
    """
    name = "_test_unpatched_analytics_core"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().parents[1] / "analytics_core.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load analytics core: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CORE = _load_unpatched_analytics_core()

import analytics_acceleration_v77 as V77  # noqa: E402


class _Summary:
    """Minimal ``BacktestSummary`` stand-in: only ``split_dates`` is read."""

    split_dates = {"global_end": "2026-09-01"}


def _freshness_frame(rows: list[list[object]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=[
            "BacktestRequested",
            "BacktestDataCutoffDate",
            "BacktestLastEvaluatedDate",
            "DataAsOf",
        ],
    )


# One row per branch of the freshness classifier: sync / delayed / stale /
# not-requested, plus the three degenerate inputs (missing cutoff, missing
# as-of, cutoff later than as-of) and the two fallback sources.
FRESHNESS_SCENARIOS = {
    "mixed_states": [
        [True, "2026-09-09", "", "2026-09-10"],
        [True, "2026-08-20", "", "2026-09-10"],
        [True, "2026-07-01", "", "2026-09-10"],
        [False, "", "", "2026-09-10"],
    ],
    "missing_cutoff": [[True, "", "", "2026-09-10"]],
    "missing_asof": [[True, "2026-09-01", "", None]],
    "cutoff_after_asof": [[True, "2026-09-20", "", "2026-09-10"]],
    "legacy_field_fallback": [[True, "", "2026-09-09", "2026-09-10"]],
    "run_cutoff_fallback": [[True, "", "", "2026-09-10"]],
    "empty_frame": [],
    # The classifier cuts at DELAYED=1 and STALE=5 trading days. A sweep made
    # only of large gaps lands entirely in the "stale" branch, so moving either
    # threshold by one changes nothing observable -- that blind spot is why
    # these five rows exist rather than one more large-gap row.
    "threshold_boundary_sweep": [
        [True, "2026-09-10", "", "2026-09-10"],  # gap 0 -> 同步
        [True, "2026-09-09", "", "2026-09-10"],  # gap 1 -> 同步 (delayed_limit)
        [True, "2026-09-08", "", "2026-09-10"],  # gap 2 -> 延迟
        [True, "2026-09-03", "", "2026-09-10"],  # gap 5 -> 延迟 (stale_limit)
        [True, "2026-09-02", "", "2026-09-10"],  # gap 6 -> 过期
    ],
}

_FRESHNESS_COLUMNS = (
    "BacktestDataCutoffDate",
    "BacktestLastEvaluatedDate",
    "BacktestFreshnessTradingDays",
    "BacktestFreshnessStatus",
    "BacktestFreshnessReason",
)


def _same(left: object, right: object) -> bool:
    """NaN-aware equality for cells pulled out of the two frames."""
    if left is None or right is None:
        return left is right
    try:
        if pd.isna(left) and pd.isna(right):
            return True
    except (TypeError, ValueError):
        pass
    return bool(left == right)


@pytest.mark.parametrize("scenario", sorted(FRESHNESS_SCENARIOS))
def test_freshness_matches_between_core_and_v77(scenario: str) -> None:
    rows = FRESHNESS_SCENARIOS[scenario]
    original = CORE._apply_backtest_freshness(_freshness_frame(rows), _Summary())
    accelerated = V77._apply_backtest_freshness(_freshness_frame(rows), _Summary())

    for column in _FRESHNESS_COLUMNS:
        left = original[column].tolist()
        right = accelerated[column].tolist()
        assert len(left) == len(right), f"{scenario}/{column}: row count differs"
        for index, (expected, actual) in enumerate(zip(left, right)):
            assert _same(expected, actual), (
                f"{scenario}/{column}[{index}]: core={expected!r} v77={actual!r}"
            )


def _pool_frame(rows: list[tuple[object, ...]]) -> pd.DataFrame:
    return pd.DataFrame(
        rows, columns=["Ticker", "EntrySignal", "RankingEligibility", "RankingScore"]
    )


_POOL_ROWS = [
    ("600000.SH", "BUY", "推荐", 90.0),
    ("600001.SH", "BUY", "谨慎候选", 80.0),
    ("600002.SH", "HOLD", "观察", 70.0),
    ("600003.SH", "BUY", "风险过滤", 95.0),
    ("600004.SH", "BUY", "推荐", 60.0),
    ("600005.SH", "SELL", "观察", 50.0),
]

_POOL_FAST_ROWS = [
    {"ticker": "600000.SH", "entry_signal": "BUY", "samples": 30, "effective_samples": 20.0},
    {"ticker": "600001.SH", "entry_signal": "BUY", "samples": 12, "effective_samples": 8.0},
    {"ticker": "600002.SH", "entry_signal": "HOLD", "samples": 40, "effective_samples": 40.0},
    {"ticker": "600003.SH", "entry_signal": "BUY", "samples": 99, "effective_samples": 99.0},
    {"ticker": "600004.SH", "entry_signal": "BUY", "samples": 3, "effective_samples": 1.0},
]

POOL_SCENARIOS = {
    "baseline": (_POOL_ROWS, _POOL_FAST_ROWS, 50),
    "top_n_one": (_POOL_ROWS, _POOL_FAST_ROWS, 1),
    "top_n_above_universe": (_POOL_ROWS, _POOL_FAST_ROWS, 999),
    "empty_frame": ([], _POOL_FAST_ROWS, 50),
    "no_fast_rows": (_POOL_ROWS, [], 50),
    "fast_row_without_ticker": (_POOL_ROWS, [{"entry_signal": "BUY", "samples": 9}], 50),
    "unparseable_samples": (
        _POOL_ROWS,
        [{"ticker": "600000.SH", "entry_signal": "BUY", "samples": "abc"}],
        50,
    ),
    "nan_effective_samples": (
        _POOL_ROWS,
        [
            {
                "ticker": "600000.SH",
                "entry_signal": "BUY",
                "samples": 30,
                "effective_samples": np.nan,
            }
        ],
        50,
    ),
    "all_rankingscore_nan": (
        [(ticker, signal, eligibility, np.nan) for ticker, signal, eligibility, _ in _POOL_ROWS],
        _POOL_FAST_ROWS,
        50,
    ),
    "all_risk_filtered": ([("600009.SH", "BUY", "风险过滤", 90.0)], _POOL_FAST_ROWS, 50),
    # BACKTEST_EXACT_REFINEMENT_CANDIDATES caps the pool at 150. Every other
    # scenario here has a handful of rows, so the cap never binds and a change
    # to it would not move a single assertion.
    "candidate_cap_binds": (
        [(f"6{i:05d}.SH", "BUY", "推荐", float(200 - i)) for i in range(200)],
        [
            {
                "ticker": f"6{i:05d}.SH",
                "entry_signal": "BUY",
                "samples": 30,
                "effective_samples": 20.0,
            }
            for i in range(200)
        ],
        999,
    ),
}


def _visible(frame: pd.DataFrame) -> pd.DataFrame:
    """Drop the ``_``-prefixed scratch columns the two implementations add.

    They are working state, not contract: either side is free to compute the
    same selection with different scratch columns. What has to agree is the set
    of rows that comes back.
    """
    return frame.drop(
        columns=[name for name in frame.columns if name.startswith("_")]
    ).reset_index(drop=True)


@pytest.mark.parametrize("scenario", sorted(POOL_SCENARIOS))
def test_exact_refinement_pool_matches_between_core_and_v77(scenario: str) -> None:
    rows, fast_rows, top_n = POOL_SCENARIOS[scenario]
    original = CORE._select_exact_refinement_pool(_pool_frame(rows), list(fast_rows), top_n)
    accelerated = V77._select_exact_refinement_pool(
        _pool_frame(rows), list(fast_rows), top_n
    )

    left = _visible(original)
    right = _visible(accelerated)
    assert left.equals(right), (
        f"{scenario}: core selected {left.to_dict('list')} but "
        f"v77 selected {right.to_dict('list')}"
    )


class _ResultStub:
    """Duck-typed ``ScanResult``: enrichment only touches plain attributes."""

    def __init__(self, ticker: str, *, is_etf: bool = False, sector: str = "银行") -> None:
        self.ticker = ticker
        self.is_etf = is_etf
        self.name = ticker
        self.industry = "银行"
        self.sector = sector
        self.model_classification = ""
        self.etf_tracking_key = ""
        self.theme_cluster = ""
        self.failure_adjusted_score = 70.0
        self.final_score = 70.0
        self.score = type("_Score", (), {"total": 70.0})()
        self.sector_confirmation_factor = 1.0
        self.breakout_quality_factor = 1.0
        self.entry_signal = "BUY"
        self.industry_momentum_60d = np.nan
        self.technical_institutional_score = 0.0
        self.institutional_score = 0.0

    def __getattr__(self, name: str) -> float:
        return 0.0


_ENRICH_FIELDS = (
    "model_classification",
    "sector",
    "industry_momentum_60d",
    "sector_confirmation_factor",
    "technical_institutional_score",
    "institutional_score",
)


@pytest.mark.parametrize("scenario", ["normal", "no_frame", "etf_empty_sector"])
def test_enrich_results_matches_between_core_and_v77(
    scenario: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``enrich_results`` is the third rebind and needs both internals stubbed.

    It mutates ``ScanResult`` objects in place and reaches for real benchmark
    frames, so the two process-global helpers it depends on are replaced rather
    than satisfied with data. That makes this test aware of ``_enrich_one_result``'s
    signature -- if that signature changes, this test fails loudly, which is the
    intended trade for covering a function that had no coverage at all.

    The stub returns ``_safe_return(frame["Close"], 60)`` as ``relative`` because
    that is exactly what the real ``_enrich_one_result`` returns. Feeding it an
    arbitrary number instead manufactures a divergence that cannot occur.
    """
    real_core = sys.modules["analytics_core"]
    close = [1.0] * 80
    close[-1] = 1.10
    frame = pd.DataFrame({"Close": close})

    def fake_enrich(result: object, source: str, regime: str, reason: str, *a: object, **k: object):
        if scenario == "no_frame":
            # A real branch: ``_enrich_one_result`` returns None for a ticker
            # with no usable K-line frame, three separate times.
            return result, None, 0.0
        return result, frame, CORE._safe_return(frame["Close"], 60)

    for module in (CORE, real_core):
        monkeypatch.setattr(module, "_load_benchmark_frames", lambda _source: {})
        monkeypatch.setattr(module, "_enrich_one_result", fake_enrich)

    is_etf = scenario == "etf_empty_sector"
    sector = "" if is_etf else "银行"
    left = [_ResultStub("600000.SH", is_etf=is_etf, sector=sector),
            _ResultStub("600001.SH", is_etf=is_etf, sector=sector)]
    right = [_ResultStub("600000.SH", is_etf=is_etf, sector=sector),
             _ResultStub("600001.SH", is_etf=is_etf, sector=sector)]

    CORE.enrich_results(left, "tickflow")
    V77.enrich_results(right, "tickflow")

    for index, (expected, actual) in enumerate(zip(left, right)):
        for field in _ENRICH_FIELDS:
            wanted = getattr(expected, field)
            got = getattr(actual, field)
            assert _same(wanted, got), (
                f"{scenario}/result[{index}].{field}: core={wanted!r} v77={got!r}"
            )


def test_the_two_implementations_are_actually_distinct() -> None:
    """Guard against the test comparing one function with itself.

    If module loading ever regresses and both sides resolve to the same
    function object, every parity assertion above becomes vacuous while still
    passing. This is the same class of defect as a byte budget sitting far
    above the file it guards.
    """
    assert (
        CORE._apply_backtest_freshness.__code__.co_filename
        != V77._apply_backtest_freshness.__code__.co_filename
    ), "both sides resolve to the same file; the parity tests are vacuous"
    assert (
        CORE._select_exact_refinement_pool.__code__.co_filename
        != V77._select_exact_refinement_pool.__code__.co_filename
    ), "both sides resolve to the same file; the parity tests are vacuous"
