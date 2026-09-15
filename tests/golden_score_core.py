"""Deterministic corpus + golden capture for ``score_core``'s scoring helpers.

``score_core`` is 1183 lines / 29 functions and decides the actual numbers that
end up in the scan output.  It had one test (``test_score_kernel.py``) covering
two properties of the *kernel*, not the feature functions themselves.

Two things make this file subtle, both learned the hard way on the other
stage-2 cores:

* **Assembly state.**  ``score.py`` is a v113 facade that, at *import* time,
  runs ``_threshold_migration_v95.install(config)``, replaces
  ``score_core._style_adjustment`` / ``score_volatility`` / ``score_ticker``,
  adds ``trigger_event_score``, then installs ``score_runtime`` on top, and
  finally does ``sys.modules[__name__] = _core``.  ``scanner.py:62`` does
  ``from score import ...``, so **production runs the patched assembly**.
  Capturing a bare ``score_core`` would freeze semantics that never execute.
* **Column coverage.**  ``_series`` returns an all-NaN series for a missing
  column rather than raising, so a corpus with the wrong columns silently
  exercises only the fail-closed branches.  The frames below carry every
  indicator column the module reads.

Recapture with::

    python tests/golden_score_core.py
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Pin the production assembly state before capturing.  See the module docstring.
#
# ``import score`` alone is NOT enough: it reaches the v79/v95/endpoint overlays
# and the v113 facade, but not ``score_cache_guard_v80``, which is pulled in via
# scanner -> analytics -> institution_scanner.analytics_runtime ->
# backtest_acceleration_v77 and replaces ``entry_point`` at import time.  Verified
# by `import scanner` in a clean interpreter: entry_point resolves to
# score_cache_guard_v80.entry_point, not score_acceleration_v79.entry_point.
# Importing the real entry point means any future overlay shows up here
# automatically instead of drifting silently.
import scanner  # noqa: E402,F401
import score  # noqa: E402,F401
import score_core  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "score_core_golden.json"

START = "2025-01-02"
ROWS = 300  # > 252 so the trend/structure gates are open

# Every indicator column any function in score_core reads.  A corpus missing one
# of these silently collapses to the fail-closed branch and stops being able to
# see that function's logic change.
INDICATOR_COLUMNS = (
    "MA20",
    "MA50",
    "MA200",
    "ATR14",
    "ATR50",
    "RSI14",
    "ROC",
    "BB_Width",
    "HV20",
    "HV60",
    "VolMA20",
    "VolMA120",
    "VolZScore",
    "AD",
    "AD_Slope",
    "OBV",
    "CMF",
    "MFI",
    "RegSlope",
    "RegR2",
    "DistToLow52W",
    "Above_HVN",
    "DistToHVN_Pct",
)


def _frame(
    rows: int = ROWS,
    *,
    drift: float = 0.0012,
    base_price: float = 20.0,
    volume_level: float = 1.0e6,
    volume_trend: float = 0.0,
    amplitude: float = 0.02,
    volume_surge: float = 1.0,
    start: str = START,
    drop: tuple[str, ...] = (),
    corrupt: str | None = None,
) -> pd.DataFrame:
    """Build one deterministic indicator frame.

    No RNG anywhere: every column is a closed-form function of the row index so
    the corpus is byte-identical on every machine and every pandas version.
    """
    index = pd.bdate_range(start, periods=rows)
    steps = np.arange(rows, dtype="float64")
    wave = np.sin(steps / 11.0) * 0.006

    close = base_price * np.power(1.0 + drift, steps) * (1.0 + wave)
    high = close * (1.0 + amplitude)
    low = close * (1.0 - amplitude)
    open_ = close * 0.995
    volume = volume_level * (1.0 + volume_trend * steps / max(rows, 1)) * (1.0 + np.cos(steps / 7.0) * 0.08)
    if volume_surge != 1.0:
        # Amplify only the trailing 20 sessions so VolMA20 / VolMA120 clears the
        # 1.25 "资金吸筹" threshold while the long average barely moves.
        tail = steps >= (rows - 20)
        volume = np.where(tail, volume * volume_surge, volume)

    frame = pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=index,
    )

    close_s = frame["Close"]
    vol_s = frame["Volume"]
    frame["MA20"] = close_s.rolling(20, min_periods=1).mean()
    frame["MA50"] = close_s.rolling(50, min_periods=1).mean()
    frame["MA200"] = close_s.rolling(200, min_periods=1).mean()
    true_range = frame["High"] - frame["Low"]
    frame["ATR14"] = true_range.rolling(14, min_periods=1).mean()
    frame["ATR50"] = true_range.rolling(50, min_periods=1).mean()
    frame["RSI14"] = 50.0 + 18.0 * np.sin(steps / 17.0)
    frame["ROC"] = close_s.pct_change(20, fill_method=None) * 100.0
    frame["BB_Width"] = 0.04 + 0.02 * np.sin(steps / 23.0)
    frame["HV20"] = close_s.pct_change(fill_method=None).rolling(20, min_periods=2).std() * np.sqrt(252.0)
    frame["HV60"] = close_s.pct_change(fill_method=None).rolling(60, min_periods=2).std() * np.sqrt(252.0)
    frame["VolMA20"] = vol_s.rolling(20, min_periods=1).mean()
    frame["VolMA120"] = vol_s.rolling(120, min_periods=1).mean()
    spread = vol_s.rolling(20, min_periods=2).std()
    frame["VolZScore"] = ((vol_s - frame["VolMA20"]) / spread.replace(0.0, np.nan)).fillna(0.0)
    # Money flow: OBV accumulates signed volume, AD/AD_Slope track it, CMF/MFI
    # stay in a plausible band.  Sign follows the drift so the up/down trend
    # scenarios actually disagree on accumulation.
    direction = np.sign(drift) if drift else 0.0
    frame["OBV"] = np.cumsum(np.where(close_s.diff().fillna(0.0) >= 0, vol_s, -vol_s))
    frame["AD"] = np.cumsum(close_s.diff().fillna(0.0) * vol_s) / 1.0e9
    frame["AD_Slope"] = frame["AD"].diff(5).fillna(0.0) * (1.0 if direction >= 0 else -1.0)
    frame["CMF"] = 0.12 * direction + 0.03 * np.sin(steps / 13.0)
    frame["MFI"] = 55.0 + 12.0 * direction + 4.0 * np.sin(steps / 19.0)
    frame["RegSlope"] = float(drift) * 100.0
    frame["RegR2"] = 0.55
    frame["DistToLow52W"] = (close_s / close_s.rolling(252, min_periods=1).min() - 1.0) * 100.0
    frame["Above_HVN"] = (close_s > frame["MA50"]).astype(float)
    frame["DistToHVN_Pct"] = (close_s / frame["MA50"] - 1.0) * 100.0

    if corrupt == "nan_close_tail":
        frame.iloc[-5:, frame.columns.get_loc("Close")] = np.nan
    elif corrupt == "zero_volume":
        frame["Volume"] = 0.0
        frame["VolMA20"] = 0.0
        frame["VolMA120"] = 0.0
    elif corrupt == "zero_price":
        frame["Close"] = 0.0
        frame["MA20"] = 0.0
        frame["MA50"] = 0.0
        frame["MA200"] = 0.0
    elif corrupt == "flat_prices":
        frame["Close"] = base_price
        frame["High"] = base_price
        frame["Low"] = base_price
        frame["MA20"] = base_price
        frame["MA50"] = base_price
        frame["MA200"] = base_price
    elif corrupt == "all_nan_indicators":
        for column in INDICATOR_COLUMNS:
            frame[column] = np.nan
    elif corrupt == "nonfinite_tail":
        frame.iloc[-1, frame.columns.get_loc("Close")] = np.inf
        frame.iloc[-2, frame.columns.get_loc("ATR14")] = np.nan

    if drop:
        frame = frame.drop(columns=[c for c in drop if c in frame.columns])
    return frame


def _scenarios() -> list[tuple[str, pd.DataFrame]]:
    """Named frames covering the regimes each helper branches on."""
    return [
        ("uptrend", _frame(drift=0.0012, volume_trend=0.35)),
        ("downtrend", _frame(drift=-0.0010, volume_trend=-0.30)),
        ("chop", _frame(drift=0.0, volume_trend=0.0)),
        ("etf_like", _frame(drift=0.0006, volume_trend=0.10)),
        # classify_style returns seven distinct labels and the corpus must reach
        # every one of them, otherwise deleting a branch is unobservable.
        #   ROC(20) >= 12%                      -> 趋势成长
        #   VolMA20 / VolMA120 >= 1.25          -> 资金吸筹
        #   ATR14 / Close >= 4.5%               -> 高波动成长
        #   ATR14 / Close <= 2.5%               -> 低波动防守
        ("high_momentum", _frame(drift=0.008, volume_trend=0.20)),
        ("volume_surge", _frame(drift=0.0012, volume_surge=3.0)),
        ("high_volatility", _frame(drift=0.0012, amplitude=0.030)),
        ("low_volatility", _frame(drift=0.0012, amplitude=0.008)),
        ("short_history", _frame(rows=30, drift=0.002)),
        ("ohlcv_only", _frame(drop=INDICATOR_COLUMNS)),
        ("no_ma200", _frame(drop=("MA200",))),
        ("no_money_flow", _frame(drop=("OBV", "AD", "AD_Slope", "CMF", "MFI"))),
        ("no_volatility", _frame(drop=("ATR14", "ATR50", "BB_Width", "HV20", "HV60"))),
        ("corrupt_nan_close_tail", _frame(corrupt="nan_close_tail")),
        ("corrupt_zero_volume", _frame(corrupt="zero_volume")),
        ("corrupt_zero_price", _frame(corrupt="zero_price")),
        ("corrupt_flat_prices", _frame(corrupt="flat_prices")),
        ("corrupt_all_nan_indicators", _frame(corrupt="all_nan_indicators")),
        ("corrupt_nonfinite_tail", _frame(corrupt="nonfinite_tail")),
        ("empty", pd.DataFrame()),
    ]


def build_cases() -> list[tuple[str, str, tuple[Any, ...], dict[str, Any]]]:
    """Return ``(case name, function name, args, kwargs)`` in a stable order."""
    cases: list[tuple[str, str, tuple[Any, ...], dict[str, Any]]] = []

    # --- Scalar helpers ----------------------------------------------------
    for raw in (0.0, 0.5, 1.0, -1.0, 2.5, np.nan, np.inf, -np.inf, "0.3", None, [], {}):
        cases.append((f"_is_finite[{raw!r}]", "_is_finite", (raw,), {}))
    for value, low, high in (
        (0.5, 0.0, 1.0),
        (-1.0, 0.0, 1.0),
        (2.0, 0.0, 1.0),
        (np.nan, 0.0, 1.0),
        (np.inf, 0.0, 1.0),
        (5.0, 0.0, 20.0),
        (-3.0, -5.0, 5.0),
    ):
        cases.append((f"_clamp[{value!r},{low!r},{high!r}]", "_clamp", (value, low, high), {}))
    for value, min_val, max_val, invert in (
        (5.0, 0.0, 10.0, False),
        (5.0, 0.0, 10.0, True),
        (-1.0, 0.0, 10.0, False),
        (11.0, 0.0, 10.0, False),
        (3.0, 3.0, 3.0, False),
        (3.0, 3.0, 3.0, True),
    ):
        cases.append(
            (
                f"_normalize_to_range[{value!r},{min_val!r},{max_val!r},{invert!r}]",
                "_normalize_to_range",
                (value, min_val, max_val),
                {"invert": invert},
            )
        )
    for is_etf in (False, True):
        cases.append((f"tradable_price_decimals[{is_etf}]", "tradable_price_decimals", (is_etf,), {}))

    # --- Series helpers ----------------------------------------------------
    frame = _frame(rows=120)
    for column in ("Close", "Volume", "MA200", "Missing"):
        cases.append((f"_series[{column}]", "_series", (frame, column), {}))
        cases.append((f"_latest[{column}]", "_latest", (frame, column), {}))
    # ``score_acceleration_v79._latest`` falls back to the last *finite* value
    # when the tail is NaN, whereas score_core's own ``_latest`` would just return
    # nan.  Without a tail-NaN input that fallback never runs, so deleting it
    # would leave the golden file untouched.
    tail_nan = _frame(rows=120, corrupt="nan_close_tail")
    for column in ("Close", "Volume"):
        cases.append((f"_series[{column},tail_nan]", "_series", (tail_nan, column), {}))
        cases.append((f"_latest[{column},tail_nan]", "_latest", (tail_nan, column), {}))
    for window in (1, 10, 20, 500):
        cases.append((f"_rolling_mean[Close,{window}]", "_rolling_mean", (frame, "Close", window), {}))
    for periods in (0, 5, 20, 60, 400):
        cases.append((f"_safe_return[Close,{periods}]", "_safe_return", (frame["Close"], periods), {}))

    # --- Per-scenario scoring ----------------------------------------------
    for label, scenario in _scenarios():
        for function_name in (
            "score_trend",
            "score_volume",
            "score_accumulation",
            "score_volatility",
            "score_structure",
            "breakout_score",
            "value_trap_risk_score",
            "classify_style",
            "_score_dimensions_available",
        ):
            cases.append((f"{function_name}[{label}]", function_name, (scenario,), {}))
        for is_etf in (False, True):
            cases.append(
                (f"value_trap_risk[{label},is_etf={is_etf}]", "value_trap_risk", (scenario,), {"is_etf": is_etf})
            )
            cases.append(
                (f"classify_style[{label},is_etf={is_etf}]", "classify_style", (scenario,), {"is_etf": is_etf})
            )
        cases.append((f"cyclical_turn_factor[{label}]", "cyclical_turn_factor", (scenario,), {}))
        cases.append(
            (
                f"cyclical_turn_factor[{label},industry]",
                "cyclical_turn_factor",
                (scenario, 0.04, 1.0e8, 9.0e7, 8.0e7),
                {},
            )
        )
        cases.append((f"smart_money_stage[{label}]", "smart_money_stage", (scenario,), {}))
        cases.append((f"smart_money_stage[{label},fixed]", "smart_money_stage", (scenario, 80.0, 10.0), {}))
        cases.append((f"execution_quality_score[{label}]", "execution_quality_score", (scenario, None), {}))
        cases.append(
            (
                f"execution_quality_score[{label},entry]",
                "execution_quality_score",
                (scenario, {"stop": 18.0, "price_breakout": True, "projected_target": 26.0}),
                {},
            )
        )
        cases.append(
            (
                f"entry_point[{label}]",
                "entry_point",
                (scenario,),
                {"price_decimals": 2},
            )
        )
        cases.append(
            (
                f"entry_point[{label},etf]",
                "entry_point",
                (scenario,),
                {"price_decimals": 3},
            )
        )
        cases.append((f"score_ticker[{label}]", "score_ticker", (scenario,), {}))
        cases.append((f"score_ticker[{label},is_etf]", "score_ticker", (scenario,), {"is_etf": True}))

    # --- Availability predicate --------------------------------------------
    probe = _frame(rows=120)
    for columns in (
        ("Close",),
        ("Close", "MA200"),
        ("Close", "Missing"),
        ("VolZScore",),
        ("ATR14", "ATR50"),
    ):
        cases.append(
            (
                f"_has_finite_values[{'-'.join(columns)}]",
                "_has_finite_values",
                (probe, columns),
                {},
            )
        )

    # --- Weight plumbing ----------------------------------------------------
    # Not a pure function (it reads output/ScoreCalibration.json and memoises),
    # but its value silently scales every final_score, so freeze it too: a drift
    # here should fail loudly instead of showing up as 200 unrelated diffs.
    cases.append(("model_weight_signature[]", "model_weight_signature", (), {}))

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
        if np.isnan(number):
            return {"__nan__": True}
        if np.isinf(number):
            return {"__inf__": 1 if number > 0 else -1}
        return number
    if value is None or isinstance(value, str):
        return value
    if hasattr(value, "__dataclass_fields__"):
        payload: dict[str, Any] = {"__dataclass__": type(value).__name__}
        try:
            payload["fields"] = plain(asdict(value))
        except TypeError:
            payload["fields"] = {f: plain(getattr(value, f)) for f in value.__dataclass_fields__}
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            try:
                payload["to_dict"] = plain(to_dict())
            except Exception:  # noqa: BLE001 - freeze whatever comes out
                payload["to_dict"] = {"__repr__": "to_dict raised"}
        return payload
    return {"__repr__": repr(value)}


def _resolve(name: str) -> Callable[..., Any]:
    function = getattr(score_core, name)
    if not callable(function):
        raise TypeError(f"{name} is not callable")
    return function


def capture() -> dict[str, Any]:
    """Run every case and return the golden payload."""
    cases: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    for label, function_name, args, kwargs in build_cases():
        function = _resolve(function_name)
        # Record where each callable actually lives.  score.py rebinds several of
        # these at import time; a new rebinding must be visible instead of
        # silently redefining what the golden file means.
        provenance[function_name] = f"{function.__module__}.{function.__qualname__}"
        try:
            cases[label] = {"value": plain(function(*args, **kwargs))}
        except Exception as exc:  # noqa: BLE001 - freezing whatever comes out
            cases[label] = {"raised": type(exc).__name__}

    return {
        "schema": 1,
        "note": (
            "Golden output of score_core's scoring helpers, captured AFTER the "
            "score facade has been imported so the production assembly is in "
            "place. Recapture with `python tests/golden_score_core.py` ONLY "
            "after reviewing the diff."
        ),
        "captured_with": {
            "python": sys.version.split()[0],
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
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
