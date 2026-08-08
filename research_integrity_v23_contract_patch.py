from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one anchor, got {count}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "test_model_v19_regressions.py",
    '''    def test_v20_version_and_refinement_contract(self):
        self.assertEqual(SCORING_VERSION, "2026-08-09-v21-output-integrity")
''',
    '''    def test_v23_version_and_refinement_contract(self):
        self.assertEqual(SCORING_VERSION, "2026-08-09-v23-research-integrity")
''',
)

replace_once(
    "test_regressions.py",
    '''    def test_enrichment_blends_available_quality_score(self):
''',
    '''    def test_enrichment_keeps_fundamentals_as_gate_not_alpha(self):
''',
)
replace_once(
    "test_regressions.py",
    '''        self.assertEqual(result.institutional_score, 68.0)
''',
    '''        self.assertEqual(result.institutional_score, 80.0)
''',
)

replace_once(
    "test_regressions.py",
    '''        with TemporaryDirectory() as temp_dir, patch("gui.OUTPUT_DIR", Path(temp_dir)):
            path = Path(temp_dir) / "Top50.csv"
            path.write_text("", encoding="utf-8")
            scanner._write_top50_csv(["000003.SZ", "000001.SZ"])
            with path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.reader(file))
            self.assertEqual(rows, [["Ticker", "Score"], ["000003.SZ", "70"], ["000001.SZ", "90"]])
            self.assertIsNone(scanner._csv_path)
            self.assertFalse((Path(temp_dir) / ".Top50.csv.tmp").exists())
''',
    '''        with TemporaryDirectory() as temp_dir, patch("gui_core.OUTPUT_DIR", Path(temp_dir)):
            path = Path(temp_dir) / "Top50Filtered.csv"
            path.write_text("", encoding="utf-8")
            scanner._write_top50_csv(["000003.SZ", "000001.SZ"])
            with path.open("r", encoding="utf-8-sig", newline="") as file:
                rows = list(csv.reader(file))
            self.assertEqual(rows, [["Ticker", "Score"], ["000003.SZ", "70"], ["000001.SZ", "90"]])
            self.assertIsNone(scanner._csv_path)
            self.assertFalse((Path(temp_dir) / ".Top50Filtered.csv.tmp").exists())
''',
)

replace_once(
    "test_regressions.py",
    '''        with TemporaryDirectory() as temp_dir, patch("gui.OUTPUT_DIR", Path(temp_dir)), patch("gui.os.replace", side_effect=OSError("replace failed")):
            path = Path(temp_dir) / "Top50.csv"
            path.write_text("old", encoding="utf-8")
            with self.assertRaises(OSError):
                scanner._write_top50_csv(["000001.SZ"])
            self.assertEqual(path.read_text(encoding="utf-8"), "old")
            self.assertFalse((Path(temp_dir) / ".Top50.csv.tmp").exists())
''',
    '''        with TemporaryDirectory() as temp_dir, patch("gui_core.OUTPUT_DIR", Path(temp_dir)), patch("gui_core.os.replace", side_effect=OSError("replace failed")):
            path = Path(temp_dir) / "Top50Filtered.csv"
            path.write_text("old", encoding="utf-8")
            with self.assertRaises(OSError):
                scanner._write_top50_csv(["000001.SZ"])
            self.assertEqual(path.read_text(encoding="utf-8"), "old")
            self.assertFalse((Path(temp_dir) / ".Top50Filtered.csv.tmp").exists())
''',
)

Path("research_integrity_v23_contract_patch.py").unlink(missing_ok=True)
print("v23 regression contracts aligned")
