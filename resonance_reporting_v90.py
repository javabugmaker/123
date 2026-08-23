"""Materialize vectorized five-factor resonance diagnostics into published outputs.

The historical engine deliberately keeps resonance outside the production score.
This module joins held-out resonance diagnostics from ``BacktestSummary.json``
onto canonical results after ranking, preserving ordering and eligibility.
v91 removes row-wise metric parsing from the publication hot path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

RESONANCE_OUTPUT_VERSION = "2026-08-23-v91-resonance-output-vectorized-v1"

_SUMMARY_TO_OUTPUT = {
    "resonance_mean_count": "BacktestResonanceMeanCount",
    "resonance_strong_bull_share": "BacktestResonanceStrongBullShare",
    "resonance_rising_share": "BacktestResonanceRisingShare",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _signal_text(value: object) -> str:
    text = str(value or "UNKNOWN").strip().upper()
    return text or "UNKNOWN"


def _signal_series(series: pd.Series) -> pd.Series:
    return (
        series.fillna("UNKNOWN")
        .astype(str)
        .str.strip()
        .str.upper()
        .replace("", "UNKNOWN")
    )


def _metric_frame(
    summary: dict[str, Any],
    results: pd.DataFrame,
) -> pd.DataFrame:
    raw_rows = summary.get("by_ticker", [])
    if not isinstance(raw_rows, list) or not raw_rows:
        return pd.DataFrame()
    if "Ticker" not in results or "EntrySignal" not in results:
        return pd.DataFrame()

    dictionaries = [row for row in raw_rows if isinstance(row, dict)]
    if not dictionaries:
        return pd.DataFrame()
    metrics = pd.DataFrame.from_records(dictionaries)
    if "ticker" not in metrics.columns:
        return pd.DataFrame()

    defaults: dict[str, object] = {
        "entry_signal": "UNKNOWN",
        "backtest_stage": "",
    }
    for column, value in defaults.items():
        if column not in metrics.columns:
            metrics[column] = value
    for source in _SUMMARY_TO_OUTPUT:
        if source not in metrics.columns:
            metrics[source] = np.nan

    metrics["Ticker"] = (
        metrics["ticker"].fillna("").astype(str).str.strip().str.upper()
    )
    metrics["EntrySignal"] = _signal_series(metrics["entry_signal"])
    metrics["_stage"] = (
        metrics["backtest_stage"].fillna("").astype(str).str.strip().str.upper()
    )

    source_columns = list(_SUMMARY_TO_OUTPUT)
    numeric = metrics[source_columns].apply(pd.to_numeric, errors="coerce")
    numeric = numeric.replace([np.inf, -np.inf], np.nan).rename(
        columns=_SUMMARY_TO_OUTPUT
    )
    metrics = pd.concat(
        [metrics[["Ticker", "EntrySignal", "_stage"]], numeric],
        axis=1,
    )
    metrics = metrics.loc[
        metrics["Ticker"].ne("")
        & metrics[list(_SUMMARY_TO_OUTPUT.values())].notna().any(axis=1)
    ].copy()
    if metrics.empty:
        return pd.DataFrame()

    current = results[["Ticker", "EntrySignal"]].copy()
    current["Ticker"] = (
        current["Ticker"].fillna("").astype(str).str.strip().str.upper()
    )
    current["EntrySignal"] = _signal_series(current["EntrySignal"])
    current_signal = (
        current.drop_duplicates("Ticker", keep="first")
        .set_index("Ticker")["EntrySignal"]
    )
    unknown = metrics["EntrySignal"].eq("UNKNOWN")
    if unknown.any():
        replacement = metrics.loc[unknown, "Ticker"].map(current_signal)
        metrics.loc[unknown, "EntrySignal"] = replacement.fillna("UNKNOWN")

    stage_priority = {
        "EXACT_REFINEMENT": 0,
        "EXACT": 1,
        "FAST_SCREEN": 2,
        "FAST": 3,
    }
    metrics["_priority"] = metrics["_stage"].map(stage_priority).fillna(9)
    return (
        metrics.sort_values("_priority", kind="mergesort")
        .drop_duplicates(["Ticker", "EntrySignal"], keep="first")
        .drop(columns=["_stage", "_priority"])
    )


def _atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_name(f".{path.name}.resonance.tmp")
    try:
        frame.to_csv(temporary, index=False, encoding="utf-8-sig")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_parquet(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_name(f".{path.name}.resonance.tmp")
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _diagnostic_rows(summary: dict[str, Any]) -> list[dict[str, object]]:
    analysis = summary.get("resonance_analysis", {})
    if not isinstance(analysis, dict):
        return []
    version = str(analysis.get("version", "") or "")
    rows: list[dict[str, object]] = []
    for dimension, key in (
        ("COUNT", "by_count"),
        ("BAND", "by_band"),
        ("TRANSITION", "by_transition"),
    ):
        groups = analysis.get(key, [])
        if not isinstance(groups, list):
            continue
        for raw in groups:
            if not isinstance(raw, dict):
                continue
            rows.append(
                {
                    "Dimension": dimension,
                    "Group": str(raw.get("group", "") or ""),
                    "Samples": raw.get("samples", 0),
                    "EffectiveSamples": raw.get("effective_samples", 0.0),
                    "NetExcessWinRate20D": raw.get(
                        "net_excess_win_rate_20d", np.nan
                    ),
                    "AverageNetExcess20D": raw.get(
                        "average_net_excess_20d", np.nan
                    ),
                    "AverageNetExcess60D": raw.get(
                        "average_net_excess_60d", np.nan
                    ),
                    "MaxDrawdown60D": raw.get("max_drawdown_60d", np.nan),
                    "ResonanceVersion": version,
                    "OutputVersion": RESONANCE_OUTPUT_VERSION,
                }
            )
    return rows


def materialize_resonance_outputs(
    output_dir: Path,
    *,
    refresh_candidate_exports: bool = True,
) -> dict[str, object]:
    """Join resonance diagnostics onto stable outputs after a successful backtest."""
    root = Path(output_dir)
    summary_path = root / "BacktestSummary.json"
    results_path = root / "AllResults.csv"
    summary = _read_json(summary_path)
    if not summary or not results_path.is_file():
        return {"status": "SKIPPED", "reason": "missing-summary-or-results"}

    try:
        results = pd.read_csv(results_path, encoding="utf-8-sig", low_memory=False)
    except (OSError, UnicodeError, pd.errors.ParserError) as exc:
        return {"status": "SKIPPED", "reason": f"results-read-failed:{exc}"}
    if results.empty or not {"Ticker", "EntrySignal"}.issubset(results.columns):
        return {"status": "SKIPPED", "reason": "missing-join-keys"}

    metrics = _metric_frame(summary, results)
    drop_columns = [
        column
        for column in (*_SUMMARY_TO_OUTPUT.values(), "BacktestResonanceVersion")
        if column in results.columns
    ]
    if drop_columns:
        results = results.drop(columns=drop_columns)

    if not metrics.empty:
        results["Ticker"] = (
            results["Ticker"].fillna("").astype(str).str.strip().str.upper()
        )
        results["EntrySignal"] = _signal_series(results["EntrySignal"])
        results = results.merge(
            metrics,
            on=["Ticker", "EntrySignal"],
            how="left",
            validate="one_to_one",
        )

    analysis = summary.get("resonance_analysis", {})
    version = (
        str(analysis.get("version", "") or "")
        if isinstance(analysis, dict)
        else ""
    )
    results["BacktestResonanceVersion"] = version
    _atomic_write_csv(results, results_path)

    parquet_path = root / "AllResults.parquet"
    if parquet_path.is_file():
        try:
            _atomic_write_parquet(results, parquet_path)
        except (OSError, ImportError, ValueError, TypeError):
            pass

    by_ticker_path = root / "FiveFactorResonanceByTicker.csv"
    if metrics.empty:
        _atomic_write_csv(
            pd.DataFrame(
                columns=["Ticker", "EntrySignal", *_SUMMARY_TO_OUTPUT.values()]
            ),
            by_ticker_path,
        )
    else:
        by_ticker = metrics.copy()
        by_ticker["BacktestResonanceVersion"] = version
        _atomic_write_csv(by_ticker, by_ticker_path)

    diagnostic_path = root / "FiveFactorResonance.csv"
    diagnostic_rows = _diagnostic_rows(summary)
    _atomic_write_csv(pd.DataFrame.from_records(diagnostic_rows), diagnostic_path)

    refresh_status = "NOT_REQUESTED"
    if refresh_candidate_exports:
        try:
            import report as report_module

            report_module.refresh_candidate_exports(results, output_dir=root)
            refresh_status = "REFRESHED"
        except (OSError, ValueError, TypeError, KeyError, ImportError, RuntimeError):
            refresh_status = "FAILED_NON_FATAL"

    return {
        "status": "MATERIALIZED",
        "rows": len(results),
        "ticker_metrics": len(metrics),
        "diagnostic_groups": len(diagnostic_rows),
        "candidate_exports": refresh_status,
        "version": version,
        "output_version": RESONANCE_OUTPUT_VERSION,
    }
