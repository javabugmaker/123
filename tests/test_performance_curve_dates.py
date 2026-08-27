import pandas as pd
from performance_curve import build_performance_curve


def test_invalid_dates_are_dropped() -> None:
    history = pd.DataFrame([{"TradeDate":"bad","Ticker":"x","Score":50,"Return20D":1.0},{"TradeDate":"2026-08-27","Ticker":"y","Score":60,"Return20D":2.0}])
    curve = build_performance_curve(history)
    assert len(curve) == 1
