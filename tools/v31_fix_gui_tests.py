from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_required(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new), encoding="utf-8")
        return
    if new not in text:
        raise RuntimeError(f"expected test pattern not found in {path}: {old!r}")


# v25 tests predate the modular DecisionScannerGUI builder.  Verify the real
# filter helper and public formatter instead of deleted v16 monkey-patch names.
path = ROOT / "test_gui_clean_v25.py"
replace_required(
    path,
    "source = inspect.getsource(gui._build_ui_v16)",
    "source = inspect.getsource(gui.DecisionScannerGUI._build_ui_filters)",
)
replace_required(
    path,
    'gui._format_table_value_v16(instance, "SignalStatus", "NEW")',
    'instance._format_table_value("SignalStatus", "NEW")',
)
replace_required(
    path,
    'gui._format_table_value_v16(instance, "SignalStatus", "ACTIVE")',
    'instance._format_table_value("SignalStatus", "ACTIVE")',
)
replace_required(
    path,
    'gui._format_table_value_v16(instance, "SignalStatus", "FAILED")',
    'instance._format_table_value("SignalStatus", "FAILED")',
)


# v26/v27 source-inspection tests should inspect the helper that owns each UI
# surface after v30 split the former ~600-line _build_ui method.
path = ROOT / "test_gui_workstation_v26.py"
text = path.read_text(encoding="utf-8")
text = text.replace(
    "source = inspect.getsource(gui.DecisionScannerGUI._build_ui)\n        self.assertIn(\"综合 Top50\", source)",
    "source = inspect.getsource(gui.DecisionScannerGUI._build_ui_navigation)\n        self.assertIn(\"综合 Top50\", source)",
    1,
)
text = text.replace(
    "source = inspect.getsource(gui.DecisionScannerGUI._build_ui)\n        self.assertIn(\"▶ 开始扫描\", source)",
    "source = inspect.getsource(gui.DecisionScannerGUI._build_ui_controls)\n        self.assertIn(\"▶ 开始扫描\", source)",
    1,
)
text = text.replace(
    "source = inspect.getsource(gui.DecisionScannerGUI._build_ui)\n        for label in (",
    "source = inspect.getsource(gui.DecisionScannerGUI._build_ui_footer)\n        for label in (",
    1,
)
old = '''        source = inspect.getsource(gui.DecisionScannerGUI._build_ui)\n        self.assertIn("高级设置", source)\n        self.assertIn("更多筛选", source)\n        self.assertIn("日志 ›", source)\n        self.assertIn("扫描完成后回测强推荐", source)\n'''
new = '''        source = "\\n".join(\n            inspect.getsource(method)\n            for method in (\n                gui.DecisionScannerGUI._build_ui_controls,\n                gui.DecisionScannerGUI._build_ui_filters,\n                gui.DecisionScannerGUI._build_ui_footer,\n            )\n        )\n        self.assertIn("高级设置", source)\n        self.assertIn("更多筛选", source)\n        self.assertIn("日志 ›", source)\n        self.assertIn("扫描完成后回测强推荐", source)\n'''
if old in text:
    text = text.replace(old, new, 1)
elif new not in text:
    raise RuntimeError("v26 collapsible UI test pattern not found")
path.write_text(text, encoding="utf-8")

path = ROOT / "test_v27_gui_pipeline.py"
replace_required(
    path,
    "source = inspect.getsource(gui.DecisionScannerGUI._build_ui)",
    "source = inspect.getsource(gui.DecisionScannerGUI._build_ui_controls)",
)


# gui.py intentionally delegates Tk/threading/messagebox to gui_core.  Patch
# the actual owner in regression tests instead of stale module-level aliases.
path = ROOT / "test_regressions.py"
text = path.read_text(encoding="utf-8")
text = text.replace('"gui.tk.', '"gui._core.tk.')
text = text.replace('"gui.messagebox.', '"gui._core.messagebox.')
text = text.replace('"gui.threading.', '"gui._core.threading.')
text = text.replace("gui.tk.", "gui._core.tk.")
path.write_text(text, encoding="utf-8")

print("legacy GUI tests aligned with modular workstation")
