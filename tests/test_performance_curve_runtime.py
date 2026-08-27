from __future__ import annotations

import pandas as pd

from institution_scanner import performance_curve_runtime


def test_after_history_refresh_delegates(monkeypatch) -> None:
    monkeypatch.setattr(performance_curve_runtime, "safe_emit", lambda frame: {"rows": len(frame)})
    result = performance_curve_runtime.after_history_refresh(pd.DataFrame([{"x": 1}]))
    assert result["rows"] == 1
