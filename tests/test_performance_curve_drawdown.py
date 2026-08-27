import pandas as pd
from performance_curve import build_performance_curve


def test_drawdown_is_non_positive() -> None:
    history = pd.DataFrame([
        {"TradeDate":"2026-08-26","Ticker":"a","Score":50,"Return20D":10.0},
        {"TradeDate":"2026-08-27","Ticker":"a","Score":50,"Return20D":-10.0},
    ])
    curve = build_performance_curve(history)
    assert curve["ResearchCohortDrawdown"].le(0.0).all()
