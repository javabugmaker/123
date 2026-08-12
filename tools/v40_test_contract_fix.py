from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "test_regressions.py"
text = TARGET.read_text(encoding="utf-8")

old = '''        results[0].score.total = 90.0\n        results[0].score.trend = 25.0\n'''
new = '''        # This historical export fixture represents valid research candidates.\n        # Make its modern entry-state intent explicit so v40 can keep genuine\n        # risk-filtered rows out of Opportunity without weakening compatibility.\n        for result in results:\n            result.entry_signal = "WAIT_PULLBACK"\n            result.raw_entry_signal = "WAIT_PULLBACK"\n\n        results[0].score.total = 90.0\n        results[0].score.trend = 25.0\n'''
if text.count(old) != 1:
    raise RuntimeError(f"expected one legacy export fixture block, got {text.count(old)}")
TARGET.write_text(text.replace(old, new, 1), encoding="utf-8")
