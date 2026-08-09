from __future__ import annotations

"""Decision-focused Tkinter GUI entrypoint.

``gui_core.py`` keeps the stable implementation and compatibility surface.
This layer owns the decision-oriented presentation and the richer result
filters so GUI changes do not disturb the scanner/backtest engine.
"""

import re
from collections.abc import Sequence
from pathlib import Path

import gui_core as _core

# Compatibility alias: external callers historically patched gui.OUTPUT_DIR.
OUTPUT_DIR = _core.OUTPUT_DIR

# Market data is fixed to TickFlow Free; AkShare is fundamentals-only.
_core.DATA_SOURCE_HINTS.clear()
_core.DATA_SOURCE_HINTS["TickFlow Free"] = "行情：TickFlow Free（日K / 标的池）"

# Main table: trading decision first. Diagnostics remain in CSV/detail view.
_core.DISPLAY_COLUMNS = (
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

_core.COLUMN_NAMES.update(
    {
        "Close": "当日收盘价",
        "EntrySignal": "当前买点",
        "SignalStatus": "近期买点",
        "SignalDays": "持续天数",
        "RankingScore": "排序分",
        "InstitutionalTier": "机构等级",
        "InstitutionalScore": "机构分",
        "EntryZone": "回调买点",
        "BreakoutBuyPrice": "突破买点",
        "StopLoss": "止损位",
        "RankingEligibility": "交易资格",
        "TradeReadinessReason": "执行说明",
        "BreakoutScore": "突破强度",
        "InstitutionHoldingStatus": "机构覆盖趋势",
        "Quality": "旧质量标签",
        "QualityGate": "基本面门槛",
        "QualityDataAvailable": "基本面数据",
        "QualityDataCompleteness": "基本面完整度",
        "BacktestMode": "回测模式",
        "BacktestCacheHit": "回测缓存",
        "BacktestLastEvaluatedDate": "回测截至",
        "BacktestEngine": "回测引擎",
        "ETFTheme": "ETF主题",
        "ResearchPoolRank": "研究池排名",
        "DecisionState": "决策状态",
        "DecisionReason": "决策说明",
        "TradeReadiness": "交易就绪",
        "ResearchTier": "研究等级",
        "ModelClassification": "模型分类",
        "EntryZoneDistancePct": "距买区%",
        "EntryZoneDistanceATR": "距买区ATR",
        "PullbackQualityScore": "回踩质量",
        "QualityApplicable": "基本面适用",
        "BacktestStatus": "回测状态",
        "GlobalCalibrationScore": "全局校准分",
        "GlobalCalibrationConfidence": "全局校准可信度",
        "GlobalCalibrationLevel": "全局校准层级",
    }
)

_core.COLUMN_WIDTHS.update(
    {
        "OverallRank": 64,
        "Ticker": 90,
        "Name": 104,
        "Sector": 92,
        "Industry": 106,
        "Close": 86,
        "EntrySignal": 112,
        "SignalStatus": 82,
        "SignalDays": 72,
        "EntryZone": 104,
        "BreakoutBuyPrice": 88,
        "StopLoss": 72,
        "RankingEligibility": 74,
        "RankingScore": 82,
        "InstitutionalTier": 92,
        "InstitutionalScore": 78,
        "FinalScore": 76,
        "QualityGate": 84,
        "QualityDataAvailable": 82,
        "QualityDataCompleteness": 96,
    }
)


# ---------------------------------------------------------------------------
# Result-filter rebuild
# ---------------------------------------------------------------------------

_original_build_ui = _core.ScannerGUI._build_ui
_original_update_filter_values = _core.ScannerGUI._update_filter_values
_original_row_matches_filters = _core.ScannerGUI._row_matches_filters
_original_clear_filters = _core.ScannerGUI.clear_filters
_original_format_table_value = _core.ScannerGUI._format_table_value


def _read_filter(self, attribute: str, default: str) -> str:
    variable = getattr(self, attribute, None)
    if variable is None:
        return default
    try:
        return variable.get()
    except Exception:
        return default


def _value_for(indexes: dict[str, int], row: list[str], column: str) -> str:
    index = indexes.get(column)
    if index is None or index >= len(row):
        return ""
    return _core.ScannerGUI._cell_text(row[index])


def _asset_label(indexes: dict[str, int], row: list[str]) -> str:
    raw_asset = _value_for(indexes, row, "AssetType").strip().lower()
    raw_is_etf = _value_for(indexes, row, "IsETF").strip().lower()
    has_asset_evidence = bool(raw_asset or raw_is_etf)
    is_etf = raw_asset == "etf" or raw_is_etf in {"true", "1", "yes", "y", "是"}
    return "ETF" if is_etf else "股票" if has_asset_evidence else ""


def _configure_filter_box(widget, variable, default: str, values: list[str], enabled: bool) -> None:
    options = [default, *values]
    widget["values"] = options
    if variable.get() not in options:
        variable.set(default)
    widget.configure(state="readonly" if enabled else "disabled")


def _build_ui_v16(self) -> None:
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


def _update_filter_values_v16(self, headers: list[str], rows: list[list[str]]) -> None:
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


def _row_matches_filters_v16(
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


def _clear_filters_v16(self) -> None:
    for attribute, default in (
        ("asset_filter", "全部类型"),
        ("tier_filter", "全部等级"),
        ("score_filter", "全部分数"),
    ):
        variable = getattr(self, attribute, None)
        if variable is not None:
            variable.set(default)
    _original_clear_filters(self)


def _format_table_value_v16(self, column: str, value: str) -> str:
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


# ---------------------------------------------------------------------------
# Decision-focused overview / summary
# ---------------------------------------------------------------------------

def _update_market_overview_decision(self, rows, indexes) -> None:
    if not hasattr(self, "market_overview"):
        return
    total, _active, _confirmed, breakout, actionable, average = (
        self._market_overview_values(rows, indexes)
    )
    self.market_overview.set(
        f"概览：{total} 只 · 启动 {breakout} · 可交易 {actionable} · 最终均分 {average:.1f}"
    )


_original_render_cached_rows = _core.ScannerGUI._render_cached_rows


def _render_cached_rows_decision(self) -> bool:
    rendered = _original_render_cached_rows(self)
    if rendered and hasattr(self, "result_summary"):
        summary = self.result_summary.get()
        summary = re.sub(r" · 过期 \d+", "", summary)
        self.result_summary.set(summary)
    return rendered


class DecisionScannerGUI(_core.ScannerGUI):
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

    def _write_top50_csv(self, tickers: list[str]) -> Path:
        return self._call_core_with_legacy_output_dir(
            _core.ScannerGUI._write_top50_csv, tickers
        )

    def _build_ui(self) -> None:
        _build_ui_v16(self)

    def _update_filter_values(self, headers: list[str], rows: list[list[str]]) -> None:
        _update_filter_values_v16(self, headers, rows)

    def _row_matches_filters(
        self,
        indexes: dict[str, int],
        row: list[str],
        query: str,
        search_text: str | None = None,
        filter_values: Sequence[str] | None = None,
    ) -> bool:
        return _row_matches_filters_v16(
            self, indexes, row, query, search_text, filter_values
        )

    def clear_filters(self) -> None:
        _clear_filters_v16(self)

    def _format_table_value(self, column: str, value: str) -> str:
        return _format_table_value_v16(self, column, value)

    def _update_market_overview(self, rows, indexes) -> None:
        _update_market_overview_decision(self, rows, indexes)

    def _render_cached_rows(self) -> bool:
        return _render_cached_rows_decision(self)


# Preserve the historical import surface without mutating gui_core.ScannerGUI.
ScannerGUI = DecisionScannerGUI

def main() -> None:
    _core.main(gui_class=DecisionScannerGUI)

def __getattr__(name: str):
    return getattr(_core, name)

if __name__ == "__main__":
    main()
