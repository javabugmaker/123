"""Materialize v90 five-factor resonance diagnostics into published outputs.

The historical engine deliberately keeps resonance outside the production score.
This module closes the presentation gap after a successful backtest: the held-out
resonance diagnostics already stored in ``BacktestSummary.json`` are joined onto
``AllResults`` and candidate exports without changing ranking, eligibility, or
any calibrated score.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

RESONANCE_OUTPUT_VERSION = "2026-08-23-v90-resonance-output-v1"

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


def _metric_frame(
    summary: dict[str, Any],
    results: pd.DataFrame,
) -> pd.DataFrame:
    raw_rows = summary.get("by_ticker", [])
    if not isinstance(raw_rows, list):
        return pd.DataFrame()

    records: list[dict[str, object]] = []
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        ticker = str(raw.get("ticker", "") or "").strip().upper()
        if not ticker:
            continue
        record: dict[str, object] = {
            "Ticker": ticker,
            "EntrySignal": _signal_text(raw.get("entry_signal", "UNKNOWN")),
            "_stage": str(raw.get("backtest_stage", "") or "").strip().upper(),
        }
        has_metric = False
        for source, target in _SUMMARY_TO_OUTPUT.items():
            value = pd.to_numeric(pd.Series([raw.get(source, np.nan)]), errors="coerce").iloc[0]
            record[target] = float(value) if pd.notna(value) and np.isfinite(float(value)) else np.nan
            has_metric = has_metric or pd.notna(record[target])
        if has_metric:
            records.append(record)
    if not records:
        return pd.DataFrame()

    metrics = pd.DataFrame.from_records(records)
    if "Ticker" not in results or "EntrySignal" not in results:
        return pd.DataFrame()

    current_signal = (
        results[["Ticker", "EntrySignal"]]
        .assign(
            Ticker=lambda frame: frame["Ticker"].fillna("").astype(str).str.strip().str.upper(),
            EntrySignal=lambda frame: frame["EntrySignal"].map(_signal_text),
        )
        .drop_duplicates("Ticker", keep="first")
        .set_index("Ticker")["EntrySignal"]
        .to_dict()
    )
    unknown = metrics["EntrySignal"].eq("UNKNOWN")
    metrics.loc[unknown, "EntrySignal"] = metrics.loc[unknown, "Ticker"].map(
        current_signal
    ).fillna("UNKNOWN")

    stage_priority = {
        "EXACT_REFINEMENT": 0,
        "EXACT": 1,
        "FAST_SCREEN": 2,
        "FAST": 3,
    }
    metrics["_priority"] = metrics["_stage"].map(stage_priority).fillna(9)
    metrics = (
        metrics.sort_values("_priority", kind="mergesort")
        .drop_duplicates(["Ticker", "EntrySignal"], keep="first")
        .drop(columns=["_stage", "_priority"])
    )
    return metrics


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
                    "NetExcessWinRate20D": raw.get("net_excess_win_rate_20d", np.nan),
                    "AverageNetExcess20D": raw.get("average_net_excess_20d", np.nan),
                    "AverageNetExcess60D": raw.get("average_net_excess_60d", np.nan),
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
    """Join resonance diagnostics onto stable outputs after a successful backtest.

    This function is intentionally post-ranking.  It never changes any score or
    ordering field; it only appends diagnostic columns and refreshes derivative
    candidate files so the GUI and public research briefing can display them.
    """
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
    for column in (*_SUMMARY_TO_OUTPUT.values(), "BacktestResonanceVersion"):
        if column in results.columns:
            results = results.drop(columns=column)

    if not metrics.empty:
        results["Ticker"] = results["Ticker"].fillna("").astype(str).str.strip().str.upper()
        results["EntrySignal"] = results["EntrySignal"].map(_signal_text)
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
            # CSV is the canonical interoperability surface; parquet remains a
            # best-effort acceleration artifact on systems without pyarrow.
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
        "rows": int(len(results)),
        "ticker_metrics": int(len(metrics)),
        "diagnostic_groups": int(len(diagnostic_rows)),
        "candidate_exports": refresh_status,
        "version": version,
        "output_version": RESONANCE_OUTPUT_VERSION,
    }
