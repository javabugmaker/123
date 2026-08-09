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
    updated, count = re.subn(
        pattern,
        lambda _match: replacement,
        text,
        count=1,
        flags=re.S,
    )
    if count != 1:
        raise SystemExit(f"expected exactly one regex match: {pattern!r}; got {count}")
    text = updated


replace_once(
    '_core.DATA_SOURCE_HINTS["TickFlow Free"] = "日K/标的池：TickFlow Free；基本面：AkShare 低频缓存"',
    '_core.DATA_SOURCE_HINTS["TickFlow Free"] = "行情：TickFlow Free（日K / 标的池）"',
)

replace_regex(
    r'_core\.DISPLAY_COLUMNS = \(\n.*?\n\)\n\n_core\.COLUMN_NAMES\.update',
    '''_core.DISPLAY_COLUMNS = (
    "OverallRank",
    "Ticker",
    "Name",
    "AssetType",
    "Industry",
    "Close",
    "EntrySignal",
    "SignalStatus",
    "SignalDays",
    "EntryZone",
    "BreakoutBuyPrice",
    "StopLoss",
    "RankingEligibility",
    "RankingScore",
    "InstitutionalTier",
    "InstitutionalScore",
    "TradeReadinessReason",
    "DataAsOf",
)

_core.COLUMN_NAMES.update''',
)

replace_once(
    '        "Close": "当日收盘价",\n',
    '''        "Close": "当日收盘价",
        "EntrySignal": "当前买点",
        "SignalStatus": "近期买点",
        "SignalDays": "持续天数",
        "RankingScore": "排序分",
        "InstitutionalTier": "机构等级",
        "InstitutionalScore": "机构分",
''',
)
replace_once(
    '        "EntrySignal": 108,\n',
    '''        "EntrySignal": 112,
        "SignalStatus": 82,
        "SignalDays": 72,
''',
)

replace_regex(
    r'def _bool_text\(.*?\n(?=def _configure_filter_box\()',
    '''def _asset_label(indexes: dict[str, int], row: list[str]) -> str:
    raw_asset = _value_for(indexes, row, "AssetType").strip().lower()
    raw_is_etf = _value_for(indexes, row, "IsETF").strip().lower()
    has_asset_evidence = bool(raw_asset or raw_is_etf)
    is_etf = raw_asset == "etf" or raw_is_etf in {"true", "1", "yes", "y", "是"}
    return "ETF" if is_etf else "股票" if has_asset_evidence else ""


''',
)

replace_regex(
    r'def _build_ui_v16\(self\) -> None:.*?\n(?=def _update_filter_values_v16\()',
    '''def _build_ui_v16(self) -> None:
    _original_build_ui(self)

    # Keep the toolbar centered on actionable research lists.
    actions = self.progress.master
    for child in list(actions.winfo_children()):
        try:
            label = str(child.cget("text"))
        except Exception:
            continue
        if label in {"风险榜", "市场概览", "连续信号"}:
            child.destroy()
        elif label == "生成前50名":
            child.configure(text="综合榜", command=lambda: self.load_csv("Top50Mixed.csv"))
        elif label == "交易就绪":
            child.configure(text="强推荐")

    _core.ttk.Button(
        actions,
        text="股票榜",
        style="Quiet.TButton",
        command=lambda: self.load_csv("Top50Stocks.csv"),
    ).pack(side=_core.tk.LEFT, padx=(0, 6))
    _core.ttk.Button(
        actions,
        text="ETF榜",
        style="Quiet.TButton",
        command=lambda: self.load_csv("Top50ETF.csv"),
    ).pack(side=_core.tk.LEFT, padx=(0, 6))

    # Only keep filters that change an actual trading decision.
    if not hasattr(self, "asset_filter"):
        self.asset_filter = _core.tk.StringVar(value="全部类型")
        self.tier_filter = _core.tk.StringVar(value="全部等级")
        self.score_filter = _core.tk.StringVar(value="全部分数")
        for variable in (self.asset_filter, self.tier_filter, self.score_filter):
            variable.trace_add("write", self._schedule_filter_refresh)

    filters = self.sector_box.master
    for child in filters.winfo_children():
        child.destroy()
    for column in range(12):
        filters.columnconfigure(column, weight=0)
    filters.columnconfigure(9, weight=1)

    ttk = _core.ttk
    tk = _core.tk

    ttk.Label(filters, text="类型").grid(row=0, column=0, padx=(0, 4), sticky=tk.W)
    self.asset_box = ttk.Combobox(
        filters,
        textvariable=self.asset_filter,
        values=("全部类型", "股票", "ETF"),
        state="readonly",
        width=9,
    )
    self.asset_box.grid(row=0, column=1, padx=(0, 10), sticky=tk.W)

    ttk.Label(filters, text="行业").grid(row=0, column=2, padx=(0, 4), sticky=tk.W)
    self.industry_box = ttk.Combobox(
        filters,
        textvariable=self.industry_filter,
        state="readonly",
        width=14,
    )
    self.industry_box.grid(row=0, column=3, padx=(0, 10), sticky=tk.W)

    ttk.Label(filters, text="买点").grid(row=0, column=4, padx=(0, 4), sticky=tk.W)
    self.entry_box = ttk.Combobox(
        filters,
        textvariable=self.entry_filter,
        state="readonly",
        width=15,
    )
    self.entry_box.grid(row=0, column=5, padx=(0, 10), sticky=tk.W)

    ttk.Label(filters, text="资格").grid(row=0, column=6, padx=(0, 4), sticky=tk.W)
    self.eligibility_box = ttk.Combobox(
        filters,
        textvariable=self.eligibility_filter,
        values=("全部资格", "推荐", "谨慎候选", "观察", "风险过滤"),
        state="readonly",
        width=9,
    )
    self.eligibility_box.grid(row=0, column=7, padx=(0, 10), sticky=tk.W)

    ttk.Label(filters, text="板块").grid(
        row=1, column=0, padx=(0, 4), pady=(8, 0), sticky=tk.W
    )
    self.sector_box = ttk.Combobox(
        filters,
        textvariable=self.sector_filter,
        state="readonly",
        width=12,
    )
    self.sector_box.grid(row=1, column=1, padx=(0, 10), pady=(8, 0), sticky=tk.W)
    self.sector_box.bind("<<ComboboxSelected>>", self._sector_changed)

    ttk.Label(filters, text="资金阶段").grid(
        row=1, column=2, padx=(0, 4), pady=(8, 0), sticky=tk.W
    )
    self.stage_box = ttk.Combobox(
        filters,
        textvariable=self.stage_filter,
        state="readonly",
        width=12,
    )
    self.stage_box.grid(row=1, column=3, padx=(0, 10), pady=(8, 0), sticky=tk.W)

    ttk.Label(filters, text="机构等级").grid(
        row=1, column=4, padx=(0, 4), pady=(8, 0), sticky=tk.W
    )
    self.tier_box = ttk.Combobox(
        filters,
        textvariable=self.tier_filter,
        state="readonly",
        width=12,
    )
    self.tier_box.grid(row=1, column=5, padx=(0, 10), pady=(8, 0), sticky=tk.W)

    ttk.Label(filters, text="最低分").grid(
        row=1, column=6, padx=(0, 4), pady=(8, 0), sticky=tk.W
    )
    self.score_box = ttk.Combobox(
        filters,
        textvariable=self.score_filter,
        values=("全部分数", "≥25", "≥30", "≥35", "≥40", "≥50"),
        state="readonly",
        width=9,
    )
    self.score_box.grid(row=1, column=7, padx=(0, 10), pady=(8, 0), sticky=tk.W)

    ttk.Label(filters, text="搜索").grid(
        row=1, column=8, padx=(0, 4), pady=(8, 0), sticky=tk.W
    )
    self.search_entry = ttk.Entry(filters, textvariable=self.search, width=24)
    self.search_entry.grid(
        row=1, column=9, padx=(0, 8), pady=(8, 0), sticky=tk.EW
    )
    ttk.Button(filters, text="重置", command=self.clear_filters).grid(
        row=1, column=10, padx=(0, 6), pady=(8, 0), sticky=tk.W
    )
    ttk.Button(filters, text="刷新", command=self.refresh_results).grid(
        row=1, column=11, padx=(0, 6), pady=(8, 0), sticky=tk.W
    )


''',
)

replace_regex(
    r'def _update_filter_values_v16\(.*?\n(?=def _row_matches_filters_v16\()',
    '''def _update_filter_values_v16(self, headers: list[str], rows: list[list[str]]) -> None:
    _original_update_filter_values(self, headers, rows)
    indexes = {header: index for index, header in enumerate(headers)}

    asset_values: set[str] = set()
    if "AssetType" in indexes or "IsETF" in indexes:
        for row in rows:
            asset = _asset_label(indexes, row)
            if asset:
                asset_values.add(asset)
    _configure_filter_box(
        self.asset_box,
        self.asset_filter,
        "全部类型",
        [value for value in ("股票", "ETF") if value in asset_values],
        bool(asset_values),
    )

    tiers = []
    if "InstitutionalTier" in indexes:
        tier_index = indexes["InstitutionalTier"]
        tiers = sorted(
            {
                self._cell_text(row[tier_index])
                for row in rows
                if len(row) > tier_index and self._cell_text(row[tier_index])
            }
        )
    _configure_filter_box(
        self.tier_box,
        self.tier_filter,
        "全部等级",
        tiers,
        bool(tiers),
    )

    score_enabled = any(
        column in indexes
        for column in ("RankingScore", "InstitutionalScore", "FinalScore", "Score")
    )
    _configure_filter_box(
        self.score_box,
        self.score_filter,
        "全部分数",
        ["≥25", "≥30", "≥35", "≥40", "≥50"] if score_enabled else [],
        score_enabled,
    )


''',
)

replace_regex(
    r'def _row_matches_filters_v16\(.*?\n(?=def _clear_filters_v16\()',
    '''def _row_matches_filters_v16(
    self,
    indexes: dict[str, int],
    row: list[str],
    query: str,
    search_text: str | None = None,
    filter_values: Sequence[str] | None = None,
) -> bool:
    # Core owns search/sector/industry/legacy-quality/stage/entry/eligibility.
    legacy_values = tuple(filter_values[:6]) if filter_values is not None else None
    if not _original_row_matches_filters(
        self,
        indexes,
        row,
        query,
        search_text,
        legacy_values,
    ):
        return False

    if filter_values is not None and len(filter_values) >= 9:
        asset_value, tier_value, score_value = filter_values[6:9]
    else:
        asset_value = _read_filter(self, "asset_filter", "全部类型")
        tier_value = _read_filter(self, "tier_filter", "全部等级")
        score_value = _read_filter(self, "score_filter", "全部分数")

    padded = (
        row
        if len(row) >= len(self._csv_headers)
        else row + [""] * (len(self._csv_headers) - len(row))
    )
    asset_label = _asset_label(indexes, padded)

    if asset_value != "全部类型" and asset_label != asset_value:
        return False
    if (
        tier_value != "全部等级"
        and _value_for(indexes, padded, "InstitutionalTier") != tier_value
    ):
        return False

    if score_value != "全部分数":
        threshold_match = re.search(r"([0-9]+(?:\.[0-9]+)?)", score_value)
        if threshold_match is None:
            return False
        threshold = float(threshold_match.group(1))
        ranking_value = None
        for column in ("RankingScore", "InstitutionalScore", "FinalScore", "Score"):
            ranking_value = self._numeric_value(_value_for(indexes, padded, column))
            if ranking_value is not None:
                break
        if ranking_value is None or ranking_value < threshold:
            return False
    return True


''',
)

replace_regex(
    r'def _clear_filters_v16\(self\) -> None:.*?\n(?=def _format_table_value_v16\()',
    '''def _clear_filters_v16(self) -> None:
    for attribute, default in (
        ("asset_filter", "全部类型"),
        ("tier_filter", "全部等级"),
        ("score_filter", "全部分数"),
    ):
        variable = getattr(self, attribute, None)
        if variable is not None:
            variable.set(default)
    _original_clear_filters(self)


''',
)

replace_regex(
    r'def _format_table_value_v16\(self, column: str, value: str\) -> str:.*?(?=\n# ---------------------------------------------------------------------------\n# Decision-focused overview / summary)',
    '''def _format_table_value_v16(self, column: str, value: str) -> str:
    text = self._cell_text(value)
    if column == "SignalStatus":
        if self._is_missing_text(text):
            return "-"
        status = text.strip().upper()
        return {
            "NEW": "新出现",
            "ACTIVE": "持续有效",
            "CONFIRMED": "持续有效",
            "FAILED": "已失效",
            "EXPIRED": "已失效",
            "INACTIVE": "已结束",
        }.get(status, text)
    return _original_format_table_value(self, column, value)

''',
)

GUI.write_text(text, encoding="utf-8")

Path("test_gui_clean_v25.py").write_text(
    '''from __future__ import annotations

import inspect
import unittest

import gui


class GuiCleanV25Tests(unittest.TestCase):
    def test_main_table_replaces_research_diagnostics_with_recent_entry_state(self):
        self.assertIn("SignalStatus", gui.DISPLAY_COLUMNS)
        self.assertIn("SignalDays", gui.DISPLAY_COLUMNS)
        self.assertNotIn("QualityGate", gui.DISPLAY_COLUMNS)
        self.assertNotIn("BacktestConfidenceTier", gui.DISPLAY_COLUMNS)
        self.assertNotIn("PassedFilters", gui.DISPLAY_COLUMNS)
        self.assertNotIn("FinalScore", gui.DISPLAY_COLUMNS)
        self.assertNotIn("RankingReason", gui.DISPLAY_COLUMNS)
        self.assertEqual(gui.COLUMN_NAMES["EntrySignal"], "当前买点")
        self.assertEqual(gui.COLUMN_NAMES["SignalStatus"], "近期买点")
        self.assertEqual(gui.COLUMN_NAMES["SignalDays"], "持续天数")

    def test_filter_bar_drops_fundamental_and_backtest_controls(self):
        source = inspect.getsource(gui._build_ui_v16)
        self.assertNotIn("fundamental_filter", source)
        self.assertNotIn("backtest_filter", source)
        self.assertNotIn("fundamental_box", source)
        self.assertNotIn("backtest_box", source)
        self.assertNotIn("回测可信度", source)
        self.assertIn("最低分", source)
        self.assertIn("重置", source)
        self.assertIn("刷新", source)

    def test_recent_entry_status_is_human_readable(self):
        instance = gui.DecisionScannerGUI.__new__(gui.DecisionScannerGUI)
        self.assertEqual(
            gui._format_table_value_v16(instance, "SignalStatus", "NEW"),
            "新出现",
        )
        self.assertEqual(
            gui._format_table_value_v16(instance, "SignalStatus", "ACTIVE"),
            "持续有效",
        )
        self.assertEqual(
            gui._format_table_value_v16(instance, "SignalStatus", "FAILED"),
            "已失效",
        )


if __name__ == "__main__":
    unittest.main()
''',
    encoding="utf-8",
)

# The migration mechanism is intentionally one-shot and must not land in main.
Path("scripts/apply_gui_clean_v25.py").unlink(missing_ok=True)
Path(".github/workflows/apply_gui_clean_v25.yml").unlink(missing_ok=True)
