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
        raise RuntimeError(f"{label}: start marker missing")
    right = text.find(end, left)
    if right < 0:
        raise RuntimeError(f"{label}: end marker missing")
    return text[:left] + replacement + text[right:]


def patch_gui() -> None:
    path = ROOT / "gui.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "import re\nimport sys\n\nimport gui_core as _core\n",
        "import re\nfrom collections.abc import Sequence\nfrom pathlib import Path\n\nimport gui_core as _core\n",
        "gui imports",
    )
    text = text.replace(
        "filter_values: tuple[str, ...] | None = None,",
        "filter_values: Sequence[str] | None = None,",
    )
    text = replace_once(
        text,
        '''    def _write_top50_csv(self, tickers: list[str]) -> None:\n        self._call_core_with_legacy_output_dir(\n            _core.ScannerGUI._write_top50_csv, tickers\n        )\n''',
        '''    def _write_top50_csv(self, tickers: list[str]) -> Path:\n        return self._call_core_with_legacy_output_dir(\n            _core.ScannerGUI._write_top50_csv, tickers\n        )\n''',
        "gui top50 return type",
    )
    path.write_text(text, encoding="utf-8")


def patch_gui_core() -> None:
    path = ROOT / "gui_core.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''        self._log_queue: queue.Queue[str] = queue.Queue()\n        self._scan_event_queue: queue.Queue[tuple[str, int, int, str]] = queue.Queue()\n        self._scan_cancel_event: threading.Event | None = None\n        self._scan_execution_mode = ""\n        self._last_scan_execution = None\n''',
        '''        self._log_queue: queue.Queue[str] = queue.Queue()\n        self._scan_event_queue: queue.Queue[tuple[str, int, int, str]] = queue.Queue()\n        self._scan_completion_queue: queue.Queue[tuple[str, int | str]] = queue.Queue()\n        self._scan_cancel_event: threading.Event | None = None\n        self._scan_execution_mode = ""\n        self._last_scan_execution = None\n        self._last_scan_progress_text = ""\n''',
        "gui core queue state",
    )
    text = replace_once(
        text,
        '        filter_values: tuple[str, str, str, str, str, str] | None = None,\n',
        '        filter_values: Sequence[str] | None = None,\n',
        "gui core flexible filter values",
    )
    text = replace_once(
        text,
        '''        self._scan_cancel_event = threading.Event()\n        self._scan_execution_mode = "inprocess"\n        self.append_log("执行：进程内扫描（异常时自动回退子进程）\\n")\n''',
        '''        self._scan_cancel_event = threading.Event()\n        self._scan_execution_mode = "inprocess"\n        self._last_scan_progress_text = ""\n        self.append_log("执行：进程内扫描（异常时自动回退子进程）\\n")\n''',
        "gui core reset progress text",
    )
    new_runtime = '''    def _run_scan_inprocess(self, request, fallback_command: list[str]) -> None:\n        from scan_service import execute_scan\n        from scanner import ScanCancelled\n\n        def progress(stage: str, current: int, total: int, message: str) -> None:\n            self._scan_event_queue.put((stage, current, total, message))\n\n        try:\n            result = execute_scan(\n                request,\n                progress_callback=progress,\n                cancel_event=self._scan_cancel_event,\n            )\n        except ScanCancelled:\n            self._scan_completion_queue.put(("finished", 130))\n            return\n        except Exception as exc:\n            if self._cancel_requested:\n                self._scan_completion_queue.put(("finished", 130))\n                return\n            self._log_queue.put(\n                f"进程内扫描异常：{exc}\\n自动回退到兼容子进程模式。\\n"\n            )\n            self._scan_execution_mode = "process-fallback"\n            self._scan_cancel_event = None\n            self.run_process(fallback_command)\n            return\n        self._last_scan_execution = result\n        self._scan_completion_queue.put(("finished", 0))\n\n    def _apply_scan_progress_event(\n        self, stage: str, current: int, total: int, message: str\n    ) -> None:\n        labels = {\n            "prepare": "准备扫描",\n            "download": "行情准备",\n            "analyse": "指标分析",\n            "enrich": "评分排序",\n            "export": "写入结果",\n            "complete": "扫描完成",\n        }\n        prefix = labels.get(stage, "扫描")\n        if stage == "prepare":\n            self.progress.stop()\n            self.progress.configure(mode="indeterminate")\n            self.progress.start(12)\n            status_text = message or prefix\n        else:\n            self.progress.stop()\n            self.progress.configure(\n                mode="determinate",\n                maximum=max(int(total), 1),\n                value=min(max(int(current), 0), max(int(total), 1)),\n            )\n            if total > 0 and stage != "complete":\n                status_text = f"{prefix} {current}/{total} · {message}"\n            else:\n                status_text = message or prefix\n        if status_text != self._last_scan_progress_text:\n            self.append_log(f"{status_text}\\n")\n            self._last_scan_progress_text = status_text\n        self.status.set(status_text)\n\n    def run_process(self, command: list[str]) -> None:\n        try:\n            env = os.environ.copy()\n            env["PYTHONIOENCODING"] = "utf-8"\n            env["PYTHONUTF8"] = "1"\n            self.process = subprocess.Popen(\n                command,\n                cwd=PROJECT_ROOT,\n                stdout=subprocess.PIPE,\n                stderr=subprocess.STDOUT,\n                text=True,\n                encoding="utf-8",\n                errors="replace",\n                bufsize=1,\n                env=env,\n            )\n            if self._cancel_requested:\n                self.process.terminate()\n            assert self.process.stdout is not None\n            for line in self.process.stdout:\n                self._log_queue.put(line)\n            code = self.process.wait()\n            self.process = None\n            self._scan_completion_queue.put(("finished", code))\n        except (OSError, subprocess.SubprocessError) as exc:\n            self._scan_completion_queue.put(("failed", str(exc)))\n\n    def _drain_scan_progress_events(self) -> None:\n        latest_scan_event = None\n        while True:\n            try:\n                latest_scan_event = self._scan_event_queue.get_nowait()\n            except queue.Empty:\n                break\n        if latest_scan_event is not None:\n            self._apply_scan_progress_event(*latest_scan_event)\n\n    def _drain_scan_completion_events(self) -> None:\n        while True:\n            try:\n                event, payload = self._scan_completion_queue.get_nowait()\n            except queue.Empty:\n                break\n            if event == "failed":\n                self.scan_failed(str(payload))\n            else:\n                try:\n                    code = int(payload)\n                except (TypeError, ValueError):\n                    code = 1\n                self.scan_finished(code)\n\n    def _flush_log_queue(self) -> None:\n        if self._closing:\n            return\n        lines: list[str] = []\n        while len(lines) < 200:\n            try:\n                lines.append(self._log_queue.get_nowait())\n            except queue.Empty:\n                break\n        if lines:\n            latest_fundamental_progress = None\n            latest_download_progress = None\n            latest_analyse_progress = None\n            latest_backtest_progress = None\n            rendered_lines: list[str] = []\n            for line in lines:\n                if FUNDAMENTAL_PROGRESS_RE.search(line):\n                    latest_fundamental_progress = line\n                elif DOWNLOAD_PROGRESS_RE.search(line):\n                    latest_download_progress = line\n                elif ANALYSE_PROGRESS_RE.search(line):\n                    latest_analyse_progress = line\n                elif BACKTEST_PROGRESS_RE.search(line):\n                    latest_backtest_progress = line\n                else:\n                    rendered_lines.append(line)\n            if latest_fundamental_progress:\n                rendered_lines.append(latest_fundamental_progress)\n            if latest_download_progress:\n                rendered_lines.append(latest_download_progress)\n            if latest_analyse_progress:\n                rendered_lines.append(latest_analyse_progress)\n            if latest_backtest_progress:\n                rendered_lines.append(latest_backtest_progress)\n            self.append_log("".join(rendered_lines))\n        self._drain_scan_progress_events()\n        self._drain_scan_completion_events()\n        if not self._closing:\n            self._log_job = self.root.after(150, self._flush_log_queue)\n\n'''
    text = replace_between(
        text,
        '    def _run_scan_inprocess(self, request, fallback_command: list[str]) -> None:\n',
        '    def scan_finished(self, code: int) -> None:\n',
        new_runtime,
        "gui core runtime bridge",
    )
    path.write_text(text, encoding="utf-8")


def patch_downloader() -> None:
    path = ROOT / "downloader.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'from typing import Any, Mapping, cast\n',
        'from typing import Any, Callable, Mapping, cast\n',
        "downloader callable import",
    )
    text = replace_once(
        text,
        '''class DownloadError(RuntimeError):\n    pass\n\n\n@dataclass\nclass TickerInfo:\n''',
        '''class DownloadError(RuntimeError):\n    pass\n\n\nDownloadProgressCallback = Callable[[int, int, int, int], None]\n\n\n@dataclass\nclass TickerInfo:\n''',
        "downloader progress alias",
    )
    text = replace_once(
        text,
        '''def normalize_data_source(source: str | None = None) -> str:\n''',
        '''def _notify_download_progress(\n    callback: DownloadProgressCallback | None,\n    completed: int,\n    total: int,\n    successful: int,\n    skipped: int,\n) -> None:\n    if callback is not None:\n        try:\n            callback(int(completed), int(total), int(successful), int(skipped))\n        except Exception:\n            logger.debug("Download progress callback failed.", exc_info=True)\n    _log_download_progress(completed, total, successful, skipped)\n\n\ndef _request_chunks(symbols: list[str]) -> list[list[str]]:\n    # TickFlow already parallelises batches internally.  Keeping up to one\n    # worker-wave per outer request preserves throughput while allowing the\n    # GUI to receive progress between waves instead of waiting for the whole\n    # market request to return.\n    size = max(1, int(TICKFLOW_BATCH_SIZE) * max(1, int(TICKFLOW_MAX_WORKERS)))\n    return [symbols[index : index + size] for index in range(0, len(symbols), size)]\n\n\ndef normalize_data_source(source: str | None = None) -> str:\n''',
        "downloader progress helper",
    )
    new_download_batch = '''def download_batch(\n    tickers: list[TickerInfo],\n    desc: str = "Downloading",\n    force: bool = False,\n    source: str | None = None,\n    cache_first: bool = False,\n    skip_tickers: set[str] | None = None,\n    progress_callback: DownloadProgressCallback | None = None,\n) -> dict[str, pd.DataFrame]:\n    del desc\n    normalize_data_source(source)\n    skip = {normalize_ticker(ticker) for ticker in (skip_tickers or set())}\n    symbols = list(\n        dict.fromkeys(\n            normalize_ticker(item.ticker)\n            for item in tickers\n            if item.ticker and normalize_ticker(item.ticker) not in skip\n        )\n    )\n    total = len(symbols)\n    results: dict[str, pd.DataFrame] = {}\n    stale_cache: dict[str, pd.DataFrame] = {}\n    missing: list[str] = []\n\n    cached_frames = {} if force else _load_caches_parallel(symbols)\n    for symbol in symbols:\n        cached = cached_frames.get(symbol)\n        if cached is None:\n            missing.append(symbol)\n        elif cache_first or _cache_has_completed_daily_bar(cached):\n            results[symbol] = cached\n        else:\n            stale_cache[symbol] = cached\n\n    logger.info(\n        "DOWNLOAD start: %d tickers via TickFlow Free; %d fresh cache, "\n        "%d incremental, %d full.",\n        total,\n        len(results),\n        len(stale_cache),\n        len(missing),\n    )\n    completed = len(results)\n    failed = 0\n    _notify_download_progress(\n        progress_callback, completed, total, len(results), failed\n    )\n\n    rebase: list[str] = []\n    if stale_cache:\n        stale_symbols = list(stale_cache)\n        for batch in _request_chunks(stale_symbols):\n            try:\n                recent_frames = _batch_fetch(batch, _INCREMENTAL_BARS)\n            except DownloadError as exc:\n                logger.warning("%s", exc)\n                recent_frames = {}\n\n            for symbol in batch:\n                cached = stale_cache[symbol]\n                recent = recent_frames.get(symbol)\n                if recent is None or recent.empty:\n                    results[symbol] = cached\n                    completed += 1\n                    continue\n                if _requires_full_rebase(cached, recent):\n                    rebase.append(symbol)\n                    continue\n                merged = _merge_cached(cached, recent)\n                _save_cache(symbol, merged)\n                results[symbol] = merged\n                completed += 1\n            _notify_download_progress(\n                progress_callback, completed, total, len(results), failed\n            )\n\n    full_symbols = list(dict.fromkeys(missing + rebase))\n    for batch in _request_chunks(full_symbols):\n        try:\n            full_frames = _batch_fetch(batch)\n        except DownloadError as exc:\n            logger.error("%s", exc)\n            full_frames = {}\n\n        for symbol in batch:\n            frame = full_frames.get(symbol)\n            if frame is not None and not frame.empty:\n                _save_cache(symbol, frame)\n                results[symbol] = frame\n            else:\n                old = stale_cache.get(symbol)\n                if old is not None and not force:\n                    results[symbol] = old\n                    logger.warning("TickFlow 无法重建 %s，暂时沿用旧缓存。", symbol)\n                else:\n                    failed += 1\n            completed += 1\n        _notify_download_progress(\n            progress_callback, completed, total, len(results), failed\n        )\n\n    for symbol, frame in results.items():\n        _record_market_manifest(symbol, frame)\n    _flush_market_manifest()\n    if completed != total or total == 0:\n        completed = total\n        _notify_download_progress(\n            progress_callback, completed, total, len(results), failed\n        )\n    logger.info(\n        "Download batch complete (TickFlow Free): %d/%d tickers available.",\n        len(results),\n        total,\n    )\n    return results\n\n\n'''
    text = replace_between(
        text,
        'def download_batch(\n',
        'def _load_or_fetch_meta(symbol: str) -> dict[str, Any]:\n',
        new_download_batch,
        "downloader batch",
    )
    path.write_text(text, encoding="utf-8")


def patch_scanner() -> None:
    path = ROOT / "scanner.py"
    text = path.read_text(encoding="utf-8")
    old = '''    downloaded = download_batch(\n        all_tickers,\n        desc="Downloading",\n        force=force_download,\n        source=data_source,\n        cache_first=cache_first and not force_download,\n        skip_tickers=set(skip_processed) if resume else None,\n    )\n'''
    new = '''    def on_download_progress(\n        completed: int, total: int, available: int, unavailable: int\n    ) -> None:\n        _emit_progress(\n            progress_callback,\n            "download",\n            completed,\n            total,\n            f"TickFlow 行情 {completed}/{total} · 可用 {available} · 无数据/失败 {unavailable}",\n        )\n\n    downloaded = download_batch(\n        all_tickers,\n        desc="Downloading",\n        force=force_download,\n        source=data_source,\n        cache_first=cache_first and not force_download,\n        skip_tickers=set(skip_processed) if resume else None,\n        progress_callback=on_download_progress,\n    )\n'''
    text = replace_once(text, old, new, "scanner download callback")
    text = replace_once(
        text,
        '    analysis_started = time.perf_counter()\n',
        '''    _emit_progress(\n        progress_callback,\n        "analyse",\n        0,\n        len(analyse_queue),\n        f"开始指标分析：{len(analyse_queue)} 个标的",\n    )\n    analysis_started = time.perf_counter()\n''',
        "scanner analysis start progress",
    )
    path.write_text(text, encoding="utf-8")


def patch_scan_service() -> None:
    path = ROOT / "scan_service.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''def execute_scan(\n''',
        '''def _emit_progress(\n    callback: ScanProgressCallback | None,\n    stage: str,\n    current: int,\n    total: int,\n    message: str,\n) -> None:\n    if callback is None:\n        return\n    try:\n        callback(stage, int(current), int(total), str(message))\n    except Exception:\n        logging.getLogger("institution_scanner").debug(\n            "Scan service progress callback failed.", exc_info=True\n        )\n\n\ndef execute_scan(\n''',
        "scan service emit helper",
    )
    text = replace_once(
        text,
        '''    log = logger or logging.getLogger("institution_scanner")\n    stocks, etfs = prepare_universe(\n''',
        '''    log = logger or logging.getLogger("institution_scanner")\n    _emit_progress(progress_callback, "prepare", 0, 0, "正在准备股票池")\n    stocks, etfs = prepare_universe(\n''',
        "scan service universe start",
    )
    text = replace_once(
        text,
        '''    if refresh_policy_fn is not None:\n''',
        '''    _emit_progress(\n        progress_callback,\n        "prepare",\n        0,\n        0,\n        f"股票池准备完成：股票 {len(stocks)} · ETF {len(etfs)}；正在检查基本面",\n    )\n    if refresh_policy_fn is not None:\n''',
        "scan service universe ready",
    )
    text = replace_once(
        text,
        '''    top_csv, top_parquet, full_csv, full_parquet = export_all_fn(\n''',
        '''    _emit_progress(\n        progress_callback, "export", 0, len(report.results), "正在写入 CSV / Parquet 结果"\n    )\n    top_csv, top_parquet, full_csv, full_parquet = export_all_fn(\n''',
        "scan service export start",
    )
    text = replace_once(
        text,
        '''    return ScanExecutionResult(\n''',
        '''    _emit_progress(\n        progress_callback,\n        "export",\n        len(report.results),\n        len(report.results),\n        "结果文件写入完成",\n    )\n    return ScanExecutionResult(\n''',
        "scan service export complete",
    )
    path.write_text(text, encoding="utf-8")


def write_regressions() -> None:
    path = ROOT / "test_gui_progress_regressions.py"
    path.write_text(
        '''from __future__ import annotations\n\nimport queue\nimport threading\nimport unittest\nfrom unittest.mock import Mock, patch\n\nimport pandas as pd\n\nimport downloader\nimport gui_core\nimport scanner\nfrom downloader import TickerInfo\n\n\nclass GuiProgressRegressionTests(unittest.TestCase):\n    def test_download_batch_reports_intermediate_outer_batch_progress(self) -> None:\n        tickers = [TickerInfo(ticker=f"{index:06d}.SZ") for index in range(1001)]\n        frame = pd.DataFrame(\n            {\n                "Open": [1.0, 1.0],\n                "High": [1.0, 1.0],\n                "Low": [1.0, 1.0],\n                "Close": [1.0, 1.0],\n                "Volume": [1.0, 1.0],\n                "Amount": [1.0, 1.0],\n            },\n            index=pd.to_datetime(["2026-08-06", "2026-08-07"]),\n        )\n        events: list[tuple[int, int, int, int]] = []\n\n        def fake_batch(symbols, count=None):\n            del count\n            return {symbol: frame for symbol in symbols}\n\n        with (\n            patch.object(downloader, "_batch_fetch", side_effect=fake_batch) as batch_fetch,\n            patch.object(downloader, "_save_cache"),\n            patch.object(downloader, "_record_market_manifest"),\n            patch.object(downloader, "_flush_market_manifest"),\n        ):\n            result = downloader.download_batch(\n                tickers,\n                force=True,\n                progress_callback=lambda *values: events.append(values),\n            )\n\n        self.assertEqual(len(result), len(tickers))\n        self.assertGreaterEqual(len(events), 4)\n        self.assertEqual(events[-1][:2], (len(tickers), len(tickers)))\n        self.assertTrue(any(0 < current < total for current, total, _, _ in events))\n        max_outer_batch = downloader.TICKFLOW_BATCH_SIZE * downloader.TICKFLOW_MAX_WORKERS\n        self.assertTrue(\n            all(len(call.args[0]) <= max_outer_batch for call in batch_fetch.call_args_list)\n        )\n\n    def test_run_scan_forwards_downloader_progress_to_structured_callback(self) -> None:\n        events: list[tuple[str, int, int, str]] = []\n\n        def fake_download_batch(*args, **kwargs):\n            callback = kwargs["progress_callback"]\n            callback(3, 10, 3, 0)\n            return {}\n\n        with (\n            patch.object(scanner, "download_batch", side_effect=fake_download_batch),\n            patch.object(scanner, "load_checkpoint", return_value=set()),\n            patch.object(scanner, "clear_checkpoint"),\n            patch.object(scanner, "enrich_results"),\n        ):\n            scanner.run_scan(\n                stock_universe=[TickerInfo(ticker="000001.SZ")],\n                etf_universe=[],\n                resume=False,\n                progress_callback=lambda *values: events.append(values),\n            )\n\n        self.assertTrue(\n            any(stage == "download" and current == 3 and total == 10 for stage, current, total, _ in events)\n        )\n        self.assertTrue(any(stage == "analyse" and current == 0 for stage, current, _, _ in events))\n\n    def test_inprocess_worker_never_calls_tk_from_worker_thread(self) -> None:\n        gui = gui_core.ScannerGUI.__new__(gui_core.ScannerGUI)\n        gui._scan_event_queue = queue.Queue()\n        gui._scan_completion_queue = queue.Queue()\n        gui._scan_cancel_event = threading.Event()\n        gui._cancel_requested = False\n        gui._log_queue = queue.Queue()\n        gui._last_scan_execution = None\n        gui._scan_execution_mode = "inprocess"\n        gui.root = Mock()\n        result = object()\n        with patch("scan_service.execute_scan", return_value=result):\n            gui._run_scan_inprocess(object(), ["python", "main.py", "scan"])\n        gui.root.after.assert_not_called()\n        self.assertIs(gui._last_scan_execution, result)\n        self.assertEqual(gui._scan_completion_queue.get_nowait(), ("finished", 0))\n\n    def test_structured_progress_updates_bar_status_and_visible_log(self) -> None:\n        gui = gui_core.ScannerGUI.__new__(gui_core.ScannerGUI)\n        gui.progress = Mock()\n        gui.status = Mock()\n        gui.append_log = Mock()\n        gui._last_scan_progress_text = ""\n\n        gui._apply_scan_progress_event(\n            "download", 500, 5985, "TickFlow 行情 500/5985 · 可用 497 · 无数据/失败 3"\n        )\n\n        gui.progress.configure.assert_called_with(\n            mode="determinate", maximum=5985, value=500\n        )\n        status = gui.status.set.call_args.args[0]\n        self.assertIn("500/5985", status)\n        gui.append_log.assert_called_once()\n\n\nif __name__ == "__main__":\n    unittest.main()\n''',
        encoding="utf-8",
    )


def main() -> None:
    patch_gui()
    patch_gui_core()
    patch_downloader()
    patch_scanner()
    patch_scan_service()
    write_regressions()
    print("GUI progress/type migration applied")


if __name__ == "__main__":
    main()
