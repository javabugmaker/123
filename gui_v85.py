"""v85 A-share research briefing desktop GUI.

v85 keeps the editorial briefing identity, date header and compact research
language, but deliberately reuses the proven v84/DecisionScannerGUI geometry
shell.  The first v85 shell recreated every container with CustomTkinter frames
and accidentally relied on Tk geometry propagation semantics that CTkFrame does
not guarantee; several default-height frames therefore expanded to roughly
200px and the CTkScrollableFrame sidebar could collapse its child layout.

This module is presentation-only.  Scanner, ranking, backtest, publication,
filters, details and execution semantics remain inherited from the stable GUI
stack.
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
    PAGE_LABEL,
    TERMINAL_VERSION,
    TYPOGRAPHY,
)

GUI_VERSION = TERMINAL_VERSION + "-stable-layout-v2"


class ResearchBriefingGUI(_v84.ResearchTerminalGUI):
    """v85 briefing presentation on top of the stable v84 workstation shell."""

    def __init__(self, root) -> None:
        self.data_asof = _core.tk.StringVar(master=root, value="等待数据")
        self.header_note = _core.tk.StringVar(
            master=root,
            value="研究排名与交易执行分层 · 双击标的查看完整证据",
        )
        super().__init__(root)

    def _build_ui_configure_styles(self) -> None:
        """Retain v84 geometry while applying the v85 editorial palette."""
        _v84.ResearchTerminalGUI._build_ui_configure_styles(self)
        ttk = _core.ttk
        sans = str(TYPOGRAPHY["sans"])
        mono = str(TYPOGRAPHY["mono"])
        self.root.configure(fg_color=COLORS["background"])
        self.root.option_add("*Font", (sans, 9))
        style = ttk.Style()
        style.configure(
            "Compact.Treeview",
            rowheight=int(LAYOUT["table_row_height"]),
            font=(sans, 9),
            background=COLORS["paper"],
            fieldbackground=COLORS["paper"],
            foreground=COLORS["ink"],
            bordercolor=COLORS["line"],
            relief="flat",
        )
        style.configure(
            "Compact.Treeview.Heading",
            font=(mono, 9, "bold"),
            background=COLORS["ink"],
            foreground="#FFFFFF",
            padding=(8, 8),
            bordercolor="#3C4045",
            relief="flat",
        )
        style.map(
            "Compact.Treeview",
            background=[("selected", "#FCE8E8")],
            foreground=[("selected", COLORS["ink"])],
        )

    def _build_ui_header(self) -> None:
        """Compact v85 briefing header; all lower geometry remains v84 stable."""
        tk = _core.tk
        sans = str(TYPOGRAPHY["sans"])
        mono = str(TYPOGRAPHY["mono"])
        minimum = tuple(LAYOUT["minimum"])
        self.root.title("InstitutionScanner · A股研究简报")
        self.root.geometry(str(LAYOUT["window"]))
        self.root.minsize(int(minimum[0]), int(minimum[1]))

        header = ctk.CTkFrame(
            self.root,
            height=72,
            corner_radius=0,
            fg_color=COLORS["background"],
        )
        header.pack(fill=tk.X, padx=18, pady=(10, 5))
        header.pack_propagate(False)

        brand = ctk.CTkFrame(header, width=145, height=54, corner_radius=0, fg_color="transparent")
        brand.place(x=0, rely=0.5, anchor="w")
        ctk.CTkLabel(
            brand,
            text=BRAND_LABEL,
            text_color=COLORS["ink"],
            font=(mono, 11, "bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            brand,
            text=PAGE_LABEL,
            text_color=COLORS["muted"],
            font=(sans, 8, "bold"),
        ).pack(anchor="w", pady=(1, 0))

        ctk.CTkLabel(
            header,
            textvariable=self.data_asof,
            text_color=COLORS["ink"],
            font=(mono, 27, "bold"),
        ).place(x=165, rely=0.5, anchor="w")

        status_box = ctk.CTkFrame(
            header,
            height=36,
            corner_radius=0,
            fg_color=COLORS["ink"],
        )
        status_box.pack(side=tk.RIGHT, padx=(8, 0), pady=17)
        ctk.CTkLabel(
            status_box,
            text="LIVE",
            text_color="#FFFFFF",
            font=(mono, 8, "bold"),
        ).pack(side=tk.LEFT, padx=(10, 7), pady=5)
        ctk.CTkLabel(
            status_box,
            textvariable=self.status,
            text_color="#FFFFFF",
            font=(sans, 8),
        ).pack(side=tk.LEFT, padx=(0, 10), pady=5)

        rule = ctk.CTkFrame(
            self.root,
            height=1,
            corner_radius=0,
            fg_color=COLORS["ink"],
        )
        rule.pack(fill=tk.X, padx=18, pady=(0, 7))

    def _build_ui_controls(self) -> None:
        """Use v84's proven horizontal control bar and apply v85 emphasis."""
        _v84.ResearchTerminalGUI._build_ui_controls(self)
        self.daily_button.configure(
            fg_color=COLORS["red"],
            hover_color=COLORS["red_dark"],
        )
        self.start_button.configure(
            fg_color=COLORS["ink"],
            hover_color="#30343A",
        )
        self.backtest_button.configure(
            fg_color=COLORS["paper"],
            hover_color=COLORS["soft"],
            text_color=COLORS["ink"],
            border_color=COLORS["ink"],
        )

    def _set_active_nav(self, key: str) -> None:
        """Keep v84 navigation geometry; use red only for the active view."""
        self._active_nav = key
        for nav_key, button in self._nav_buttons.items():
            if nav_key == key:
                button.configure(
                    fg_color=COLORS["red"],
                    hover_color=COLORS["red_dark"],
                    text_color="#FFFFFF",
                    corner_radius=2,
                )
            else:
                button.configure(
                    fg_color="transparent",
                    hover_color=COLORS["soft"],
                    text_color=COLORS["ink"],
                    corner_radius=2,
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
