from __future__ import annotations

from pathlib import Path


def patch_gui() -> None:
    path = Path("gui.py")
    text = path.read_text(encoding="utf-8")

    def replace_once(old: str, new: str, label: str) -> None:
        nonlocal text
        count = text.count(old)
        if count != 1:
            raise RuntimeError(f"gui/{label}: expected 1 match, found {count}")
        text = text.replace(old, new, 1)

    replace_once(
        'BACKTEST_SCOPE_FILES = {\n    "股票 Top50": "Top50Stocks.csv",\n    "ETF Top50": "Top50ETF.csv",\n    "综合 Top50": "Top50Mixed.csv",\n    "强推荐": "Top50TradeReady.csv",\n}\n',
        'BACKTEST_SCOPE_FILES = {\n    "股票 Top50": "Top50Stocks.csv",\n    "ETF Top50": "Top50ETF.csv",\n    "综合 Top50": "Top50Mixed.csv",\n    "强推荐": "Top50TradeReady.csv",\n}\nDAILY_PIPELINE_FILE = Path(__file__).resolve().with_name("daily_pipeline.py")\n',
        "daily pipeline constant",
    )

    replace_once(
        '            "CONFIRMED": "持续有效",\n            "FAILED": "已失效",\n            "EXPIRED": "已失效",\n            "INACTIVE": "已结束",\n',
        '            "CONFIRMED": "持续确认",\n            "STRENGTHEN": "正在增强",\n            "WATCH": "观察中",\n            "WEAKEN": "正在转弱",\n            "FAILED": "已失效",\n            "EXPIRED": "已过期",\n            "INACTIVE": "已结束",\n',
        "signal status translations",
    )

    replace_once(
        '        self._new_signal_only = False\n        self._advanced_visible = False\n',
        '        self._new_signal_only = False\n        self._daily_pipeline_active = False\n        self._advanced_visible = False\n',
        "daily state",
    )

    replace_once(
        '        self.root.bind("<Control-r>", lambda _event: self.start_scan())\n        self.root.bind("<Control-b>", lambda _event: self.start_backtest())\n',
        '        self.root.bind("<Control-r>", lambda _event: self.start_scan())\n        self.root.bind("<Control-b>", lambda _event: self.start_backtest())\n        self.root.bind("<Control-Shift-R>", lambda _event: self.start_daily_pipeline())\n',
        "daily shortcut",
    )

    marker = '    self.start_button = ctk.CTkButton(\n        controls,\n        text="▶ 开始扫描",\n'
    if text.count(marker) != 1:
        raise RuntimeError(f"gui/daily button marker: expected 1 match, found {text.count(marker)}")
    daily_button = '''    self.daily_button = ctk.CTkButton(
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

'''
    text = text.replace(marker, daily_button + marker, 1)
    replace_once('self.start_button.grid(row=0, column=6,', 'self.start_button.grid(row=0, column=7,', "scan button column")
    replace_once('self.backtest_button.grid(row=0, column=7,', 'self.backtest_button.grid(row=0, column=8,', "backtest button column")
    replace_once('self.cancel_button.grid(row=0, column=8,', 'self.cancel_button.grid(row=0, column=9,', "cancel button column")
    replace_once(').grid(row=0, column=9, padx=(0, 16), pady=12)\n\n    self.advanced_frame', ').grid(row=0, column=10, padx=(0, 16), pady=12)\n\n    self.advanced_frame', "advanced button column")
    replace_once('self.advanced_frame.grid(row=1, column=0, columnspan=10,', 'self.advanced_frame.grid(row=1, column=0, columnspan=11,', "advanced span")

    old_scan = '''    def start_scan(self) -> None:
        if self.scan_running:
            return _core.ScannerGUI.start_scan(self)
        self._scan_mode_changed(self.scan_mode.get())
        self.backtest_button.configure(state=_core.tk.DISABLED)
        self.start_button.configure(text="扫描运行中")
        _core.ScannerGUI.start_scan(self)
        if not self.scan_running:
            self.backtest_button.configure(state=_core.tk.NORMAL)
            self.start_button.configure(text="▶ 开始扫描")
'''
    new_scan = '''    def start_daily_pipeline(self) -> None:
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
'''
    replace_once(old_scan, new_scan, "daily start method")

    replace_once(
        '        backtest_button = getattr(self, "backtest_button", None)\n        if backtest_button is not None:\n            backtest_button.configure(state=_core.tk.DISABLED, text="回测运行中")\n',
        '        backtest_button = getattr(self, "backtest_button", None)\n        daily_button = getattr(self, "daily_button", None)\n        if daily_button is not None:\n            daily_button.configure(state=_core.tk.DISABLED)\n        if backtest_button is not None:\n            backtest_button.configure(state=_core.tk.DISABLED, text="回测运行中")\n',
        "disable daily during backtest",
    )
    replace_once(
        '        if not self.scan_running and backtest_button is not None:\n            backtest_button.configure(state=_core.tk.NORMAL, text="▶ 运行回测")\n',
        '        if not self.scan_running and backtest_button is not None:\n            backtest_button.configure(state=_core.tk.NORMAL, text="▶ 运行回测")\n            if daily_button is not None:\n                daily_button.configure(state=_core.tk.NORMAL, text="⚡ 今日一键更新")\n',
        "restore daily after rejected backtest",
    )

    old_finished = '''    def scan_finished(self, code: int) -> None:
        was_backtest = self.backtest_running
        _core.ScannerGUI.scan_finished(self, code)
        self.start_button.configure(state=_core.tk.NORMAL, text="▶ 开始扫描")
        self.backtest_button.configure(state=_core.tk.NORMAL, text="▶ 运行回测")
        if code == 0 and not was_backtest and self.auto_backtest_recommended.get():
            tickers = self._tickers_from_output_file("Top50TradeReady.csv")
            if tickers:
                self.root.after(200, lambda values=tickers: self._start_backtest_for_tickers(values))

    def scan_failed(self, error: str) -> None:
        _core.ScannerGUI.scan_failed(self, error)
        self.start_button.configure(state=_core.tk.NORMAL, text="▶ 开始扫描")
        self.backtest_button.configure(state=_core.tk.NORMAL, text="▶ 运行回测")
        self._show_log_for_error()

    def append_log(self, text: str) -> None:
        _core.ScannerGUI.append_log(self, text)
        lowered = text.casefold()
        if "traceback" in lowered or "异常" in text or "启动失败" in text:
            self._show_log_for_error()
'''
    new_finished = '''    def scan_finished(self, code: int) -> None:
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
                self.status.set("今日全流程完成 · 综合/股票/ETF Top50 已更新")
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
        if "DAILY stage 1/3" in text:
            self.status.set("今日全流程 1/3 · 获取最新行情并扫描")
        elif "DAILY stage 2/3" in text:
            self.status.set("今日全流程 2/3 · 全量回测与候选精炼")
        elif "DAILY stage 3/3" in text:
            self.status.set("今日全流程 3/3 · 生成最终 Top50")
        lowered = text.casefold()
        if "traceback" in lowered or "异常" in text or "启动失败" in text:
            self._show_log_for_error()
'''
    replace_once(old_finished, new_finished, "daily completion")

    path.write_text(text, encoding="utf-8")


def patch_analytics() -> None:
    path = Path("analytics.py")
    text = path.read_text(encoding="utf-8")

    def replace_once(old: str, new: str, label: str) -> None:
        nonlocal text
        count = text.count(old)
        if count != 1:
            raise RuntimeError(f"analytics/{label}: expected 1 match, found {count}")
        text = text.replace(old, new, 1)

    old_workers = '''def _adaptive_worker_count(
    total: int,
    requested: int | None,
    profile: BacktestExecutionProfile,
) -> int:
    cpu_limit = max(1, (os.cpu_count() or 2) - 1)
    hard_limit = min(max(1, int(BACKTEST_MAX_PROCESSES)), cpu_limit, max(1, total))
    if requested is not None:
        return min(hard_limit, max(1, int(requested)))
    utilization = 0.90 if profile.name == "fast" else 0.75
    target = max(2, int(round(cpu_limit * utilization))) if total >= BACKTEST_PROCESS_MIN_TICKERS else 1
    return min(hard_limit, target, max(1, total))
'''
    new_workers = '''def _adaptive_worker_count(
    total: int,
    requested: int | None,
    profile: BacktestExecutionProfile,
) -> int:
    cpu_limit = max(1, (os.cpu_count() or 2) - 1)
    hard_limit = min(max(1, int(BACKTEST_MAX_PROCESSES)), cpu_limit, max(1, total))
    if requested is not None:
        return min(hard_limit, max(1, int(requested)))
    if total <= 1:
        return 1
    # Small batches now use threads instead of being artificially serialized;
    # larger CPU-heavy batches still switch to isolated worker processes.
    utilization = 0.90 if profile.name == "fast" else 0.80
    target = max(2, int(round(cpu_limit * utilization)))
    return min(hard_limit, target, max(1, total))
'''
    replace_once(old_workers, new_workers, "adaptive workers")

    replace_once(
        '''    use_process_pool = bool(
        total >= int(BACKTEST_PROCESS_MIN_TICKERS) and worker_count > 1
    )
    engine = "process" if use_process_pool else "sequential"
''',
        '''    use_process_pool = bool(
        total >= int(BACKTEST_PROCESS_MIN_TICKERS) and worker_count > 1
    )
    use_thread_pool = bool(
        1 < total < int(BACKTEST_PROCESS_MIN_TICKERS) and worker_count > 1
    )
    engine = "process" if use_process_pool else "thread" if use_thread_pool else "sequential"
''',
        "thread engine selection",
    )

    marker = '''    else:
        for ticker in unique_tickers:
            try:
                ticker_samples, cache_hit = _backtest_one_ticker_cached(
'''
    if text.count(marker) != 1:
        raise RuntimeError(f"analytics/thread marker: expected 1 match, found {text.count(marker)}")
    thread_block = '''    elif use_thread_pool:
        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="backtest",
        ) as executor:
            futures = {
                executor.submit(
                    _backtest_one_ticker_cached,
                    ticker,
                    source,
                    benchmark_frame,
                    commission,
                    stamp_duty,
                    slippage,
                    (validation_end, test_start),
                    benchmark_signature,
                    profile=profile,
                    benchmark_name=benchmark,
                ): ticker
                for ticker in unique_tickers
            }
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    ticker_samples, cache_hit = future.result()
                except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
                    logger.warning("Backtest failed for %s: %s", ticker, exc)
                    ticker_samples, cache_hit = [], False
                batch_frame = (
                    pd.DataFrame.from_records(ticker_samples)
                    if ticker_samples
                    else pd.DataFrame()
                )
                record_progress(
                    batch_frame,
                    1,
                    int(cache_hit),
                    [str(ticker)] if cache_hit else [],
                )
    else:
        for ticker in unique_tickers:
            try:
                ticker_samples, cache_hit = _backtest_one_ticker_cached(
'''
    text = text.replace(marker, thread_block, 1)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    patch_gui()
    patch_analytics()
    print("v27 daily pipeline + small-batch backtest parallelism applied")
