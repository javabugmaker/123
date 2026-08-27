from __future__ import annotations

import pandas as pd

from performance_curve import build_performance_curve


def test_negative_predictive_power_sets_ic_risk() -> None:
    rows = []
    for day in pd.bdate_range("2026-01-05", periods=85):
        for i in range(6):
            rows.append({"TradeDate": day.strftime("%Y-%m-%d"), "Ticker": f"60000{i}.SH", "Score": 50 + i, "InstitutionalScore": 50 + i, "Return20D": float(-i), "Return60D": float(-2 * i)})
    curve = build_performance_curve(pd.DataFrame(rows))
    assert curve["ICRiskFlag"].tail(10).all()
