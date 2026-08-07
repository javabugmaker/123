from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    left = text.find(start)
    if left < 0:
        raise RuntimeError(f"{label}: start missing")
    right = text.find(end, left)
    if right < 0:
        raise RuntimeError(f"{label}: end missing")
    return text[:left] + replacement + text[right:]


def patch_main() -> None:
    path = ROOT / "main.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'from report import export_all, print_scan_summary, print_terminal_report\nfrom scanner import clear_checkpoint, run_parallel_indicator_scan, run_scan\n',
        'from report import export_all, print_scan_summary, print_terminal_report\nfrom scan_service import ScanRequest, execute_scan\nfrom scanner import clear_checkpoint, run_parallel_indicator_scan, run_scan\n',
        "main service import",
    )
    new_cmd = '''def cmd_scan(args: argparse.Namespace) -> int:\n    logger = logging.getLogger("institution_scanner")\n    request = ScanRequest(\n        include_stocks=not args.etfs_only,\n        include_etfs=not args.stocks_only,\n        tickers=tuple(\n            value.strip() for value in str(args.tickers or "").split(",") if value.strip()\n        ),\n        force_download=bool(args.force_download),\n        resume=not bool(args.no_resume),\n        data_source=args.data_source,\n        cache_first=bool(args.cache_first),\n        refresh_fundamentals=bool(getattr(args, "refresh_fundamentals", False)),\n        top_n_csv=args.top,\n        top_n_parquet=args.top_parquet,\n    )\n    execution = execute_scan(\n        request,\n        logger=logger,\n        build_universe_fn=build_ticker_universe,\n        run_scan_fn=run_scan,\n        export_all_fn=export_all,\n        refresh_policy_fn=_refresh_fundamentals_if_needed,\n    )\n    report = execution.report\n\n    if report.successful == 0:\n        logger.error("没有可用 TickFlow 行情数据，扫描失败；请检查网络或 TickFlow Free 服务后重试。")\n        print_scan_summary(report)\n        return 2\n\n    print_terminal_report(report.results, n=args.top)\n    print_scan_summary(report)\n    logger.info("Top CSV:    %s", execution.top_csv)\n    logger.info("Top PQ:     %s", execution.top_parquet)\n    logger.info("All CSV:    %s", execution.full_csv)\n    logger.info("All PQ:     %s", execution.full_parquet)\n    return 0\n\n\n'''
    text = replace_between(text, 'def cmd_scan(args: argparse.Namespace) -> int:\n', 'def cmd_report(args: argparse.Namespace) -> int:\n', new_cmd, "main cmd_scan")
    path.write_text(text, encoding="utf-8")


def patch_gui() -> None:
    path = ROOT / "gui_core.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '        self._log_queue: queue.Queue[str] = queue.Queue()\n        self._log_job = self.root.after(150, self._flush_log_queue)\n',
        '''        self._log_queue: queue.Queue[str] = queue.Queue()\n        self._scan_event_queue: queue.Queue[tuple[str, int, int, str]] = queue.Queue()\n        self._scan_cancel_event: threading.Event | None = None\n        self._scan_execution_mode = ""\n        self._last_scan_execution = None\n        self._log_job = self.root.after(150, self._flush_log_queue)\n''',
        "gui scan state",
    )
    new_scan = '''    def _build_scan_request(self):\n        from scan_service import ScanRequest\n\n        scope = self.scope.get()\n        return ScanRequest(\n            include_stocks=scope != "仅ETF",\n            include_etfs=scope != "仅股票",\n            tickers=tuple(\n                value.strip()\n                for value in self.tickers.get().split(",")\n                if value.strip()\n            ),\n            force_download=bool(self.force_download.get()),\n            resume=not bool(self.no_resume.get() or self.force_download.get()),\n            data_source=self._selected_data_source(),\n            cache_first=bool(self.cache_first.get() and not self.force_download.get()),\n            refresh_fundamentals=bool(self.refresh_fundamentals.get()),\n        )\n\n    def start_scan(self) -> None:\n        if self.scan_running:\n            messagebox.showinfo("提示", "扫描正在运行中")\n            return\n        self.clear_log()\n        self.scan_running = True\n        self._cancel_requested = False\n        self._csv_path = None\n        self._csv_mtime = None\n        self.scan_output_mtime = self._results_mtime()\n        self.start_button.configure(state=tk.DISABLED)\n        self.cancel_button.configure(state=tk.NORMAL)\n        self.progress.stop()\n        self.progress.configure(mode="determinate", maximum=100, value=0)\n        self.status.set("准备扫描")\n        request = self._build_scan_request()\n        fallback_command = self.build_command()\n        self._scan_cancel_event = threading.Event()\n        self._scan_execution_mode = "inprocess"\n        self.append_log("执行：进程内扫描（异常时自动回退子进程）\\n")\n        threading.Thread(\n            target=self._run_scan_inprocess,\n            args=(request, fallback_command),\n            daemon=True,\n        ).start()\n\n    def _run_scan_inprocess(self, request, fallback_command: list[str]) -> None:\n        from scan_service import execute_scan\n        from scanner import ScanCancelled\n\n        def progress(stage: str, current: int, total: int, message: str) -> None:\n            self._scan_event_queue.put((stage, current, total, message))\n\n        try:\n            result = execute_scan(\n                request,\n                progress_callback=progress,\n                cancel_event=self._scan_cancel_event,\n            )\n        except ScanCancelled:\n            try:\n                self.root.after(0, self.scan_finished, 130)\n            except tk.TclError:\n                pass\n            return\n        except Exception as exc:\n            if self._cancel_requested:\n                try:\n                    self.root.after(0, self.scan_finished, 130)\n                except tk.TclError:\n                    pass\n                return\n            self._log_queue.put(\n                f"进程内扫描异常：{exc}\\n自动回退到兼容子进程模式。\\n"\n            )\n            self._scan_execution_mode = "process-fallback"\n            self._scan_cancel_event = None\n            self.run_process(fallback_command)\n            return\n        self._last_scan_execution = result\n        try:\n            self.root.after(0, self.scan_finished, 0)\n        except tk.TclError:\n            pass\n\n    def _apply_scan_progress_event(\n        self, stage: str, current: int, total: int, message: str\n    ) -> None:\n        if stage == "prepare":\n            self.progress.stop()\n            self.progress.configure(mode="indeterminate")\n            self.progress.start(12)\n            self.status.set(message or "准备扫描")\n            return\n        self.progress.stop()\n        self.progress.configure(\n            mode="determinate", maximum=max(int(total), 1), value=max(int(current), 0)\n        )\n        labels = {\n            "download": "行情准备",\n            "analyse": "指标分析",\n            "enrich": "评分排序",\n            "complete": "扫描完成",\n        }\n        prefix = labels.get(stage, "扫描")\n        if total > 0 and stage != "complete":\n            self.status.set(f"{prefix} {current}/{total} · {message}")\n        else:\n            self.status.set(message or prefix)\n\n'''
    text = replace_between(text, '    def start_scan(self) -> None:\n', '    def run_process(self, command: list[str]) -> None:\n', new_scan, "gui start scan")
    text = replace_once(
        text,
        '''            if latest_backtest_progress:\n                rendered_lines.append(latest_backtest_progress)\n            self.append_log("".join(rendered_lines))\n        self._log_job = self.root.after(150, self._flush_log_queue)\n''',
        '''            if latest_backtest_progress:\n                rendered_lines.append(latest_backtest_progress)\n            self.append_log("".join(rendered_lines))\n        latest_scan_event = None\n        while True:\n            try:\n                latest_scan_event = self._scan_event_queue.get_nowait()\n            except queue.Empty:\n                break\n        if latest_scan_event is not None:\n            self._apply_scan_progress_event(*latest_scan_event)\n        self._log_job = self.root.after(150, self._flush_log_queue)\n''',
        "gui structured progress flush",
    )
    reset_old = '        self.scan_running = False\n        self.process = None\n        self.start_button.configure(state=tk.NORMAL)\n'
    reset_new = '        self.scan_running = False\n        self.process = None\n        self._scan_cancel_event = None\n        self._scan_execution_mode = ""\n        self.start_button.configure(state=tk.NORMAL)\n'
    reset_count = text.count(reset_old)
    if reset_count != 2:
        raise RuntimeError(f"gui state reset: expected two matches, found {reset_count}")
    text = text.replace(reset_old, reset_new, 2)
    text = replace_once(
        text,
        '''        self.status.set("正在取消任务")\n        try:\n            if self.process is not None:\n                self.process.terminate()\n''',
        '''        self.status.set("正在取消任务")\n        if self._scan_cancel_event is not None:\n            self._scan_cancel_event.set()\n        try:\n            if self.process is not None:\n                self.process.terminate()\n''',
        "gui cooperative cancellation",
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    patch_main()
    patch_gui()
    print("scan service migration applied")


if __name__ == "__main__":
    main()
