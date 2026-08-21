"""v84 中文研究终端桌面展示层。

复用 gui.py 的成熟扫描、回测、筛选、日志和详情逻辑，只重构信息架构与视觉语言：
研究排名负责“谁强”，交易排名/执行状态负责“谁现在能做”。

模块导入本身不修改 gui_core 全局状态；只有真正启动 v84 工作台时才安装展示配置，
从而保证旧 GUI、旧测试与 v84 可以长期共存。
"""

from __future__ import annotations

import customtkinter as ctk

import gui as _legacy
import gui_core as _core

背景 = "#F1F2F4"
纸色 = "#FFFFFF"
墨色 = "#15171A"
次级 = "#6B7078"
线色 = "#D9DDE3"
软灰 = "#EEF0F3"
强调红 = "#E33D3D"
深红 = "#B52B32"
通过绿 = "#197A55"
观察黄 = "#B56A13"

GUI_VERSION = "2026-08-21-v84-chinese-research-terminal-v2"

V84_DISPLAY_COLUMNS = (
    "ResearchRank",
    "TradeRank",
    "Ticker",
    "Name",
    "AssetType",
    "IndustryTopic",
    "Close",
    "AlphaScore",
    "ExecutionState",
    "EntrySignal",
    "ReferenceBuyPrice",
    "StopLoss",
    "ProjectedTarget",
    "SmoothTriggerScore",
    "SignalStatus",
    "SignalDays",
)

V84_COLUMN_NAMES = {
    "ResearchRank": "研究排名",
    "TradeRank": "交易排名",
    "AlphaScore": "研究 Alpha",
    "ExecutionState": "执行状态",
    "SmoothTriggerScore": "平滑触发",
    "QualityLayerStatus": "质量层",
    "ProjectedTarget": "模型目标",
    "ReferenceBuyPrice": "参考买点",
    "IndustryTopic": "行业 / 主题",
    "Close": "收盘",
    "EntrySignal": "技术信号",
    "SignalStatus": "信号状态",
    "SignalDays": "持续天数",
}

V84_COLUMN_WIDTHS = {
    "ResearchRank": 68,
    "TradeRank": 68,
    "Ticker": 94,
    "Name": 108,
    "AssetType": 54,
    "IndustryTopic": 118,
    "Close": 80,
    "AlphaScore": 86,
    "ExecutionState": 82,
    "EntrySignal": 102,
    "ReferenceBuyPrice": 110,
    "StopLoss": 80,
    "ProjectedTarget": 86,
    "SmoothTriggerScore": 86,
    "SignalStatus": 82,
    "SignalDays": 66,
}


def install_v84_presentation() -> None:
    """幂等安装 v84 展示字段；不改变任何评分、筛选或交易决策逻辑。"""
    _core.DISPLAY_COLUMNS = V84_DISPLAY_COLUMNS
    _core.COLUMN_NAMES.update(V84_COLUMN_NAMES)
    _core.COLUMN_WIDTHS.update(V84_COLUMN_WIDTHS)
    for column in ("ResearchRank", "TradeRank", "AlphaScore", "SmoothTriggerScore"):
        _core.NUMBER_COLUMNS.add(column)
    for column in ("ResearchRank", "TradeRank"):
        _core.INTEGER_COLUMNS.add(column)


class ResearchTerminalGUI(_legacy.DecisionScannerGUI):
    """灰白黑红的中文研究终端，业务行为继承稳定工作台。"""

    def __init__(self, root) -> None:
        install_v84_presentation()
        super().__init__(root)

    def _build_ui_configure_styles(self) -> None:
        ttk = _core.ttk
        self.root.configure(fg_color=背景)
        style = ttk.Style()
        style.configure("Panel.TLabel", background=纸色, foreground=墨色)
        style.configure("Panel.TCheckbutton", background=纸色, foreground=墨色)
        style.configure(
            "Compact.Treeview",
            rowheight=30,
            font=("Microsoft YaHei UI", 9),
            background=纸色,
            fieldbackground=纸色,
            foreground=墨色,
            bordercolor=线色,
        )
        style.configure(
            "Compact.Treeview.Heading",
            font=("Microsoft YaHei UI", 9, "bold"),
            background=墨色,
            foreground="#FFFFFF",
            padding=(8, 9),
            bordercolor=墨色,
        )
        style.map(
            "Compact.Treeview",
            background=[("selected", "#FCE8E8")],
            foreground=[("selected", 墨色)],
        )

    def _build_ui_header(self) -> None:
        tk = _core.tk
        self.root.title("InstitutionScanner · 中文研究终端")
        self.root.geometry("1560x940")
        self.root.minsize(1200, 740)
        header = ctk.CTkFrame(self.root, corner_radius=0, fg_color=背景)
        header.pack(fill=tk.X, padx=24, pady=(18, 8))
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            header,
            text="机构交易研究终端",
            text_color=墨色,
            font=("Microsoft YaHei UI", 17, "bold"),
        ).grid(row=0, column=0, sticky="sw")
        ctk.CTkLabel(
            header,
            text="研究排名 · 执行状态 · 买点 · 风险 · 回测",
            text_color=次级,
            font=("Microsoft YaHei UI", 9),
        ).grid(row=1, column=0, sticky="nw", pady=(2, 0))
        status_box = ctk.CTkFrame(header, corner_radius=0, fg_color=墨色)
        status_box.grid(row=0, column=2, rowspan=2, sticky="e")
        ctk.CTkLabel(
            status_box,
            text="● TickFlow 日 K",
            text_color="#FFFFFF",
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side=tk.LEFT, padx=(10, 8), pady=7)
        ctk.CTkLabel(
            status_box,
            textvariable=self.status,
            text_color="#FFFFFF",
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=(0, 10), pady=7)
        ctk.CTkFrame(self.root, height=1, corner_radius=0, fg_color=墨色).pack(
            fill=tk.X, padx=24, pady=(0, 8)
        )

    def _build_ui_controls(self) -> None:
        super()._build_ui_controls()
        self.daily_button.configure(
            text="今日一键更新",
            fg_color=强调红,
            hover_color=深红,
            corner_radius=2,
        )
        self.start_button.configure(
            text="开始扫描",
            fg_color=墨色,
            hover_color="#2E3135",
            corner_radius=2,
        )
        self.backtest_button.configure(
            text="运行回测",
            fg_color=纸色,
            hover_color=软灰,
            text_color=墨色,
            border_width=1,
            border_color=墨色,
            corner_radius=2,
        )
        self.cancel_button.configure(
            text="停止",
            fg_color="#7A8087",
            hover_color="#5F656C",
            corner_radius=2,
        )

    def _build_ui_navigation(self) -> None:
        super()._build_ui_navigation()
        for button in self._nav_buttons.values():
            button.configure(
                corner_radius=2,
                fg_color="transparent",
                hover_color=软灰,
                text_color=墨色,
            )

    def _build_ui_cards(self) -> None:
        tk = _core.tk
        cards = ctk.CTkFrame(self.root, fg_color="transparent")
        cards.pack(fill=tk.X, padx=18, pady=(0, 8))
        for column in range(4):
            cards.grid_columnconfigure(column, weight=1, uniform="cards")
        specs = (
            ("可执行", self.card_recommended, 强调红),
            ("谨慎候选", self.card_cautious, 观察黄),
            ("新信号", self.card_new, 墨色),
            ("资产结构", self.card_total, 通过绿),
        )
        for column, (title, variable, accent) in enumerate(specs):
            card = ctk.CTkFrame(
                cards,
                corner_radius=0,
                fg_color=纸色,
                border_width=1,
                border_color=线色,
            )
            card.grid(
                row=0,
                column=column,
                padx=(0 if column == 0 else 5, 0 if column == 3 else 5),
                sticky="ew",
            )
            ctk.CTkFrame(card, height=3, corner_radius=0, fg_color=accent).pack(fill=tk.X)
            ctk.CTkLabel(
                card,
                text=title,
                text_color=次级,
                font=("Microsoft YaHei UI", 9, "bold"),
            ).pack(anchor="w", padx=14, pady=(9, 0))
            ctk.CTkLabel(
                card,
                textvariable=variable,
                text_color=墨色,
                font=("Consolas", 21, "bold"),
            ).pack(anchor="w", padx=14, pady=(0, 9))

    def _set_active_nav(self, key: str) -> None:
        self._active_nav = key
        for nav_key, button in self._nav_buttons.items():
            if nav_key == key:
                button.configure(
                    fg_color=墨色,
                    hover_color=墨色,
                    text_color="#FFFFFF",
                    corner_radius=2,
                )
            else:
                button.configure(
                    fg_color="transparent",
                    hover_color=软灰,
                    text_color=墨色,
                    corner_radius=2,
                )
        if key in _legacy.NAV_TITLES:
            self.view_title.set(_legacy.NAV_TITLES[key])

    def _format_table_value(self, column: str, value: str) -> str:
        text = self._cell_text(value)
        if column == "ExecutionState":
            return {
                "READY": "可执行",
                "CAUTIOUS": "谨慎",
                "OBSERVE": "观察",
                "BLOCKED": "阻断",
            }.get(text.strip().upper(), text or "观察")
        if column == "QualityLayerStatus":
            return {
                "PASS": "通过",
                "POLICY_FAIL": "策略未通过",
                "DATA_INCOMPLETE": "数据不完整",
                "NOT_APPLICABLE": "不适用",
            }.get(text.strip().upper(), text or "—")
        if column in {"AlphaScore", "SmoothTriggerScore"}:
            number = self._numeric_value(text)
            return f"{number:.1f}" if number is not None else "—"
        return super()._format_table_value(column, value)

    def scan_finished(self, code: int) -> None:
        super().scan_finished(code)
        if code == 0 and not getattr(self, "_daily_pipeline_active", False):
            self.status.set("结果已刷新 · 研究排名与执行状态已更新")


ScannerGUI = ResearchTerminalGUI


def main() -> None:
    install_v84_presentation()
    ctk.set_appearance_mode("light")
    root = ctk.CTk(fg_color=背景)
    ResearchTerminalGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
