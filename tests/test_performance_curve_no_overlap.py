from __future__ import annotations

import pandas as pd

from performance_curve import build_performance_curve


def test_duplicate_same_day_rows_do_not_create_extra_nav_dates() -> None:
    history = pd.DataFrame(
        [
            {"TradeDate": "2026-08-01", "Ticker": "600000.SH", "Score": 60, "Return20D": 10},
            {"TradeDate": "2026-08-01", "Ticker": "600001.SH", "Score": 70, "Return20D": 20},
            {"TradeDate": "2026-08-02", "Ticker": "600000.SH", "Score": 61, "Return20D": 5},
            {"TradeDate": "2026-08-02", "Ticker": "600001.SH", "Score": 71, "Return20D": 15},
        ]
    )
    curve = build_performance_curve(history)
    assert len(curve) == 2
    assert curve["Samples"].tolist() == [2, 2]
