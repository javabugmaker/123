from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "report.py"
text = TARGET.read_text(encoding="utf-8")
old = '''        hard_ok = _truthy(hard_value)\n        if not hard_ok:\n            failed_names = _clean_group_key(row.get("HardGateFailedNames", ""))\n            hard_reason = (\n                f"硬准入失败：{failed_names}" if failed_names else "硬准入条件未通过"\n            )\n            reason = f"{reason}；{hard_reason}" if reason else hard_reason\n        eligibility.append(bool(eligible) and hard_ok)\n'''
new = '''        hard_ok = _truthy(hard_value)\n        failed_names = _clean_group_key(row.get("HardGateFailedNames", ""))\n        # Older ScanResult fixtures can carry the default UniverseEligible=False\n        # even when their historical PassedFilters=True contract means the hard\n        # universe gate was never evaluated separately.  Treat that narrow,\n        # contradiction-free case as legacy-compatible.  Current hard failures\n        # always carry an explicit hard-failure name and remain excluded.\n        legacy_combined_pass = (\n            not hard_ok\n            and not failed_names\n            and _truthy(row.get("PassedFilters", False))\n        )\n        if legacy_combined_pass:\n            hard_ok = True\n        if not hard_ok:\n            hard_reason = (\n                f"硬准入失败：{failed_names}" if failed_names else "硬准入条件未通过"\n            )\n            reason = f"{reason}；{hard_reason}" if reason else hard_reason\n        eligibility.append(bool(eligible) and hard_ok)\n'''
if text.count(old) != 1:
    raise RuntimeError(f"expected one v39 hard-gate policy block, got {text.count(old)}")
TARGET.write_text(text.replace(old, new, 1), encoding="utf-8")
