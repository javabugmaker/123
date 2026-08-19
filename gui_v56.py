"""v56 GUI: first-class TickFlow API credential controls.

This presentation layer extends the existing decision workstation without
placing secrets in source control.  API keys are stored only in the local
``.env.local`` file through :mod:`tickflow_settings` and can be tested against
the authenticated TickFlow quote endpoint from the Advanced Settings panel.
"""

from __future__ import annotations

import threading
from typing import Any

import customtkinter as ctk

import gui as _legacy
import gui_core as _core
from tickflow_settings import (
    get_tickflow_api_key,
    get_tickflow_setting_source,
    normalize_api_key,
    save_tickflow_api_key,
    use_tickflow_free,
)


class TickFlowScannerGUI(_legacy.DecisionScannerGUI):
    """Decision workstation with explicit TickFlow API / Free selection."""

    def __init__(self, root) -> None:
        tk = _core.tk
        self.tickflow_api_key_var = tk.StringVar(
            master=root, value=get_tickflow_api_key()
        )
        self.tickflow_header_status = tk.StringVar(master=root, value="● TickFlow")
        self.tickflow_connection_status = tk.StringVar(
            master=root, value="正在读取 TickFlow 配置…"
        )
        self._tickflow_key_visible = False
        self._market_source_label: Any | None = None
        super().__init__(root)
        self._refresh_tickflow_ui()

    # Header ---------------------------------------------------------------
    def _build_ui_header(self) -> None:
        tk = _core.tk
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
            textvariable=self.tickflow_header_status,
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

    # Advanced settings ----------------------------------------------------
    def _build_ui_controls(self) -> None:
        super()._build_ui_controls()

        # The legacy v26 source selector is fixed to the symbolic source code
        # "tickflow" and must remain so for CLI compatibility.  Hide its stale
        # 'TickFlow Free' display and replace it with actual credential status.
        self.source_box.grid_remove()
        for child in self.advanced_frame.winfo_children():
            try:
                text = str(child.cget("text") or "")
            except Exception:
                continue
            if text.startswith("行情：TickFlow Free"):
                self._market_source_label = child
                break

        ctk.CTkLabel(
            self.advanced_frame,
            text="TickFlow API Key",
            text_color="#334e68",
            font=("Microsoft YaHei UI", 9, "bold"),
        ).grid(row=1, column=0, padx=(12, 8), pady=(2, 8), sticky="w")

        self.tickflow_api_entry = ctk.CTkEntry(
            self.advanced_frame,
            textvariable=self.tickflow_api_key_var,
            show="•",
            placeholder_text="tk_...",
            width=330,
        )
        self.tickflow_api_entry.grid(
            row=1,
            column=1,
            columnspan=2,
            padx=(0, 8),
            pady=(2, 8),
            sticky="ew",
        )
        self.advanced_frame.grid_columnconfigure(1, weight=1)

        ctk.CTkButton(
            self.advanced_frame,
            text="保存并启用 API",
            command=self._save_tickflow_key,
            width=116,
            height=30,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
        ).grid(row=1, column=3, padx=4, pady=(2, 8), sticky="w")
        self.tickflow_test_button = ctk.CTkButton(
            self.advanced_frame,
            text="测试连接",
            command=self._test_tickflow_api,
            width=88,
            height=30,
            fg_color="#0f766e",
            hover_color="#115e59",
        )
        self.tickflow_test_button.grid(
            row=1, column=4, padx=4, pady=(2, 8), sticky="w"
        )
        ctk.CTkButton(
            self.advanced_frame,
            text="使用 Free",
            command=self._select_tickflow_free,
            width=88,
            height=30,
            fg_color="#64748b",
            hover_color="#475569",
        ).grid(row=1, column=5, padx=4, pady=(2, 8), sticky="w")
        self.tickflow_show_button = ctk.CTkButton(
            self.advanced_frame,
            text="显示",
            command=self._toggle_tickflow_key_visibility,
            width=64,
            height=30,
            fg_color="transparent",
            hover_color="#e2e8f0",
            text_color="#334e68",
            border_width=1,
            border_color="#cbd5e1",
        )
        self.tickflow_show_button.grid(
            row=1, column=6, padx=(4, 12), pady=(2, 8), sticky="w"
        )

        ctk.CTkLabel(
            self.advanced_frame,
            textvariable=self.tickflow_connection_status,
            text_color="#475569",
            anchor="w",
        ).grid(
            row=2,
            column=0,
            columnspan=7,
            padx=12,
            pady=(0, 2),
            sticky="ew",
        )
        ctk.CTkLabel(
            self.advanced_frame,
            text="API Key 仅保存在本机 .env.local（Git 已忽略），不会写入仓库；保存后下载器将优先使用 TickFlow API。",
            text_color="#64748b",
            anchor="w",
        ).grid(
            row=3,
            column=0,
            columnspan=7,
            padx=12,
            pady=(0, 10),
            sticky="ew",
        )

    def _refresh_tickflow_ui(self, message: str = "") -> None:
        source = get_tickflow_setting_source()
        api_enabled = bool(get_tickflow_api_key())
        if api_enabled:
            self.tickflow_header_status.set("● TickFlow API")
            source_name = {
                "gui-local": "GUI 本机配置",
                "environment": "Windows / 进程环境变量",
            }.get(source, "认证配置")
            default_message = f"TickFlow API 已启用 · 凭据来源：{source_name}"
            market_text = "行情：TickFlow API（认证） · 基本面：AkShare（低频缓存）"
        else:
            self.tickflow_header_status.set("● TickFlow Free")
            default_message = "TickFlow Free 已启用 · 仅使用免密历史行情服务"
            market_text = "行情：TickFlow Free · 基本面：AkShare（低频缓存）"

        if self._market_source_label is not None:
            try:
                self._market_source_label.configure(text=market_text)
            except Exception:
                pass
        self.tickflow_connection_status.set(message or default_message)

    def _reset_tickflow_client(self) -> None:
        try:
            import downloader

            closer = getattr(downloader, "close_tickflow_client", None)
            if callable(closer):
                closer()
        except Exception:
            # Saving local configuration should not fail merely because a
            # previously initialized provider client cannot be closed cleanly.
            pass

    def _save_tickflow_key(self) -> None:
        key = normalize_api_key(self.tickflow_api_key_var.get())
        if not key:
            _core.messagebox.showerror(
                "TickFlow API Key", "请先输入 TickFlow API Key。"
            )
            return
        try:
            save_tickflow_api_key(key)
        except (OSError, ValueError, UnicodeError) as exc:
            _core.messagebox.showerror(
                "保存失败", f"无法保存 TickFlow API 配置：{exc}"
            )
            return
        self.tickflow_api_key_var.set(key)
        self._reset_tickflow_client()
        self._refresh_tickflow_ui("TickFlow API Key 已保存，正在测试认证连接…")
        self._test_tickflow_api(key_override=key)

    def _select_tickflow_free(self) -> None:
        try:
            use_tickflow_free()
        except (OSError, ValueError, UnicodeError) as exc:
            _core.messagebox.showerror(
                "切换失败", f"无法切换 TickFlow Free：{exc}"
            )
            return
        self.tickflow_api_key_var.set("")
        self._reset_tickflow_client()
        self._refresh_tickflow_ui("已切换到 TickFlow Free；本地 API Key 覆盖已移除。")

    def _toggle_tickflow_key_visibility(self) -> None:
        self._tickflow_key_visible = not self._tickflow_key_visible
        self.tickflow_api_entry.configure(show="" if self._tickflow_key_visible else "•")
        self.tickflow_show_button.configure(
            text="隐藏" if self._tickflow_key_visible else "显示"
        )

    @staticmethod
    def _safe_provider_error(exc: BaseException, secret: str) -> str:
        message = str(exc)
        if secret:
            message = message.replace(secret, "***")
        message = message.replace("\r", " ").replace("\n", " ").strip()
        return message[:260] or type(exc).__name__

    def _test_tickflow_api(self, key_override: str | None = None) -> None:
        key = normalize_api_key(
            key_override if key_override is not None else self.tickflow_api_key_var.get()
        )
        if not key:
            _core.messagebox.showerror(
                "TickFlow API", "没有可测试的 API Key。请先输入 Key。"
            )
            return
        self.tickflow_test_button.configure(state="disabled", text="测试中…")
        self.tickflow_connection_status.set("正在连接 TickFlow API 并读取 000001.SZ 实时报价…")

        def worker() -> None:
            client = None
            try:
                from tickflow import TickFlow

                # Official authenticated SDK path.  This must not call
                # TickFlow.free(), otherwise the key and account plan are bypassed.
                client = TickFlow(api_key=key)
                try:
                    quote = client.quotes.get(
                        symbols=["000001.SZ"], as_dataframe=True
                    )
                except TypeError:
                    quote = client.quotes.get(symbols=["000001.SZ"])
                if quote is None:
                    raise RuntimeError("报价接口返回空响应")
                if hasattr(quote, "empty") and bool(getattr(quote, "empty")):
                    raise RuntimeError("报价接口返回空数据")
            except Exception as exc:
                error = self._safe_provider_error(exc, key)
                self.root.after(0, lambda text=error: self._tickflow_test_failed(text))
            else:
                self.root.after(0, self._tickflow_test_succeeded)
            finally:
                if client is not None and hasattr(client, "close"):
                    try:
                        client.close()
                    except Exception:
                        pass

        threading.Thread(
            target=worker,
            name="tickflow-api-test",
            daemon=True,
        ).start()

    def _tickflow_test_succeeded(self) -> None:
        self.tickflow_test_button.configure(state="normal", text="测试连接")
        self._refresh_tickflow_ui(
            "✓ TickFlow API 认证成功，实时行情接口可用；扫描将使用认证客户端。"
        )

    def _tickflow_test_failed(self, error: str) -> None:
        self.tickflow_test_button.configure(state="normal", text="测试连接")
        self._refresh_tickflow_ui(f"✗ TickFlow API 连接失败：{error}")


ScannerGUI = TickFlowScannerGUI


def main() -> None:
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    TickFlowScannerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
