from __future__ import annotations

from pathlib import Path

PATH = Path("gui.py")
text = PATH.read_text(encoding="utf-8")


def replace_once(old: str, new: str, label: str) -> None:
    global text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    text = text.replace(old, new, 1)


replace_once(
    '    "InstitutionalStrength",\n)\n',
    '    "InstitutionalStrength",\n    "TradeReadinessReason",\n)\n',
    "restore public display compatibility",
)

replace_once(
    '        fg_color="#244864",\n        corner_radius=8,\n        padx=12,\n        pady=6,\n        text_color="#e6f2ff",\n',
    '        fg_color="#244864",\n        corner_radius=8,\n        height=32,\n        text_color="#e6f2ff",\n',
    "remove unsupported header label padding",
)

replace_once(
    '        text_color="#1d4ed8",\n        font=("Microsoft YaHei UI", 12, "bold"),\n        padx=12,\n        pady=8,\n    )\n',
    '        text_color="#1d4ed8",\n        font=("Microsoft YaHei UI", 12, "bold"),\n        height=36,\n    )\n',
    "remove unsupported signal label padding",
)

replace_once(
    '    self._update_dashboard_cards()\n    self._reset_decision_card_if_needed()\n    return True\n',
    '    if hasattr(self, "card_recommended"):\n        self._update_dashboard_cards()\n    if hasattr(self, "detail_title") and hasattr(self, "table"):\n        self._reset_decision_card_if_needed()\n    return True\n',
    "guard v26-only dashboard state",
)

replace_once(
    '    def _quality_tag(self, quality: str) -> str:\n        return ""\n\n    def _entry_tag(self, signal: str) -> str:\n        return ""\n',
    '    def _quality_tag(self, quality: str) -> str:\n        # Preserve the historical method contract.  v26 intentionally does\n        # not configure these tags, so eligibility remains the only row color.\n        return _core.ScannerGUI._quality_tag(self, quality)\n\n    def _entry_tag(self, signal: str) -> str:\n        return _core.ScannerGUI._entry_tag(self, signal)\n',
    "restore tag method compatibility",
)

if text.count('"eligibility-risk"') != 2:
    raise RuntimeError(f"risk tag: expected two matches, found {text.count(chr(34) + 'eligibility-risk' + chr(34))}")
text = text.replace('"eligibility-risk"', '"risk-filter"')

replace_once(
    '        columns = list(_core.DISPLAY_COLUMNS)\n        if filename in {"Top50Stocks.csv", "Top50ETF.csv"} and "AssetType" in columns:\n',
    '        columns = list(_core.DISPLAY_COLUMNS)\n        # Keep TradeReadinessReason in the public compatibility contract, but\n        # move the long explanation out of the real table into the decision card.\n        if "TradeReadinessReason" in columns:\n            columns.remove("TradeReadinessReason")\n        if filename in {"Top50Stocks.csv", "Top50ETF.csv"} and "AssetType" in columns:\n',
    "hide long explanation from real table",
)

replace_once(
    '        if not loaded:\n            return False\n        self._ensure_derived_columns()\n',
    '        if not loaded:\n            return False\n        # Older callers/tests intentionally construct the GUI without __init__.\n        # In that compatibility path the core load/render is already complete.\n        if not hasattr(self, "view_title"):\n            return loaded\n        self._ensure_derived_columns()\n',
    "guard derived v26 render",
)

replace_once(
    '        scope = self.backtest_scope.get()\n',
    '        scope_var = getattr(self, "backtest_scope", None)\n        scope = scope_var.get() if scope_var is not None else "当前筛选"\n',
    "backtest scope compatibility fallback",
)

replace_once(
    '        self.backtest_button.configure(state=_core.tk.DISABLED, text="回测运行中")\n        try:\n            _core.ScannerGUI.start_backtest(self)\n        finally:\n            self.filtered_tickers = previous\n        if not self.scan_running:\n            self.backtest_button.configure(state=_core.tk.NORMAL, text="▶ 运行回测")\n',
    '        backtest_button = getattr(self, "backtest_button", None)\n        if backtest_button is not None:\n            backtest_button.configure(state=_core.tk.DISABLED, text="回测运行中")\n        try:\n            _core.ScannerGUI.start_backtest(self)\n        finally:\n            self.filtered_tickers = previous\n        if not self.scan_running and backtest_button is not None:\n            backtest_button.configure(state=_core.tk.NORMAL, text="▶ 运行回测")\n',
    "backtest button compatibility guard",
)

PATH.write_text(text, encoding="utf-8")
print("v26 compatibility fixes applied")
