from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "test_v39_decision_integrity.py"
text = TARGET.read_text(encoding="utf-8")

old_import = "from fundamental_quality import calculate_quality\n"
new_import = "from fundamental_quality import calculate_quality\nfrom scanner import ScanResult\n"
if text.count(old_import) != 1:
    raise RuntimeError("expected one fundamental_quality import")
text = text.replace(old_import, new_import, 1)

marker = '''    def test_versions_advance_without_replacing_v38_gate_policy(self):\n'''
legacy_test = '''    def test_legacy_combined_filter_export_does_not_invent_hard_failures(self):\n        result = ScanResult(\n            ticker="000001.SZ",\n            passed_filters=True,\n            filter_details={"signal_count": 4},\n        )\n        frame = report._results_to_dataframe([result])\n        row = frame.iloc[0]\n        self.assertTrue(bool(row["UniverseEligible"]))\n        self.assertTrue(bool(row["HardGatePassed"]))\n        self.assertEqual(str(row["HardGateFailedNames"]), "")\n        self.assertEqual(int(row["HardGateFailedCount"]), 0)\n\n'''
if text.count(marker) != 1:
    raise RuntimeError("expected one v39 version test marker")
text = text.replace(marker, legacy_test + marker, 1)
TARGET.write_text(text, encoding="utf-8")
