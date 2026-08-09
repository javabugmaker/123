from __future__ import annotations

import re
from pathlib import Path

GUI = Path("gui.py")
text = GUI.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global text
    if old not in text:
        raise SystemExit(f"expected block not found: {old[:140]!r}")
    text = text.replace(old, new, 1)


def replace_regex(pattern: str, replacement: str) -> None:
    global text
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise SystemExit(f"expected exactly one regex match: {pattern!r}; got {count}")
    text = updated


replace_once(
    '_core.DATA_SOURCE_HINTS["TickFlow Free"] = "日K/标的池：TickFlow Free；基本面：AkShare 低频缓存"',
    '_core.DATA_SOURCE_HINTS["TickFlow Free"] = "行情：TickFlow Free（日K / 标的池）"',
)

replace_regex(
    r'_core\.DISPLAY_COLUMNS = \(\n.*?\n\)\n\n_core\.COLUMN_NAMES\.update',
    '''_core.DISPLAY_COLUMNS = (\n'
    '    "OverallRank",\n'
    '    "Ticker",\n'
    '    "Name",\n'
    '    "AssetType",\n'
    '    "Industry",\n'
    '    "Close",\n'
    '    "EntrySignal",\n'
    '    "SignalStatus",\n'
    '    "SignalDays",\n'
    '    "EntryZone",\n'
    '    "BreakoutBuyPrice",\n'
    '    "StopLoss",\n'
    '    "RankingEligibility",\n'
    '    "RankingScore",\n'
    '    "InstitutionalTier",\n'
    '    "InstitutionalScore",\n'
    '    "TradeReadinessReason",\n'
    '    "DataAsOf",\n'
    ')\n\n_core.COLUMN_NAMES.update''',
)

replace_once(
    '        "Close": "当日收盘价",\n',
    '        "Close": "当日收盘价",\n'
    '        "EntrySignal": "当前买点",\n'
    '        "SignalStatus": "近期买点",\n'
    '        "SignalDays": "持续天数",\n'
    '        "RankingScore": "排序分",\n'
    '        "InstitutionalTier": "机构等级",\n'
    '        "InstitutionalScore": "机构分",\n',
)
replace_once(
    '        "EntrySignal": 108,\n',
    '        "EntrySignal": 112,\n'
    '        "SignalStatus": 82,\n'
    '        "SignalDays": 72,\n',
)

replace_regex(
    r'def _bool_text\(.*?\n(?=def _configure_filter_box\()',
    '''def _asset_label(indexes: dict[str, int], row: list[str]) -> str:\n'
    '    raw_asset = _value_for(indexes, row, "AssetType").strip().lower()\n'
    '    raw_is_etf = _value_for(indexes, row, "IsETF").strip().lower()\n'
    '    has_asset_evidence = bool(raw_asset or raw_is_etf)\n'
    '    is_etf = raw_asset == "etf" or raw_is_etf in {"true", "1", "yes", "y", "是"}\n'
    '    return "ETF" if is_etf else "股票" if has_asset_evidence else ""\n\n\n''',
)

replace_regex(
    r'def _build_ui_v16\(self\) -> None:.*?\n(?=def _update_filter_values_v16\()',
    '''def _build_ui_v16(self) -> None:\n'
    '    _original_build_ui(self)\n\n'
    '    # Keep the toolbar centered on actionable research lists.\n'
    '    actions = self.progress.master\n'
    '    for child in list(actions.winfo_children()):\n'
    '        try:\n'
    '            label = str(child.cget("text"))\n'
    '        except Exception:\n'
    '            continue\n'
    '        if label in {"风险榜", "市场概览", "连续信号"}:\n'
    '            child.destroy()\n'
    '        elif label == "生成前50名":\n'
    '            child.configure(text="综合榜", command=lambda: self.load_csv("Top50Mixed.csv"))\n'
    '        elif label == "交易就绪":\n'
    '            child.configure(text="强推荐")\n\n'
    '    _core.ttk.Button(\n'
    '        actions, text="股票榜", style="Quiet.TButton",\n'
    '        command=lambda: self.load_csv("Top50Stocks.csv"),\n'
    '    ).pack(side=_core.tk.LEFT, padx=(0, 6))\n'
    '    _core.ttk.Button(\n'
    '        actions, text="ETF榜", style="Quiet.TButton",\n'
    '        command=lambda: self.load_csv("Top50ETF.csv"),\n'
    '    ).pack(side=_core.tk.LEFT, padx=(0, 6))\n\n'
    '    # Only keep filters that change an actual trading decision.\n'
    '    if not hasattr(self, "asset_filter"):\n'
    '        self.asset_filter = _core.tk.StringVar(value="全部类型")\n'
    '        self.tier_filter = _core.tk.StringVar(value="全部等级")\n'
    '        self.score_filter = _core.tk.StringVar(value="全部分数")\n'
    '        for variable in (self.asset_filter, self.tier_filter, self.score_filter):\n'
    '            variable.trace_add("write", self._schedule_filter_refresh)\n\n'
    '    filters = self.sector_box.master\n'
    '    for child in filters.winfo_children():\n'
    '        child.destroy()\n'
    '    for column in range(12):\n'
    '        filters.columnconfigure(column, weight=0)\n'
    '    filters.columnconfigure(7, weight=1)\n\n'
    '    ttk = _core.ttk\n'
    '    tk = _core.tk\n\n'
    '    ttk.Label(filters, text="类型").grid(row=0, column=0, padx=(0, 4), sticky=tk.W)\n'
    '    self.asset_box = ttk.Combobox(\n'
    '        filters, textvariable=self.asset_filter,\n'
    '        values=("全部类型", "股票", "ETF"), state="readonly", width=9,\n'
    '    )\n'
    '    self.asset_box.grid(row=0, column=1, padx=(0, 10), sticky=tk.W)\n\n'
    '    ttk.Label(filters, text="行业").grid(row=0, column=2, padx=(0, 4), sticky=tk.W)\n'
    '    self.industry_box = ttk.Combobox(\n'
    '        filters, textvariable=self.industry_filter, state="readonly", width=14\n'
    '    )\n'
    '    self.industry_box.grid(row=0, column=3, padx=(0, 10), sticky=tk.W)\n\n'
    '    ttk.Label(filters, text="买点").grid(row=0, column=4, padx=(0, 4), sticky=tk.W)\n'
    '    self.entry_box = ttk.Combobox(\n'
    '        filters, textvariable=self.entry_filter, state="readonly", width=15\n'
    '    )\n'
    '    self.entry_box.grid(row=0, column=5, padx=(0, 10), sticky=tk.W)\n\n'
    '    ttk.Label(filters, text="资格").grid(row=0, column=6, padx=(0, 4), sticky=tk.W)\n'
    '    self.eligibility_box = ttk.Combobox(\n'
    '        filters, textvariable=self.eligibility_filter,\n'
    '        values=("全部资格", "推荐", "谨慎候选", "观察", "风险过滤"),\n'
    '        state="readonly", width=9,\n'
    '    )\n'
    '    self.eligibility_box.grid(row=0, column=7, padx=(0, 10), sticky=tk.W)\n\n'
    '    ttk.Label(filters, text="板块").grid(\n'
    '        row=1, column=0, padx=(0, 4), pady=(8, 0), sticky=tk.W\n'
    '    )\n'
    '    self.sector_box = ttk.Combobox(\n'
    '        filters, textvariable=self.sector_filter, state="readonly", width=12\n'
    '    )\n'
    '    self.sector_box.grid(row=1, column=1, padx=(0, 10), pady=(8, 0), sticky=tk.W)\n'
    '    self.sector_box.bind("<<ComboboxSelected>>", self._sector_changed)\n\n'
    '    ttk.Label(filters, text="资金阶段").grid(\n'
    '        row=1, column=2, padx=(0, 4), pady=(8, 0), sticky=tk.W\n'
    '    )\n'
    '    self.stage_box = ttk.Combobox(\n'
    '        filters, textvariable=self.stage_filter, state="readonly", width=12\n'
    '    )\n'
    '    self.stage_box.grid(row=1, column=3, padx=(0, 10), pady=(8, 0), sticky=tk.W)\n\n'
    '    ttk.Label(filters, text="机构等级").grid(\n'
    '        row=1, column=4, padx=(0, 4), pady=(8, 0), sticky=tk.W\n'
    '    )\n'
    '    self.tier_box = ttk.Combobox(\n'
    '        filters, textvariable=self.tier_filter, state="readonly", width=12\n'
    '    )\n'
    '    self.tier_box.grid(row=1, column=5, padx=(0, 10), pady=(8, 0), sticky=tk.W)\n\n'
    '    ttk.Label(filters, text="最低分").grid(\n'
    '        row=1, column=6, padx=(0, 4), pady=(8, 0), sticky=tk.W\n'
    '    )\n'
    '    self.score_box = ttk.Combobox(\n'
    '        filters, textvariable=self.score_filter,\n'
    '        values=("全部分数", "≥25", "≥30", "≥35", "≥40", "≥50"),\n'
    '        state="readonly", width=9,\n'
    '    )\n'
    '    self.score_box.grid(row=1, column=7, padx=(0, 10), pady=(8, 0), sticky=tk.W)\n\n'
    '    ttk.Label(filters, text="搜索").grid(\n'
    '        row=1, column=8, padx=(0, 4), pady=(8, 0), sticky=tk.W\n'
    '    )\n'
    '    self.search_entry = ttk.Entry(filters, textvariable=self.search, width=24)\n'
    '    self.search_entry.grid(\n'
    '        row=1, column=9, padx=(0, 8), pady=(8, 0), sticky=tk.EW\n'
    '    )\n'
    '    ttk.Button(filters, text="重置", command=self.clear_filters).grid(\n'
    '        row=1, column=10, padx=(0, 6), pady=(8, 0), sticky=tk.W\n'
    '    )\n'
    '    ttk.Button(filters, text="刷新", command=self.refresh_results).grid(\n'
    '        row=1, column=11, padx=(0, 6), pady=(8, 0), sticky=tk.W\n'
    '    )\n\n\n''',
)

replace_regex(
    r'def _update_filter_values_v16\(.*?\n(?=def _row_matches_filters_v16\()',
    '''def _update_filter_values_v16(self, headers: list[str], rows: list[list[str]]) -> None:\n'
    '    _original_update_filter_values(self, headers, rows)\n'
    '    indexes = {header: index for index, header in enumerate(headers)}\n\n'
    '    asset_values: set[str] = set()\n'
    '    if "AssetType" in indexes or "IsETF" in indexes:\n'
    '        for row in rows:\n'
    '            asset = _asset_label(indexes, row)\n'
    '            if asset:\n'
    '                asset_values.add(asset)\n'
    '    _configure_filter_box(\n'
    '        self.asset_box, self.asset_filter, "全部类型",\n'
    '        [value for value in ("股票", "ETF") if value in asset_values],\n'
    '        bool(asset_values),\n'
    '    )\n\n'
    '    tiers = []\n'
    '    if "InstitutionalTier" in indexes:\n'
    '        tier_index = indexes["InstitutionalTier"]\n'
    '        tiers = sorted({\n'
    '            self._cell_text(row[tier_index])\n'
    '            for row in rows\n'
    '            if len(row) > tier_index and self._cell_text(row[tier_index])\n'
    '        })\n'
    '    _configure_filter_box(\n'
    '        self.tier_box, self.tier_filter, "全部等级", tiers, bool(tiers)\n'
    '    )\n\n'
    '    score_enabled = any(\n'
    '        column in indexes\n'
    '        for column in ("RankingScore", "InstitutionalScore", "FinalScore", "Score")\n'
    '    )\n'
    '    _configure_filter_box(\n'
    '        self.score_box, self.score_filter, "全部分数",\n'
    '        ["≥25", "≥30", "≥35", "≥40", "≥50"] if score_enabled else [],\n'
    '        score_enabled,\n'
    '    )\n\n\n''',
)

replace_regex(
    r'def _row_matches_filters_v16\(.*?\n(?=def _clear_filters_v16\()',
    '''def _row_matches_filters_v16(\n'
    '    self,\n'
    '    indexes: dict[str, int],\n'
    '    row: list[str],\n'
    '    query: str,\n'
    '    search_text: str | None = None,\n'
    '    filter_values: Sequence[str] | None = None,\n'
    ') -> bool:\n'
    '    # Core owns search/sector/industry/legacy-quality/stage/entry/eligibility.\n'
    '    legacy_values = tuple(filter_values[:6]) if filter_values is not None else None\n'
    '    if not _original_row_matches_filters(\n'
    '        self, indexes, row, query, search_text, legacy_values\n'
    '    ):\n'
    '        return False\n\n'
    '    if filter_values is not None and len(filter_values) >= 9:\n'
    '        asset_value, tier_value, score_value = filter_values[6:9]\n'
    '    else:\n'
    '        asset_value = _read_filter(self, "asset_filter", "全部类型")\n'
    '        tier_value = _read_filter(self, "tier_filter", "全部等级")\n'
    '        score_value = _read_filter(self, "score_filter", "全部分数")\n\n'
    '    padded = (\n'
    '        row if len(row) >= len(self._csv_headers)\n'
    '        else row + [""] * (len(self._csv_headers) - len(row))\n'
    '    )\n'
    '    asset_label = _asset_label(indexes, padded)\n\n'
    '    if asset_value != "全部类型" and asset_label != asset_value:\n'
    '        return False\n'
    '    if (\n'
    '        tier_value != "全部等级"\n'
    '        and _value_for(indexes, padded, "InstitutionalTier") != tier_value\n'
    '    ):\n'
    '        return False\n\n'
    '    if score_value != "全部分数":\n'
    '        threshold_match = re.search(r"([0-9]+(?:\\.[0-9]+)?)", score_value)\n'
    '        if threshold_match is None:\n'
    '            return False\n'
    '        threshold = float(threshold_match.group(1))\n'
    '        ranking_value = None\n'
    '        for column in ("RankingScore", "InstitutionalScore", "FinalScore", "Score"):\n'
    '            ranking_value = self._numeric_value(_value_for(indexes, padded, column))\n'
    '            if ranking_value is not None:\n'
    '                break\n'
    '        if ranking_value is None or ranking_value < threshold:\n'
    '            return False\n'
    '    return True\n\n\n''',
)

replace_regex(
    r'def _clear_filters_v16\(self\) -> None:.*?\n(?=def _format_table_value_v16\()',
    '''def _clear_filters_v16(self) -> None:\n'
    '    for attribute, default in (\n'
    '        ("asset_filter", "全部类型"),\n'
    '        ("tier_filter", "全部等级"),\n'
    '        ("score_filter", "全部分数"),\n'
    '    ):\n'
    '        variable = getattr(self, attribute, None)\n'
    '        if variable is not None:\n'
    '            variable.set(default)\n'
    '    _original_clear_filters(self)\n\n\n''',
)

replace_regex(
    r'def _format_table_value_v16\(self, column: str, value: str\) -> str:.*?\n\n\n\n\n# ---------------------------------------------------------------------------\n# Decision-focused overview / summary',
    '''def _format_table_value_v16(self, column: str, value: str) -> str:\n'
    '    text = self._cell_text(value)\n'
    '    if column == "SignalStatus":\n'
    '        if self._is_missing_text(text):\n'
    '            return "-"\n'
    '        status = text.strip().upper()\n'
    '        return {\n'
    '            "NEW": "新出现",\n'
    '            "ACTIVE": "持续有效",\n'
    '            "CONFIRMED": "持续有效",\n'
    '            "FAILED": "已失效",\n'
    '            "EXPIRED": "已失效",\n'
    '            "INACTIVE": "已结束",\n'
    '        }.get(status, text)\n'
    '    return _original_format_table_value(self, column, value)\n\n\n# ---------------------------------------------------------------------------\n# Decision-focused overview / summary''',
)

GUI.write_text(text, encoding="utf-8")

TEST = Path("test_gui_clean_v25.py")
TEST.write_text(
    '''from __future__ import annotations\n\n'
    'import inspect\n'
    'import unittest\n\n'
    'import gui\n\n\n'
    'class GuiCleanV25Tests(unittest.TestCase):\n'
    '    def test_main_table_replaces_research_diagnostics_with_recent_entry_state(self):\n'
    '        self.assertIn("SignalStatus", gui.DISPLAY_COLUMNS)\n'
    '        self.assertIn("SignalDays", gui.DISPLAY_COLUMNS)\n'
    '        self.assertNotIn("QualityGate", gui.DISPLAY_COLUMNS)\n'
    '        self.assertNotIn("BacktestConfidenceTier", gui.DISPLAY_COLUMNS)\n'
    '        self.assertNotIn("PassedFilters", gui.DISPLAY_COLUMNS)\n'
    '        self.assertNotIn("FinalScore", gui.DISPLAY_COLUMNS)\n'
    '        self.assertNotIn("RankingReason", gui.DISPLAY_COLUMNS)\n'
    '        self.assertEqual(gui.COLUMN_NAMES["EntrySignal"], "当前买点")\n'
    '        self.assertEqual(gui.COLUMN_NAMES["SignalStatus"], "近期买点")\n'
    '        self.assertEqual(gui.COLUMN_NAMES["SignalDays"], "持续天数")\n\n'
    '    def test_filter_bar_drops_fundamental_and_backtest_controls(self):\n'
    '        source = inspect.getsource(gui._build_ui_v16)\n'
    '        self.assertNotIn("fundamental_filter", source)\n'
    '        self.assertNotIn("backtest_filter", source)\n'
    '        self.assertNotIn("fundamental_box", source)\n'
    '        self.assertNotIn("backtest_box", source)\n'
    '        self.assertNotIn("回测可信度", source)\n'
    '        self.assertIn("最低分", source)\n'
    '        self.assertIn("重置", source)\n'
    '        self.assertIn("刷新", source)\n\n'
    '    def test_recent_entry_status_is_human_readable(self):\n'
    '        instance = gui.DecisionScannerGUI.__new__(gui.DecisionScannerGUI)\n'
    '        self.assertEqual(gui._format_table_value_v16(instance, "SignalStatus", "NEW"), "新出现")\n'
    '        self.assertEqual(gui._format_table_value_v16(instance, "SignalStatus", "ACTIVE"), "持续有效")\n'
    '        self.assertEqual(gui._format_table_value_v16(instance, "SignalStatus", "FAILED"), "已失效")\n\n\n'
    'if __name__ == "__main__":\n'
    '    unittest.main()\n'''.replace("'\n    '", ""),
    encoding="utf-8",
)

# The migration mechanism is intentionally one-shot and must not land in main.
Path("scripts/apply_gui_clean_v25.py").unlink(missing_ok=True)
Path(".github/workflows/apply_gui_clean_v25.yml").unlink(missing_ok=True)
