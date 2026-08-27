from __future__ import annotations

import pandas as pd

from performance_curve import write_performance_curve


def test_write_creates_both_artifacts(tmp_path) -> None:
    history = pd.DataFrame([{"TradeDate":"2026-08-27","Ticker":"600000.SH","Score":60,"Return20D":1.0}])
    csv_path = tmp_path / "curve.csv"
    json_path = tmp_path / "curve.json"
    write_performance_curve(history, csv_path=csv_path, json_path=json_path)
    assert csv_path.is_file()
    assert json_path.is_file()
