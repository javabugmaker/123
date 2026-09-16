"""Out-of-process parity probe: v83 vectorised lifecycle vs the stable original.

``lifecycle_acceleration_v83`` replaces ``signal_lifecycle_core.enrich_signal_lifecycle``
outright (it stores the original as ``_v83_legacy_enrich_signal_lifecycle`` but never
calls it).  The module docstring claims "Ranking, persistence columns and signal
semantics stay identical to the stable engine."  Nothing tested that claim.

This probe runs both implementations on the *same* frame and diffs.  The frame is a
sample of a real ``AllResults.csv``: a parity check only needs identical input, so
using a historical run is fine even though its schema predates current code.

Prints one JSON object on stdout.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
from typing import Any

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORT: dict[str, Any] = {}
COMPARE_COLUMNS = (
    "Ticker",
    "SignalDays",
    "SignalStartDate",
    "SignalStatus",
    "SignalTrend",
    "SignalStrengthHistory",
    "SignalRecencyFactor",
    "OpportunityScore",
)


def _synthetic_frame(rows: int = 12) -> pd.DataFrame:
    """Fallback for environments with no cached run -- notably CI.

    ``output/runs/*/AllResults.csv`` is not in version control, so on a fresh
    checkout the probe had nothing to sample and every parity gate failed with
    ``KeyError``.  A parity check only needs *identical* input on both sides,
    not a real frame, so a synthetic one is sufficient -- and it keeps the gate
    runnable where no historical export exists.
    """
    return pd.DataFrame(
        {
            "Ticker": [f"{600000 + index}.SH" for index in range(rows)],
            "DataAsOf": ["2026-09-15"] * rows,
            "Score": [50.0 + index for index in range(rows)],
            "OpportunityScore": [60.0 + index for index in range(rows)],
            "InstitutionalScore": [55.0 + index for index in range(rows)],
            "InstitutionalTier": ["B"] * rows,
            "LifecycleStage": ["BASE"] * rows,
            "SignalStatus": ["WATCH"] * rows,
            "SignalDays": [3 + index for index in range(rows)],
            "SignalStartDate": ["2026-09-01"] * rows,
            "SignalRecencyDays": [1] * rows,
            "SignalRecencyFactor": [1.0] * rows,
            "BreakoutQualityFactor": [1.0] * rows,
            "LongTermScore": [50.0] * rows,
            "MediumTermScore": [50.0] * rows,
            "ShortTermScore": [50.0] * rows,
            "ScoreConfidencePct": [80.0] * rows,
            "SignalStrengthHistory": ["[]"] * rows,
            "SignalTrend": ["UP"] * rows,
            "ActionSuggestion": ["WATCH"] * rows,
            "RiskNote": [""] * rows,
            "LifecycleAccelerationVersion": ["v83"] * rows,
        }
    )


def _sample_frame(limit: int = 120) -> tuple[pd.DataFrame, str]:
    candidates = sorted(
        (ROOT / "output" / "runs").glob("*/AllResults.csv"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return _synthetic_frame(max(5, min(limit, 12))), "synthetic"
    frame = pd.read_csv(candidates[0], encoding="utf-8-sig", nrows=limit, low_memory=False)
    frame["Ticker"] = frame["Ticker"].astype(str).str.strip().str.upper()
    frame["DataAsOf"] = "2026-09-15"
    return frame, f"real:{candidates[0].parent.name}"


def _seed_history(path: pathlib.Path, trade_date: str, tickers: list[str]) -> None:
    import signal_lifecycle_core as core

    rows = []
    for offset, ticker in enumerate(tickers):
        row = {column: None for column in core.HISTORY_COLUMNS}
        row.update(
            {
                "TradeDate": trade_date,
                "Ticker": ticker,
                "Score": 70.0,
                "OpportunityScore": 60.0 + offset,
                "SignalActive": True,
                "SignalDays": 3 + offset,
                "SignalStartDate": "2026-09-01",
                "SignalStatus": "WATCH",
                "Return20D": 1.5,
                "BenchmarkReturn20D": 0.35,
                "MaxDrawdown20D": -2.0,
                "Return60D": 4.5,
                "BenchmarkReturn60D": 0.8,
                "MaxDrawdown60D": -5.0,
            }
        )
        rows.append(row)
    pd.DataFrame(rows, columns=core.HISTORY_COLUMNS).to_csv(
        path, index=False, encoding="utf-8-sig"
    )


def _run_one(
    impl,
    frame: pd.DataFrame,
    tmp: pathlib.Path,
    seed_date: str | None,
    seeded_tickers: list[str],
) -> dict[str, Any]:
    import signal_lifecycle_core as core

    core.HISTORY_FILE = tmp / "SignalHistory.csv"
    core.TRACKING_FILE = tmp / "SignalTracking.csv"
    if seed_date is not None:
        _seed_history(core.HISTORY_FILE, seed_date, frame["Ticker"].head(5).tolist())
    try:
        result = impl(frame.copy())
    except Exception as error:  # noqa: BLE001 - we are classifying outcomes
        return {"error": f"{type(error).__name__}: {error}"}
    history = (
        pd.read_csv(core.HISTORY_FILE, encoding="utf-8-sig")
        if core.HISTORY_FILE.exists()
        else pd.DataFrame()
    )
    payload: dict[str, Any] = {
        "rows": len(result),
        "columns_present": [
            column for column in COMPARE_COLUMNS if column in result.columns
        ],
    }
    for column in COMPARE_COLUMNS:
        if column in result.columns:
            payload[column] = [str(value) for value in result[column].tolist()]
    payload["history_columns"] = list(history.columns)
    payload["history_rows"] = len(history)
    if not history.empty and seed_date is not None and seeded_tickers:
        # Keyed by ticker: the seeded rows are not necessarily the first rows
        # after the TradeDate/Ticker sort, so a positional head() would miss
        # them and report a false "identical".
        for column in ("BenchmarkReturn20D", "BenchmarkReturn60D", "Return20D"):
            if column not in history.columns:
                payload[f"seeded_{column}"] = "COLUMN_ABSENT"
                continue
            # Same ticker appears once per trade date; without narrowing to the
            # frame's date the lookup returns a Series and the isna() check
            # raises on an ambiguous truth value.
            current = history[history["TradeDate"].astype(str) == str(seed_date)]
            lookup = current.drop_duplicates("Ticker", keep="last").set_index("Ticker")[column]
            payload[f"seeded_{column}"] = {
                ticker: (
                    None
                    if ticker not in lookup.index or pd.isna(lookup[ticker])
                    else float(lookup[ticker])
                )
                for ticker in seeded_tickers
            }
    return payload


def _diff(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    """Names whose observed value differs between the two implementations."""
    keys = set(left) | set(right)
    return sorted(
        key
        for key in keys
        if key not in ("rows", "history_rows")
        and json.dumps(left.get(key), sort_keys=True)
        != json.dumps(right.get(key), sort_keys=True)
    )


def main() -> int:
    import lifecycle_acceleration_v83 as v83
    import signal_lifecycle_core as core

    original = core.enrich_signal_lifecycle
    vectorised = v83._build_enricher(core)
    REPORT["owners"] = {
        "original": pathlib.Path(original.__code__.co_filename).name,
        "vectorised": pathlib.Path(vectorised.__code__.co_filename).name,
    }
    REPORT["history_columns_expected"] = list(core.HISTORY_COLUMNS)

    frame, frame_source = _sample_frame()
    REPORT["frame_source"] = frame_source

    scenarios = (
        ("fresh_no_history", None),
        ("prior_date_history", "2026-09-10"),
        ("same_date_rerun", "2026-09-15"),
    )
    seeds = frame["Ticker"].head(5).tolist()
    for label, seed in scenarios:
        with tempfile.TemporaryDirectory() as raw_a, tempfile.TemporaryDirectory() as raw_b:
            stable = _run_one(original, frame, pathlib.Path(raw_a), seed, seeds)
            accelerated = _run_one(vectorised, frame, pathlib.Path(raw_b), seed, seeds)
        REPORT[label] = {
            "stable": stable,
            "v83": accelerated,
            "differs_on": _diff(stable, accelerated),
        }
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001
        REPORT["probe_error"] = f"{type(error).__name__}: {error}"
    json.dump(REPORT, sys.stdout, ensure_ascii=False, indent=2)
    sys.exit(0)
