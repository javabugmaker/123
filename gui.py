"""High-frequency decision workstation GUI.

``gui_core.py`` remains the stable scanner/backtest implementation.  This
module owns the v26 presentation layer: a CustomTkinter shell, independent
stock/ETF Top50 views, first-class scan/backtest actions, a compact decision
card, and collapsible engineering controls/logs.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Sequence
from pathlib import Path

import customtkinter as ctk

import gui_core as _core

# Compatibility alias: external callers historically patched gui.OUTPUT_DIR.
OUTPUT_DIR = _core.OUTPUT_DIR

# Market data is fixed to TickFlow Free; AkShare is fundamentals-only.
_core.DATA_SOURCE_HINTS.clear()
_core.DATA_SOURCE_HINTS["TickFlow Free"] = "行情：TickFlow Free（日K / 标的池）"

# The main table is intentionally compact.  Long diagnostics stay in the
# detail dialog/right-side decision card.  Two derived columns are appended
# in-memory after a CSV is loaded.
_core.DISPLAY_COLUMNS = (
    "DisplayRank",
    "Ticker",
    "Name",
    "AssetType",
    "IndustryTopic",
    "Close",
    "EntrySignal",
    "SignalStatus",
    "SignalDays",
    "ReferenceBuyPrice",
    "StopLoss",
    "RankingEligibility",
    "RankingScore",
    "InstitutionalStrength",
    "TradeReadinessReason",
)

_core.COLUMN_NAMES.update(
    {
        "DisplayRank": "当前排名",
        "IndustryTopic": "行业 / 主题",
        "Close": "当日收盘价",
        "EntrySignal": "技术信号",
        "SignalStatus": "近期状态",
        "SignalDays": "持续天数",
        "ReferenceBuyPrice": "参考买点",
        "RankingScore": "排序分",
        "InstitutionalStrength": "机构强度",
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
        "BacktestRunMode": "本轮回测模式",
        "BacktestStage": "回测阶段",
        "BacktestEligibleForRanking": "回测参与排名",
        "BacktestSkipReason": "回测说明",
        "HardGatePassed": "基础硬准入",
        "DiagnosticFailedCount": "诊断未通过数",
        "DiagnosticFailedNames": "诊断未通过项",
        "ResearchEligible": "研究榜资格",
        "ResearchExclusionReason": "研究榜排除原因",
        "TickerEvidence": "本票回测证据",
        "PeerCalibrationEvidence": "同类校准证据",
        "EvidenceStrengthScore": "证据强度",
        "EvidenceTier": "证据等级",
        "EvidenceReason": "证据说明",
        "QualityProfile": "基本面模型",
        "ProfitTrendStatus": "利润趋势",
        "CyclicalQualityOverride": "周期恢复放行",
    }
)

_core.COLUMN_WIDTHS.update(
    {
        "DisplayRank": 60,
        "IndustryTopic": 100,
        "OverallRank": 62,
        "Ticker": 84,
        "Name": 100,
        "AssetType": 52,
        "Industry": 110,
        "Close": 82,
        "EntrySignal": 98,
        "SignalStatus": 80,
        "SignalDays": 60,
        "ReferenceBuyPrice": 106,
        "StopLoss": 74,
        "RankingEligibility": 76,
        "RankingScore": 74,
        "InstitutionalStrength": 94,
    }
)
_core.NUMBER_COLUMNS.add("DisplayRank")
_core.INTEGER_COLUMNS.add("DisplayRank")

NAV_FILES = {
    "mixed": "Top50Mixed.csv",
    "stocks": "Top50Stocks.csv",
    "etf": "Top50ETF.csv",
    "ready": "Top50TradeReady.csv",
    "sustained": "Top50SustainedSignals.csv",
    "risk": "Top50ValueTrapRisk.csv",
    "all": "DecisionResults.csv",
}
NAV_TITLES = {
    "mixed": "综合 Top50",
    "stocks": "股票 Top50",
    "etf": "ETF Top50",
    "ready": "强推荐",
    "new": "新信号",
    "sustained": "持续信号",
    "risk": "风险警示",
    "all": "全部结果",
}
BACKTEST_SCOPE_FILES = {
    "股票 Top50": "Top50Stocks.csv",
    "ETF Top50": "Top50ETF.csv",
    "综合 Top50": "Top50Mixed.csv",
    "强推荐": "Top50TradeReady.csv",
}
DAILY_PIPELINE_FILE = Path(__file__).resolve().with_name("daily_pipeline.py")


def _duration_label(seconds: float) -> str:
    total = max(0, round(float(seconds or 0.0)))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"




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


def _panel_label(parent, text: str, **grid_kwargs):
    label = _core.ttk.Label(parent, text=text, style="Panel.TLabel")
    label.grid(**grid_kwargs)
    return label


class DecisionScannerGUI(_core.ScannerGUI):
    """CustomTkinter workstation on top of the stable scanner/backtest core."""

    def __init__(self, root) -> None:
        tk = _core.tk
        self.scan_mode = tk.StringVar(master=root, value="快速")
        self.backtest_scope = tk.StringVar(master=root, value="当前页面")
        self.auto_backtest_recommended = tk.BooleanVar(master=root, value=False)
        self.view_title = tk.StringVar(master=root, value="综合 Top50")
        self.card_recommended = tk.StringVar(master=root, value="0")
        self.card_cautious = tk.StringVar(master=root, value="0")
        self.card_new = tk.StringVar(master=root, value="0")
        self.card_total = tk.StringVar(master=root, value="0")
        self.run_quality = tk.StringVar(master=root, value="运行质量：尚无本轮数据")
        self.detail_title = tk.StringVar(master=root, value="选择一个标的")
        self.detail_subtitle = tk.StringVar(master=root, value="从左侧列表查看资格与研究详情")
        self.detail_signal = tk.StringVar(master=root, value="等待选择")
        self.detail_recent = tk.StringVar(master=root, value="-")
        self.detail_buy = tk.StringVar(master=root, value="-")
        self.detail_stop = tk.StringVar(master=root, value="-")
        self.detail_eligibility = tk.StringVar(master=root, value="-")
        self.detail_rank = tk.StringVar(master=root, value="-")
        self.detail_score = tk.StringVar(master=root, value="-")
        self.detail_backtest = tk.StringVar(master=root, value="-")
        self.detail_peer_calibration = tk.StringVar(master=root, value="-")
        self.detail_evidence = tk.StringVar(master=root, value="-")
        self.detail_reason = tk.StringVar(master=root, value="双击可查看完整研究字段。")
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._active_nav = "mixed"
        self._new_signal_only = False
        self._daily_pipeline_active = False
        self._advanced_visible = False
        self._more_filters_visible = False
        self._log_visible = False
        self._detail_visible = True
        self._run_performance_payload: dict[str, object] = {}
        super().__init__(root)
        self._scan_mode_changed(self.scan_mode.get())
        self.root.bind("<Control-r>", lambda _event: self.start_scan())
        self.root.bind("<Control-b>", lambda _event: self.start_backtest())
        self.root.bind("<Control-Shift-R>", lambda _event: self.start_daily_pipeline())
        self.root.bind("<Control-d>", lambda _event: self._toggle_detail_panel())
        for key, shortcut in zip(
            ("mixed", "stocks", "etf", "ready", "new", "sustained", "risk", "all"),
            "12345678",
        ):
            self.root.bind(f"<Control-Key-{shortcut}>", lambda _event, nav_key=key: self._load_navigation(nav_key))
        self.root.after(80, self._update_run_quality_summary)

    def _call_core_with_legacy_output_dir(self, method, *args, **kwargs):
        previous = _core.OUTPUT_DIR
        _core.OUTPUT_DIR = OUTPUT_DIR
        try:
            return method(self, *args, **kwargs)
        finally:
            _core.OUTPUT_DIR = previous

    # ------------------------------------------------------------------
    # UI building (split from former _build_ui_v26 ~600-line function)
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self._build_ui_configure_styles()
        self._build_ui_header()
        self._build_ui_controls()
        self._build_ui_navigation()
        self._build_ui_cards()
        self._build_ui_filters()
        self._build_ui_table_area()
        self._build_ui_decision_card()
        self._build_ui_footer()
        self._build_ui_log_panel()
        self.market_overview = _core.tk.StringVar(value="市场概览：等待结果")
        self._set_active_nav("mixed")

    def _build_ui_configure_styles(self) -> None:
        ttk = _core.ttk
        style = ttk.Style()
        style.configure("Panel.TLabel", background="#ffffff", foreground="#334e68")
        style.configure("Panel.TCheckbutton", background="#ffffff", foreground="#334e68")
        style.configure("Compact.Treeview", rowheight=28, font=("Microsoft YaHei UI", 9))
        style.configure(
            "Compact.Treeview.Heading",
            font=("Microsoft YaHei UI", 9, "bold"),
            background="#eef4fb",
            foreground="#17324d",
            padding=(8, 8),
        )

    def _build_ui_header(self) -> None:
        tk = _core.tk
        self.root.title("InstitutionScanner · 机构交易决策台")
        self.root.geometry("1520x920")
        self.root.minsize(1180, 720)
        header = ctk.CTkFrame(self.root, corner_radius=0, fg_color="#17324d")
        header.pack(fill=tk.X)
        header.grid_columnconfigure(0, weight=1)
        title_box = ctk.CTkFrame(header, fg_color="transparent")
        title_box.grid(row=0, column=0, padx=24, pady=15, sticky="w")
        ctk.CTkLabel(
            title_box,
            text="InstitutionScanner",
            font=("Microsoft YaHei UI", 22, "bold"),
            text_color="#ffffff",
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_box,
            text="A股 / ETF · 买点 · 机构强度 · 交易决策",
            font=("Microsoft YaHei UI", 10),
            text_color="#cbd9e8",
        ).pack(anchor="w", pady=(2, 0))
        header_right = ctk.CTkFrame(header, fg_color="transparent")
        header_right.grid(row=0, column=1, padx=24, pady=15, sticky="e")
        ctk.CTkLabel(
            header_right,
            text="● TickFlow Free",
            text_color="#a7f3d0",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 10))
        ctk.CTkLabel(
            header_right,
            textvariable=self.status,
            fg_color="#244864",
            corner_radius=8,
            height=32,
            text_color="#e6f2ff",
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side=tk.LEFT)

    def _build_ui_controls(self) -> None:
        tk = _core.tk
        ttk = _core.ttk
        controls = ctk.CTkFrame(self.root, corner_radius=12, fg_color="#ffffff")
        controls.pack(fill=tk.X, padx=18, pady=(14, 8))
        controls.grid_columnconfigure(5, weight=1)
        ctk.CTkLabel(controls, text="扫描范围", text_color="#334e68").grid(
            row=0, column=0, padx=(16, 6), pady=14, sticky="w"
        )
        self.scope_menu = ctk.CTkOptionMenu(
            controls,
            variable=self.scope,
            values=["全部股票和ETF", "仅股票", "仅ETF"],
            width=150,
        )
        self.scope_menu.grid(row=0, column=1, padx=(0, 16), pady=14, sticky="w")
        ctk.CTkLabel(controls, text="扫描模式", text_color="#334e68").grid(
            row=0, column=2, padx=(0, 6), pady=14, sticky="w"
        )
        self.scan_mode_menu = ctk.CTkOptionMenu(
            controls,
            variable=self.scan_mode,
            values=["快速", "标准", "完整刷新", "自定义"],
            command=self._scan_mode_changed,
            width=118,
        )
        self.scan_mode_menu.grid(row=0, column=3, padx=(0, 16), pady=14, sticky="w")
        ctk.CTkLabel(controls, text="指定代码", text_color="#334e68").grid(
            row=0, column=4, padx=(0, 6), pady=14, sticky="w"
        )
        self.ticker_entry = ctk.CTkEntry(
            controls,
            textvariable=self.tickers,
            placeholder_text="588000.SH,000001.SZ",
            width=260,
        )
        self.ticker_entry.grid(row=0, column=5, padx=(0, 16), pady=14, sticky="ew")
        self.daily_button = ctk.CTkButton(
            controls,
            text="⚡ 今日一键更新",
            command=self.start_daily_pipeline,
            width=146,
            height=38,
            fg_color="#7c3aed",
            hover_color="#6d28d9",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.daily_button.grid(row=0, column=6, padx=(0, 8), pady=12)
        self.start_button = ctk.CTkButton(
            controls,
            text="▶ 开始扫描",
            command=self.start_scan,
            width=126,
            height=38,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.start_button.grid(row=0, column=7, padx=(0, 8), pady=12)
        self.backtest_button = ctk.CTkButton(
            controls,
            text="▶ 运行回测",
            command=self.start_backtest,
            width=126,
            height=38,
            fg_color="#0f766e",
            hover_color="#115e59",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.backtest_button.grid(row=0, column=8, padx=(0, 8), pady=12)
        self.cancel_button = ctk.CTkButton(
            controls,
            text="■ 停止",
            command=self.cancel_running_task,
            state=tk.DISABLED,
            width=84,
            height=38,
            fg_color="#64748b",
            hover_color="#475569",
        )
        self.cancel_button.grid(row=0, column=9, padx=(0, 8), pady=12)
        ctk.CTkButton(
            controls,
            text="⚙ 高级设置",
            command=self._toggle_advanced,
            width=106,
            height=38,
            fg_color="transparent",
            hover_color="#eef4fb",
            text_color="#334e68",
            border_width=1,
            border_color="#d7e2ee",
        ).grid(row=0, column=10, padx=(0, 16), pady=12)
        self.advanced_frame = ctk.CTkFrame(controls, fg_color="#f8fafc", corner_radius=8)
        self.advanced_frame.grid(row=1, column=0, columnspan=11, padx=14, pady=(0, 14), sticky="ew")
        self.advanced_frame.grid_remove()
        self.source_box = ttk.Combobox(
            self.advanced_frame,
            textvariable=self.data_source,
            values=("TickFlow Free",),
            state="disabled",
            width=14,
        )
        self.source_box.grid(row=0, column=0, padx=(12, 8), pady=10, sticky="w")
        ctk.CTkLabel(
            self.advanced_frame,
            text="行情：TickFlow Free · 基本面：AkShare（低频缓存）",
            text_color="#64748b",
        ).grid(row=0, column=1, padx=(0, 18), pady=10, sticky="w")
        ctk.CTkCheckBox(
            self.advanced_frame,
            text="优先缓存",
            variable=self.cache_first,
            command=self._advanced_changed,
        ).grid(row=0, column=2, padx=8, pady=10, sticky="w")
        ctk.CTkCheckBox(
            self.advanced_frame,
            text="强制重新下载",
            variable=self.force_download,
            command=self._advanced_changed,
        ).grid(row=0, column=3, padx=8, pady=10, sticky="w")
        ctk.CTkCheckBox(
            self.advanced_frame,
            text="不使用断点",
            variable=self.no_resume,
            command=self._advanced_changed,
        ).grid(row=0, column=4, padx=8, pady=10, sticky="w")
        ctk.CTkCheckBox(
            self.advanced_frame,
            text="刷新基本面",
            variable=self.refresh_fundamentals,
            command=self._advanced_changed,
        ).grid(row=0, column=5, padx=8, pady=10, sticky="w")
        ctk.CTkCheckBox(
            self.advanced_frame,
            text="扫描完成后回测强推荐",
            variable=self.auto_backtest_recommended,
        ).grid(row=0, column=6, padx=(8, 12), pady=10, sticky="w")

    def _build_ui_navigation(self) -> None:
        tk = _core.tk
        nav = ctk.CTkFrame(self.root, corner_radius=10, fg_color="#ffffff")
        nav.pack(fill=tk.X, padx=18, pady=(0, 8))
        nav_items = (
            ("mixed", "综合 Top50"),
            ("stocks", "股票 Top50"),
            ("etf", "ETF Top50"),
            ("ready", "强推荐"),
            ("new", "新信号"),
            ("sustained", "持续信号"),
            ("risk", "风险警示"),
            ("all", "全部结果"),
        )
        for key, label in nav_items:
            button = ctk.CTkButton(
                nav,
                text=label,
                width=92,
                height=34,
                fg_color="transparent",
                hover_color="#eaf2fb",
                text_color="#334e68",
                command=lambda nav_key=key: self._load_navigation(nav_key),
            )
            button.pack(side=tk.LEFT, padx=(8 if key == "mixed" else 2, 2), pady=8)
            self._nav_buttons[key] = button
        ctk.CTkButton(
            nav,
            text="↻ 刷新",
            width=82,
            height=34,
            fg_color="transparent",
            hover_color="#eaf2fb",
            text_color="#334e68",
            command=self.refresh_results,
        ).pack(side=tk.RIGHT, padx=(2, 10), pady=8)
        ctk.CTkButton(
            nav,
            text="结果目录",
            width=86,
            height=34,
            fg_color="transparent",
            hover_color="#eaf2fb",
            text_color="#334e68",
            command=self.open_output,
        ).pack(side=tk.RIGHT, padx=2, pady=8)

    def _build_ui_cards(self) -> None:
        tk = _core.tk
        cards = ctk.CTkFrame(self.root, fg_color="transparent")
        cards.pack(fill=tk.X, padx=18, pady=(0, 8))
        for column in range(4):
            cards.grid_columnconfigure(column, weight=1, uniform="cards")
        card_specs = (
            ("推荐", self.card_recommended, "#166534"),
            ("谨慎候选", self.card_cautious, "#92400e"),
            ("新信号", self.card_new, "#1d4ed8"),
            ("资产结构", self.card_total, "#334e68"),
        )
        for column, (title, variable, accent) in enumerate(card_specs):
            card = ctk.CTkFrame(cards, corner_radius=10, fg_color="#ffffff")
            card.grid(row=0, column=column, padx=(0 if column == 0 else 5, 0 if column == 3 else 5), sticky="ew")
            ctk.CTkLabel(card, text=title, text_color="#64748b", font=("Microsoft YaHei UI", 9)).pack(
                anchor="w", padx=16, pady=(10, 0)
            )
            ctk.CTkLabel(
                card,
                textvariable=variable,
                text_color=accent,
                font=("Microsoft YaHei UI", 20, "bold"),
            ).pack(anchor="w", padx=16, pady=(0, 10))

    def _build_ui_filters(self) -> None:
        tk = _core.tk
        ttk = _core.ttk
        filters_root = ctk.CTkFrame(self.root, corner_radius=10, fg_color="#ffffff")
        filters_root.pack(fill=tk.X, padx=18, pady=(0, 8))
        top_filters = ctk.CTkFrame(filters_root, fg_color="transparent")
        top_filters.pack(fill=tk.X, padx=12, pady=10)
        if not hasattr(self, "asset_filter"):
            self.asset_filter = tk.StringVar(value="全部类型")
            self.tier_filter = tk.StringVar(value="全部等级")
            self.score_filter = tk.StringVar(value="全部分数")
            for variable in (self.asset_filter, self.tier_filter, self.score_filter):
                variable.trace_add("write", self._schedule_filter_refresh)
        _panel_label(top_filters, "类型", row=0, column=0, padx=(0, 4), sticky="w")
        self.asset_box = ttk.Combobox(
            top_filters,
            textvariable=self.asset_filter,
            values=("全部类型", "股票", "ETF"),
            state="readonly",
            width=9,
        )
        self.asset_box.grid(row=0, column=1, padx=(0, 10), sticky="w")
        _panel_label(top_filters, "行业 / 主题", row=0, column=2, padx=(0, 4), sticky="w")
        self.industry_box = ttk.Combobox(top_filters, textvariable=self.industry_filter, state="readonly", width=14)
        self.industry_box.grid(row=0, column=3, padx=(0, 10), sticky="w")
        _panel_label(top_filters, "技术信号", row=0, column=4, padx=(0, 4), sticky="w")
        self.entry_box = ttk.Combobox(top_filters, textvariable=self.entry_filter, state="readonly", width=15)
        self.entry_box.grid(row=0, column=5, padx=(0, 10), sticky="w")
        _panel_label(top_filters, "资格", row=0, column=6, padx=(0, 4), sticky="w")
        self.eligibility_box = ttk.Combobox(
            top_filters,
            textvariable=self.eligibility_filter,
            values=("全部资格", "推荐", "谨慎候选", "观察", "风险过滤"),
            state="readonly",
            width=10,
        )
        self.eligibility_box.grid(row=0, column=7, padx=(0, 10), sticky="w")
        _panel_label(top_filters, "最低分", row=0, column=8, padx=(0, 4), sticky="w")
        self.score_box = ttk.Combobox(
            top_filters,
            textvariable=self.score_filter,
            values=("全部分数", "≥25", "≥30", "≥35", "≥40", "≥50"),
            state="readonly",
            width=9,
        )
        self.score_box.grid(row=0, column=9, padx=(0, 10), sticky="w")
        _panel_label(top_filters, "搜索", row=0, column=10, padx=(0, 4), sticky="w")
        top_filters.grid_columnconfigure(11, weight=1)
        self.search_entry = ttk.Entry(top_filters, textvariable=self.search, width=24)
        self.search_entry.grid(row=0, column=11, padx=(0, 8), sticky="ew")
        ctk.CTkButton(
            top_filters,
            text="更多筛选",
            width=82,
            height=30,
            fg_color="transparent",
            hover_color="#eef4fb",
            text_color="#334e68",
            border_width=1,
            border_color="#d7e2ee",
            command=self._toggle_more_filters,
        ).grid(row=0, column=12, padx=(0, 6))
        ctk.CTkButton(top_filters, text="重置", width=62, height=30, command=self.clear_filters).grid(
            row=0, column=13, padx=(0, 6)
        )
        ctk.CTkButton(
            top_filters,
            text="刷新",
            width=62,
            height=30,
            fg_color="#64748b",
            hover_color="#475569",
            command=self.refresh_results,
        ).grid(row=0, column=14)
        self.filter_more_frame = ctk.CTkFrame(filters_root, fg_color="#f8fafc", corner_radius=8)
        self.sector_box = ttk.Combobox(
            self.filter_more_frame, textvariable=self.sector_filter, state="readonly", width=14
        )
        self.stage_box = ttk.Combobox(
            self.filter_more_frame, textvariable=self.stage_filter, state="readonly", width=14
        )
        self.tier_box = ttk.Combobox(
            self.filter_more_frame, textvariable=self.tier_filter, state="readonly", width=12
        )
        _panel_label(self.filter_more_frame, "板块", row=0, column=0, padx=(12, 4), pady=10, sticky="w")
        self.sector_box.grid(row=0, column=1, padx=(0, 16), pady=10, sticky="w")
        self.sector_box.bind("<<ComboboxSelected>>", self._sector_changed)
        _panel_label(self.filter_more_frame, "资金阶段", row=0, column=2, padx=(0, 4), pady=10, sticky="w")
        self.stage_box.grid(row=0, column=3, padx=(0, 16), pady=10, sticky="w")
        _panel_label(self.filter_more_frame, "机构等级", row=0, column=4, padx=(0, 4), pady=10, sticky="w")
        self.tier_box.grid(row=0, column=5, padx=(0, 12), pady=10, sticky="w")

    def _build_ui_table_area(self) -> None:
        tk = _core.tk
        ttk = _core.ttk
        body = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        self.body_paned = body
        body.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 8))
        table_frame = ctk.CTkFrame(body, corner_radius=10, fg_color="#ffffff")
        self._table_frame = table_frame
        table_frame.grid_rowconfigure(2, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)
        result_bar = ctk.CTkFrame(table_frame, fg_color="transparent")
        result_bar.grid(row=0, column=0, columnspan=2, padx=14, pady=(10, 4), sticky="ew")
        result_bar.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            result_bar,
            textvariable=self.view_title,
            font=("Microsoft YaHei UI", 14, "bold"),
            text_color="#17324d",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            result_bar,
            textvariable=self.result_summary,
            font=("Microsoft YaHei UI", 9),
            text_color="#64748b",
        ).grid(row=0, column=1, padx=(12, 0), sticky="w")
        self.detail_toggle_button = ctk.CTkButton(
            result_bar,
            text="详情 ‹",
            width=72,
            height=28,
            fg_color="transparent",
            hover_color="#eef4fb",
            text_color="#334e68",
            border_width=1,
            border_color="#d7e2ee",
            command=self._toggle_detail_panel,
        )
        self.detail_toggle_button.grid(row=0, column=2, padx=(8, 0), sticky="e")
        pagination = ctk.CTkFrame(table_frame, fg_color="transparent")
        pagination.grid(row=1, column=0, columnspan=2, padx=14, pady=(0, 6), sticky="e")
        self.previous_page_button = ctk.CTkButton(
            pagination,
            text="上一页",
            width=70,
            height=28,
            fg_color="#eaf2fb",
            hover_color="#dbeafe",
            text_color="#334e68",
            command=self._show_previous_page,
        )
        self.previous_page_button.pack(side=tk.LEFT, padx=(0, 6))
        ctk.CTkLabel(pagination, textvariable=self.page_summary, text_color="#64748b").pack(side=tk.LEFT)
        self.next_page_button = ctk.CTkButton(
            pagination,
            text="下一页",
            width=70,
            height=28,
            fg_color="#eaf2fb",
            hover_color="#dbeafe",
            text_color="#334e68",
            command=self._show_next_page,
        )
        self.next_page_button.pack(side=tk.LEFT, padx=(6, 0))
        self.table = ttk.Treeview(table_frame, show="headings", selectmode="browse", style="Compact.Treeview")
        self.table.tag_configure("eligibility-recommended", background="#ecfdf3", foreground="#166534")
        self.table.tag_configure("eligibility-cautious", background="#fffbeb", foreground="#92400e")
        self.table.tag_configure("risk-filter", background="#fef2f2", foreground="#991b1b")
        self.table.tag_configure("eligibility-observe", background="#ffffff", foreground="#334e68")
        ybar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.table.yview)
        xbar = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.table.xview)
        self.table.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.table.grid(row=2, column=0, sticky="nsew", padx=(10, 0), pady=(0, 4))
        ybar.grid(row=2, column=1, sticky="ns", pady=(0, 4))
        xbar.grid(row=3, column=0, sticky="ew", padx=(10, 0), pady=(0, 10))
        self.table.bind("<<TreeviewSelect>>", self._update_decision_card)
        self.table.bind("<Double-1>", self.show_selected_detail)
        self.table.bind("<Return>", self.show_selected_detail)

    def _build_ui_decision_card(self) -> None:
        tk = _core.tk
        body = self.body_paned
        detail = ctk.CTkFrame(body, width=300, corner_radius=10, fg_color="#ffffff")
        self.detail_panel = detail
        detail.pack_propagate(False)
        ctk.CTkLabel(detail, text="当前标的", text_color="#64748b", font=("Microsoft YaHei UI", 9)).pack(
            anchor="w", padx=18, pady=(18, 2)
        )
        ctk.CTkLabel(
            detail,
            textvariable=self.detail_title,
            text_color="#17324d",
            font=("Microsoft YaHei UI", 18, "bold"),
        ).pack(anchor="w", padx=18)
        ctk.CTkLabel(detail, textvariable=self.detail_subtitle, text_color="#64748b").pack(
            anchor="w", padx=18, pady=(0, 14)
        )
        self.detail_signal_label = ctk.CTkLabel(
            detail,
            textvariable=self.detail_signal,
            fg_color="#eaf2fb",
            corner_radius=8,
            text_color="#1d4ed8",
            font=("Microsoft YaHei UI", 12, "bold"),
            height=36,
        )
        self.detail_signal_label.pack(fill=tk.X, padx=18, pady=(0, 14))
        for label, variable in (
            ("近期状态", self.detail_recent),
            ("参考买点", self.detail_buy),
            ("止损位", self.detail_stop),
            ("交易资格", self.detail_eligibility),
            ("榜单 / 全局", self.detail_rank),
            ("排序 / 机构", self.detail_score),
            ("本票回测", self.detail_backtest),
            ("同类校准", self.detail_peer_calibration),
            ("证据等级", self.detail_evidence),
        ):
            row = ctk.CTkFrame(detail, fg_color="transparent")
            row.pack(fill=tk.X, padx=18, pady=4)
            ctk.CTkLabel(row, text=label, text_color="#64748b", width=82, anchor="w").pack(side=tk.LEFT)
            ctk.CTkLabel(
                row,
                textvariable=variable,
                text_color="#17324d",
                font=("Microsoft YaHei UI", 10, "bold"),
                anchor="e",
            ).pack(side=tk.RIGHT, fill=tk.X, expand=True)
        ctk.CTkLabel(detail, text="执行说明", text_color="#64748b").pack(
            anchor="w", padx=18, pady=(16, 4)
        )
        ctk.CTkLabel(
            detail,
            textvariable=self.detail_reason,
            text_color="#334e68",
            justify="left",
            anchor="nw",
            wraplength=260,
        ).pack(fill=tk.X, padx=18)
        detail_actions = ctk.CTkFrame(detail, fg_color="transparent")
        detail_actions.pack(side=tk.BOTTOM, fill=tk.X, padx=18, pady=18)
        ctk.CTkButton(
            detail_actions,
            text="回测此标的",
            width=118,
            command=self._backtest_selected,
            fg_color="#0f766e",
            hover_color="#115e59",
        ).pack(side=tk.LEFT)
        ctk.CTkButton(
            detail_actions,
            text="完整详情",
            width=110,
            command=self.show_selected_detail,
            fg_color="#64748b",
            hover_color="#475569",
        ).pack(side=tk.RIGHT)
        body.add(self._table_frame, weight=1)
        body.add(detail, weight=0)

    def _build_ui_footer(self) -> None:
        tk = _core.tk
        ttk = _core.ttk
        footer = ctk.CTkFrame(self.root, corner_radius=10, fg_color="#ffffff")
        footer.pack(fill=tk.X, padx=18, pady=(0, 12))
        self.footer_frame = footer
        ctk.CTkLabel(footer, text="回测范围", text_color="#64748b").pack(side=tk.LEFT, padx=(14, 6), pady=9)
        self.backtest_scope_menu = ctk.CTkOptionMenu(
            footer,
            variable=self.backtest_scope,
            values=["当前页面", "当前筛选", "股票 Top50", "ETF Top50", "综合 Top50", "强推荐", "新信号", "当前选中标的"],
            width=136,
        )
        self.backtest_scope_menu.pack(side=tk.LEFT, pady=9)
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=180)
        self.progress.pack(side=tk.LEFT, padx=12, pady=9)
        ctk.CTkLabel(
            footer,
            textvariable=self.run_quality,
            text_color="#52677d",
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=(2, 8), pady=9)
        ctk.CTkButton(
            footer,
            text="性能详情",
            width=76,
            height=28,
            fg_color="transparent",
            hover_color="#eef4fb",
            text_color="#334e68",
            border_width=1,
            border_color="#d7e2ee",
            command=self._show_run_performance,
        ).pack(side=tk.LEFT, padx=(0, 8), pady=7)
        ctk.CTkLabel(footer, textvariable=self.page_summary, text_color="#64748b").pack(side=tk.RIGHT, padx=(10, 6), pady=9)
        self.log_toggle_button = ctk.CTkButton(
            footer,
            text="日志 ›",
            width=74,
            height=30,
            fg_color="transparent",
            hover_color="#eef4fb",
            text_color="#334e68",
            command=self._toggle_log,
        )
        self.log_toggle_button.pack(side=tk.RIGHT, padx=(6, 12), pady=7)

    def _build_ui_log_panel(self) -> None:
        tk = _core.tk
        self.log_panel = ctk.CTkFrame(self.root, corner_radius=10, fg_color="#17212b")
        log_header = ctk.CTkFrame(self.log_panel, fg_color="transparent")
        log_header.pack(fill=tk.X, padx=10, pady=(8, 0))
        ctk.CTkLabel(log_header, text="运行日志", text_color="#d5e4f2", font=("Microsoft YaHei UI", 10, "bold")).pack(side=tk.LEFT)
        ctk.CTkButton(
            log_header,
            text="清空",
            width=58,
            height=26,
            fg_color="#334155",
            hover_color="#475569",
            command=self.clear_log,
        ).pack(side=tk.RIGHT)
        self.log_text = tk.Text(
            self.log_panel,
            height=7,
            wrap=tk.NONE,
            state=tk.DISABLED,
            bg="#17212b",
            fg="#d5e4f2",
            insertbackground="white",
            relief=tk.FLAT,
            padx=12,
            pady=8,
            font=("Consolas", 9),
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 8))

    # ------------------------------------------------------------------
    # Filter helpers
    # ------------------------------------------------------------------
    def _read_filter(self, attribute: str, default: str) -> str:
        variable = getattr(self, attribute, None)
        if variable is None:
            return default
        try:
            return variable.get()
        except Exception:
            return default

    # ------------------------------------------------------------------
    # Override methods (formerly v26 module-level monkey-patches)
    # ------------------------------------------------------------------
    def _update_filter_values(self, headers: list[str], rows: list[list[str]]) -> None:
        _core.ScannerGUI._update_filter_values(self, headers, rows)
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
        tiers: list[str] = []
        if "InstitutionalTier" in indexes:
            tier_index = indexes["InstitutionalTier"]
            tiers = sorted(
                {
                    self._cell_text(row[tier_index])
                    for row in rows
                    if len(row) > tier_index and self._cell_text(row[tier_index])
                }
            )
        _configure_filter_box(self.tier_box, self.tier_filter, "全部等级", tiers, bool(tiers))
        score_enabled = any(
            column in indexes for column in ("RankingScore", "InstitutionalScore", "FinalScore", "Score")
        )
        _configure_filter_box(
            self.score_box,
            self.score_filter,
            "全部分数",
            ["≥25", "≥30", "≥35", "≥40", "≥50"] if score_enabled else [],
            score_enabled,
        )
        topic_index = indexes.get("IndustryTopic")
        if topic_index is not None:
            topics = sorted(
                {
                    self._cell_text(row[topic_index])
                    for row in rows
                    if len(row) > topic_index and self._cell_text(row[topic_index])
                }
            )
            self.industry_box["values"] = ["全部行业", *topics]
            if self.industry_filter.get() not in self.industry_box["values"]:
                self.industry_filter.set("全部行业")

    def _row_matches_filters(
        self,
        indexes: dict[str, int],
        row: list[str],
        query: str,
        search_text: str | None = None,
        filter_values: Sequence[str] | None = None,
    ) -> bool:
        if filter_values is not None:
            values = list(filter_values[:6])
            while len(values) < 6:
                values.append("")
            industry_value = values[1] or "全部行业"
            values[1] = "全部行业"
            legacy_values = tuple(values)
        else:
            industry_value = self._read_filter("industry_filter", "全部行业")
            legacy_values = (
                self._read_filter("sector_filter", "全部板块"),
                "全部行业",
                self._read_filter("quality_filter", "全部质量"),
                self._read_filter("stage_filter", "全部阶段"),
                self._read_filter("entry_filter", "全部买点"),
                self._read_filter("eligibility_filter", "全部资格"),
            )
        if not _core.ScannerGUI._row_matches_filters(self, indexes, row, query, search_text, legacy_values):
            return False
        if filter_values is not None and len(filter_values) >= 9:
            asset_value, tier_value, score_value = filter_values[6:9]
        else:
            asset_value = self._read_filter("asset_filter", "全部类型")
            tier_value = self._read_filter("tier_filter", "全部等级")
            score_value = self._read_filter("score_filter", "全部分数")
        padded = row if len(row) >= len(self._csv_headers) else row + [""] * (len(self._csv_headers) - len(row))
        if industry_value != "全部行业":
            topic = _value_for(indexes, padded, "IndustryTopic") or _value_for(indexes, padded, "Industry")
            if topic != industry_value:
                return False
        asset = _asset_label(indexes, padded)
        if asset_value != "全部类型" and asset != asset_value:
            return False
        if tier_value != "全部等级" and _value_for(indexes, padded, "InstitutionalTier") != tier_value:
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
        if getattr(self, "_new_signal_only", False):
            if _value_for(indexes, padded, "SignalStatus").strip().upper() != "NEW":
                return False
        return True

    def clear_filters(self) -> None:
        self._new_signal_only = False
        for attribute, default in (
            ("asset_filter", "全部类型"),
            ("tier_filter", "全部等级"),
            ("score_filter", "全部分数"),
        ):
            variable = getattr(self, attribute, None)
            if variable is not None:
                variable.set(default)
        _core.ScannerGUI.clear_filters(self)

    def _format_table_value(self, column: str, value: str) -> str:
        text = self._cell_text(value)
        detail_data = getattr(self, "_detail_format_data", None)
        if column in {"Close", "StopLoss", "BreakoutBuyPrice", "ProjectedTarget"} and isinstance(
            detail_data, dict
        ):
            return self._format_asset_price(value, detail_data)
        if column == "SignalStatus":
            if self._is_missing_text(text):
                return "-"
            return {
                "NEW": "新出现",
                "ACTIVE": "持续有效",
                "CONFIRMED": "持续确认",
                "STRENGTHEN": "正在增强",
                "WATCH": "观察中",
                "WEAKEN": "正在转弱",
                "FAILED": "已失效",
                "EXPIRED": "已过期",
                "INACTIVE": "已结束",
            }.get(text.strip().upper(), text)
        return _core.ScannerGUI._format_table_value(self, column, value)

    def _format_asset_price(self, value: object, data: dict[str, str]) -> str:
        text = self._cell_text(value)
        number = self._numeric_value(text)
        if number is None:
            return "—" if self._is_missing_text(text) else text
        asset_type = str(data.get("AssetType", "") or "").strip().casefold()
        is_etf = asset_type == "etf" or str(data.get("IsETF", "") or "").strip().casefold() in {
            "true",
            "1",
            "yes",
            "y",
            "是",
        }
        precision = 3 if is_etf else 2
        return f"{number:,.{precision}f}"

    def _apply_visible_price_precision(self) -> None:
        columns = tuple(self.table["columns"])
        price_columns = tuple(
            column for column in ("Close", "StopLoss") if column in columns
        )
        if not price_columns:
            return
        for item_id in self.table.get_children():
            data = self._row_details.get(item_id, {})
            if not data:
                continue
            for column in price_columns:
                self.table.set(
                    item_id,
                    column,
                    self._format_asset_price(data.get(column, ""), data),
                )

    def _restore_table_selection(self, ticker: str) -> None:
        children = tuple(self.table.get_children())
        if not children:
            self._reset_decision_card_if_needed()
            return
        normalized = str(ticker or "").strip().upper()
        target = next(
            (
                item_id
                for item_id in children
                if str(self._row_details.get(item_id, {}).get("Ticker", "")).strip().upper()
                == normalized
            ),
            children[0],
        )
        self.table.selection_set(target)
        self.table.focus(target)
        self.table.see(target)
        self._update_decision_card()

    def _update_market_overview(self, rows, indexes) -> None:
        if not hasattr(self, "market_overview"):
            return
        total, _active, _confirmed, breakout, actionable, average = self._market_overview_values(rows, indexes)
        self.market_overview.set(
            f"概览：{total} 只 · 启动 {breakout} · 可交易 {actionable} · 最终均分 {average:.1f}"
        )

    def _render_cached_rows(self) -> bool:
        selected_ticker = ""
        if hasattr(self, "table") and hasattr(self, "_row_details"):
            selected_ticker = str(self._selected_detail().get("Ticker", "") or "")
        rendered = _core.ScannerGUI._render_cached_rows(self)
        if not rendered:
            return False
        if hasattr(self, "result_summary"):
            summary = re.sub(r" · 过期 \d+", "", self.result_summary.get())
            summary = summary.replace("当前文件：", "")
            self.result_summary.set(summary)
        if hasattr(self, "card_recommended"):
            self._update_dashboard_cards()
        if hasattr(self, "detail_title") and hasattr(self, "table"):
            self._apply_visible_price_precision()
            self._restore_table_selection(selected_ticker)
        return True

    def show_selected_detail(self, _event=None) -> None:
        data = self._selected_detail()
        if not data:
            return
        self._detail_format_data = data
        try:
            _core.ScannerGUI.show_selected_detail(self, _event)
        finally:
            self._detail_format_data = None

    def _quality_tag(self, quality: str) -> str:
        # Preserve the historical method contract.  v26 intentionally does
        # not configure these tags, so eligibility remains the only row color.
        return _core.ScannerGUI._quality_tag(self, quality)

    def _entry_tag(self, signal: str) -> str:
        return _core.ScannerGUI._entry_tag(self, signal)

    def _risk_tag(self, values: list[str], indexes: dict[str, int]) -> str:
        eligibility = _value_for(indexes, values, "RankingEligibility")
        if eligibility == "推荐":
            return "eligibility-recommended"
        if eligibility == "谨慎候选":
            return "eligibility-cautious"
        if eligibility == "风险过滤":
            return "risk-filter"
        return "eligibility-observe"

    # Layout toggles ----------------------------------------------------------
    def _toggle_advanced(self) -> None:
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self.advanced_frame.grid()
        else:
            self.advanced_frame.grid_remove()

    def _toggle_more_filters(self) -> None:
        self._more_filters_visible = not self._more_filters_visible
        if self._more_filters_visible:
            self.filter_more_frame.pack(fill=_core.tk.X, padx=12, pady=(0, 10))
        else:
            self.filter_more_frame.pack_forget()

    def _toggle_log(self) -> None:
        self._log_visible = not self._log_visible
        if self._log_visible:
            self.log_panel.pack(fill=_core.tk.X, padx=18, pady=(0, 12))
            self.log_toggle_button.configure(text="日志 ⌄")
        else:
            self.log_panel.pack_forget()
            self.log_toggle_button.configure(text="日志 ›")

    def _show_log_for_error(self) -> None:
        if not self._log_visible:
            self._toggle_log()

    def _toggle_detail_panel(self) -> None:
        pane = getattr(self, "body_paned", None)
        detail = getattr(self, "detail_panel", None)
        button = getattr(self, "detail_toggle_button", None)
        if pane is None or detail is None:
            return
        visible = bool(getattr(self, "_detail_visible", True))
        if visible:
            try:
                pane.forget(detail)
            except Exception:
                return
            self._detail_visible = False
            if button is not None:
                button.configure(text="详情 ›")
        else:
            try:
                pane.add(detail, weight=1)
            except Exception:
                return
            self._detail_visible = True
            if button is not None:
                button.configure(text="详情 ‹")

    # Scan modes --------------------------------------------------------------
    def _scan_mode_changed(self, choice: str) -> None:
        if choice == "快速":
            self.cache_first.set(True)
            self.force_download.set(False)
            self.no_resume.set(False)
        elif choice == "标准":
            self.cache_first.set(False)
            self.force_download.set(False)
            self.no_resume.set(False)
        elif choice == "完整刷新":
            self.cache_first.set(False)
            self.force_download.set(True)
            self.no_resume.set(True)

    def _advanced_changed(self) -> None:
        self.scan_mode.set("自定义")

    def start_daily_pipeline(self) -> None:
        if self.scan_running:
            _core.messagebox.showinfo("提示", "当前任务正在运行中")
            return
        if not DAILY_PIPELINE_FILE.exists():
            _core.messagebox.showerror("无法启动", f"缺少 {DAILY_PIPELINE_FILE.name}")
            return
        self.clear_log()
        self.scan_running = True
        self.backtest_running = True
        self._daily_pipeline_active = True
        self._cancel_requested = False
        self._csv_path = None
        self._csv_mtime = None
        self.scan_output_mtime = self._results_mtime()
        self.daily_button.configure(state=_core.tk.DISABLED, text="今日全流程运行中")
        self.start_button.configure(state=_core.tk.DISABLED)
        self.backtest_button.configure(state=_core.tk.DISABLED)
        self.cancel_button.configure(state=_core.tk.NORMAL)
        self.progress.stop()
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.status.set("今日全流程：准备获取最新数据")
        command = [
            _core.sys.executable,
            str(DAILY_PIPELINE_FILE),
            "--data-source",
            self._selected_data_source(),
            "--backtest-mode",
            "fast",
        ]
        if bool(self.refresh_fundamentals.get()):
            command.append("--refresh-fundamentals")
        self.append_log(
            "今日一键更新：最新日K → 全市场扫描 → FAST回测 → EXACT精炼 → 最终Top50。\n"
        )
        _core.threading.Thread(target=self.run_process, args=(command,), daemon=True).start()

    def start_scan(self) -> None:
        if self.scan_running:
            return _core.ScannerGUI.start_scan(self)
        self._scan_mode_changed(self.scan_mode.get())
        self.daily_button.configure(state=_core.tk.DISABLED)
        self.backtest_button.configure(state=_core.tk.DISABLED)
        self.start_button.configure(text="扫描运行中")
        _core.ScannerGUI.start_scan(self)
        if not self.scan_running:
            self.daily_button.configure(state=_core.tk.NORMAL)
            self.backtest_button.configure(state=_core.tk.NORMAL)
            self.start_button.configure(text="▶ 开始扫描")

    # Navigation --------------------------------------------------------------
    def _set_active_nav(self, key: str) -> None:
        self._active_nav = key
        for nav_key, button in self._nav_buttons.items():
            if nav_key == key:
                button.configure(fg_color="#1677ff", hover_color="#0f6ad8", text_color="#ffffff")
            else:
                button.configure(fg_color="transparent", hover_color="#eaf2fb", text_color="#334e68")
        if key in NAV_TITLES:
            self.view_title.set(NAV_TITLES[key])

    def _load_navigation(self, key: str) -> None:
        self._new_signal_only = key == "new"
        if key == "new":
            filename = "DecisionResults.csv" if self._csv_has_results("DecisionResults.csv") else "AllResults.csv"
        else:
            filename = NAV_FILES[key]
            if filename == "DecisionResults.csv" and not self._csv_has_results(filename):
                filename = "AllResults.csv"
        if self.load_csv(filename, preserve_new_signal=(key == "new")):
            self._set_active_nav(key)

    def _infer_nav_key(self, filename: str) -> str | None:
        for key, value in NAV_FILES.items():
            if value == filename:
                return key
        if filename == "Top50.csv":
            return "mixed"
        return None

    def _load_best_available_results(self) -> bool:
        for filename in ("Top50Mixed.csv", "Top50Stocks.csv", "Top50ETF.csv", "Top50.csv", "DecisionResults.csv", "AllResults.csv"):
            if self._csv_has_results(filename):
                return self.load_csv(filename)
        return False

    # Derived display fields --------------------------------------------------
    @staticmethod
    def _compact_price_range(value: str) -> str:
        text = str(value or "").strip()
        match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*[-~～—–]\s*(-?\d+(?:\.\d+)?)\s*", text)
        if not match:
            return text
        try:
            left = float(match.group(1))
            right = float(match.group(2))
        except ValueError:
            return text
        return match.group(1) if abs(left - right) <= 1e-12 else text

    @staticmethod
    def _compact_institution_tier(value: str) -> str:
        text = str(value or "").strip()
        return {
            "A级机构启动": "A",
            "B级观察": "B",
            "C级价值观察": "C",
            "D级等待确认": "D",
            "D级陷阱池": "D陷阱",
        }.get(text, text)

    def _ensure_derived_columns(self) -> None:
        if not self._csv_headers:
            return
        for column in ("DisplayRank", "IndustryTopic", "ReferenceBuyPrice", "InstitutionalStrength"):
            if column not in self._csv_headers:
                self._csv_headers.append(column)
                for row in self._csv_rows:
                    row.append("")
        indexes = {header: index for index, header in enumerate(self._csv_headers)}
        for row in self._csv_rows:
            if len(row) < len(self._csv_headers):
                row.extend([""] * (len(self._csv_headers) - len(row)))
            pool_rank = _value_for(indexes, row, "CandidateViewRank") or _value_for(
                indexes, row, "ResearchPoolRank"
            )
            overall_rank = _value_for(indexes, row, "OverallRank")
            row[indexes["DisplayRank"]] = pool_rank or overall_rank

            asset = _asset_label(indexes, row)
            industry = _value_for(indexes, row, "Industry")
            etf_theme = _value_for(indexes, row, "ETFTheme")
            classification = _value_for(indexes, row, "ModelClassification")
            sector = _value_for(indexes, row, "Sector")
            row[indexes["IndustryTopic"]] = (
                (etf_theme or classification or sector or industry)
                if asset == "ETF"
                else (industry or classification or sector)
            )

            signal = _value_for(indexes, row, "EntrySignal").strip().upper()
            entry_zone = self._compact_price_range(_value_for(indexes, row, "EntryZone"))
            breakout = self._compact_price_range(_value_for(indexes, row, "BreakoutBuyPrice"))
            reference = breakout if signal == "BREAKOUT_CONFIRM" and breakout else entry_zone or breakout
            row[indexes["ReferenceBuyPrice"]] = reference
            tier = self._compact_institution_tier(_value_for(indexes, row, "InstitutionalTier"))
            score = _value_for(indexes, row, "InstitutionalScore")
            if tier and score:
                strength = f"{tier} · {self._format_table_value('InstitutionalScore', score)}"
            else:
                strength = tier or score
            row[indexes["InstitutionalStrength"]] = strength
        self._csv_indexes = indexes
        self._csv_search_text = [" ".join(map(self._cell_text, row)).casefold() for row in self._csv_rows]
        if hasattr(self, "industry_box"):
            self._update_filter_values(self._csv_headers, self._csv_rows)

    def _set_display_columns_for_file(self, filename: str) -> None:
        columns = list(_core.DISPLAY_COLUMNS)
        # Keep TradeReadinessReason in the public compatibility contract, but
        # move the long explanation out of the real table into the decision card.
        if "TradeReadinessReason" in columns:
            columns.remove("TradeReadinessReason")
        if filename in {
            "Top50Stocks.csv",
            "Top50ETF.csv",
            "Top50SustainedSignals.csv",
            "Top50ValueTrapRisk.csv",
        } and "AssetType" in columns:
            columns.remove("AssetType")
        self._display_headers = [column for column in columns if column in self._csv_headers]
        self._display_indexes = [self._csv_indexes[column] for column in self._display_headers]
        self._table_headers = ()

    def load_csv(self, filename: str, preserve_new_signal: bool = False) -> bool:
        if not preserve_new_signal:
            self._new_signal_only = False
        previous_file = getattr(self, "current_file", None)
        loaded = self._call_core_with_legacy_output_dir(_core.ScannerGUI.load_csv, filename)
        if not loaded:
            return False
        # Older callers/tests intentionally construct the GUI without __init__.
        # In that compatibility path the core load/render is already complete.
        if not hasattr(self, "view_title"):
            return loaded
        self._ensure_derived_columns()
        self._set_display_columns_for_file(filename)
        if previous_file != filename:
            self._sort_column = "DisplayRank"
            self._sort_descending = False
            self._current_page = 0
        rendered = self._render_cached_rows()
        key = self._infer_nav_key(filename)
        if preserve_new_signal:
            key = "new"
        if key:
            self._set_active_nav(key)
        return rendered

    def _csv_has_results(self, filename: str) -> bool:
        return self._call_core_with_legacy_output_dir(_core.ScannerGUI._csv_has_results, filename)

    def _write_top50_csv(self, tickers: list[str]) -> Path:
        return self._call_core_with_legacy_output_dir(_core.ScannerGUI._write_top50_csv, tickers)

    def _update_run_quality_summary(self) -> None:
        path = OUTPUT_DIR / "DailyRunSummary.json"
        if not path.exists() or not hasattr(self, "run_quality"):
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        self._run_performance_payload = payload
        expected = str(payload.get("expected_trading_date", "") or "-")
        stages = payload.get("stage_seconds", {}) if isinstance(payload.get("stage_seconds", {}), dict) else {}
        backtest = payload.get("backtest", {}) if isinstance(payload.get("backtest", {}), dict) else {}
        elapsed = float(payload.get("elapsed_seconds", 0.0) or 0.0)
        scan_seconds = float(stages.get("scan", 0.0) or 0.0)
        engine_seconds = float(backtest.get("elapsed_seconds", 0.0) or 0.0)
        postprocess_seconds = float(backtest.get("postprocess_seconds", 0.0) or 0.0)
        if postprocess_seconds <= 0:
            postprocess_seconds = max(
                0.0,
                float(stages.get("backtest", 0.0) or 0.0) - engine_seconds,
            )
        cache = float(backtest.get("cache_hit_rate", 0.0) or 0.0)
        cache_health = str(backtest.get("cache_health", "") or "").strip()
        cache_label = f"Cache {cache:.0%}" + (f"·{cache_health}" if cache_health else "")
        self.run_quality.set(
            f"✓ {expected} · 总{_duration_label(elapsed)} · 扫描{_duration_label(scan_seconds)} · "
            f"引擎{_duration_label(engine_seconds)} · 后处理{_duration_label(postprocess_seconds)} · {cache_label}"
        )

    def _show_run_performance(self) -> None:
        payload = dict(getattr(self, "_run_performance_payload", {}) or {})
        if not payload:
            self._update_run_quality_summary()
            payload = dict(getattr(self, "_run_performance_payload", {}) or {})
        if not payload:
            _core.messagebox.showinfo("运行性能", "还没有可用的 DailyRunSummary.json。")
            return
        universe = payload.get("universe", {}) if isinstance(payload.get("universe", {}), dict) else {}
        freshness = payload.get("freshness", {}) if isinstance(payload.get("freshness", {}), dict) else {}
        scan = payload.get("scan_breakdown", {}) if isinstance(payload.get("scan_breakdown", {}), dict) else {}
        backtest = payload.get("backtest", {}) if isinstance(payload.get("backtest", {}), dict) else {}
        lines = [
            f"状态：{payload.get('publish_status', '-')}",
            f"交易日：{payload.get('expected_trading_date', '-')}",
            f"总耗时：{_duration_label(float(payload.get('elapsed_seconds', 0.0) or 0.0))}",
            f"标的：{int(universe.get('rows', 0) or 0)} · 股票 {int(universe.get('stocks', 0) or 0)} · ETF {int(universe.get('etfs', 0) or 0)}",
            f"最新覆盖：{float(freshness.get('all_results_ratio', 0.0) or 0.0):.2%}",
            "",
            "扫描阶段",
            f"  股票池：{_duration_label(float(scan.get('universe_seconds', 0.0) or 0.0))}",
            f"  基本面：{_duration_label(float(scan.get('fundamentals_seconds', 0.0) or 0.0))}",
            f"  行情更新：{_duration_label(float(scan.get('download_seconds', 0.0) or 0.0))}",
            f"  指标分析：{_duration_label(float(scan.get('analysis_seconds', 0.0) or 0.0))}",
            f"  评分增强：{_duration_label(float(scan.get('enrichment_seconds', 0.0) or 0.0))}",
            f"  扫描导出：{_duration_label(float(scan.get('export_seconds', 0.0) or 0.0))}",
            "",
            "回测阶段",
            f"  FAST：{int(backtest.get('fast_screen_tickers', 0) or 0)}",
            f"  EXACT：{int(backtest.get('exact_refinement_tickers', 0) or 0)}",
            f"  回测引擎：{_duration_label(float(backtest.get('elapsed_seconds', 0.0) or 0.0))}",
            f"  校准查表：{_duration_label(float(backtest.get('calibration_lookup_seconds', 0.0) or 0.0))}",
            f"  排名计算：{_duration_label(float(backtest.get('ranking_compute_seconds', 0.0) or 0.0))}",
            f"  文件落盘：{_duration_label(float(backtest.get('persistence_seconds', 0.0) or 0.0))}",
            f"  后处理：{_duration_label(float(backtest.get('postprocess_seconds', 0.0) or 0.0))}",
            f"  Cache：{float(backtest.get('cache_hit_rate', 0.0) or 0.0):.2%}",
            f"  Cache健康：{backtest.get('cache_health', '-')}",
            f"  较上轮：{float(backtest.get('cache_hit_rate_delta', 0.0) or 0.0):+.2%}",
        ]
        _core.messagebox.showinfo("本轮运行性能", "\n".join(lines))

    # Dashboard cards / decision card ----------------------------------------
    def _update_dashboard_cards(self) -> None:
        indexes = getattr(self, "_csv_indexes", {})
        ticker_index = indexes.get("Ticker")
        if ticker_index is None:
            return
        visible = set(self.filtered_tickers)
        recommended = cautious = new_signals = stocks = etfs = 0
        for row in self._csv_rows:
            if len(row) <= ticker_index:
                continue
            ticker = self._cell_text(row[ticker_index]).upper()
            if ticker not in visible:
                continue
            eligibility = _value_for(indexes, row, "RankingEligibility")
            status = _value_for(indexes, row, "SignalStatus").strip().upper()
            asset = _asset_label(indexes, row)
            recommended += eligibility == "推荐"
            cautious += eligibility == "谨慎候选"
            new_signals += status == "NEW"
            stocks += asset == "股票"
            etfs += asset == "ETF"
        self.card_recommended.set(str(recommended))
        self.card_cautious.set(str(cautious))
        self.card_new.set(str(new_signals))
        if stocks or etfs:
            self.card_total.set(f"股票 {stocks} · ETF {etfs}")
        else:
            self.card_total.set(str(len(self.filtered_tickers)))

    def _selected_detail(self) -> dict[str, str]:
        selection = self.table.selection()
        if not selection:
            return {}
        return self._row_details.get(selection[0], {})

    def _reset_decision_card_if_needed(self) -> None:
        if self.table.selection():
            return
        self.detail_title.set("选择一个标的")
        self.detail_subtitle.set("从左侧列表查看资格与研究详情")
        self.detail_signal.set("等待选择")
        self.detail_recent.set("-")
        self.detail_buy.set("-")
        self.detail_stop.set("-")
        self.detail_eligibility.set("-")
        self.detail_rank.set("-")
        self.detail_score.set("-")
        self.detail_backtest.set("-")
        self.detail_peer_calibration.set("-")
        self.detail_evidence.set("-")
        self.detail_reason.set("双击可查看完整研究字段。")

    def _update_decision_card(self, _event=None) -> None:
        data = self._selected_detail()
        if not data:
            self._reset_decision_card_if_needed()
            return
        ticker = data.get("Ticker", "")
        name = data.get("Name", "")
        self.detail_title.set(name or ticker or "当前标的")
        self.detail_subtitle.set(ticker)
        signal = self._format_table_value("EntrySignal", data.get("EntrySignal", "")) or "-"
        recent = self._format_table_value("SignalStatus", data.get("SignalStatus", "")) or "-"
        days = self._format_table_value("SignalDays", data.get("SignalDays", ""))
        if days and days not in {"0", "0.00"}:
            recent = f"{recent} · {days}天"
        reference = data.get("ReferenceBuyPrice", "") or data.get("EntryZone", "") or data.get("BreakoutBuyPrice", "")
        self.detail_signal.set(signal)
        self.detail_recent.set(recent)
        self.detail_buy.set(reference or "-")
        self.detail_stop.set(self._format_asset_price(data.get("StopLoss", ""), data) or "-")
        eligibility = data.get("RankingEligibility", "") or "-"
        self.detail_eligibility.set(eligibility)
        view_rank = data.get("CandidateViewRank", "") or data.get("ResearchPoolRank", "")
        display_rank = self._format_table_value("CandidateViewRank", view_rank)
        overall_rank = self._format_table_value("OverallRank", data.get("OverallRank", ""))
        self.detail_rank.set(f"{display_rank or '-'} / {overall_rank or '-'}")
        ranking = self._format_table_value("RankingScore", data.get("RankingScore", "")) or "-"
        strength = data.get("InstitutionalStrength", "")
        if not strength:
            tier = self._compact_institution_tier(data.get("InstitutionalTier", ""))
            institution_score = self._format_table_value("InstitutionalScore", data.get("InstitutionalScore", ""))
            strength = " · ".join(value for value in (tier, institution_score) if value)
        self.detail_score.set(f"{ranking} / {strength or '-'}")
        mode = str(data.get("BacktestMode", "") or "").strip().upper()
        samples_value = self._numeric_value(data.get("BacktestSamples", ""))
        samples = int(samples_value) if samples_value is not None else 0
        confidence = str(data.get("BacktestConfidenceTier", "") or "").strip() or "未评估"
        ranking_enabled = str(data.get("BacktestEligibleForRanking", "")).strip().lower() in {"true", "1", "yes", "y", "是"}
        if mode in {"", "NONE"}:
            backtest_parts = ["未评估"]
        else:
            backtest_parts = [mode, f"{samples}样本", confidence]
            if not ranking_enabled:
                backtest_parts.append("不参与排名")
        ticker_evidence = str(data.get("TickerEvidence", "") or "").strip()
        self.detail_backtest.set(ticker_evidence or " · ".join(value for value in backtest_parts if value) or "-")
        peer_evidence = str(data.get("PeerCalibrationEvidence", "") or "").strip()
        self.detail_peer_calibration.set(peer_evidence or "-")
        evidence_tier = str(data.get("EvidenceTier", "") or "").strip()
        evidence_score = self._format_table_value(
            "EvidenceStrengthScore", data.get("EvidenceStrengthScore", "")
        )
        self.detail_evidence.set(
            " · ".join(value for value in (evidence_tier, evidence_score) if value) or "-"
        )
        reason = data.get("TradeReadinessReason", "") or data.get("RankingReason", "") or "暂无额外执行说明。"
        evidence_reason = str(data.get("EvidenceReason", "") or "").strip()
        if evidence_reason:
            reason = f"{reason}\n\n证据：{evidence_reason}"
        skip_reason = str(data.get("BacktestSkipReason", "") or "").strip()
        if skip_reason:
            reason = f"{reason}\n\n回测：{skip_reason}。"
        elif confidence == "样本不足":
            reason = f"{reason}\n\n历史样本不足，回测暂不作为主要排序依据。"
        self.detail_reason.set(reason)
        if eligibility == "推荐":
            self.detail_signal_label.configure(fg_color="#ecfdf3", text_color="#166534")
        elif eligibility == "谨慎候选":
            self.detail_signal_label.configure(fg_color="#fffbeb", text_color="#92400e")
        elif eligibility == "风险过滤":
            self.detail_signal_label.configure(fg_color="#fef2f2", text_color="#991b1b")
        else:
            self.detail_signal_label.configure(fg_color="#eaf2fb", text_color="#1d4ed8")

    # Backtest workflow -------------------------------------------------------
    def _tickers_from_output_file(self, filename: str) -> list[str]:
        path = OUTPUT_DIR / filename
        if not path.exists():
            return []
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as file:
                reader = csv.DictReader(file)
                return list(
                    dict.fromkeys(
                        str(row.get("Ticker", "")).strip().upper()
                        for row in reader
                        if str(row.get("Ticker", "")).strip()
                    )
                )
        except (OSError, UnicodeError, csv.Error):
            return []

    def _visible_page_tickers(self) -> list[str]:
        columns = list(self.table["columns"])
        if "Ticker" not in columns:
            return []
        index = columns.index("Ticker")
        return [
            str(self.table.item(item, "values")[index]).strip().upper()
            for item in self.table.get_children()
            if len(self.table.item(item, "values")) > index
            and str(self.table.item(item, "values")[index]).strip()
        ]

    def _resolve_backtest_tickers(self) -> list[str]:
        scope_var = getattr(self, "backtest_scope", None)
        scope = scope_var.get() if scope_var is not None else "当前筛选"
        if scope == "当前页面":
            return self._visible_page_tickers()
        if scope == "当前筛选":
            return list(dict.fromkeys(self.filtered_tickers))
        if scope in BACKTEST_SCOPE_FILES:
            return self._tickers_from_output_file(BACKTEST_SCOPE_FILES[scope])
        if scope in {"新信号", "新买点"}:
            indexes = getattr(self, "_csv_indexes", {})
            ticker_index = indexes.get("Ticker")
            status_index = indexes.get("SignalStatus")
            if ticker_index is None or status_index is None:
                return []
            return list(
                dict.fromkeys(
                    self._cell_text(row[ticker_index]).upper()
                    for row in self._csv_rows
                    if len(row) > max(ticker_index, status_index)
                    and self._cell_text(row[ticker_index])
                    and self._cell_text(row[status_index]).strip().upper() == "NEW"
                )
            )
        if scope == "当前选中标的":
            data = self._selected_detail()
            ticker = str(data.get("Ticker", "")).strip().upper()
            return [ticker] if ticker else []
        return []

    def _start_backtest_for_tickers(self, tickers: list[str]) -> None:
        tickers = list(dict.fromkeys(ticker for ticker in tickers if ticker))
        if not tickers:
            _core.messagebox.showerror("无法运行回测", "当前回测范围没有有效标的。")
            return
        previous = list(self.filtered_tickers)
        self.filtered_tickers = tickers
        backtest_button = getattr(self, "backtest_button", None)
        daily_button = getattr(self, "daily_button", None)
        if daily_button is not None:
            daily_button.configure(state=_core.tk.DISABLED)
        if backtest_button is not None:
            backtest_button.configure(state=_core.tk.DISABLED, text="回测运行中")
        try:
            _core.ScannerGUI.start_backtest(self)
        finally:
            self.filtered_tickers = previous
        if not self.scan_running and backtest_button is not None:
            backtest_button.configure(state=_core.tk.NORMAL, text="▶ 运行回测")
            if daily_button is not None:
                daily_button.configure(state=_core.tk.NORMAL, text="⚡ 今日一键更新")

    def start_backtest(self) -> None:
        if self.scan_running:
            return _core.ScannerGUI.start_backtest(self)
        self._start_backtest_for_tickers(self._resolve_backtest_tickers())

    def _backtest_selected(self) -> None:
        data = self._selected_detail()
        ticker = str(data.get("Ticker", "")).strip().upper()
        self._start_backtest_for_tickers([ticker] if ticker else [])

    # Task completion ---------------------------------------------------------
    def scan_finished(self, code: int) -> None:
        daily_pipeline = bool(getattr(self, "_daily_pipeline_active", False))
        was_backtest = self.backtest_running
        # The generic core opens the backtest result dialog when backtest_running
        # is true. A daily run should instead land directly on the final mixed Top50.
        if daily_pipeline:
            self.backtest_running = False
        _core.ScannerGUI.scan_finished(self, code)
        self.start_button.configure(state=_core.tk.NORMAL, text="▶ 开始扫描")
        self.backtest_button.configure(state=_core.tk.NORMAL, text="▶ 运行回测")
        if hasattr(self, "daily_button"):
            self.daily_button.configure(state=_core.tk.NORMAL, text="⚡ 今日一键更新")
        if daily_pipeline:
            self._daily_pipeline_active = False
            if code == 0:
                self.load_csv("Top50Mixed.csv")
                self._update_run_quality_summary()
                self.status.set("今日全流程完成 · 数据闸门通过 · Top50 已发布")
                self.append_log(
                    "今日全流程完成：Top50Mixed.csv / Top50Stocks.csv / Top50ETF.csv 已刷新。\n"
                )
            else:
                self._show_log_for_error()
            return
        if code == 0 and not was_backtest and self.auto_backtest_recommended.get():
            tickers = self._tickers_from_output_file("Top50TradeReady.csv")
            if tickers:
                self.root.after(200, lambda values=tickers: self._start_backtest_for_tickers(values))

    def scan_failed(self, error: str) -> None:
        self._daily_pipeline_active = False
        _core.ScannerGUI.scan_failed(self, error)
        self.start_button.configure(state=_core.tk.NORMAL, text="▶ 开始扫描")
        self.backtest_button.configure(state=_core.tk.NORMAL, text="▶ 运行回测")
        if hasattr(self, "daily_button"):
            self.daily_button.configure(state=_core.tk.NORMAL, text="⚡ 今日一键更新")
        self._show_log_for_error()

    def append_log(self, text: str) -> None:
        _core.ScannerGUI.append_log(self, text)
        if "DAILY stage 1/4" in text:
            self.status.set("今日全流程 1/4 · 获取最新行情并扫描")
        elif "DAILY stage 2/4" in text:
            self.status.set("今日全流程 2/4 · 数据完整性与新鲜度校验")
        elif "DAILY stage 3/4" in text:
            self.status.set("今日全流程 3/4 · FAST回测与EXACT精炼")
        elif "DAILY stage 4/4" in text:
            self.status.set("今日全流程 4/4 · 同RunId校验与发布")
        lowered = text.casefold()
        if "traceback" in lowered or "异常" in text or "启动失败" in text:
            self._show_log_for_error()


# Preserve the historical import surface without mutating gui_core.ScannerGUI.
# External callers can import ``ScannerGUI`` from gui and get the v26 workstation.
ScannerGUI = DecisionScannerGUI  # type: ignore[assignment]

# Re-export commonly used names from gui_core for backward compatibility.
# Previously handled by a module-level __getattr__ which Pylance cannot resolve.
DATA_SOURCE_HINTS = _core.DATA_SOURCE_HINTS
DATA_SOURCE_CODES = _core.DATA_SOURCE_CODES
DISPLAY_VALUE_NAMES = _core.DISPLAY_VALUE_NAMES
DISPLAY_VALUE_CODES = _core.DISPLAY_VALUE_CODES
COLUMN_NAMES = _core.COLUMN_NAMES
COLUMN_WIDTHS = _core.COLUMN_WIDTHS
DISPLAY_COLUMNS = _core.DISPLAY_COLUMNS
NUMBER_COLUMNS = _core.NUMBER_COLUMNS
TEXT_COLUMNS = _core.TEXT_COLUMNS
INTEGER_COLUMNS = _core.INTEGER_COLUMNS
PERCENTAGE_COLUMNS = _core.PERCENTAGE_COLUMNS
FRACTION_PERCENTAGE_COLUMNS = _core.FRACTION_PERCENTAGE_COLUMNS
FOUR_DECIMAL_COLUMNS = _core.FOUR_DECIMAL_COLUMNS
MAX_RENDERED_ROWS = _core.MAX_RENDERED_ROWS
MISSING_VALUE_TEXTS = _core.MISSING_VALUE_TEXTS
CsvCacheToken = _core.CsvCacheToken
PROJECT_ROOT = _core.PROJECT_ROOT
MAIN_FILE = _core.MAIN_FILE
DOWNLOAD_PROGRESS_RE = _core.DOWNLOAD_PROGRESS_RE
FUNDAMENTAL_PROGRESS_RE = _core.FUNDAMENTAL_PROGRESS_RE
ANALYSE_PROGRESS_RE = _core.ANALYSE_PROGRESS_RE
BACKTEST_PROGRESS_RE = _core.BACKTEST_PROGRESS_RE
BACKTEST_ETA_RE = _core.BACKTEST_ETA_RE
BACKTEST_MODE_RE = _core.BACKTEST_MODE_RE


def main() -> None:
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    DecisionScannerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
