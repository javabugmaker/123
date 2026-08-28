"""Point-in-time model health curves for the public research console.

This module converts the existing SignalHistory research ledger into daily,
non-overlapping diagnostics suitable for longitudinal model monitoring.  It is
presentation-adjacent by design: production ranking, TradeReady eligibility,
position sizing and order logic are not changed here.

The exported curve intentionally separates three questions:
1. Is the cross-sectional model still predictive?  (daily / rolling Rank IC)
2. Is the public research cohort adding value versus CSI 300? (cohort NAV)
3. Is high-beta risk appetite deteriorating? (beta canary proxy)

The cohort NAV is *not* a tradable portfolio backtest.  It uses one observation
per signal date and horizon-aligned realised outcomes, avoiding the common error
of compounding overlapping 20D sample rows as if they were independent daily
returns.  A future execution-ledger engine can replace the proxy series without
changing the web-report contract.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from config import OUTPUT_DIR

PERFORMANCE_CURVE_VERSION = "2026-08-28-v2-pit-model-health-benchmark"
PERFORMANCE_CURVE_CSV = OUTPUT_DIR / "PerformanceCurve.csv"
PERFORMANCE_CURVE_JSON = OUTPUT_DIR / "PerformanceCurve.json"
PERFORMANCE_CURVE_MIN_MATURE_DATES = 20


def _numeric(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce").replace([np.inf, -np.inf], np.nan)


def _rank_ic(group: pd.DataFrame, horizon: int) -> float:
    score = _numeric(group, "InstitutionalScore")
    if score.notna().sum() < 3:
        score = _numeric(group, "Score")
    target = _numeric(group, f"Return{horizon}D")
    valid = score.notna() & target.notna()
    if int(valid.sum()) < 3 or score.loc[valid].nunique() < 2 or target.loc[valid].nunique() < 2:
        return float("nan")
    value = spearmanr(score.loc[valid], target.loc[valid], nan_policy="omit").statistic
    return float(value) if value is not None and np.isfinite(value) else float("nan")


def _cohort_return(
    group: pd.DataFrame,
    horizon: int,
    *,
    prefix: str = "Return",
) -> float:
    """Equal-weight one cross-section per signal date; percent return."""
    values = _numeric(group, f"{prefix}{horizon}D").dropna()
    if values.empty:
        return float("nan")
    # Winsorise only the diagnostic aggregate, never the underlying samples.
    if len(values) >= 10:
        lo, hi = values.quantile([0.02, 0.98]).tolist()
        values = values.clip(float(lo), float(hi))
    return float(values.mean())


def _drawdown(nav: pd.Series) -> pd.Series:
    clean = pd.to_numeric(nav, errors="coerce").replace([np.inf, -np.inf], np.nan).ffill()
    if clean.empty:
        return pd.Series(dtype=float)
    peak = clean.cummax().replace(0.0, np.nan)
    return (clean / peak - 1.0) * 100.0


def _beta_canary_proxy(day: pd.DataFrame) -> float:
    """Cross-sectional risk-appetite proxy using fields already exported.

    The full rolling market-beta basket needs daily historical constituent
    returns and therefore belongs in the future execution/performance ledger.
    Until then, use a deterministic high-risk cohort proxy from current
    longitudinal research fields.  Positive values mean the risk-seeking cohort
    is outperforming its same-day peer set.
    """
    returns = _numeric(day, "Return20D")
    if returns.notna().sum() < 6:
        return float("nan")

    risk = pd.Series(0.0, index=day.index, dtype=float)
    if "ChaseRiskScore" in day:
        risk += _numeric(day, "ChaseRiskScore").fillna(0.0)
    if "IndustryRelativeStrength" in day:
        risk += _numeric(day, "IndustryRelativeStrength").fillna(0.0).clip(lower=0.0)
    if "InstitutionalScore" in day:
        risk += _numeric(day, "InstitutionalScore").fillna(0.0) * 0.15
    elif "Score" in day:
        risk += _numeric(day, "Score").fillna(0.0) * 0.15

    valid = returns.notna() & risk.notna()
    if int(valid.sum()) < 6 or risk.loc[valid].nunique() < 2:
        return float("nan")
    cutoff = float(risk.loc[valid].quantile(0.8))
    high = returns.loc[valid & risk.ge(cutoff)]
    all_returns = returns.loc[valid]
    if high.empty:
        return float("nan")
    return float(high.mean() - all_returns.mean())


def build_performance_curve(history: pd.DataFrame) -> pd.DataFrame:
    if history is None or history.empty or "TradeDate" not in history:
        return pd.DataFrame()

    data = history.copy()
    data["Date"] = pd.to_datetime(
        data["TradeDate"],
        errors="coerce",
        format="mixed",
    ).dt.normalize()
    data = data.loc[data["Date"].notna()].sort_values(["Date", "Ticker"], kind="mergesort")
    if data.empty:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for trade_date, group in data.groupby("Date", sort=True):
        matured20 = _numeric(group, "Return20D").notna()
        matured60 = _numeric(group, "Return60D").notna()
        rows.append(
            {
                "Date": trade_date,
                "Samples": len(group),
                "MaturedSamples20": int(matured20.sum()),
                "MaturedSamples60": int(matured60.sum()),
                "MaturedCoverage20": float(matured20.mean()),
                "RankIC20": _rank_ic(group, 20),
                "RankIC60": _rank_ic(group, 60),
                "CohortReturn20": _cohort_return(group, 20),
                "CohortReturn60": _cohort_return(group, 60),
                "BenchmarkCohortReturn20": _cohort_return(
                    group,
                    20,
                    prefix="BenchmarkReturn",
                ),
                "BenchmarkCohortReturn60": _cohort_return(
                    group,
                    60,
                    prefix="BenchmarkReturn",
                ),
                "BetaCanarySpread20": _beta_canary_proxy(group),
            }
        )

    curve = pd.DataFrame(rows).sort_values("Date", kind="mergesort").reset_index(drop=True)
    curve["RollingRankIC60"] = curve["RankIC20"].rolling(60, min_periods=20).mean()
    observed_ic = curve["RollingRankIC60"].dropna()
    ic_median = float(observed_ic.median()) if not observed_ic.empty else float("nan")
    curve["ICMedian"] = ic_median
    curve["ICRiskFlag"] = curve["RollingRankIC60"].lt(0.0) & curve["RollingRankIC60"].notna()
    curve["MaturedDates20"] = curve["MaturedSamples20"].gt(0).cumsum()
    curve["MaturedSamples20Cumulative"] = curve["MaturedSamples20"].cumsum()
    curve["CoverageStatus"] = np.select(
        [
            curve["MaturedDates20"].eq(0),
            curve["MaturedDates20"].lt(PERFORMANCE_CURVE_MIN_MATURE_DATES),
        ],
        ["WARMUP_NO_OUTCOMES", "WARMUP"],
        default="MONITORABLE",
    )

    # Convert horizon returns to a horizon-spaced diagnostic NAV.  Using
    # 1/horizon exponents avoids pretending each overlapping sample is a fresh
    # daily realised return while preserving the direction and magnitude of the
    # cohort evidence.
    cohort20 = pd.to_numeric(curve["CohortReturn20"], errors="coerce") / 100.0
    daily_equiv = (1.0 + cohort20.clip(lower=-0.99)).pow(1.0 / 20.0) - 1.0
    curve["ResearchCohortNAV"] = (1.0 + daily_equiv.fillna(0.0)).cumprod()
    curve["ResearchCohortDrawdown"] = _drawdown(curve["ResearchCohortNAV"])

    benchmark20 = pd.to_numeric(
        curve["BenchmarkCohortReturn20"],
        errors="coerce",
    ) / 100.0
    benchmark_daily_equiv = (
        (1.0 + benchmark20.clip(lower=-0.99)).pow(1.0 / 20.0) - 1.0
    )
    benchmark_observed = benchmark20.notna().cummax()
    curve["BenchmarkNAV"] = (
        (1.0 + benchmark_daily_equiv.fillna(0.0)).cumprod()
    ).where(benchmark_observed)
    curve["BenchmarkDrawdown"] = _drawdown(curve["BenchmarkNAV"])
    curve["ResearchExcessNAV"] = (
        curve["ResearchCohortNAV"]
        / curve["BenchmarkNAV"].replace(0.0, np.nan)
    )

    canary = pd.to_numeric(curve["BetaCanarySpread20"], errors="coerce") / 100.0
    canary_daily = (1.0 + canary.clip(lower=-0.99)).pow(1.0 / 20.0) - 1.0
    curve["BetaCanaryNAV"] = (1.0 + canary_daily.fillna(0.0)).cumprod()
    rolling_canary = curve["BetaCanarySpread20"].rolling(20, min_periods=8).mean()
    curve["BetaRiskFlag"] = rolling_canary.lt(0.0) & rolling_canary.notna()

    curve["Version"] = PERFORMANCE_CURVE_VERSION
    return curve


def _json_ready(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return round(float(value), 6) if np.isfinite(value) else None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat()
    if pd.isna(value):
        return None
    return value


def write_performance_curve(
    history: pd.DataFrame,
    *,
    csv_path: Path = PERFORMANCE_CURVE_CSV,
    json_path: Path = PERFORMANCE_CURVE_JSON,
) -> tuple[Path, Path, pd.DataFrame]:
    curve = build_performance_curve(history)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)

    csv_tmp = csv_path.with_name(f".{csv_path.name}.tmp")
    json_tmp = json_path.with_name(f".{json_path.name}.tmp")
    try:
        output = curve.copy()
        if "Date" in output:
            output["Date"] = pd.to_datetime(output["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
        output.to_csv(csv_tmp, index=False, encoding="utf-8-sig")
        payload = {
            "version": PERFORMANCE_CURVE_VERSION,
            "rows": [
                {str(key): _json_ready(value) for key, value in row.items()}
                for row in curve.to_dict(orient="records")
            ],
        }
        json_tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(csv_tmp, csv_path)
        os.replace(json_tmp, json_path)
    finally:
        for path in (csv_tmp, json_tmp):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
    return csv_path, json_path, curve


def curve_summary(curve: pd.DataFrame) -> dict[str, Any]:
    if curve is None or curve.empty:
        return {"rows": 0, "version": PERFORMANCE_CURVE_VERSION}
    latest = curve.iloc[-1]
    return {
        "rows": len(curve),
        "version": PERFORMANCE_CURVE_VERSION,
        "latest_date": _json_ready(latest.get("Date")),
        "research_cohort_nav": _json_ready(latest.get("ResearchCohortNAV")),
        "research_cohort_drawdown": _json_ready(latest.get("ResearchCohortDrawdown")),
        "benchmark_nav": _json_ready(latest.get("BenchmarkNAV")),
        "research_excess_nav": _json_ready(latest.get("ResearchExcessNAV")),
        "rolling_rank_ic_60": _json_ready(latest.get("RollingRankIC60")),
        "matured_dates_20": _json_ready(latest.get("MaturedDates20")),
        "matured_samples_20": _json_ready(
            latest.get("MaturedSamples20Cumulative")
        ),
        "coverage_status": _json_ready(latest.get("CoverageStatus")),
        "ic_risk": bool(latest.get("ICRiskFlag", False)),
        "beta_risk": bool(latest.get("BetaRiskFlag", False)),
    }
