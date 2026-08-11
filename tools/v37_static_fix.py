from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "report.py"
text = path.read_text(encoding="utf-8")
old = ")\nfrom evidence import enrich_evidence_fields\nfrom config import ("
if text.count(old) != 1:
    raise RuntimeError("report import prelude not found exactly once")
text = text.replace(old, ")\nfrom config import (", 1)
old_tail = "    VALUE_TRAP_RISK_THRESHOLD,\n)\nfrom performance_cache import BACKTEST_CACHE_VERSION, INDICATOR_CACHE_VERSION\n"
new_tail = "    VALUE_TRAP_RISK_THRESHOLD,\n)\nfrom evidence import enrich_evidence_fields\nfrom performance_cache import BACKTEST_CACHE_VERSION, INDICATOR_CACHE_VERSION\n"
if text.count(old_tail) != 1:
    raise RuntimeError("report config import tail not found exactly once")
path.write_text(text.replace(old_tail, new_tail, 1), encoding="utf-8")
subprocess.run(["git", "add", "report.py"], cwd=ROOT, check=True)
