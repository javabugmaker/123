"""v85 A-share research briefing desktop GUI.

The scanner, ranking, backtest and publication behaviour remains inherited from
``gui.py``/``gui_core.py``.  This module only replaces the desktop information
architecture with an editorial research-terminal shell that is compact at
1366x768 and visually aligned with the static v85 briefing.
"""

from __future__ import annotations

from collections import Counter

import customtkinter as ctk

import gui as _legacy
import gui_core as _core
import gui_v84 as _v84
from v85_terminal_config import (
    BRAND_LABEL,
    COLORS,
    LAYOUT,
    NAV_ITEMS,
    PAGE_LABEL,
    TERMINAL_VERSION,
    TYPOGRAPHY,
)

GUI_VERSION = TERMINAL_VERSION


class ResearchBriefingGUI(_v84.ResearchTerminalGUI):
    """Editorial, high-density shell around the stable decision workstation."""

    def __init__(self, root) -> None:
        self.data_asof = _core.tk.StringVar(master=root, value="等待数据")
        self.header_note = _core.tk.StringVar(
            master=root,
            value="研究排名与交易执行分层 · 点击标的查看完整证据",
        )
        super().__init__(root)

    # ------------------------------------------------------------------
    # Complete layout shell
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        tk = _core.tk
        self._build_ui_configure_styles()
        self._build_ui_header()

        workspace = ctk.CTkFrame(self.root, corner_radius=0, fg_color=COLORS["background"])
        workspace.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))

        self._sidebar = ctk.CTkScrollableFrame(
            workspace,
            width=int(LAYOUT["sidebar_width"]),
            corner_radius=0,
            fg_color=COLORS["paper"],
            border_width=1,
            border_color=COLORS["line"],
            scrollbar_button_color="#C7CBD1",
            scrollbar_button_hover_color="#AEB4BC",
        )
        self._sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 10))
        self._sidebar.pack_propagate(False)

        self._content = ctk.CTkFrame(workspace, corner_radius=0, fg_color=COLORS["background"])
        self._content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_ui_controls()
        self._build_ui_navigation()
        self._build_ui_cards()
        self._build_ui_filters()
        self._build_ui_table_area()
        self._build_ui_decision_card()
        self._build_ui_footer()
        self._build_ui_log_panel()
        self._set_active_nav("mixed")

    def _build_ui_configure_styles(self) -> None:
        ttk = _core.ttk
        sans = str(TYPOGRAPHY["sans"])
        mono = str(TYPOGRAPHY["mono"])
        self.root.configure(fg_color=COLORS["background"])
        self.root.option_add("*Font", (sans, 9))
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Panel.TLabel", background=COLORS["paper"], foreground=COLORS["ink"])
        style.configure("Panel.TCheckbutton", background=COLORS["paper"], foreground=COLORS["ink"])
        style.configure(
            "Briefing.Treeview",
            rowheight=int(LAYOUT["table_row_height"]),
            font=(sans, 9),
            background=COLORS["paper"],
            fieldbackground=COLORS["paper"],
            foreground=COLORS["ink"],
            bordercolor=COLORS["line"],
            relief="flat",
        )
        style.configure(
            "Briefing.Treeview.Heading",
            font=(mono, 9, "bold"),
            background=COLORS["ink"],
            foreground="#FFFFFF",
            padding=(8, 8),
            bordercolor="#3C4045",
            relief="flat",
        )
        style.map(
            "Briefing.Treeview",
            background=[("selected", "#FCE8E8")],
            foreground=[("selected", COLORS["ink"])],
        )
        style.configure("Briefing.TCombobox", padding=4)
        style.configure("Briefing.TEntry", padding=4)

    def _build_ui_header(self) -> None:
        tk = _core.tk
        sans = str(TYPOGRAPHY["sans"])
        mono = str(TYPOGRAPHY["mono"])
        minimum = tuple(LAYOUT["minimum"])
        self.root.title("InstitutionScanner · A股研究简报")
        self.root.geometry(str(LAYOUT["window"]))
        self.root.minsize(int(minimum[0]), int(minimum[1]))

        header = ctk.CTkFrame(self.root, corner_radius=0, fg_color=COLORS["background"])
        header.pack(fill=tk.X, padx=18, pady=(14, 8))
        header.grid_columnconfigure(1, weight=1)

        brand = ctk.CTkFrame(header, corner_radius=0, fg_color="transparent")
        brand.grid(row=0, column=0, rowspan=2, sticky="sw")
        ctk.CTkLabel(
            brand,
            text=BRAND_LABEL,
            text_color=COLORS["ink"],
            font=(mono, 12, "bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand,
            text=PAGE_LABEL,
            text_color=COLORS["muted"],
            font=(sans, 9, "bold"),
        ).pack(anchor="w", pady=(1, 0))

        ctk.CTkLabel(
            header,
            textvariable=self.data_asof,
            text_color=COLORS["ink"],
            font=(mono, 29, "bold"),
        ).grid(row=0, column=1, rowspan=2, padx=(26, 0), sticky="w")

        live = ctk.CTkFrame(header, corner_radius=0, fg_color=COLORS["ink"])
        live.grid(row=0, column=2, rowspan=2, sticky="e")
        ctk.CTkLabel(
            live,
            text="LIVE",
            text_color="#FFFFFF",
            font=(mono, 9, "bold"),
        ).pack(side=tk.LEFT, padx=(10, 7), pady=6)
        ctk.CTkLabel(
            live,
            textvariable=self.status,
            text_color="#FFFFFF",
            font=(sans, 9),
        ).pack(side=tk.LEFT, padx=(0, 10), pady=6)

        rule = ctk.CTkFrame(self.root, height=1, corner_radius=0, fg_color=COLORS["ink"])
        rule.pack(fill=tk.X, padx=18, pady=(0, 8))
        ctk.CTkFrame(rule, width=11, height=11, corner_radius=0, fg_color=COLORS["red"]).place(
            relx=1.0, rely=0.5, anchor="e"
        )

    @staticmethod
    def _section_bar(parent, title: str) -> ctk.CTkFrame:
        tk = _core.tk
        mono = str(TYPOGRAPHY["mono"])
        frame = ctk.CTkFrame(parent, corner_radius=0, fg_color=COLORS["ink"])
        frame.pack(fill=tk.X, pady=(0, 8))
        ctk.CTkFrame(frame, width=4, corner_radius=0, fg_color=COLORS["red"]).pack(
            side=tk.LEFT, fill=tk.Y
        )
        ctk.CTkLabel(
            frame,
            text=title,
            text_color="#FFFFFF",
            font=(mono, 9, "bold"),
            anchor="w",
        ).pack(side=tk.LEFT, padx=9, pady=7)
        return frame

    def _sidebar_label(self, text: str) -> None:
        ctk.CTkLabel(
            self._sidebar,
            text=text,
            text_color=COLORS["muted"],
            font=(str(TYPOGRAPHY["sans"]), 9, "bold"),
            anchor="w",
        ).pack(fill=_core.tk.X, pady=(0, 3))

    def _build_ui_controls(self) -> None:
        tk = _core.tk
        ttk = _core.ttk
        self._section_bar(self._sidebar, "SCAN CONTROL / 扫描控制")

        self._sidebar_label("扫描范围")
        self.scope_menu = ctk.CTkOptionMenu(
            self._sidebar,
            variable=self.scope,
            values=["全部股票和ETF", "仅股票", "仅ETF"],
            height=30,
            corner_radius=0,
            fg_color=COLORS["soft"],
            button_color=COLORS["ink"],
            button_hover_color="#32363B",
            text_color=COLORS["ink"],
        )
        self.scope_menu.pack(fill=tk.X, pady=(0, 8))

        self._sidebar_label("扫描模式")
        self.scan_mode_menu = ctk.CTkOptionMenu(
            self._sidebar,
            variable=self.scan_mode,
            values=["快速", "标准", "完整刷新", "自定义"],
            command=self._scan_mode_changed,
            height=30,
            corner_radius=0,
            fg_color=COLORS["soft"],
            button_color=COLORS["ink"],
            button_hover_color="#32363B",
            text_color=COLORS["ink"],
        )
        self.scan_mode_menu.pack(fill=tk.X, pady=(0, 8))

        self._sidebar_label("指定代码（可选）")
        self.ticker_entry = ctk.CTkEntry(
            self._sidebar,
            textvariable=self.tickers,
            placeholder_text="588000.SH, 000001.SZ",
            height=30,
            corner_radius=0,
            border_color=COLORS["line"],
        )
        self.ticker_entry.pack(fill=tk.X, pady=(0, 10))

        self.daily_button = ctk.CTkButton(
            self._sidebar,
            text="今日一键更新",
            command=self.start_daily_pipeline,
            height=34,
            corner_radius=0,
            fg_color=COLORS["red"],
            hover_color=COLORS["red_dark"],
            font=(str(TYPOGRAPHY["sans"]), 10, "bold"),
        )
        self.daily_button.pack(fill=tk.X, pady=(0, 6))

        actions = ctk.CTkFrame(self._sidebar, corner_radius=0, fg_color="transparent")
        actions.pack(fill=tk.X, pady=(0, 6))
        actions.grid_columnconfigure((0, 1), weight=1, uniform="action")
        self.start_button = ctk.CTkButton(
            actions,
            text="开始扫描",
            command=self.start_scan,
            height=32,
            corner_radius=0,
            fg_color=COLORS["ink"],
            hover_color="#30343A",
        )
        self.start_button.grid(row=0, column=0, padx=(0, 3), sticky="ew")
        self.backtest_button = ctk.CTkButton(
            actions,
            text="运行回测",
            command=self.start_backtest,
            height=32,
            corner_radius=0,
            fg_color=COLORS["paper"],
            hover_color=COLORS["soft"],
            text_color=COLORS["ink"],
            border_width=1,
            border_color=COLORS["ink"],
        )
        self.backtest_button.grid(row=0, column=1, padx=(3, 0), sticky="ew")

        self.cancel_button = ctk.CTkButton(
            self._sidebar,
            text="停止当前任务",
            command=self.cancel_running_task,
            state=tk.DISABLED,
            height=28,
            corner_radius=0,
            fg_color="#777D85",
            hover_color="#5E646B",
        )
        self.cancel_button.pack(fill=tk.X, pady=(0, 10))

        self._section_bar(self._sidebar, "DATA & OUTPUT / 数据与输出")
        self.source_box = ttk.Combobox(
            self._sidebar,
            textvariable=self.data_source,
            values=("TickFlow Free",),
            state="disabled",
            style="Briefing.TCombobox",
        )
        self.source_box.pack(fill=tk.X, pady=(0, 6))
        ctk.CTkLabel(
            self._sidebar,
            text="日K：TickFlow Free\n基本面：AkShare 低频缓存",
            text_color=COLORS["muted"],
            justify="left",
            anchor="w",
            font=(str(TYPOGRAPHY["sans"]), 8),
        ).pack(fill=tk.X, pady=(0, 8))

        utility = ctk.CTkFrame(self._sidebar, corner_radius=0, fg_color="transparent")
        utility.pack(fill=tk.X, pady=(0, 6))
        utility.grid_columnconfigure((0, 1), weight=1, uniform="utility")
        ctk.CTkButton(
            utility,
            text="刷新结果",
            command=self.refresh_results,
            height=29,
            corner_radius=0,
            fg_color=COLORS["soft"],
            hover_color="#DFE2E6",
            text_color=COLORS["ink"],
        ).grid(row=0, column=0, padx=(0, 3), sticky="ew")
        ctk.CTkButton(
            utility,
            text="结果目录",
            command=self.open_output,
            height=29,
            corner_radius=0,
            fg_color=COLORS["soft"],
            hover_color="#DFE2E6",
            text_color=COLORS["ink"],
        ).grid(row=0, column=1, padx=(3, 0), sticky="ew")

        ctk.CTkButton(
            self._sidebar,
            text="高级设置  +",
            command=self._toggle_advanced,
            height=28,
            corner_radius=0,
            fg_color="transparent",
            hover_color=COLORS["soft"],
            text_color=COLORS["muted"],
            border_width=1,
            border_color=COLORS["line"],
        ).pack(fill=tk.X, pady=(0, 6))

        self.advanced_frame = ctk.CTkFrame(
            self._sidebar,
            corner_radius=0,
            fg_color=COLORS["soft"],
            border_width=1,
            border_color=COLORS["line"],
        )
        for text, variable, command in (
            ("优先缓存", self.cache_first, self._advanced_changed),
            ("强制重新下载", self.force_download, self._advanced_changed),
            ("不使用断点", self.no_resume, self._advanced_changed),
            ("刷新基本面", self.refresh_fundamentals, self._advanced_changed),
            ("扫描后回测强推荐", self.auto_backtest_recommended, None),
        ):
            ctk.CTkCheckBox(
                self.advanced_frame,
                text=text,
                variable=variable,
                command=command,
                checkbox_width=16,
                checkbox_height=16,
                corner_radius=0,
                fg_color=COLORS["red"],
                hover_color=COLORS["red_dark"],
                text_color=COLORS["ink"],
                font=(str(TYPOGRAPHY["sans"]), 8),
            ).pack(anchor="w", padx=9, pady=4)

    def _build_ui_navigation(self) -> None:
        tk = _core.tk
        nav = ctk.CTkFrame(self._content, corner_radius=0, fg_color=COLORS["ink"])
        nav.pack(fill=tk.X, pady=(0, 8))
        ctk.CTkFrame(nav, width=5, corner_radius=0, fg_color=COLORS["red"]).pack(
            side=tk.LEFT, fill=tk.Y
        )
        ctk.CTkLabel(
            nav,
            text="RESEARCH UNIVERSE",
            text_color="#FFFFFF",
            font=(str(TYPOGRAPHY["mono"]), 9, "bold"),
        ).pack(side=tk.LEFT, padx=(10, 8), pady=7)
        for key, label in NAV_ITEMS:
            button = ctk.CTkButton(
                nav,
                text=label,
                width=58,
                height=28,
                corner_radius=0,
                fg_color="transparent",
                hover_color="#30343A",
                text_color="#D7DBE0",
                font=(str(TYPOGRAPHY["sans"]), 9, "bold"),
                command=lambda nav_key=key: self._load_navigation(nav_key),
            )
            button.pack(side=tk.LEFT, padx=1, pady=5)
            self._nav_buttons[key] = button

    def _build_ui_cards(self) -> None:
        tk = _core.tk
        cards = ctk.CTkFrame(self._content, corner_radius=0, fg_color="transparent")
        cards.pack(fill=tk.X, pady=(0, 8))
        for column in range(4):
            cards.grid_columnconfigure(column, weight=1, uniform="summary")
        specs = (
            ("可执行", self.card_recommended, COLORS["red"], "READY / 推荐"),
            ("谨慎候选", self.card_cautious, COLORS["amber"], "CAUTIOUS"),
            ("新信号", self.card_new, COLORS["ink"], "SignalStatus = NEW"),
            ("资产结构", self.card_total, COLORS["green"], "股票 / ETF"),
        )
        for column, (title, variable, accent, note) in enumerate(specs):
            card = ctk.CTkFrame(
                cards,
                corner_radius=0,
                fg_color=COLORS["paper"],
                border_width=1,
                border_color=COLORS["line"],
            )
            card.grid(
                row=0,
                column=column,
                padx=(0 if column == 0 else 4, 0 if column == 3 else 4),
                sticky="ew",
            )
            ctk.CTkFrame(card, height=3, corner_radius=0, fg_color=accent).pack(fill=tk.X)
            line = ctk.CTkFrame(card, corner_radius=0, fg_color="transparent")
            line.pack(fill=tk.X, padx=11, pady=(6, 5))
            ctk.CTkLabel(
                line,
                text=title,
                text_color=COLORS["muted"],
                font=(str(TYPOGRAPHY["sans"]), 8, "bold"),
            ).pack(side=tk.LEFT)
            ctk.CTkLabel(
                line,
                textvariable=variable,
                text_color=COLORS["ink"],
                font=(str(TYPOGRAPHY["mono"]), 19, "bold"),
            ).pack(side=tk.RIGHT)
            ctk.CTkLabel(
                card,
                text=note,
                text_color=COLORS["muted"],
                font=(str(TYPOGRAPHY["mono"]), 7),
                anchor="w",
            ).pack(fill=tk.X, padx=11, pady=(0, 6))

    def _build_ui_filters(self) -> None:
        tk = _core.tk
        ttk = _core.ttk
        filters_root = ctk.CTkFrame(
            self._content,
            corner_radius=0,
            fg_color=COLORS["paper"],
            border_width=1,
            border_color=COLORS["line"],
        )
        filters_root.pack(fill=tk.X, pady=(0, 8))
        top = ctk.CTkFrame(filters_root, corner_radius=0, fg_color="transparent")
        top.pack(fill=tk.X, padx=9, pady=7)
        if not hasattr(self, "asset_filter"):
            self.asset_filter = tk.StringVar(value="全部类型")
            self.tier_filter = tk.StringVar(value="全部等级")
            self.score_filter = tk.StringVar(value="全部分数")
            for variable in (self.asset_filter, self.tier_filter, self.score_filter):
                variable.trace_add("write", self._schedule_filter_refresh)

        def add_box(label: str, widget, column: int, width_pad: tuple[int, int] = (0, 7)) -> None:
            ctk.CTkLabel(top, text=label, text_color=COLORS["muted"], font=(str(TYPOGRAPHY["sans"]), 8)).grid(
                row=0, column=column, padx=(0, 3), sticky="w"
            )
            widget.grid(row=0, column=column + 1, padx=width_pad, sticky="w")

        self.asset_box = ttk.Combobox(
            top,
            textvariable=self.asset_filter,
            values=("全部类型", "股票", "ETF"),
            state="readonly",
            width=8,
            style="Briefing.TCombobox",
        )
        add_box("类型", self.asset_box, 0)
        self.industry_box = ttk.Combobox(
            top, textvariable=self.industry_filter, state="readonly", width=12, style="Briefing.TCombobox"
        )
        add_box("行业", self.industry_box, 2)
        self.entry_box = ttk.Combobox(
            top, textvariable=self.entry_filter, state="readonly", width=12, style="Briefing.TCombobox"
        )
        add_box("信号", self.entry_box, 4)
        self.eligibility_box = ttk.Combobox(
            top,
            textvariable=self.eligibility_filter,
            values=("全部资格", "推荐", "谨慎候选", "观察", "风险过滤"),
            state="readonly",
            width=9,
            style="Briefing.TCombobox",
        )
        add_box("资格", self.eligibility_box, 6)
        self.score_box = ttk.Combobox(
            top,
            textvariable=self.score_filter,
            values=("全部分数", "≥25", "≥30", "≥35", "≥40", "≥50"),
            state="readonly",
            width=8,
            style="Briefing.TCombobox",
        )
        add_box("最低分", self.score_box, 8)
        top.grid_columnconfigure(11, weight=1)
        ctk.CTkLabel(top, text="搜索", text_color=COLORS["muted"], font=(str(TYPOGRAPHY["sans"]), 8)).grid(
            row=0, column=10, padx=(0, 3), sticky="w"
        )
        self.search_entry = ttk.Entry(top, textvariable=self.search, style="Briefing.TEntry")
        self.search_entry.grid(row=0, column=11, padx=(0, 7), sticky="ew")
        ctk.CTkButton(
            top,
            text="更多",
            width=50,
            height=27,
            corner_radius=0,
            fg_color=COLORS["soft"],
            hover_color="#DFE2E6",
            text_color=COLORS["ink"],
            command=self._toggle_more_filters,
        ).grid(row=0, column=12, padx=(0, 4))
        ctk.CTkButton(
            top,
            text="重置",
            width=50,
            height=27,
            corner_radius=0,
            fg_color=COLORS["ink"],
            hover_color="#30343A",
            command=self.clear_filters,
        ).grid(row=0, column=13)

        self.filter_more_frame = ctk.CTkFrame(filters_root, corner_radius=0, fg_color=COLORS["soft"])
        self.sector_box = ttk.Combobox(
            self.filter_more_frame,
            textvariable=self.sector_filter,
            state="readonly",
            width=14,
            style="Briefing.TCombobox",
        )
        self.stage_box = ttk.Combobox(
            self.filter_more_frame,
            textvariable=self.stage_filter,
            state="readonly",
            width=14,
            style="Briefing.TCombobox",
        )
        self.tier_box = ttk.Combobox(
            self.filter_more_frame,
            textvariable=self.tier_filter,
            state="readonly",
            width=12,
            style="Briefing.TCombobox",
        )
        for column, (label, widget) in enumerate(
            (("板块", self.sector_box), ("资金阶段", self.stage_box), ("机构等级", self.tier_box))
        ):
            ctk.CTkLabel(
                self.filter_more_frame,
                text=label,
                text_color=COLORS["muted"],
                font=(str(TYPOGRAPHY["sans"]), 8),
            ).grid(row=0, column=column * 2, padx=(10 if column == 0 else 0, 4), pady=7)
            widget.grid(row=0, column=column * 2 + 1, padx=(0, 14), pady=7)
        self.sector_box.bind("<<ComboboxSelected>>", self._sector_changed)

    def _build_ui_table_area(self) -> None:
        tk = _core.tk
        ttk = _core.ttk
        body = ttk.PanedWindow(self._content, orient=tk.HORIZONTAL)
        self.body_paned = body
        body.pack(fill=tk.BOTH, expand=True, pady=(0, 8))

        table_frame = ctk.CTkFrame(body, corner_radius=0, fg_color=COLORS["paper"])
        self._table_frame = table_frame
        table_frame.grid_rowconfigure(1, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        result_bar = ctk.CTkFrame(table_frame, corner_radius=0, fg_color=COLORS["ink"])
        result_bar.grid(row=0, column=0, columnspan=2, sticky="ew")
        ctk.CTkFrame(result_bar, width=5, corner_radius=0, fg_color=COLORS["red"]).pack(
            side=tk.LEFT, fill=tk.Y
        )
        ctk.CTkLabel(
            result_bar,
            textvariable=self.view_title,
            text_color="#FFFFFF",
            font=(str(TYPOGRAPHY["mono"]), 10, "bold"),
        ).pack(side=tk.LEFT, padx=(10, 8), pady=8)
        ctk.CTkLabel(
            result_bar,
            textvariable=self.result_summary,
            text_color="#BFC4CA",
            font=(str(TYPOGRAPHY["sans"]), 8),
        ).pack(side=tk.LEFT, padx=(0, 8), pady=8)
        self.detail_toggle_button = ctk.CTkButton(
            result_bar,
            text="详情 ‹",
            width=62,
            height=25,
            corner_radius=0,
            fg_color="#2B2F34",
            hover_color="#3A3F45",
            text_color="#FFFFFF",
            command=self._toggle_detail_panel,
        )
        self.detail_toggle_button.pack(side=tk.RIGHT, padx=7, pady=5)
        self.next_page_button = ctk.CTkButton(
            result_bar,
            text="下一页",
            width=55,
            height=25,
            corner_radius=0,
            fg_color="transparent",
            hover_color="#30343A",
            command=self._show_next_page,
        )
        self.next_page_button.pack(side=tk.RIGHT, padx=1, pady=5)
        ctk.CTkLabel(
            result_bar,
            textvariable=self.page_summary,
            text_color="#BFC4CA",
            font=(str(TYPOGRAPHY["mono"]), 8),
        ).pack(side=tk.RIGHT, padx=4)
        self.previous_page_button = ctk.CTkButton(
            result_bar,
            text="上一页",
            width=55,
            height=25,
            corner_radius=0,
            fg_color="transparent",
            hover_color="#30343A",
            command=self._show_previous_page,
        )
        self.previous_page_button.pack(side=tk.RIGHT, padx=1, pady=5)

        self.table = ttk.Treeview(
            table_frame,
            show="headings",
            selectmode="browse",
            style="Briefing.Treeview",
        )
        self.table.tag_configure("eligibility-recommended", background="#FFF3F3", foreground=COLORS["red_dark"])
        self.table.tag_configure("eligibility-cautious", background="#FFF8ED", foreground="#8D5713")
        self.table.tag_configure("risk-filter", background="#F0F7F3", foreground=COLORS["green"])
        self.table.tag_configure("eligibility-observe", background=COLORS["paper"], foreground=COLORS["ink"])
        ybar = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.table.yview)
        xbar = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.table.xview)
        self.table.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.table.grid(row=1, column=0, sticky="nsew")
        ybar.grid(row=1, column=1, sticky="ns")
        xbar.grid(row=2, column=0, sticky="ew")
        self.table.bind("<<TreeviewSelect>>", self._update_decision_card)
        self.table.bind("<Double-1>", self.show_selected_detail)
        self.table.bind("<Return>", self.show_selected_detail)

    def _build_ui_decision_card(self) -> None:
        tk = _core.tk
        detail = ctk.CTkFrame(
            self.body_paned,
            width=int(LAYOUT["detail_width"]),
            corner_radius=0,
            fg_color=COLORS["paper"],
            border_width=1,
            border_color=COLORS["line"],
        )
        self.detail_panel = detail
        detail.pack_propagate(False)
        head = ctk.CTkFrame(detail, corner_radius=0, fg_color=COLORS["ink"])
        head.pack(fill=tk.X)
        ctk.CTkFrame(head, width=5, corner_radius=0, fg_color=COLORS["red"]).pack(
            side=tk.LEFT, fill=tk.Y
        )
        ctk.CTkLabel(
            head,
            text="SECURITY BRIEF",
            text_color="#FFFFFF",
            font=(str(TYPOGRAPHY["mono"]), 9, "bold"),
        ).pack(side=tk.LEFT, padx=9, pady=8)
        ctk.CTkLabel(
            detail,
            textvariable=self.detail_title,
            text_color=COLORS["ink"],
            font=(str(TYPOGRAPHY["mono"]), 16, "bold"),
        ).pack(anchor="w", padx=14, pady=(12, 0))
        ctk.CTkLabel(
            detail,
            textvariable=self.detail_subtitle,
            text_color=COLORS["muted"],
            font=(str(TYPOGRAPHY["sans"]), 8),
        ).pack(anchor="w", padx=14, pady=(1, 8))
        self.detail_signal_label = ctk.CTkLabel(
            detail,
            textvariable=self.detail_signal,
            fg_color=COLORS["soft"],
            corner_radius=0,
            text_color=COLORS["red_dark"],
            font=(str(TYPOGRAPHY["sans"]), 10, "bold"),
            height=30,
        )
        self.detail_signal_label.pack(fill=tk.X, padx=14, pady=(0, 8))
        for label, variable in (
            ("近期状态", self.detail_recent),
            ("参考买点", self.detail_buy),
            ("止损位", self.detail_stop),
            ("目标 / 盈亏比", self.detail_risk_geometry),
            ("交易资格", self.detail_eligibility),
            ("榜单 / 全局", self.detail_rank),
            ("排序 / 机构", self.detail_score),
            ("本票回测", self.detail_backtest),
            ("同类校准", self.detail_peer_calibration),
            ("证据等级", self.detail_evidence),
        ):
            row = ctk.CTkFrame(detail, corner_radius=0, fg_color="transparent")
            row.pack(fill=tk.X, padx=14, pady=2)
            ctk.CTkLabel(
                row,
                text=label,
                text_color=COLORS["muted"],
                width=78,
                anchor="w",
                font=(str(TYPOGRAPHY["sans"]), 8),
            ).pack(side=tk.LEFT)
            ctk.CTkLabel(
                row,
                textvariable=variable,
                text_color=COLORS["ink"],
                font=(str(TYPOGRAPHY["mono"]), 8, "bold"),
                anchor="e",
            ).pack(side=tk.RIGHT, fill=tk.X, expand=True)
        ctk.CTkLabel(
            detail,
            text="执行说明",
            text_color=COLORS["muted"],
            font=(str(TYPOGRAPHY["sans"]), 8, "bold"),
        ).pack(anchor="w", padx=14, pady=(9, 2))
        ctk.CTkLabel(
            detail,
            textvariable=self.detail_reason,
            text_color=COLORS["ink"],
            justify="left",
            anchor="nw",
            wraplength=244,
            font=(str(TYPOGRAPHY["sans"]), 8),
        ).pack(fill=tk.X, padx=14)
        actions = ctk.CTkFrame(detail, corner_radius=0, fg_color="transparent")
        actions.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=12)
        ctk.CTkButton(
            actions,
            text="回测此标的",
            width=112,
            height=29,
            corner_radius=0,
            command=self._backtest_selected,
            fg_color=COLORS["ink"],
            hover_color="#30343A",
        ).pack(side=tk.LEFT)
        ctk.CTkButton(
            actions,
            text="完整详情",
            width=104,
            height=29,
            corner_radius=0,
            command=self.show_selected_detail,
            fg_color=COLORS["red"],
            hover_color=COLORS["red_dark"],
        ).pack(side=tk.RIGHT)
        self.body_paned.add(self._table_frame, weight=1)
        self.body_paned.add(detail, weight=0)

    def _build_ui_footer(self) -> None:
        tk = _core.tk
        ttk = _core.ttk
        footer = ctk.CTkFrame(
            self._content,
            corner_radius=0,
            fg_color=COLORS["paper"],
            border_width=1,
            border_color=COLORS["line"],
        )
        footer.pack(fill=tk.X)
        self.footer_frame = footer
        ctk.CTkLabel(
            footer,
            text="回测范围",
            text_color=COLORS["muted"],
            font=(str(TYPOGRAPHY["sans"]), 8),
        ).pack(side=tk.LEFT, padx=(9, 4), pady=6)
        self.backtest_scope_menu = ctk.CTkOptionMenu(
            footer,
            variable=self.backtest_scope,
            values=["当前页面", "当前筛选", "股票 Top50", "ETF Top50", "综合 Top50", "强推荐", "新信号", "当前选中标的"],
            width=124,
            height=26,
            corner_radius=0,
            fg_color=COLORS["soft"],
            button_color=COLORS["ink"],
            button_hover_color="#30343A",
            text_color=COLORS["ink"],
            font=(str(TYPOGRAPHY["sans"]), 8),
        )
        self.backtest_scope_menu.pack(side=tk.LEFT, pady=5)
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=150)
        self.progress.pack(side=tk.LEFT, padx=9, pady=7)
        ctk.CTkLabel(
            footer,
            textvariable=self.run_quality,
            text_color=COLORS["muted"],
            font=(str(TYPOGRAPHY["sans"]), 8),
        ).pack(side=tk.LEFT, padx=(0, 6), pady=6)
        ctk.CTkButton(
            footer,
            text="性能",
            width=48,
            height=25,
            corner_radius=0,
            fg_color="transparent",
            hover_color=COLORS["soft"],
            text_color=COLORS["ink"],
            border_width=1,
            border_color=COLORS["line"],
            command=self._show_run_performance,
        ).pack(side=tk.LEFT, padx=(0, 6), pady=5)
        self.log_toggle_button = ctk.CTkButton(
            footer,
            text="日志 ›",
            width=54,
            height=25,
            corner_radius=0,
            fg_color=COLORS["ink"],
            hover_color="#30343A",
            command=self._toggle_log,
        )
        self.log_toggle_button.pack(side=tk.RIGHT, padx=6, pady=5)

    def _build_ui_log_panel(self) -> None:
        tk = _core.tk
        self.log_panel = ctk.CTkFrame(self._content, corner_radius=0, fg_color=COLORS["ink"])
        log_header = ctk.CTkFrame(self.log_panel, corner_radius=0, fg_color="transparent")
        log_header.pack(fill=tk.X, padx=8, pady=(6, 0))
        ctk.CTkLabel(
            log_header,
            text="RUN LOG / 运行日志",
            text_color="#FFFFFF",
            font=(str(TYPOGRAPHY["mono"]), 8, "bold"),
        ).pack(side=tk.LEFT)
        ctk.CTkButton(
            log_header,
            text="清空",
            width=48,
            height=23,
            corner_radius=0,
            fg_color="#34383E",
            hover_color="#464B52",
            command=self.clear_log,
        ).pack(side=tk.RIGHT)
        self.log_text = tk.Text(
            self.log_panel,
            height=6,
            wrap=tk.NONE,
            state=tk.DISABLED,
            bg=COLORS["ink"],
            fg="#D5D9DE",
            insertbackground="white",
            relief=tk.FLAT,
            padx=10,
            pady=7,
            font=(str(TYPOGRAPHY["mono"]), 8),
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=(3, 6))

    # ------------------------------------------------------------------
    # Small compatibility/layout adaptations
    # ------------------------------------------------------------------
    def _toggle_advanced(self) -> None:
        self._advanced_visible = not self._advanced_visible
        if self._advanced_visible:
            self.advanced_frame.pack(fill=_core.tk.X, pady=(0, 8))
        else:
            self.advanced_frame.pack_forget()

    def _set_active_nav(self, key: str) -> None:
        self._active_nav = key
        for nav_key, button in self._nav_buttons.items():
            if nav_key == key:
                button.configure(
                    fg_color=COLORS["red"],
                    hover_color=COLORS["red_dark"],
                    text_color="#FFFFFF",
                )
            else:
                button.configure(
                    fg_color="transparent",
                    hover_color="#30343A",
                    text_color="#D7DBE0",
                )
        if key in _legacy.NAV_TITLES:
            self.view_title.set(_legacy.NAV_TITLES[key])

    def _refresh_data_asof(self) -> None:
        index = self._csv_indexes.get("DataAsOf")
        if index is None:
            self.data_asof.set("等待数据")
            return
        values = Counter(
            str(row[index]).strip()
            for row in self._csv_rows
            if index < len(row) and str(row[index]).strip()
        )
        self.data_asof.set(values.most_common(1)[0][0] if values else "等待数据")

    def load_csv(self, filename: str, preserve_new_signal: bool = False) -> bool:
        loaded = super().load_csv(filename, preserve_new_signal=preserve_new_signal)
        if loaded:
            self._refresh_data_asof()
        return loaded


ScannerGUI = ResearchBriefingGUI


def main() -> None:
    _v84.install_v84_presentation()
    ctk.set_appearance_mode("light")
    root = ctk.CTk(fg_color=COLORS["background"])
    ResearchBriefingGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
