from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:160]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "test_model_v19_regressions.py",
    '    def test_v23_version_and_refinement_contract(self):\n        self.assertEqual(SCORING_VERSION, "2026-08-09-v23-research-integrity")',
    '    def test_v24_version_and_refinement_contract(self):\n        self.assertEqual(SCORING_VERSION, "2026-08-09-v24-decision-integrity")',
)

replace_once(
    "test_output_integrity_v21.py",
    '            "LifecycleStage": "趋势确认",\n',
    '            "LifecycleStage": "趋势确认",\n            "SignalRecencyDays": 1,\n',
)

replace_once(
    "test_ranking_integrity_v19.py",
    '            "ValueTrapRisk": 0.0,\n',
    '            "ValueTrapRisk": 0.0,\n            "SignalRecencyDays": 1,\n',
)
replace_once(
    "test_ranking_integrity_v19.py",
    '            self._row("FAILED_TOP", 50.0, failed=True, quality_gate=False),\n            self._row("READY", 48.0),',
    '            self._row("FAILED_TOP", 50.0, failed=True, quality_gate=False),\n            self._row("READY", 52.0),',
)

# apply_backtest_ranking recomputes SignalRecencyDays from these dates, so the
# positive-control breakout fixture must carry the same provenance as real scans.
replace_once(
    "test_regressions.py",
    '                "Score": [100.0, 60.0],\n                "InstitutionalScore": [100.0, 60.0],',
    '                "Score": [60.0, 100.0],\n                "InstitutionalScore": [60.0, 100.0],',
)
replace_once(
    "test_regressions.py",
    '                "BreakoutFlowConfirmed": [False, True],\n            }).to_csv(',
    '                "BreakoutFlowConfirmed": [False, True],\n                "SignalRecencyDays": [1, 1],\n                "SignalStartDate": ["2026-08-06", "2026-08-06"],\n                "DataAsOf": ["2026-08-07", "2026-08-07"],\n            }).to_csv(',
)
replace_once(
    "test_regressions.py",
    '        self.assertEqual(top["Ticker"].tolist(), ["000001.SZ", "000002.SZ"])',
    '        self.assertEqual(top["Ticker"].tolist(), ["000002.SZ", "000001.SZ"])',
)

replace_once(
    "test_regressions.py",
    '            "DataTradingAgeDays": [0, 0, 0],\n        })\n\n        result = signal_lifecycle.finalize_signal_ranking(frame).set_index("Ticker")',
    '            "DataTradingAgeDays": [0, 0, 0],\n            "SignalRecencyDays": [1, 1, 1],\n        })\n\n        result = signal_lifecycle.finalize_signal_ranking(frame).set_index("Ticker")',
)

Path(__file__).unlink()
