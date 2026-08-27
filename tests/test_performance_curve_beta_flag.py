from __future__ import annotations

import pandas as pd

from performance_curve import build_performance_curve


def test_beta_proxy_is_available_with_cross_section() -> None:
    rows = []
    for day in pd.bdate_range("2026-01-05", periods=30):
        for i in range(10):
            rows.append({"TradeDate": day.strftime("%Y-%m-%d"), "Ticker": f"6000{i:02d}.SH", "Score": 50 + i, "Return20D": 3 - i * 0.5, "ChaseRiskScore": i * 10, "IndustryRelativeStrength": i})
    curve = build_performance_curve(pd.DataFrame(rows))
    assert curve["BetaCanarySpread20"].notna().all()
