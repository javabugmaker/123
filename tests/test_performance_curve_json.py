from __future__ import annotations

import json

import pandas as pd

from performance_curve import write_performance_curve


def test_json_export_is_browser_safe(tmp_path) -> None:
    history = pd.DataFrame([
        {"TradeDate":"2026-08-26","Ticker":"600000.SH","Score":60,"InstitutionalScore":60,"Return20D":1.0,"Return60D":2.0},
        {"TradeDate":"2026-08-27","Ticker":"600000.SH","Score":61,"InstitutionalScore":61,"Return20D":1.2,"Return60D":2.2},
    ])
    csv_path = tmp_path / "PerformanceCurve.csv"
    json_path = tmp_path / "PerformanceCurve.json"
    write_performance_curve(history, csv_path=csv_path, json_path=json_path)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["rows"]
    assert isinstance(payload["rows"][0]["Date"], str)
