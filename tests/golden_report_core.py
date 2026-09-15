"""Deterministic corpus + golden capture for ``report_core``'s candidate-selection cluster.

``report_core`` is 104,909 bytes against a 105,000 byte budget -- 91 bytes, about
two lines, of headroom.  The cluster frozen here is the 11-function / 323-line
dependency closure around ``_apply_research_policy``, ``_ensure_diversity_columns``,
``_diversify_ranked_candidates`` and ``_institutional_tier``, which
``tests/recon_extraction_targets.py`` reports as self-consistent (no patched
dependency, no dependency outside the closure).

Two things make this capture different from the analytics one, and both were
found by reconnaissance rather than by reading the code:

* **The tier thresholds are rebound, not constant.**  ``score.py:21`` calls
  ``score_threshold_migration_v95.install(config)`` at import time, rewriting
  ``INSTITUTIONAL_TIER_A/B/C_SCORE`` from 35.0/30.0/25.0 to
  36.0825/30.9278/25.7732.  ``report_core`` happens to see the migrated values
  only because its line 25 (``from analytics import ...``) drags in ``score``
  before its line 32 (``from config import ...``) runs.  That is an *import
  order* accident, so the extracted module resolves those three through the
  ``config`` module object at call time, and the corpus carries probes whose
  tier flips if the migration is ever lost (see ``MIGRATION_PROBES``).
* **``config`` and ``config_core`` are different objects.**  After assembly
  ``config.INSTITUTIONAL_TIER_A_SCORE`` is 36.0825 while
  ``config_core.INSTITUTIONAL_TIER_A_SCORE`` is still 35.0.  Importing from the
  wrong one is a silent off-by-1.08-score regression.

Recapture with::

    python tests/golden_report_core.py
"""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TESTS_DIR = Path(__file__).resolve().parent
for _path in (str(_REPO_ROOT), str(_TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# Pin the production assembly before capturing: ``report.py`` rebinds
# ``_results_to_dataframe`` / ``export_all`` / ``DECISION_RESULT_COLUMNS`` on
# ``report_core`` and finishes with ``sys.modules[__name__] = _core``, so a bare
# ``import report_core`` would freeze objects production never calls.
from golden_analytics_core import plain  # noqa: E402

import report  # noqa: E402,F401
import report_core as _core  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "report_core_golden.json"

# ---------------------------------------------------------------------------
# Deterministic corpus.  No RNG anywhere; every value is written out.
# ---------------------------------------------------------------------------


def _score(total: float = 0.0, volume: float = 0.0) -> Any:
    """A ``ScoreBreakdown`` built through the production facade."""
    from score import ScoreBreakdown

    return ScoreBreakdown(total=total, volume=volume)


def _result(**overrides: Any) -> Any:
    """A ``ScanResult`` carrying only what ``_institutional_tier`` reads."""
    from scanner import ScanResult

    defaults: dict[str, Any] = {
        "ticker": "600000.SH",
        "name": "测试标的",
        "is_etf": False,
        "score": _score(total=30.0, volume=0.0),
        "institutional_score": np.nan,
        "final_score": np.nan,
        "signal_recency_days": 5,
        "value_trap_risk": np.nan,
        "quality_data_available": False,
        "quality_gate": True,
        "filter_details": {},
    }
    defaults.update(overrides)
    return ScanResult(**defaults)


def _tier_probe(score: float) -> Any:
    """A result that can reach tier A, so only the threshold decides."""
    return _result(institutional_score=score, signal_recency_days=5, score=_score(total=score, volume=20.0))


#: Twelve rows: ETFs with resolved and unresolved themes, a cash-management ETF
#: (excluded by name), stocks, and rows whose industry/sector must substitute for
#: a missing ModelClassification.
def _candidate_frame() -> pd.DataFrame:
    rows = [
        # (Ticker, Name, IsETF, AssetType, Industry, Sector, ModelClassification, ETFTheme)
        ("510300.SH", "沪深300ETF", True, "ETF", "宽基", "宽基", "宽基指数", "沪深300"),
        ("510500.SH", "中证500ETF", True, "ETF", "宽基", "宽基", "宽基指数", "中证500"),
        ("159001.SZ", "货币ETF", True, "ETF", "现金管理", "现金管理", "货币", "货币"),
        ("512880.SH", "证券ETF", True, "ETF", "", "金融", "", ""),
        ("588000.SH", "科创50ETF", True, "ETF", "宽基", "宽基", "科创板", "科创50"),
        ("513100.SH", "纳指ETF", True, "ETF", "海外", "海外", "", ""),
        ("600519.SH", "贵州茅台", False, "stock", "白酒", "消费", "核心资产", ""),
        ("000858.SZ", "五粮液", False, "stock", "白酒", "消费", "核心资产", ""),
        ("601318.SH", "中国平安", False, "stock", "保险", "金融", "低估值", ""),
        ("600036.SH", "招商银行", False, "stock", "银行", "金融", "", ""),
        ("300750.SZ", "宁德时代", False, "stock", "电池", "新能源", "成长", ""),
        ("002594.SZ", "比亚迪", False, "stock", "汽车", "新能源", "成长", ""),
    ]
    frame = pd.DataFrame(
        rows,
        columns=["Ticker", "Name", "IsETF", "AssetType", "Industry", "Sector", "ModelClassification", "ETFTheme"],
    )
    # Non-monotonic in RankingScore on purpose: a monotonic corpus makes the
    # greedy selection collapse to "take the first N" and would not detect a
    # change in the penalty arithmetic.
    frame["RankingScore"] = [round(50.0 + 17.0 * np.sin(i * 1.1), 6) for i in range(len(rows))]
    frame["CrossAssetScore"] = [round(40.0 + 9.0 * np.cos(i * 0.9), 6) for i in range(len(rows))]
    # Two risk-filtered rows so the 1e9 penalty branch runs.
    frame["RankingEligibility"] = ["风险过滤" if i in (2, 9) else "观察" for i in range(len(rows))]
    frame["ETFTrackingKey"] = ""
    frame["ThemeCluster"] = ""
    frame["HardGatePassed"] = [True] * 10 + [False, True]
    frame["UniverseEligible"] = True
    frame["PassedFilters"] = [True] * 11 + [False]
    frame["HardGateFailedNames"] = [""] * 10 + ["流动性不足"] + [""]
    return frame


def _candidate_frame_without_optional_columns() -> pd.DataFrame:
    return _candidate_frame()[
        ["Ticker", "Name", "IsETF", "AssetType", "Industry", "Sector", "RankingScore", "RankingEligibility"]
    ].copy()


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Ticker": pd.Series(dtype="object"),
            "Name": pd.Series(dtype="object"),
            "IsETF": pd.Series(dtype=bool),
            "AssetType": pd.Series(dtype="object"),
        }
    )


#: A frame whose index repeats, proving ``_policy_column`` is position-indexed.
def _duplicate_index_frame() -> pd.DataFrame:
    return pd.DataFrame({"AssetType": ["ETF", "stock", "ETF"]}, index=[0, 0, 1])


def _legacy_policy_frame() -> pd.DataFrame:
    """No HardGatePassed at all -- the pre-hard-gate export shape."""
    return pd.DataFrame(
        {
            "Ticker": ["600000.SH", "600001.SH", "600002.SH"],
            "Name": ["甲", "乙", "丙"],
            "IsETF": [False, False, False],
            "AssetType": ["stock", "stock", "stock"],
            "UniverseEligible": [True, False, True],
            "PassedFilters": [True, True, False],
        }
    )


def _rankable_corpus() -> list[Any]:
    return [
        _result(ticker="600000.SH"),
        _result(ticker="600001.SH", error="boom"),
        _result(ticker="600002.SH"),
    ]


# ---------------------------------------------------------------------------
# Case table: (label, function_name, args, kwargs, transform)
# ---------------------------------------------------------------------------

#: Where the extracted cluster is going.  The gate in
#: test_report_core_golden.py is written against this constant so the same test
#: keeps working across the boundary, and resolves it *live* rather than from
#: the frozen file -- an overlay that rebinds ``report_core.<name>`` after the
#: move would resolve to the overlay's own module and fail.
MOVE_HOME = "institution_scanner.report_selection"

#: Where the canaries must remain.
STAY_HOME = "report_core"

#: ``_truthy`` was byte-identical to ``publication_renderer._truthy``, and
#: ``test_canonical_package_discipline`` refuses byte-identical bodies inside the
#: package.  The duplication predates this extraction -- it was invisible while
#: the copy sat in root-level ``report_core``, and only became visible once the
#: cluster crossed into ``institution_scanner``.  It went to ``_common``, which
#: exists precisely for this.
SHARED_HOME = "institution_scanner._common"

#: Module-level names the moved code reads that an overlay rebinds *after*
#: import.  ``score_threshold_migration_v95`` rewrites these on ``config``, so
#: resolving them by value would freeze whichever snapshot happened to be
#: current when the new module was first imported.  The extracted module holds
#: the ``config`` module object and reads the attribute at call time; the gate
#: asserts both routes agree.
LATE_BOUND_NAMES = (
    "INSTITUTIONAL_TIER_A_SCORE",
    "INSTITUTIONAL_TIER_B_SCORE",
    "INSTITUTIONAL_TIER_C_SCORE",
)

#: The 11-function self-consistent closure reported by
#: ``recon_extraction_targets.py --seed``.
MOVE_CANDIDATES = (
    "_institutional_tier",
    "_policy_column",
    "_policy_text",
    "_policy_truthy",
    "_vectorized_etf_research_policy",
    "_apply_research_policy",
    "_clean_group_key",
    "_etf_theme_key",
    "_ensure_diversity_columns",
    "_diversify_ranked_candidates",
)

#: Moved, but to the package's shared-helper module rather than to
#: ``MOVE_HOME``.  See ``SHARED_HOME``.
SHARED_MOVES = ("_truthy",)

#: Captured but deliberately NOT moved.  ``report_determinism`` rebinds
#: ``_rankable_results`` on ``report_core`` at import time, so extracting it
#: would leave the overlay writing to ``report_core`` while production read the
#: extracted copy -- the same failure mode as ``_date_balanced_weights`` in the
#: analytics extraction.
PATCHED_CANARIES = ("_rankable_results",)

FUNCTIONS = MOVE_CANDIDATES + SHARED_MOVES + PATCHED_CANARIES

#: (score, tier under the migrated thresholds, tier under the pre-migration
#: thresholds).  The three scores sit inside the 35.0->36.0825, 30.0->30.9278
#: and 25.0->25.7732 gaps, so each one returns a *different* answer depending on
#: whether ``score_threshold_migration_v95`` ran.  A fixture that omitted these
#: would pass identically with either constant set.
MIGRATION_PROBES: tuple[tuple[float, str, str], ...] = (
    (35.5, "B级观察", "A级机构启动"),
    (30.5, "C级价值观察", "B级观察"),
    (25.4, "D级等待确认", "C级价值观察"),
)


def build_cases() -> list[tuple[str, str, tuple[Any, ...], dict[str, Any], Callable[[Any], Any] | None]]:
    candidates = _candidate_frame()
    sparse = _candidate_frame_without_optional_columns()
    empty = _empty_frame()

    def tickers(rows: list[Any]) -> list[str]:
        return [row.ticker for row in rows]

    cases: list[tuple[str, str, tuple[Any, ...], dict[str, Any], Callable[[Any], Any] | None]] = [
        # --- _institutional_tier: one case per branch -----------------------
        ("tier/a_full", "_institutional_tier", (_tier_probe(40.0),), {}, None),
        ("tier/a_recency_out", "_institutional_tier", (_result(institutional_score=40.0, signal_recency_days=25, score=_score(total=40.0, volume=20.0)),), {}, None),
        ("tier/a_volume_unconfirmed", "_institutional_tier", (_result(institutional_score=40.0, signal_recency_days=5, score=_score(total=40.0, volume=0.0)),), {}, None),
        ("tier/a_volume_via_filter_details", "_institutional_tier", (_result(institutional_score=40.0, signal_recency_days=5, score=_score(total=40.0, volume=0.0), filter_details={"volume_accumulation": True}),), {}, None),
        ("tier/b", "_institutional_tier", (_tier_probe(33.0),), {}, None),
        ("tier/c", "_institutional_tier", (_tier_probe(26.0),), {}, None),
        ("tier/d", "_institutional_tier", (_tier_probe(20.0),), {}, None),
        ("tier/quality_downgrade_a_to_b", "_institutional_tier", (_result(institutional_score=40.0, signal_recency_days=5, score=_score(total=40.0, volume=20.0), quality_data_available=True, quality_gate=False),), {}, None),
        ("tier/quality_downgrade_b_to_c", "_institutional_tier", (_result(institutional_score=33.0, signal_recency_days=5, score=_score(total=33.0, volume=20.0), quality_data_available=True, quality_gate=False),), {}, None),
        ("tier/etf_never_downgraded", "_institutional_tier", (_result(institutional_score=40.0, signal_recency_days=5, score=_score(total=40.0, volume=20.0), is_etf=True, quality_data_available=True, quality_gate=False),), {}, None),
        ("tier/trap", "_institutional_tier", (_result(institutional_score=40.0, signal_recency_days=5, score=_score(total=40.0, volume=20.0), value_trap_risk=70.0),), {}, None),
        ("tier/trap_etf_ignored", "_institutional_tier", (_result(institutional_score=40.0, signal_recency_days=5, score=_score(total=40.0, volume=20.0), is_etf=True, value_trap_risk=70.0),), {}, None),
        ("tier/trap_nan_risk", "_institutional_tier", (_result(institutional_score=40.0, signal_recency_days=5, score=_score(total=40.0, volume=20.0), value_trap_risk=np.nan),), {}, None),
        ("tier/fallback_final_score", "_institutional_tier", (_result(final_score=33.0, score=_score(total=10.0, volume=20.0)),), {}, None),
        ("tier/fallback_total", "_institutional_tier", (_result(score=_score(total=33.0, volume=20.0)),), {}, None),
        # --- _institutional_tier: threshold-migration probes ----------------
        # These are the reason LATE_BOUND_NAMES exists.  See MIGRATION_PROBES.
        ("tier/probe_gap_a", "_institutional_tier", (_tier_probe(35.5),), {}, None),
        ("tier/probe_gap_b", "_institutional_tier", (_tier_probe(30.5),), {}, None),
        ("tier/probe_gap_c", "_institutional_tier", (_tier_probe(25.4),), {}, None),
        # --- scalar helpers -------------------------------------------------
        ("truthy/true", "_truthy", ("true",), {}, None),
        ("truthy/upper", "_truthy", (" TRUE ",), {}, None),
        ("truthy/one", "_truthy", ("1",), {}, None),
        ("truthy/int_one", "_truthy", (1,), {}, None),
        ("truthy/chinese", "_truthy", ("是",), {}, None),
        ("truthy/false", "_truthy", ("false",), {}, None),
        ("truthy/zero", "_truthy", ("0",), {}, None),
        ("truthy/empty", "_truthy", ("",), {}, None),
        ("truthy/none", "_truthy", (None,), {}, None),
        ("clean_group_key/none", "_clean_group_key", (None,), {}, None),
        ("clean_group_key/nan", "_clean_group_key", (np.nan,), {}, None),
        ("clean_group_key/pd_na", "_clean_group_key", (pd.NA,), {}, None),
        ("clean_group_key/blank", "_clean_group_key", ("   ",), {}, None),
        ("clean_group_key/text_nan", "_clean_group_key", ("nan",), {}, None),
        ("clean_group_key/text_none", "_clean_group_key", ("None",), {}, None),
        ("clean_group_key/nat", "_clean_group_key", ("NaT",), {}, None),
        ("clean_group_key/value", "_clean_group_key", ("  银行  ",), {}, None),
        ("clean_group_key/number", "_clean_group_key", (123,), {}, None),
        # --- _policy_* vectorized helpers -----------------------------------
        ("policy_column/present", "_policy_column", (candidates, "AssetType", ""), {}, None),
        ("policy_column/missing", "_policy_column", (candidates, "Nope", "fallback"), {}, None),
        ("policy_column/duplicate_index", "_policy_column", (_duplicate_index_frame(), "AssetType", ""), {}, None),
        ("policy_text/mixed", "_policy_text", (pd.Series(["  银行  ", None, "NaN", "NONE", "<NA>", "ETF "]),), {}, None),
        ("policy_text/empty", "_policy_text", (pd.Series([], dtype="object"),), {}, None),
        ("policy_truthy/mixed", "_policy_truthy", (pd.Series(["true", "1", "是", "no", None, " TRUE "]),), {}, None),
        ("policy_truthy/empty", "_policy_truthy", (pd.Series([], dtype="object"),), {}, None),
        # --- ETF research policy --------------------------------------------
        ("vectorized_policy/candidates", "_vectorized_etf_research_policy", (candidates, candidates["IsETF"].to_numpy(dtype=bool)), {}, None),
        ("vectorized_policy/none_are_etf", "_vectorized_etf_research_policy", (candidates, np.zeros(len(candidates), dtype=bool)), {}, None),
        ("vectorized_policy/sparse", "_vectorized_etf_research_policy", (sparse, sparse["IsETF"].to_numpy(dtype=bool)), {}, None),
        ("apply_research_policy/candidates", "_apply_research_policy", (candidates,), {}, None),
        ("apply_research_policy/sparse", "_apply_research_policy", (sparse,), {}, None),
        ("apply_research_policy/legacy", "_apply_research_policy", (_legacy_policy_frame(),), {}, None),
        ("apply_research_policy/empty", "_apply_research_policy", (empty,), {}, None),
        # --- diversity provenance -------------------------------------------
        ("etf_theme_key/etf_row", "_etf_theme_key", (candidates.iloc[3],), {}, None),
        ("etf_theme_key/stock_row", "_etf_theme_key", (candidates.iloc[6],), {}, None),
        ("etf_theme_key/asset_type_only", "_etf_theme_key", (pd.Series({"AssetType": "ETF", "Name": "沪深300ETF", "Industry": "宽基", "Sector": "宽基", "Ticker": "510300.SH"}),), {}, None),
        ("ensure_diversity_columns/candidates", "_ensure_diversity_columns", (candidates,), {}, None),
        ("ensure_diversity_columns/sparse", "_ensure_diversity_columns", (sparse,), {}, None),
        ("diversify/full", "_diversify_ranked_candidates", (candidates, 6), {}, None),
        ("diversify/prepared", "_diversify_ranked_candidates", (_ensure(candidates), 6), {"diversity_prepared": True}, None),
        ("diversify/one_per_theme", "_diversify_ranked_candidates", (candidates, 6), {"max_per_theme": 1}, None),
        ("diversify/one_per_industry", "_diversify_ranked_candidates", (candidates, 6), {"max_per_stock_industry": 1}, None),
        ("diversify/limit_zero", "_diversify_ranked_candidates", (candidates, 0), {}, None),
        ("diversify/empty", "_diversify_ranked_candidates", (empty, 5), {}, None),
        ("diversify/sparse", "_diversify_ranked_candidates", (sparse, 5), {}, None),
        # --- canary: rebound by report_determinism, must stay ---------------
        ("rankable_results/mixed", "_rankable_results", (_rankable_corpus(),), {}, tickers),
    ]
    return cases


def _ensure(frame: pd.DataFrame) -> pd.DataFrame:
    """Run ``_ensure_diversity_columns`` so the ``diversity_prepared`` path is real."""
    return getattr(_core, "_ensure_diversity_columns")(frame)


def resolved_global(name: str) -> Any:
    """Return the object the extracted code will actually use for ``name``.

    Two ways a module can supply a global, and only one of them survives an
    overlay rebinding it later:

    * by value (``from config import X``) -- the name sits in the module dict,
      frozen at import time.  ``score_threshold_migration_v95`` rewriting
      ``config.INSTITUTIONAL_TIER_A_SCORE`` afterwards never reaches it.
    * through a module object (``import config`` + ``config.X``) -- the
      attribute is looked up at call time, so the migrated value wins.

    This walks the module dict so the gate tests what the code really calls,
    not what the test author assumed it calls.
    """
    extracted = importlib.import_module(MOVE_HOME)
    namespace = vars(extracted)
    if name in namespace:
        return namespace[name]
    for value in namespace.values():
        if isinstance(value, ModuleType) and hasattr(value, name):
            return getattr(value, name)
    raise AssertionError(
        f"{MOVE_HOME} supplies no binding for {name}; the moved code would raise NameError"
    )


def live_provenance() -> dict[str, str]:
    """Resolve every tracked function *now*, under the production assembly.

    The patch-detection gates must read provenance live.  Reading it from the
    frozen fixture instead would make them unfalsifiable: they would assert what
    was true when the file was written, not what is true today -- which is
    exactly how a post-move overlay rebind would slip through.
    """
    return {
        name: f"{getattr(_core, name).__module__}.{getattr(_core, name).__qualname__}"
        for name in FUNCTIONS
    }


def capture() -> dict[str, Any]:
    cases: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    for label, function_name, args, kwargs, transform in build_cases():
        function = getattr(_core, function_name)
        provenance[function_name] = f"{function.__module__}.{function.__qualname__}"
        try:
            value = function(*args, **kwargs)
            cases[label] = {"value": plain(value if transform is None else transform(value))}
        except Exception as exc:  # noqa: BLE001 - freezing whatever comes out
            cases[label] = {"raised": type(exc).__name__}

    return {
        "schema": 1,
        "note": (
            "Golden output of report_core's candidate-selection cluster, captured "
            "AFTER the report facade has been imported so the production assembly "
            "is in place. Recapture with `python tests/golden_report_core.py` ONLY "
            "after reviewing the diff."
        ),
        "captured_with": {
            "python": sys.version.split()[0],
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
        "functions": list(FUNCTIONS),
        "move_candidates": list(MOVE_CANDIDATES),
        "shared_moves": list(SHARED_MOVES),
        "patched_canaries": list(PATCHED_CANARIES),
        "late_bound_names": list(LATE_BOUND_NAMES),
        "migration_probes": [
            {"score": score, "migrated": migrated, "legacy": legacy}
            for score, migrated, legacy in MIGRATION_PROBES
        ],
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
