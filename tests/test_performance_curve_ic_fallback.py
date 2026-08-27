import pandas as pd
from performance_curve import build_performance_curve


def test_rank_ic_falls_back_to_score_when_institutional_missing() -> None:
    history = pd.DataFrame([
        {"TradeDate":"2026-08-27","Ticker":"a","Score":40,"Return20D":1.0},
        {"TradeDate":"2026-08-27","Ticker":"b","Score":50,"Return20D":2.0},
        {"TradeDate":"2026-08-27","Ticker":"c","Score":60,"Return20D":3.0},
    ])
    curve = build_performance_curve(history)
    assert curve.iloc[0]["RankIC20"] > 0.9
