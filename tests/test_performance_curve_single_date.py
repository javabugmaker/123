import pandas as pd

from performance_curve import build_performance_curve


def test_single_date_has_one_curve_row() -> None:
    frame = build_performance_curve(pd.DataFrame([{"TradeDate":"2026-08-27","Ticker":"600000.SH","Score":60,"Return20D":1.0}]))
    assert len(frame) == 1
