from pathlib import Path

path = Path(__file__).resolve().parents[1] / "gui.py"
text = path.read_text(encoding="utf-8")

anchor = "import gui_core as _core\n\n"
if anchor not in text:
    raise RuntimeError("gui core import anchor missing")
text = text.replace(
    anchor,
    "import gui_core as _core\n\n# Compatibility alias: external callers historically patched gui.OUTPUT_DIR.\nOUTPUT_DIR = _core.OUTPUT_DIR\n\n",
    1,
)

class_anchor = '''class DecisionScannerGUI(_core.ScannerGUI):
    """Decision-oriented GUI implemented through normal inheritance."""

'''
if class_anchor not in text:
    raise RuntimeError("DecisionScannerGUI anchor missing")
compat_methods = '''class DecisionScannerGUI(_core.ScannerGUI):
    """Decision-oriented GUI implemented through normal inheritance."""

    def _call_core_with_legacy_output_dir(self, method, *args, **kwargs):
        previous = _core.OUTPUT_DIR
        _core.OUTPUT_DIR = OUTPUT_DIR
        try:
            return method(self, *args, **kwargs)
        finally:
            _core.OUTPUT_DIR = previous

    def load_csv(self, filename: str) -> bool:
        return self._call_core_with_legacy_output_dir(_core.ScannerGUI.load_csv, filename)

    def _csv_has_results(self, filename: str) -> bool:
        return self._call_core_with_legacy_output_dir(
            _core.ScannerGUI._csv_has_results, filename
        )

    def _load_best_available_results(self) -> bool:
        return self._call_core_with_legacy_output_dir(
            _core.ScannerGUI._load_best_available_results
        )

    def _write_top50_csv(self, tickers: list[str]) -> None:
        self._call_core_with_legacy_output_dir(
            _core.ScannerGUI._write_top50_csv, tickers
        )

'''
text = text.replace(class_anchor, compat_methods, 1)
path.write_text(text, encoding="utf-8")
print("model v2 GUI compatibility hotfix applied")
