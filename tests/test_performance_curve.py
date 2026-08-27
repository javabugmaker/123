from __future__ import annotations

import pandas as pd

from performance_curve import build_performance_curve, curve_summary


def _history(days: int = 90) -> pd.DataFrame:
    rows = []
    dates = pd.bdate_range("2026-01-05", periods=days)
    for day_index, trade_date in enumerate(dates):
        for ticker_index in range(8):
            score = 35.0 + ticker_index * 7.0 + (day_index % 5)
            realised = (score - 55.0) * 0.08 + ((day_index + ticker_index) % 3 - 1) * 0.15
            rows.append(
                {
                    "TradeDate": trade_date.strftime("%Y-%m-%d"),
                    "Ticker": f"{600000 + ticker_index}.SH",
                    "Score": score,
                    "InstitutionalScore": score,
                    "Return20D": realised,
                    "Return60D": realised * 1.8,
                    "ChaseRiskScore": ticker_index * 8.0,
                    "IndustryRelativeStrength": ticker_index - 2.0,
                }
            )
    return pd.DataFrame(rows)


def test_curve_is_daily_and_rank_ic_uses_cross_section() -> None:
    curve = build_performance_curve(_history())
    assert len(curve) == 90
    assert curve["Date"].is_monotonic_increasing
    assert curve["RankIC20"].dropna().gt(0.9).all()
    assert curve["RollingRankIC60"].dropna().gt(0.0).all()
    assert not curve["ICRiskFlag"].any()


def test_curve_nav_and_summary_are_finite() -> None:
    curve = build_performance_curve(_history())
    assert curve["ResearchCohortNAV"].notna().all()
    assert curve["BetaCanaryNAV"].notna().all()
    summary = curve_summary(curve)
    assert summary["rows"] == 90
    assert summary["latest_date"]
    assert summary["research_cohort_nav"] is not None
