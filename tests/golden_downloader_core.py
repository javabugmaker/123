"""Deterministic input scenarios for the ``downloader_core`` golden test.

``downloader_core`` is 877 lines with 43 functions and no test coverage at all.
Nine of those functions touch the network, so they are deliberately left alone;
this module exercises the 16 pure helpers that decide how tickers, data sources,
cached frames and corporate-action rebases are interpreted. Those are the parts
where a silent change corrupts everything downstream without raising anything.

Everything here is closed-form and offline: no RNG, no wall clock (every
time-dependent helper is handed an explicit ``now``), no filesystem, no network.

Run directly to recapture the fixture::

    python tests/golden_downloader_core.py

Recapturing is a deliberate act — the golden file is a contract, and silently
regenerating it would destroy the protection it exists to provide.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Pin the production assembly state before capturing.  Both ``downloader.py``
# and ``downloader_v51_base.py`` replace symbols on ``downloader_core`` as an
# import side effect, so without this the captured behaviour would depend on
# whether some other test happened to import them first.
import downloader  # noqa: E402,F401
import downloader_core  # noqa: E402
import downloader_v51  # noqa: E402,F401
import downloader_v51_base  # noqa: E402,F401

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "downloader_core_golden.json"

OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


def _frame(
    rows: int = 8,
    *,
    start: str = "2026-08-03",
    base_price: float = 10.0,
    drift: float = 0.01,
    volume: float = 1.0e6,
    with_amount: bool = False,
    corrupt: str | None = None,
) -> pd.DataFrame:
    """Build one deterministic OHLCV frame indexed by consecutive weekdays."""
    index = pd.bdate_range(start, periods=rows)
    steps = np.arange(rows, dtype="float64")
    close = base_price * np.power(1.0 + drift, steps)
    frame = pd.DataFrame(
        {
            "Open": close * 0.995,
            "High": close * 1.02,
            "Low": close * 0.98,
            "Close": close,
            "Volume": np.full(rows, volume),
        },
        index=index,
    )
    if with_amount:
        frame["Amount"] = frame["Close"] * frame["Volume"]
    if corrupt == "negative_close":
        frame.iloc[-1, frame.columns.get_loc("Close")] = -1.0
    elif corrupt == "high_below_close":
        frame.iloc[-1, frame.columns.get_loc("High")] = frame["Close"].iloc[-1] * 0.5
    elif corrupt == "nan_volume":
        frame.iloc[-2, frame.columns.get_loc("Volume")] = np.nan
    elif corrupt == "bad_index":
        frame.index = pd.Index([pd.NaT] * rows)
    elif corrupt == "duplicate_index":
        frame.index = pd.bdate_range(start, periods=rows).repeat(1)
        frame = pd.concat([frame, frame.iloc[[-1]]])
    elif corrupt == "zero_open":
        frame.iloc[0, frame.columns.get_loc("Open")] = 0.0
    return frame


def _now() -> datetime:
    return datetime(2026, 9, 14, 20, 0, 0)


def build_cases() -> list[tuple[str, str, tuple[Any, ...], dict[str, Any]]]:
    """Return ``(case name, function name, args, kwargs)`` in a stable order."""
    cases: list[tuple[str, str, tuple[Any, ...], dict[str, Any]]] = []

    # --- Ticker / source normalisation -----------------------------------
    # Prefixes 15/16/50/51/56/58 are ETFs; the rest are stocks or junk.  Without
    # real ETF codes every is_etf_ticker verdict is False and the golden file
    # cannot see that predicate change.
    for raw in (
        "600000",
        "000001",
        "300750",
        "430047",
        "830799",
        "920002",
        "510300",
        "159915",
        "512880",
        "150001",
        "160105",
        "560080",
        "600000.SH",
        "sz000002",
        " 600519 ",
        "AAPL",
        "12",
        "1234567",
        "",
        "60OOOO",
    ):
        cases.append((f"normalize_ticker[{raw!r}]", "normalize_ticker", (raw,), {}))
        cases.append((f"is_etf_ticker[{raw!r}]", "is_etf_ticker", (raw,), {}))
        cases.append((f"_safe_cache_stem[{raw!r}]", "_safe_cache_stem", (raw,), {}))

    for raw in (
        None,
        "tickflow",
        "TickFlow",
        "TICKFLOW-FREE",
        "free",
        "akshare",
        "yfinance",
        "tushare",
        "bogus",
        "",
    ):
        cases.append((f"normalize_data_source[{raw!r}]", "normalize_data_source", (raw,), {}))
    for raw in (None, "tickflow", "TICKFLOW-FREE", "bogus"):
        cases.append((f"get_data_source_label[{raw!r}]", "get_data_source_label", (raw,), {}))

    # EXCLUDED_SECURITY_KEYWORDS is 债/货币/同业存单/短融/中票/REIT/浙商沪.  Without a name
    # that actually contains one of them the keyword branch is never taken, so deleting
    # that branch would leave the golden file completely unchanged.
    for raw in (
        "招商银行",
        "ST 中安",
        "*ST 海航",
        "某某退",
        "中国平安",
        "N 新股",
        "PT 金田",
        "",
        "某某债",
        "华夏货币",
        "某某 REIT",
        "某某同业存单",
        "某某短融",
        "某某中票",
        "浙商沪港深",
    ):
        cases.append(
            (f"_is_excluded_security_name[{raw!r}]", "_is_excluded_security_name", (raw,), {})
        )

    # --- Frame validation --------------------------------------------------
    cases.append(("_validate_ohlcv[None]", "_validate_ohlcv", (None,), {}))
    cases.append(("_validate_ohlcv[empty]", "_validate_ohlcv", (pd.DataFrame(),), {}))
    cases.append(("_validate_ohlcv[missing_columns]", "_validate_ohlcv", (pd.DataFrame({"Close": [1.0]}),), {}))
    for corrupt in (
        None,
        "negative_close",
        "high_below_close",
        "nan_volume",
        "bad_index",
        "duplicate_index",
        "zero_open",
    ):
        cases.append(
            (
                f"_validate_ohlcv[{corrupt or 'clean'}]",
                "_validate_ohlcv",
                (_frame(8, corrupt=corrupt),),
                {},
            )
        )
    cases.append(
        ("_validate_ohlcv[clean_with_amount]", "_validate_ohlcv", (_frame(8, with_amount=True),), {})
    )

    # --- Cache merge / rebase ---------------------------------------------
    cached = _frame(10, start="2026-08-03", base_price=10.0, drift=0.005)
    identical = _frame(10, start="2026-08-03", base_price=10.0, drift=0.005)
    rebased = _frame(10, start="2026-08-03", base_price=10.0, drift=0.005)
    rebased["Close"] = rebased["Close"] * 0.5  # corporate action: halves history
    appended = _frame(14, start="2026-08-03", base_price=10.0, drift=0.005)
    disjoint = _frame(6, start="2026-09-01", base_price=11.0, drift=0.004)

    cases.append(("_merge_cached[identical]", "_merge_cached", (cached, identical), {}))
    cases.append(("_merge_cached[rebased]", "_merge_cached", (cached, rebased), {}))
    cases.append(("_merge_cached[appended]", "_merge_cached", (cached, appended), {}))
    cases.append(("_merge_cached[disjoint]", "_merge_cached", (cached, disjoint), {}))
    cases.append(("_merge_cached[empty_recent]", "_merge_cached", (cached, pd.DataFrame()), {}))

    cases.append(("_requires_full_rebase[identical]", "_requires_full_rebase", (cached, identical), {}))
    cases.append(("_requires_full_rebase[rebased]", "_requires_full_rebase", (cached, rebased), {}))
    cases.append(("_requires_full_rebase[disjoint]", "_requires_full_rebase", (cached, disjoint), {}))
    cases.append(
        ("_requires_full_rebase[single_overlap]", "_requires_full_rebase", (cached, cached.iloc[[-1]]), {})
    )
    # Overlap of exactly two bars: the only input that separates
    # `len(common) < 2` from `len(common) < 3`.  Without it, changing the
    # minimum-overlap rule would slip through the golden file untouched.
    two_overlap = pd.concat(
        [
            _frame(2, start="2026-08-03", base_price=10.0, drift=0.005),
            _frame(3, start="2026-09-01", base_price=10.5, drift=0.004),
        ]
    )
    cases.append(("_requires_full_rebase[two_overlap]", "_requires_full_rebase", (cached, two_overlap), {}))
    cases.append(("_merge_cached[two_overlap]", "_merge_cached", (cached, two_overlap), {}))

    # --- Cache freshness ---------------------------------------------------
    cases.append(("_cache_has_completed_daily_bar[None]", "_cache_has_completed_daily_bar", (None, _now()), {}))
    cases.append(("_cache_has_completed_daily_bar[empty]", "_cache_has_completed_daily_bar", (pd.DataFrame(), _now()), {}))
    for label, frame in (
        ("today", _frame(3, start="2026-09-14")),
        ("stale", _frame(3, start="2026-01-05")),
        ("future", _frame(3, start="2027-01-04")),
    ):
        cases.append(
            (f"_cache_has_completed_daily_bar[{label}]", "_cache_has_completed_daily_bar", (frame, _now()), {})
        )

    # --- Trading calendar ---------------------------------------------------
    for label, moment in (
        ("monday_2000", datetime(2026, 9, 14, 20, 0)),
        ("saturday_1000", datetime(2026, 9, 12, 10, 0)),
        ("weekday_0930", datetime(2026, 9, 15, 9, 30)),
        ("weekday_1500", datetime(2026, 9, 15, 15, 0)),
        ("weekday_1200", datetime(2026, 9, 15, 12, 0)),
    ):
        cases.append((f"_latest_completed_trading_day[{label}]", "_latest_completed_trading_day", (moment,), {}))
        cases.append((f"_is_a_share_market_closed[{label}]", "_is_a_share_market_closed", (moment,), {}))

    # --- Value coercion ------------------------------------------------------
    for raw in (None, "1,234", "12.5", "abc", "", 0, -3, 1e21, "1e5", [], {}, "3.0万股"):
        cases.append((f"_number_or_none[{raw!r}]", "_number_or_none", (raw,), {}))
    for raw in (None, {}, {"a": 1}, [1, 2], "text", 42, ("k", "v")):
        cases.append((f"_as_mapping[{raw!r}]", "_as_mapping", (raw,), {}))
    # ``_normalize_cn_share_count`` is supplied by ``downloader_v51_base`` and gates
    # on a sanity band of [1e6, 1e13] shares.  Inputs must therefore include values
    # that parse as *positive* numbers (``_number_or_none`` drops everything <= 0 and
    # every non-numeric string before the band is ever consulted) yet normalise
    # outside the band, otherwise removing the band check is unobservable.
    #   50  -> 5.0e5   below band   -> None
    #   99  -> 9.9e5   below band   -> None
    #   100 -> 1.0e6   exactly the lower bound -> 1_000_000.0
    #   1e15           above band   -> None
    for raw in (
        None,
        1.0e8,
        "1.2亿",
        "3.5万",
        "abc",
        0,
        -1.0,
        "8,000,000",
        50,
        99,
        100,
        1e15,
    ):
        cases.append((f"_normalize_cn_share_count[{raw!r}]", "_normalize_cn_share_count", (raw,), {}))

    # --- Batching -------------------------------------------------------------
    # ``_request_chunks`` derives its size from TICKFLOW_BATCH_SIZE * TICKFLOW_MAX_WORKERS
    # (100 * 5 = 500 by default).  Inputs below that size all collapse to a single chunk,
    # so at least one case must straddle the boundary or the size is unobservable.
    symbols = [f"{600000 + index}.SH" for index in range(7)]
    cases.append(("_request_chunks[empty]", "_request_chunks", ([],), {}))
    cases.append(("_request_chunks[7_symbols]", "_request_chunks", (symbols,), {}))
    cases.append(("_request_chunks[250_symbols]", "_request_chunks", ([f"{i:06d}.SZ" for i in range(250)],), {}))
    cases.append(("_request_chunks[501_symbols]", "_request_chunks", ([f"{i:06d}.SZ" for i in range(501)],), {}))

    return cases


def plain(value: Any) -> Any:
    """Convert a result into a JSON-representable structure."""
    if isinstance(value, pd.DataFrame):
        return {
            "__frame__": {
                "index": [plain(i) for i in value.index],
                "columns": [str(c) for c in value.columns],
                "rows": [[plain(v) for v in row] for row in value.itertuples(index=False)],
                "attrs": {str(k): plain(v) for k, v in sorted(value.attrs.items())},
            }
        }
    if isinstance(value, pd.Series):
        return {"__series__": [plain(v) for v in value.tolist()]}
    if isinstance(value, (pd.Timestamp, datetime)):
        return {"__datetime__": value.isoformat()}
    if isinstance(value, date):
        return {"__date__": value.isoformat()}
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
    return {"__repr__": repr(value)}


def _resolve(name: str) -> Callable[..., Any]:
    function = getattr(downloader_core, name)
    if not callable(function):
        raise TypeError(f"{name} is not callable")
    return function


def capture() -> dict[str, Any]:
    """Run every case and return the golden payload."""
    cases: dict[str, Any] = {}
    provenance: dict[str, str] = {}
    for label, function_name, args, kwargs in build_cases():
        function = _resolve(function_name)
        # Record where each callable actually lives.  Several overlays replace
        # symbols on downloader_core at import time; if one of them starts
        # replacing a function under test, this changes and the drift is visible
        # instead of silently redefining what the golden file means.
        provenance[function_name] = f"{function.__module__}.{function.__qualname__}"
        try:
            cases[label] = {"value": plain(function(*args, **kwargs))}
        except Exception as exc:  # noqa: BLE001 - freezing whatever comes out
            cases[label] = {"raised": type(exc).__name__}

    return {
        "schema": 1,
        "note": (
            "Golden output of downloader_core's pure helpers, captured after the "
            "downloader facades have been imported. Recapture with `python "
            "tests/golden_downloader_core.py` ONLY after reviewing the diff."
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
