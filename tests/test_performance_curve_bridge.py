from __future__ import annotations

import pandas as pd

from institution_scanner.performance_curve_bridge import emit_performance_curve


def test_bridge_emits_empty_curve_artifacts(tmp_path, monkeypatch) -> None:
    import performance_curve

    monkeypatch.setattr(performance_curve, "PERFORMANCE_CURVE_CSV", tmp_path / "PerformanceCurve.csv")
    monkeypatch.setattr(performance_curve, "PERFORMANCE_CURVE_JSON", tmp_path / "PerformanceCurve.json")
    # Bridge imports the writer function directly, so exercise its public return
    # contract with a minimal non-empty ledger instead of patching internals.
    history = pd.DataFrame(
        [
            {
                "TradeDate": "2026-08-27",
                "Ticker": "600000.SH",
                "Score": 60,
                "InstitutionalScore": 62,
                "Return20D": 1.0,
                "Return60D": 2.0,
            }
        ]
    )
    summary = emit_performance_curve(history)
    assert summary["rows"] == 1
    assert summary["csv"] == "PerformanceCurve.csv"
    assert summary["json"] == "PerformanceCurve.json"
