import pandas as pd

from performance_curve import build_performance_curve


def test_cohort_return_is_equal_weighted() -> None:
    history = pd.DataFrame(
        [
            {"TradeDate": "2026-08-27", "Ticker": "a", "Score": 50, "Return20D": 0.0},
            {"TradeDate": "2026-08-27", "Ticker": "b", "Score": 60, "Return20D": 10.0},
        ]
    )
    curve = build_performance_curve(history)
    assert curve.iloc[0]["CohortReturn20"] == 5.0
