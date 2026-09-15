"""Deterministic corpus + golden capture for ``analytics_core``'s backtest statistics.

``analytics_core`` is 3837 lines with 5 lines of byte budget left, and the two
largest functions in it (``apply_backtest_ranking`` 526 / ``run_historical_backtest``
523) are *externally reassigned*, so they cannot be extracted (see
PROJECT_ANALYSIS §22).  What can move is the cluster of statistics helpers around
them.  This module freezes those helpers' behaviour *before* the move so the
extraction is provably equivalence-preserving.

Two things make the capture subtle:

* **Assembly state.**  ``analytics.py`` is a facade that ends with
  ``sys.modules[__name__] = _core`` after rebinding five backtest helpers, and 21
  root overlays patch ``analytics_core`` at import time.  Capturing a bare
  ``analytics_core`` would freeze semantics that never execute.  We pin the
  production assembly by importing the facade first.
* **Numeric fragility.**  These are winsorized / weighted estimators.  A change
  in the 4th decimal place would not fail any existing assertion, which is
  exactly why the outputs are frozen as JSON rather than asserted on properties.

Recapture with::

    python tests/golden_analytics_core.py
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Pin the production assembly before capturing.  See the module docstring: the
# facade rebinds _core attributes and the root overlays patch analytics_core, so
# the objects we capture must be the ones production actually calls.
import analytics  # noqa: E402,F401
import analytics_core as _core  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "analytics_core_golden.json"

# ---------------------------------------------------------------------------
# Deterministic corpus.  No RNG anywhere: every value is written out so the
# fixture is reproducible across machines and numpy/pandas versions.
# ---------------------------------------------------------------------------

#: Weighted-estimator corpus.  Carries a NaN, a zero weight and a negative value
#: so the clipping / dropna / winsorization branches are all exercised.
_WEIGHTED_VALUES = [1.0, 2.0, 3.0, 4.0, 5.0, float("nan"), 8.0, -2.0, 0.5, 12.0]
_WEIGHTED_WEIGHTS = [1.0, 2.0, 0.5, 3.0, 1.0, 1.0, 0.0, 2.0, 1.5, 0.25]


def _weighted_values() -> pd.Series:
    return pd.Series(_WEIGHTED_VALUES, dtype=float)


def _weighted_weights() -> pd.Series:
    return pd.Series(_WEIGHTED_WEIGHTS, dtype=float)


def _all_zero_weights() -> pd.Series:
    return pd.Series([0.0] * len(_WEIGHTED_VALUES), dtype=float)


#: Eight distinct entry dates x five samples each: enough for qcut(5) and for
#: the date-balancing divisor to differ from 1.
_ENTRY_DATES = [f"2025-01-{day:02d}" for day in range(2, 10) for _ in range(5)]
_SCORES = [10.0 + 1.7 * i + (0.3 * (i % 4)) for i in range(40)]


def _sample_frame() -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "ticker": [f"60{i % 6:04d}.SH" for i in range(40)],
            "entry_date": _ENTRY_DATES,
            "score": _SCORES,
            # Deliberately non-monotonic in ``score``: a monotonic corpus makes
            # _spearman collapse to 1.0 and would not detect a rank/logic
            # regression in the estimator.
            "return20": [round(6.0 * np.sin(i * 0.7), 6) for i in range(40)],
            "return60": [round(-4.0 * np.cos(i * 0.41) + 0.2 * i, 6) for i in range(40)],
            "benchmark_return20": [round(0.2 * (i % 5) - 0.4, 6) for i in range(40)],
            "benchmark_return60": [round(0.4 * (i % 7) - 1.2, 6) for i in range(40)],
            "net_return20": [round(-4.3 + 0.3 * i, 6) for i in range(40)],
            "net_return60": [round(-9.6 + 0.45 * i, 6) for i in range(40)],
            "drawdown20": [round(-1.0 * (i % 9) - 0.5, 6) for i in range(40)],
            "drawdown60": [round(-1.4 * (i % 11) - 0.8, 6) for i in range(40)],
        }
    )
    # A zero and a NaN weight exercise the clip/where branches in
    # _date_balanced_weights and _spearman.
    frame["sample_weight"] = [0.0 if i % 11 == 0 else (np.nan if i == 7 else 1.0) for i in range(40)]
    return frame


def _sample_frame_without_weights() -> pd.DataFrame:
    return _sample_frame().drop(columns=["sample_weight"])


def _ohlc_frame(rows: int = 90, drift: float = 0.0015) -> pd.DataFrame:
    """A deterministic OHLCV frame long enough for the 60-bar regime gates."""
    index = pd.date_range("2024-01-02", periods=rows, freq="D")
    close = 100.0 * np.cumprod(1.0 + drift * np.sin(np.arange(rows) / 6.0))
    high = close * 1.01
    low = close * 0.99
    open_ = close * 0.999
    volume = np.full(rows, 1_000_000.0) + (np.arange(rows) % 7) * 10_000.0
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=index,
    )


def _enriched_frame(rows: int = 300) -> pd.DataFrame:
    """The column set ``_candidate_endpoint_matrix`` and friends read."""
    index = pd.date_range("2024-01-02", periods=rows, freq="D")
    close = 50.0 * np.cumprod(1.0 + 0.001 * np.sin(np.arange(rows) / 5.0))
    high = close * 1.02
    low = close * 0.98
    frame = pd.DataFrame(
        {
            "Open": close * 0.998,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": np.full(rows, 500_000.0) + (np.arange(rows) % 5) * 1_000.0,
        },
        index=index,
    )
    frame["MA20"] = frame["Close"].rolling(20, min_periods=1).mean()
    frame["MA50"] = frame["Close"].rolling(50, min_periods=1).mean()
    frame["ATR14"] = (frame["High"] - frame["Low"]).rolling(14, min_periods=1).mean()
    frame["VolMA20"] = frame["Volume"].rolling(20, min_periods=1).mean()
    frame["CMF"] = np.linspace(-0.3, 0.3, rows)
    frame["AD_Slope"] = np.linspace(-1.0, 1.0, rows)
    # Volume-profile columns exist so _backtest_scoring_window's drop() branch runs.
    frame["VP_HVN_Center"] = close * 0.99
    frame["DistToHVN_Pct"] = 0.5
    frame["Above_HVN"] = True
    frame["VP_LVN_Center"] = close * 0.97
    frame["DistToLVN_Pct"] = 0.9
    return frame


# ---------------------------------------------------------------------------
# Case table: (label, function_name, args, kwargs)
# ---------------------------------------------------------------------------

#: Where the extracted helpers now live.  Before the move this was
#: ``analytics_core``; the gate in test_analytics_core_golden.py is written
#: against this constant so the same test keeps working across the boundary.
#: It is asserted *live*, not from the frozen file: an overlay that rebinds
#: ``analytics_core.<name>`` after the move would resolve to the overlay's own
#: module instead and fail.
MOVE_HOME = "institution_scanner.backtest_statistics"

#: Where the canaries must remain.
STAY_HOME = "analytics_core"

#: Module-level names the moved helpers read that some overlay rebinds on
#: ``analytics_core`` after import.  The extracted module must end up holding
#: the *same object* -- otherwise the move silently detaches production from the
#: patch.  ``compute_volume_profile`` is rebound by
#: ``indicator_acceleration_v77`` and is invisible to value-based fixtures
#: because the accelerated and the original implementation return equal numbers,
#: so only an identity check can catch the regression.
LATE_BOUND_NAMES = ("compute_volume_profile",)

#: Helpers proposed for extraction.  Under the production assembly every name
#: here MUST resolve to ``MOVE_HOME`` -- if an overlay patched one, moving it
#: out would silently drop the patch (the overlay would keep writing to
#: ``analytics_core`` while production read the extracted copy), so the gate in
#: test_analytics_core_golden.py fails and the extraction is blocked.
MOVE_CANDIDATES = (
    "_weighted_observations",
    "_weighted_arrays",
    "_weighted_mean",
    "_weighted_rate",
    "_weighted_std",
    "_weighted_robust_mean",
    "_weighted_quantile",
    "_spearman",
    "_backtest_evidence",
    "_entry_date_equal_weight_stats",
    "_backtest_scoring_window",
    "_candidate_endpoint_matrix",
    "_benchmark_regime",
    "_benchmark_regime_components",
)

#: Captured but deliberately NOT moved.  ``backtest_math_integrity_v94:166``
#: rebinds ``_date_balanced_weights`` through its ``analytics_module``
#: *parameter* -- a pattern a module-alias scan cannot see.  ``_bucket_rows``
#: calls it, so neither can be extracted without late-binding back into
#: analytics_core.
PATCHED_CANARIES = (
    "_date_balanced_weights",
    "_bucket_rows",
)

FUNCTIONS = MOVE_CANDIDATES + PATCHED_CANARIES


def build_cases() -> list[tuple[str, str, tuple[Any, ...], dict[str, Any]]]:
    values = _weighted_values()
    weights = _weighted_weights()
    sample = _sample_frame()
    sample_plain = _sample_frame_without_weights()
    enriched = _enriched_frame()

    cases: list[tuple[str, str, tuple[Any, ...], dict[str, Any]]] = [
        # --- weighted estimator family -------------------------------------
        ("weighted_observations/with_weights", "_weighted_observations", (values, weights), {}),
        ("weighted_observations/weights_none", "_weighted_observations", (values, None), {}),
        ("weighted_observations/all_zero", "_weighted_observations", (values, _all_zero_weights()), {}),
        ("weighted_observations/empty", "_weighted_observations", (pd.Series(dtype=float), None), {}),
        ("weighted_arrays/basic", "_weighted_arrays", (values, weights), {}),
        ("weighted_mean/basic", "_weighted_mean", (values, weights), {}),
        ("weighted_mean/all_zero", "_weighted_mean", (values, _all_zero_weights()), {}),
        ("weighted_rate/basic", "_weighted_rate", (values, weights), {}),
        ("weighted_rate/all_negative", "_weighted_rate", (pd.Series([-1.0, -2.0, -3.0]), pd.Series([1.0, 1.0, 1.0])), {}),
        ("weighted_std/basic", "_weighted_std", (values, weights), {}),
        ("weighted_robust_mean/basic", "_weighted_robust_mean", (values, weights), {}),
        ("weighted_robust_mean/fewer_than_5", "_weighted_robust_mean", (pd.Series([1.0, 2.0, 3.0]), pd.Series([1.0, 1.0, 2.0])), {}),
        ("weighted_quantile/median", "_weighted_quantile", (values, weights, 0.5), {}),
        ("weighted_quantile/p10", "_weighted_quantile", (values, weights, 0.1), {}),
        ("weighted_quantile/p90", "_weighted_quantile", (values, weights, 0.9), {}),
        ("weighted_quantile/weights_none", "_weighted_quantile", (values, None, 0.5), {}),
        # --- sample-frame helpers ------------------------------------------
        ("date_balanced_weights/with_weights", "_date_balanced_weights", (sample,), {}),
        ("date_balanced_weights/no_weight_column", "_date_balanced_weights", (sample_plain,), {}),
        ("spearman/return20", "_spearman", (sample, "return20"), {}),
        ("spearman/return60", "_spearman", (sample, "return60"), {}),
        ("spearman/constant_target", "_spearman", (sample.assign(return20=1.0), "return20"), {}),
        ("spearman/too_few_rows", "_spearman", (sample.head(1), "return20"), {}),
        ("entry_date_equal_weight_stats/basic", "_entry_date_equal_weight_stats", (sample,), {}),
        ("entry_date_equal_weight_stats/empty", "_entry_date_equal_weight_stats", (sample.head(0),), {}),
        ("bucket_rows/basic", "_bucket_rows", (sample,), {}),
        ("bucket_rows/constant_score", "_bucket_rows", (sample.assign(score=1.0),), {}),
        # --- evidence / window / endpoints ----------------------------------
        # Tiers are driven by config: <10 样本不足, 10..19 低可信度,
        # 20..49 中可信度, >=50 高可信度.  One case per tier so a change in the
        # tier boundary cannot hide behind a single captured branch.
        ("backtest_evidence/insufficient", "_backtest_evidence", (5, 5.0, 12.0), {}),
        ("backtest_evidence/low_tier", "_backtest_evidence", (15, 7.0, 12.0), {}),
        ("backtest_evidence/mid_tier", "_backtest_evidence", (35, 18.0, 25.0), {}),
        ("backtest_evidence/high_tier", "_backtest_evidence", (120, 60.0, 8.0), {}),
        ("backtest_evidence/huge_dispersion", "_backtest_evidence", (120, 60.0, 500.0), {}),
        ("backtest_evidence/degenerate_effective", "_backtest_evidence", (120, 0.0, 8.0), {}),
        ("backtest_evidence/effective_exceeds_count", "_backtest_evidence", (60, 999.0, 8.0), {}),
        ("backtest_scoring_window/no_volume_profile", "_backtest_scoring_window", (enriched, 260), {"include_volume_profile": False}),
        ("backtest_scoring_window/explicit_window", "_backtest_scoring_window", (enriched, 100), {"score_window": 60, "include_volume_profile": False}),
        ("backtest_scoring_window/early_index", "_backtest_scoring_window", (enriched, 5), {"include_volume_profile": False}),
        ("candidate_endpoint_matrix/fast_true", "_candidate_endpoint_matrix", (enriched,), {"fast_prefilter": True}),
        ("candidate_endpoint_matrix/fast_false", "_candidate_endpoint_matrix", (enriched,), {"fast_prefilter": False}),
        ("candidate_endpoint_matrix/missing_columns", "_candidate_endpoint_matrix", (enriched[["Close"]].copy(),), {"fast_prefilter": True}),
        # --- benchmark regime ----------------------------------------------
        ("benchmark_regime/uptrend", "_benchmark_regime", ({"BM": _ohlc_frame(90, 0.004)},), {}),
        ("benchmark_regime/downtrend", "_benchmark_regime", ({"BM": _ohlc_frame(90, -0.004)},), {}),
        ("benchmark_regime/too_short", "_benchmark_regime", ({"BM": _ohlc_frame(30, 0.004)},), {}),
        ("benchmark_regime/empty", "_benchmark_regime", ({},), {}),
        ("benchmark_regime_components/uptrend", "_benchmark_regime_components", ({"BM": _ohlc_frame(120, 0.004)}, "风险偏好", "seed"), {}),
        ("benchmark_regime_components/downtrend", "_benchmark_regime_components", ({"BM": _ohlc_frame(120, -0.004)}, "风险规避", "seed"), {}),
    ]
    return cases


def plain(value: Any) -> Any:
    """Convert a result into a JSON-representable structure."""
    if isinstance(value, pd.DataFrame):
        return {
            "__frame__": {
                "index": [plain(i) for i in value.index],
                "columns": [str(c) for c in value.columns],
                "rows": [[plain(v) for v in row] for row in value.itertuples(index=False)],
            }
        }
    if isinstance(value, pd.Series):
        return {"__series__": [plain(v) for v in value.tolist()]}
    if isinstance(value, (pd.Timestamp,)):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, np.ndarray):
        return {"__ndarray__": [plain(v) for v in value.tolist()]}
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        # NaN != NaN and JSON has no inf, so both would make the fixture
        # mismatch on every recapture.  Freeze them as tagged strings instead.
        if np.isnan(number):
            return {"__float__": "nan"}
        if np.isinf(number):
            return {"__float__": "inf" if number > 0 else "-inf"}
        return number
    if isinstance(value, (np.str_,)):
        return str(value)
    if value is None:
        return None
    return str(value)


def resolved_global(name: str) -> Any:
    """Return the object the extracted helpers will actually use for ``name``.

    Two ways a module can supply a global, and only one of them survives an
    overlay rebinding it later:

    * by value (``from indicators import x``) -- the name sits in the module
      dict, frozen at import time.  An overlay that rebinds it afterwards never
      reaches the helpers.
    * through a module object (``import indicators`` + ``indicators.x``) -- the
      attribute is looked up at call time, so the rebound version wins.

    This walks the module dict so the gate tests what the helpers really call,
    not what the test author assumed they call.
    """
    from types import ModuleType

    extracted = importlib.import_module(MOVE_HOME)
    namespace = vars(extracted)
    if name in namespace:
        return namespace[name]
    for value in namespace.values():
        if isinstance(value, ModuleType) and hasattr(value, name):
            return getattr(value, name)
    raise AssertionError(
        f"{MOVE_HOME} supplies no binding for {name}; the moved helpers would "
        "raise NameError"
    )


def live_provenance() -> dict[str, str]:
    """Resolve every tracked helper *now*, under the production assembly.

    The patch-detection gates must read provenance live.  Reading it from the
    frozen fixture instead would make them unfalsifiable: they would assert
    what was true when the file was written, not what is true today -- which is
    exactly how a post-move overlay rebind would slip through unnoticed.
    """
    return {
        name: (
            f"{getattr(_core, name).__module__}.{getattr(_core, name).__qualname__}"
        )
        for name in FUNCTIONS
    }


def capture() -> dict[str, Any]:
    cases: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    for label, function_name, args, kwargs in build_cases():
        function = getattr(_core, function_name)
        provenance[function_name] = f"{function.__module__}.{function.__qualname__}"
        try:
            cases[label] = {"value": plain(function(*args, **kwargs))}
        except Exception as exc:  # noqa: BLE001 - freezing whatever comes out
            cases[label] = {"raised": type(exc).__name__}

    return {
        "schema": 1,
        "note": (
            "Golden output of analytics_core's backtest statistics helpers, "
            "captured AFTER the analytics facade has been imported so the "
            "production assembly is in place. Recapture with "
            "`python tests/golden_analytics_core.py` ONLY after reviewing the diff."
        ),
        "captured_with": {
            "python": sys.version.split()[0],
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
        "functions": list(FUNCTIONS),
        "move_candidates": list(MOVE_CANDIDATES),
        "patched_canaries": list(PATCHED_CANARIES),
        "function_provenance": provenance,
        "cases": cases,
    }


def load() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def main() -> None:
    payload = capture()
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {FIXTURE_PATH}  cases={len(payload['cases'])}")


if __name__ == "__main__":
    main()
