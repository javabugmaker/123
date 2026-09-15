"""Deterministic corpus + golden capture for ``signal_lifecycle_core``'s attribute cluster.

``signal_lifecycle_core`` is 67,788 bytes against a 70,000 byte budget -- 2,212
bytes of headroom.  The cluster frozen here is the 17-function / 371-line
dependency closure that ``tests/recon_extraction_targets.py --seed`` reports as
self-consistent: no patched dependency, no dependency outside the closure.

What reconnaissance established before any of this was written:

* **Three functions are rebound and must stay.**  ``_is_active``,
  ``finalize_signal_ranking`` and ``strict_filter_override_mask`` all resolve to
  ``signal_lifecycle`` at runtime.  They are captured as canaries so a future
  move that forgot them fails here.
* **The constants this cluster reads are NOT migrated.**  T3' found that
  ``score_threshold_migration_v95`` rewrites ``INSTITUTIONAL_TIER_A/B/C_SCORE``
  on ``config`` after import (35.0 -> 36.0825).  The obvious worry is that the
  same happens to ``BACKTEST_*`` / ``DATA_FRESHNESS_*`` / ``TRADE_READY_*``.
  It does not: comparing ``config`` before and after the production assembly
  leaves all eleven unchanged, while the three tier scores move.  So this module
  imports them by value -- but ``CONSTANT_NAMES`` exists so that a future
  migration is caught as a loud mismatch instead of a silent off-by-one.
* **``_load_history`` is deliberately NOT in the cluster.**  It reads
  ``HISTORY_FILE`` and ``HISTORY_COLUMNS``, which are *defined* in
  ``signal_lifecycle_core`` and also used by ``enrich_signal_lifecycle``, which
  stays.  Moving it would force the new module to import the old one -- a cycle.
  It costs 1,010 bytes and saves moving two module-level constants that
  ``analytics_core`` already imports by value from the facade.

Recapture with::

    python tests/golden_signal_lifecycle.py
"""

from __future__ import annotations

import importlib
import json
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
_TESTS_DIR = Path(__file__).resolve().parent
for _path in (str(_REPO_ROOT), str(_TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from golden_analytics_core import plain  # noqa: E402

# Pin the production assembly before capturing: ``signal_lifecycle`` rebinds
# three functions on ``signal_lifecycle_core`` and finishes with
# ``sys.modules[__name__] = _core``, so a bare ``import signal_lifecycle_core``
# would freeze objects production never calls.
import signal_lifecycle  # noqa: E402,F401
import signal_lifecycle_core as _core  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "signal_lifecycle_golden.json"

# ---------------------------------------------------------------------------
# Deterministic corpus.  No RNG anywhere; every value is written out.
# ---------------------------------------------------------------------------


def _frame() -> pd.DataFrame:
    """Twelve rows covering the branches every function in the cluster reads."""
    rows = [
        # EntrySignal, RawEntrySignal, StopDistancePct, RewardRiskRatio, SignalStatus, SignalTrend
        ("BUY_NOW", "", 5.0, 2.0, "ACTIVE", "上升"),
        ("BUY_NOW", "", np.nan, 2.0, "ACTIVE", "上升"),
        ("BUY_NOW", "", 30.0, 2.0, "ACTIVE", "上升"),
        ("BUY_NOW", "", 5.0, 0.5, "ACTIVE", "上升"),
        ("BREAKOUT_CONFIRM", "", 5.0, 2.0, "FAILED", "快速下跌"),
        ("BREAKOUT_CONFIRM", "BREAKOUT_CONFIRM", 5.0, 2.0, "WEAKEN", "快速走弱"),
        ("WATCH", "", 5.0, 2.0, "EXPIRED", "横盘"),
        ("AVOID", "", 5.0, 2.0, "WEAKEN", "缓慢走弱"),
        ("PRICE_BREAKOUT", "", 5.0, 2.0, "INACTIVE", "FAST"),
        ("BUY_NOW", "", 5.0, 2.0, "ACTIVE", "RAPID"),
        ("BREAKOUT_CONFIRM", "", 5.0, 2.0, "ACTIVE", "上升"),
        ("AVOID", "", 5.0, 2.0, "ACTIVE", "上升"),
    ]
    columns = [
        "EntrySignal", "RawEntrySignal", "StopDistancePct", "RewardRiskRatio",
        "SignalStatus", "SignalTrend",
    ]
    frame = pd.DataFrame(rows, columns=columns)

    # 突破确认：只有前两行量价齐备，第 5、6 行是"缺确认"的主力
    frame["BreakoutVolumeRatio"] = [1.5, 1.5, 1.1, 1.1, 0.9, np.nan, 1.3, 1.3, 1.3, 1.3, 1.5, 1.0]
    frame["BreakoutVolumeConfirmed"] = ["true", "true", "false", "false", "false", "false", "1", "1", "1", "1", "true", "0"]
    frame["BreakoutFlowConfirmed"] = ["true", "true", "true", "true", "false", "false", "1", "1", "1", "1", "true", "0"]
    frame["CMF_Pos"] = ["true", "false", "false", "false", "false", "false", "false", "false", "false", "false", "true", "false"]
    frame["CMF"] = [0.2, -0.1, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.05, 0.2, -0.2]
    frame["AD_SlopePos"] = ["false", "false", "false", "false", "false", "false", "false", "false", "false", "false", "false", "false"]
    frame["AD_Slope"] = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    frame["OBV_Div"] = ["false", "false", "false", "false", "false", "false", "false", "false", "false", "false", "false", "false"]
    frame["SignalAdjustmentReason"] = ["", "", "", "", "", "", "", "", "", "", "", ""]

    # 机构持仓
    frame["InstitutionHoldingStatus"] = ["PASS", "", "UNKNOWN", "FAIL", "", "", "unknown", "", "", "", "", ""]
    frame["InstitutionHoldingPeriods"] = [4.0, 4.0, 4.0, 4.0, 1.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0, 4.0]
    frame["InstitutionHoldingTrend"] = ["上涨", "Increasing", "连续减少", "否", "增加", "false", "UP", "1", "是", "0", "unknown", ""]

    # 数据新鲜度：0/2 新鲜，5 延迟，20 过期，NaN/负 未知
    frame["DataTradingAgeDays"] = [0.0, 2.0, 5.0, 20.0, np.nan, -1.0, 3.0, 11.0, 0.0, 0.0, 0.0, 0.0]
    frame["DataAgeDays"] = [0.0, 2.0, 5.0, 20.0, np.nan, 7.0, 3.0, 11.0, 0.0, 0.0, 0.0, 0.0]

    # 周期评分输入
    frame["TrendScore"] = [18.0, 12.0, 6.0, 20.0, 0.0, 4.0, 10.0, 14.0, 8.0, 16.0, 2.0, 9.0]
    frame["VolumeScore"] = [20.0, 15.0, 5.0, 25.0, 0.0, 6.0, 12.0, 18.0, 9.0, 22.0, 3.0, 11.0]
    frame["AccumulationScore"] = [20.0, 16.0, 4.0, 25.0, 0.0, 5.0, 13.0, 19.0, 8.0, 21.0, 2.0, 10.0]
    frame["StructureScore"] = [12.0, 9.0, 3.0, 15.0, 0.0, 4.0, 7.0, 11.0, 6.0, 13.0, 1.0, 8.0]
    frame["CompressionScore"] = [12.0, 9.0, 3.0, 15.0, 0.0, 4.0, 7.0, 11.0, 6.0, 13.0, 1.0, 8.0]
    frame["IndustryRelativeStrength"] = [5.0, 0.0, -5.0, 10.0, -20.0, 2.0, -2.0, 4.0, 1.0, 8.0, -8.0, 0.0]

    # 阶段
    frame["Stage"] = ["正在吸筹", "已经启动", "趋势确认", np.nan, "观察", "观察", "观察", "观察", "观察", "观察", "观察", "观察"]
    frame["RSI14"] = [55.0, 60.0, 70.0, 50.0, 30.0, 80.0, 45.0, 35.0, 68.0, 78.0, 50.0, 40.0]
    frame["DistToLow52W"] = [20.0, 25.0, 35.0, 10.0, 50.0, 65.0, 5.0, 2.0, 32.0, 61.0, 45.0, 46.0]
    frame["Score"] = [45.0, 42.0, 50.0, 38.0, 20.0, 60.0, 30.0, 25.0, 44.0, 70.0, 41.0, 15.0]
    return frame


def _minimal_frame() -> pd.DataFrame:
    """Nothing but an index -- exercises every 'column absent' branch."""
    return pd.DataFrame(index=pd.RangeIndex(3))


def _legacy_breakout_frame() -> pd.DataFrame:
    """Pre-``BreakoutVolumeRatio`` export shape: only the boolean flags."""
    return pd.DataFrame(
        {
            "EntrySignal": ["BREAKOUT_CONFIRM", "BREAKOUT_CONFIRM", "BUY_NOW"],
            "BreakoutVolumeConfirmed": ["true", "false", "true"],
            "BreakoutFlowConfirmed": ["true", "true", "false"],
        }
    )


ATOMIC_DIR = Path(tempfile.mkdtemp(prefix="golden_signal_lifecycle_"))
ATOMIC_PATH = ATOMIC_DIR / "atomic_roundtrip.csv"


def _read_atomic(_: Any) -> Any:
    """Read back what ``_atomic_write`` produced.

    ``_atomic_write`` returns ``None``, so the return value proves nothing.
    Reading the file back through pandas normalises the line terminator, which
    would otherwise differ between a CRLF checkout and an LF one.
    """
    return pd.read_csv(ATOMIC_PATH, encoding="utf-8-sig", dtype=str)


# ---------------------------------------------------------------------------
# Case table: (label, function_name, args, kwargs, transform)
# ---------------------------------------------------------------------------

#: Where the extracted cluster is going.
MOVE_HOME = "institution_scanner.signal_attributes"

#: Where the canaries must remain.
STAY_HOME = "signal_lifecycle_core"

#: An earlier attempt merged ``_bool`` into ``institution_scanner._common._truthy``
#: -- the two bodies are identical -- and it was reverted.  Two reasons, both
#: worth keeping:
#:
#: 1. ``signal_lifecycle.py:90/95`` do ``passed_filters.map(_core._bool)``.  That
#:    is an overlay reaching *into* the module to grab a symbol by attribute.
#:    recon compares ``__module__`` and therefore cannot see it, so the merge
#:    looked safe and silently broke both canaries.  Renaming a symbol that an
#:    overlay fetches by attribute is not a refactor T4 is allowed to make.
#: 2. With the merge, equivalence proof 1 stops being 17/17 byte-identical and
#:    becomes "16 identical plus one rename we assert by hand".
#:
#: The duplication is real and still open, but it belongs to the dedup pass,
#: where the overlay call sites get updated in the same change.
SHARED_HOME = "institution_scanner._common"

#: Moved, but to the package's shared-helper module rather than to ``MOVE_HOME``.
SHARED_MOVES: tuple[str, ...] = ()

#: The 17-function self-consistent closure reported by
#: ``recon_extraction_targets.py --seed``.  ``_load_history`` is excluded on
#: purpose -- see the module docstring.
MOVE_CANDIDATES = (
    "_bool",
    "_number",
    "_bool_series",
    "_text_series",
    "_append_reason",
    "_execution_risk_block",
    "_breakout_confirmation_ok",
    "_lifecycle_risk_masks",
    "_holding_status",
    "_data_freshness",
    "_backtest_confidence",
    "validate_signal_consistency",
    "_atomic_write",
    "_period_scores",
    "_opportunity_score",
    "_stage",
    "_status",
)

#: Captured but deliberately NOT moved: all three resolve to ``signal_lifecycle``
#: at runtime, so extracting them would leave the overlay writing to
#: ``signal_lifecycle_core`` while production read the extracted copy.
PATCHED_CANARIES = (
    "_is_active",
    "finalize_signal_ranking",
    "strict_filter_override_mask",
)

FUNCTIONS = MOVE_CANDIDATES + SHARED_MOVES + PATCHED_CANARIES

#: Module-level constants the cluster reads.  Verified unchanged across the
#: production assembly (unlike ``INSTITUTIONAL_TIER_*``), so the extracted module
#: may import them by value -- but the gate re-checks every run.
CONSTANT_NAMES = (
    "BACKTEST_FULL_WEIGHT_SAMPLES",
    "BACKTEST_LOW_CONFIDENCE_MAX_SAMPLES",
    "BACKTEST_MIN_SAMPLES_FOR_RANKING",
    "BACKTEST_NORMAL_WEIGHT",
    "BREAKOUT_CONFIRM_MIN_VOLUME_RATIO",
    "DATA_FRESHNESS_DELAYED_FACTOR",
    "DATA_FRESHNESS_DELAYED_TRADING_DAYS",
    "DATA_FRESHNESS_STALE_FACTOR",
    "DATA_FRESHNESS_STALE_TRADING_DAYS",
    "TRADE_READY_MAX_STOP_DISTANCE_PCT",
    "TRADE_READY_MIN_REWARD_RISK",
)


def build_cases() -> list[tuple[str, str, tuple[Any, ...], dict[str, Any], Callable[[Any], Any] | None]]:
    frame = _frame()
    minimal = _minimal_frame()
    legacy = _legacy_breakout_frame()
    signal = frame["EntrySignal"]

    def status_previous(active: bool, score: float) -> pd.Series:
        return pd.Series({"SignalActive": active, "OpportunityScore": score})

    cases: list[tuple[str, str, tuple[Any, ...], dict[str, Any], Callable[[Any], Any] | None]] = [
        # --- coercions ------------------------------------------------------
        ("number/typical", "_number", (pd.Series(["1.5", "x", None, "3"]),), {}, None),
        ("number/inf", "_number", (pd.Series([np.inf, -np.inf, np.nan]),), {}, None),
        ("number/default", "_number", (pd.Series([np.nan, None]),), {"default": -1.0}, None),
        ("number/empty", "_number", (pd.Series([], dtype=object),), {}, None),
        ("bool/true", "_bool", ("true",), {}, None),
        ("bool/padded", "_bool", (" TRUE ",), {}, None),
        ("bool/chinese", "_bool", ("是",), {}, None),
        ("bool/int", "_bool", (1,), {}, None),
        ("bool/false", "_bool", ("false",), {}, None),
        ("bool/none", "_bool", (None,), {}, None),
        ("bool_series/present", "_bool_series", (frame, "BreakoutVolumeConfirmed"), {}, None),
        ("bool_series/missing", "_bool_series", (frame, "Nope"), {}, None),
        ("bool_series/missing_default_true", "_bool_series", (frame, "Nope", True), {}, None),
        ("bool_series/empty", "_bool_series", (minimal, "Nope"), {}, None),
        ("text_series/present", "_text_series", (frame, "SignalTrend"), {}, None),
        ("text_series/missing", "_text_series", (frame, "Nope"), {}, None),
        ("text_series/nulls", "_text_series", (pd.DataFrame({"C": [" a ", None, np.nan]}), "C"), {}, None),
        ("text_series/empty", "_text_series", (minimal, "C", "z"), {}, None),
        # --- reason accumulation --------------------------------------------
        ("append_reason/onto_empty", "_append_reason", (pd.Series(["", "", ""]), pd.Series([True, False, True]), "缺量能"), {}, None),
        ("append_reason/onto_existing", "_append_reason", (pd.Series(["已有", "", None]), pd.Series([True, True, True]), "缺量能"), {}, None),
        ("append_reason/idempotent", "_append_reason", (pd.Series(["缺量能", "缺量能；其他", ""]), pd.Series([True, True, True]), "缺量能"), {}, None),
        ("append_reason/none_true", "_append_reason", (pd.Series(["a", "b"]), pd.Series([False, False]), "缺量能"), {}, None),
        # --- risk blocks -----------------------------------------------------
        ("exec_risk/full", "_execution_risk_block", (frame, signal), {}, None),
        ("exec_risk/absent_columns", "_execution_risk_block", (minimal, pd.Series(["BUY_NOW"] * 3)), {}, None),
        ("exec_risk/stop_only", "_execution_risk_block", (frame[["StopDistancePct"]], signal), {}, None),
        ("exec_risk/reward_only", "_execution_risk_block", (frame[["RewardRiskRatio"]], signal), {}, None),
        ("exec_risk/inactive_signal", "_execution_risk_block", (frame, pd.Series(["AVOID"] * len(frame))), {}, None),
        ("breakout_ok/full", "_breakout_confirmation_ok", (frame, signal), {}, None),
        ("breakout_ok/no_ratio_column", "_breakout_confirmation_ok", (frame.drop(columns=["BreakoutVolumeRatio"]), signal), {}, None),
        ("breakout_ok/legacy", "_breakout_confirmation_ok", (legacy, legacy["EntrySignal"]), {}, None),
        ("lifecycle_masks/full", "_lifecycle_risk_masks", (frame,), {}, None),
        ("lifecycle_masks/absent", "_lifecycle_risk_masks", (minimal,), {}, None),
        ("lifecycle_masks/fast_en", "_lifecycle_risk_masks", (pd.DataFrame({"SignalStatus": ["weaken", "weaken"], "SignalTrend": ["FAST", "slow"]}),), {}, None),
        # --- holding / freshness / confidence --------------------------------
        ("holding/full", "_holding_status", (frame,), {}, None),
        ("holding/upper_input", "_holding_status", (pd.DataFrame({"InstitutionHoldingStatus": ["pass", "fail", "unknown", ""]}),), {}, None),
        ("holding/absent", "_holding_status", (minimal,), {}, None),
        ("freshness/full", "_data_freshness", (frame,), {}, None),
        ("freshness/trading_only", "_data_freshness", (frame[["DataTradingAgeDays"]],), {}, None),
        ("freshness/calendar_only", "_data_freshness", (frame[["DataAgeDays"]],), {}, None),
        ("freshness/absent", "_data_freshness", (minimal,), {}, None),
        ("confidence/full", "_backtest_confidence", (pd.Series([0.0, 5.0, 10.0, 20.0, 49.0, 50.0, 80.0]), pd.Series([0.0, 5.0, 10.0, 20.0, 30.0, 50.0, 40.0]), pd.Series([0.0, 5.0, 10.0, 20.0, 30.0, 90.0, np.nan])), {}, None),
        ("confidence/effective_exceeds", "_backtest_confidence", (pd.Series([10.0, 20.0]), pd.Series([999.0, 999.0]), pd.Series([0.0, 0.0])), {}, None),
        ("confidence/effective_zero", "_backtest_confidence", (pd.Series([30.0, 30.0]), pd.Series([0.0, 0.0]), pd.Series([0.0, 40.0])), {}, None),
        ("confidence/empty", "_backtest_confidence", (pd.Series([], dtype=float), pd.Series([], dtype=float), pd.Series([], dtype=float)), {}, None),
        # --- validation ------------------------------------------------------
        ("validate/full", "validate_signal_consistency", (frame,), {}, None),
        ("validate/legacy", "validate_signal_consistency", (legacy,), {}, None),
        ("validate/no_ratio", "validate_signal_consistency", (frame.drop(columns=["BreakoutVolumeRatio"]),), {}, None),
        ("validate/no_flow_metrics", "validate_signal_consistency", (frame.drop(columns=["CMF_Pos", "CMF", "AD_SlopePos", "AD_Slope", "OBV_Div"]),), {}, None),
        ("validate/minimal", "validate_signal_consistency", (minimal,), {}, None),
        ("validate/reason_roundtrip", "validate_signal_consistency", (getattr(_core, "validate_signal_consistency")(frame),), {}, None),
        # --- atomic write ----------------------------------------------------
        ("atomic_write/roundtrip", "_atomic_write", (pd.DataFrame({"A": ["1", "2"], "B": ["x", "y"]}), ATOMIC_PATH), {}, _read_atomic),
        # --- period scores / stage / status ----------------------------------
        ("period_scores/full", "_period_scores", (frame,), {}, None),
        ("period_scores/partial", "_period_scores", (frame[["TrendScore", "VolumeScore"]],), {}, None),
        ("period_scores/absent", "_period_scores", (minimal,), {}, None),
        ("opportunity/typical", "_opportunity_score", (pd.Series([10.0, 90.0]), pd.Series([20.0, 80.0]), pd.Series([30.0, 70.0])), {}, None),
        ("opportunity/clipped", "_opportunity_score", (pd.Series([200.0]), pd.Series([200.0]), pd.Series([200.0])), {}, None),
        ("stage/full", "_stage", (frame,), {}, None),
        ("stage/absent", "_stage", (minimal,), {}, None),
        ("stage/near_low", "_stage", (pd.DataFrame({"Stage": ["观察"], "RSI14": [50.0], "DistToLow52W": [3.0], "Score": [10.0], "AccumulationScore": [0.0]}),), {}, None),
        ("status/inactive_failed", "_status", (False, status_previous(True, 50.0), 0.0, 0), {}, None),
        ("status/inactive_blank", "_status", (False, status_previous(False, 50.0), 0.0, 0), {}, None),
        ("status/inactive_no_previous", "_status", (False, None, 0.0, 0), {}, None),
        ("status/new_no_previous", "_status", (True, None, 50.0, 0), {}, None),
        ("status/new_was_inactive", "_status", (True, status_previous(False, 50.0), 50.0, 0), {}, None),
        ("status/confirmed", "_status", (True, status_previous(True, 50.0), 49.5, 5), {}, None),
        ("status/strengthen", "_status", (True, status_previous(True, 50.0), 52.5, 1), {}, None),
        ("status/weaken", "_status", (True, status_previous(True, 50.0), 47.5, 1), {}, None),
        ("status/watch", "_status", (True, status_previous(True, 50.0), 50.0, 1), {}, None),
        # --- canaries: rebound by signal_lifecycle, must stay -----------------
        ("is_active/canary", "_is_active", (frame,), {}, None),
        ("finalize_ranking/canary", "finalize_signal_ranking", (frame,), {}, None),
        ("strict_override/canary", "strict_filter_override_mask", (frame,), {}, None),
    ]
    return cases


def resolved_constant(name: str) -> Any:
    """Return the value the extracted code will actually use for ``name``."""
    extracted = importlib.import_module(MOVE_HOME)
    namespace = vars(extracted)
    if name in namespace:
        return namespace[name]
    raise AssertionError(f"{MOVE_HOME} supplies no binding for {name}")


def live_provenance() -> dict[str, str]:
    """Resolve every tracked function *now*, under the production assembly.

    Reading provenance from the frozen fixture instead would make the gates
    unfalsifiable: they would assert what was true when the file was written,
    not what is true today.
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
            "Golden output of signal_lifecycle_core's attribute cluster, captured "
            "AFTER the signal_lifecycle facade has been imported so the production "
            "assembly is in place. Recapture with "
            "`python tests/golden_signal_lifecycle.py` ONLY after reviewing the diff."
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
        "constant_names": list(CONSTANT_NAMES),
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
