from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
TARGET = ROOT / "test_regressions.py"
text = TARGET.read_text(encoding="utf-8")
old = '''        with TemporaryDirectory() as temp_dir, patch("gui_core.OUTPUT_DIR", Path(temp_dir)):
            path = Path(temp_dir) / "Top50Filtered.csv"
'''
new = '''        with TemporaryDirectory() as temp_dir, patch("gui.OUTPUT_DIR", Path(temp_dir)):
            path = Path(temp_dir) / "Top50Filtered.csv"
'''
if text.count(old) != 1:
    raise RuntimeError(f"expected one GUI success fixture anchor, got {text.count(old)}")
text = text.replace(old, new, 1)
old_failure = '''        with TemporaryDirectory() as temp_dir, patch("gui_core.OUTPUT_DIR", Path(temp_dir)), patch("gui_core.os.replace", side_effect=OSError("replace failed")):
            path = Path(temp_dir) / "Top50Filtered.csv"
'''
new_failure = '''        with TemporaryDirectory() as temp_dir, patch("gui.OUTPUT_DIR", Path(temp_dir)), patch("gui_core.os.replace", side_effect=OSError("replace failed")):
            path = Path(temp_dir) / "Top50Filtered.csv"
'''
if text.count(old_failure) != 1:
    raise RuntimeError(f"expected one GUI failure fixture anchor, got {text.count(old_failure)}")
text = text.replace(old_failure, new_failure, 1)
TARGET.write_text(text, encoding="utf-8")
Path("research_integrity_v23_gui_contract_patch.py").unlink(missing_ok=True)
print("v23 GUI contract fixture aligned")
