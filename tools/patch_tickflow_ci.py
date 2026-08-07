from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_required(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"missing target: {label}")
    return text.replace(old, new, 1)


def replace_test_method(text: str, name: str, replacement: str) -> str:
    pattern = re.compile(
        rf"^    def {re.escape(name)}\(self[^\n]*\):\n.*?(?=^    def test_|^if __name__ ==)",
        re.M | re.S,
    )
    new_text, count = pattern.subn(replacement.rstrip() + "\n\n", text, count=1)
    if count != 1:
        raise RuntimeError(f"test method not found: {name}")
    return new_text


# --- real backtest boundary: legacy synthetic signal helper must respect outcome horizon ---
analytics = read("analytics.py")
analytics = replace_required(
    analytics,
    "    for index in candidates:\n        if index - last_signal < cooldown:\n            continue\n",
    "    outcome_limit = max(0, len(enriched) - BACKTEST_OUTCOME_HORIZON_DAYS)\n"
    "    for index in candidates:\n"
    "        if index >= outcome_limit:\n"
    "            continue\n"
    "        if index - last_signal < cooldown:\n"
    "            continue\n",
    "legacy signal horizon",
)
write("analytics.py", analytics)

# --- market download progress is intentionally throttled; TickFlow batch logs start/final only ---
downloader = read("downloader.py")
old_progress = '''def _log_download_progress(\n    completed: int, total: int, successful: int, skipped: int\n) -> None:\n    """Stable GUI/test log format used by the scan progress parser."""\n    logger.info(\n        "DOWNLOAD progress: %d/%d (%d succeeded, %d no-data/failed).",\n        completed,\n        total,\n        successful,\n        skipped,\n    )\n'''
new_progress = '''def _log_download_progress(\n    completed: int, total: int, successful: int, skipped: int\n) -> None:\n    """Stable, throttled GUI/test log format used by the scan progress parser."""\n    interval = max(1, total // 100)\n    if completed == 1 or completed == total or completed % interval == 0:\n        logger.info(\n            "DOWNLOAD progress: %d/%d (%d succeeded, %d no-data/failed).",\n            completed,\n            total,\n            successful,\n            skipped,\n        )\n'''
downloader = replace_required(downloader, old_progress, new_progress, "download progress")
write("downloader.py", downloader)

# --- AkShare fundamentals: cache-first. No first-time network hit during ordinary scans. ---
main = read("main.py")
needle = '''    if not stock_universe:\n        return\n    try:\n        fundamental_path = refresh_fundamental_data(\n'''
replacement = '''    if not stock_universe:\n        return\n    existing_path = fundamental_data_path()\n    explicit_refresh = bool(force or FUNDAMENTAL_REFRESH_FORCE)\n    if existing_path is None and not explicit_refresh:\n        logger.info(\n            "AkShare 基本面缓存尚未初始化；普通扫描不主动联网。"\n            "需要基本面时请勾选/使用 --refresh-fundamentals。"\n        )\n        return\n    try:\n        fundamental_path = refresh_fundamental_data(\n'''
main = replace_required(main, needle, replacement, "fundamental cache-first guard")
write("main.py", main)

fund = read("fundamental_data.py")
fund = replace_required(
    fund,
    "from downloader import configure_akshare_proxy_from_system, normalize_ticker\n",
    "from downloader import normalize_ticker\nfrom network_proxy import configure_akshare_proxy_from_system\n",
    "fundamental proxy import",
)
write("fundamental_data.py", fund)

# --- Scanner log must describe TickFlow batch workers, not obsolete downloader threads. ---
scanner = read("scanner.py")
scanner = scanner.replace("    DOWNLOAD_THREADS,\n", "")
if "    TICKFLOW_MAX_WORKERS,\n" not in scanner:
    scanner = replace_required(
        scanner,
        "    SCORING_VERSION,\n    setup_logging,\n",
        "    SCORING_VERSION,\n    TICKFLOW_MAX_WORKERS,\n    setup_logging,\n",
        "scanner tickflow worker import",
    )
scanner = scanner.replace(
    '        "Phase 1/2: downloading data for %d tickers (%d threads)...",\n        len(all_tickers),\n        DOWNLOAD_THREADS,\n',
    '        "Phase 1/2: preparing TickFlow data for %d tickers (batch workers=%d)...",\n        len(all_tickers),\n        TICKFLOW_MAX_WORKERS,\n',
)
write("scanner.py", scanner)

# --- Rewrite stale regression expectations to the new architecture. ---
tests = read("test_regressions.py")

tests = replace_test_method(
    tests,
    "test_gui_market_overview_summarizes_filtered_lifecycle_rows",
    '''    def test_gui_market_overview_summarizes_filtered_lifecycle_rows(self):\n        scanner = object.__new__(gui.ScannerGUI)\n        scanner.market_overview = Mock()\n        indexes = {"OpportunityScore": 0, "SignalDays": 1, "LifecycleStage": 2}\n        rows = [["80", "3", "趋势确认"], ["40", "0", "机构吸筹"]]\n\n        scanner._update_market_overview(rows, indexes)\n\n        scanner.market_overview.set.assert_called_once_with(\n            "概览：2 只 · 启动 0 · 可交易 0 · 最终均分 60.0"\n        )''',
)

tests = tests.replace('"DataSource": ["eastmoney"],', '"DataSource": ["tickflow"],', 1)
tests = tests.replace('metadata = pd.DataFrame({"DataSource": ["eastmoney"]})', 'metadata = pd.DataFrame({"DataSource": ["tickflow"]})', 1)
tests = tests.replace('cache_path = output_dir / "000001.SZ__eastmoney.parquet"', 'cache_path = output_dir / "000001.SZ__tickflow.parquet"', 1)
# Only the first resume fixture needs the explicit source update.
resume_marker = 'report = scanner.run_scan(stock_universe=[ticker], etf_universe=[], data_source="eastmoney")'
if resume_marker in tests:
    tests = tests.replace(resume_marker, 'report = scanner.run_scan(stock_universe=[ticker], etf_universe=[], data_source="tickflow")', 1)

tests = replace_test_method(
    tests,
    "test_enrichment_uses_realtime_close_after_market_close",
    '''    def test_tickflow_free_never_promotes_daily_close_to_realtime(self):\n        result = ScanResult(ticker="000858.SZ", close=78.56)\n        trade_date = (\n            pd.Timestamp.now(tz="Asia/Shanghai").normalize() - pd.offsets.BDay(1)\n        ).tz_localize(None)\n        frame = pd.DataFrame({\n            "Open": [78.0],\n            "High": [79.0],\n            "Low": [77.5],\n            "Close": [78.56],\n            "Volume": [1000.0],\n        }, index=pd.DatetimeIndex([trade_date]))\n\n        with patch.object(analytics, "_load_benchmark_frames", return_value={}), patch.object(\n            analytics, "_benchmark_regime", return_value=("震荡", "基准数据不足")\n        ):\n            analytics.enrich_results([result], "tickflow", frames={"000858.SZ": frame})\n\n        self.assertEqual(result.close, 78.56)\n        self.assertEqual(result.data_asof, trade_date.strftime("%Y-%m-%d"))''',
)

tests = replace_test_method(
    tests,
    "test_enrichment_keeps_daily_close_when_realtime_close_is_unavailable",
    '''    def test_enrichment_keeps_tickflow_daily_close(self):\n        result = ScanResult(ticker="000858.SZ", close=78.56)\n        frame = pd.DataFrame({\n            "Open": [78.0],\n            "High": [79.0],\n            "Low": [77.5],\n            "Close": [78.56],\n            "Volume": [1000.0],\n        }, index=pd.to_datetime(["2026-07-30"]))\n\n        with patch.object(analytics, "_load_benchmark_frames", return_value={}), patch.object(\n            analytics, "_benchmark_regime", return_value=("震荡", "基准数据不足")\n        ):\n            analytics.enrich_results([result], "tickflow", frames={"000858.SZ": frame})\n\n        self.assertEqual(result.close, 78.56)\n        self.assertEqual(result.data_asof, "2026-07-30")''',
)

tests = replace_test_method(
    tests,
    "test_all_tqdm_calls_disable_non_tty_stderr",
    '''    def test_all_tqdm_calls_disable_non_tty_stderr(self):\n        # TickFlow owns market-data batch progress, so downloader.py no longer\n        # creates a per-ticker tqdm bar. Scanner analysis still uses two bars.\n        for filename, expected_calls in (("downloader.py", 0), ("scanner.py", 2)):\n            tree = ast.parse(Path(filename).read_text(encoding="utf-8"))\n            calls = [\n                node for node in ast.walk(tree)\n                if isinstance(node, ast.Call)\n                and isinstance(node.func, ast.Name)\n                and node.func.id == "tqdm"\n            ]\n            self.assertEqual(len(calls), expected_calls)\n            for call in calls:\n                disable = next(\n                    (keyword.value for keyword in call.keywords if keyword.arg == "disable"),\n                    None,\n                )\n                self.assertIsNotNone(disable)\n                self.assertEqual(ast.unparse(disable), "not sys.stderr.isatty()")''',
)

tests = replace_test_method(
    tests,
    "test_dynamic_tiers_produce_all_levels",
    '''    def test_dynamic_tiers_keep_ordered_research_levels(self):\n        frame = pd.DataFrame({\n            "Ticker": ["A", "B", "C", "D"],\n            "Score": [60, 45, 35, 20],\n            "FinalScore": [60, 45, 35, 20],\n            "InstitutionalScore": [60, 45, 35, 20],\n            "EntrySignal": ["BUY_NOW", "WAIT_PULLBACK", "HOLD_WAIT", "HOLD_WAIT"],\n            "QualityGate": [True] * 4,\n            "QualityDataCompleteness": [1.0] * 4,\n        })\n        result = signal_lifecycle.finalize_signal_ranking(frame).set_index("Ticker")\n        self.assertEqual(result.loc["D", "InstitutionalTier"], "D级等待确认")\n        self.assertIn(\n            result.loc["A", "InstitutionalTier"],\n            {"A级机构启动", "B级观察", "C级价值观察"},\n        )\n        self.assertGreater(\n            result.loc["A", "InstitutionalScore"], result.loc["D", "InstitutionalScore"]\n        )''',
)

tests = tests.replace(
    '        self.assertIn("HardRiskFlag", gui.DISPLAY_COLUMNS)\n        self.assertIn("MarketRegime", gui.DISPLAY_COLUMNS)\n',
    '        self.assertNotIn("HardRiskFlag", gui.DISPLAY_COLUMNS)\n        self.assertNotIn("MarketRegime", gui.DISPLAY_COLUMNS)\n',
    1,
)
write("test_regressions.py", tests)

# Provider tests: add incremental and rebase guards once.
provider_tests = read("test_tickflow_provider.py")
if "test_incremental_cache_update_uses_short_batch" not in provider_tests:
    insert = '''\n    def test_incremental_cache_update_uses_short_batch(self):\n        stale = pd.DataFrame({\n            "Open": [10.0, 10.1], "High": [10.2, 10.3],\n            "Low": [9.9, 10.0], "Close": [10.1, 10.2],\n            "Volume": [1000, 1100],\n        }, index=pd.to_datetime(["2026-08-06", "2026-08-07"]))\n        recent = pd.DataFrame({\n            "Open": [10.1, 10.2], "High": [10.3, 10.4],\n            "Low": [10.0, 10.1], "Close": [10.2, 10.3],\n            "Volume": [1100, 1200],\n        }, index=pd.to_datetime(["2026-08-07", "2026-08-10"]))\n        with (\n            patch.object(downloader, "_load_cache", return_value=stale),\n            patch.object(downloader, "_cache_has_completed_daily_bar", return_value=False),\n            patch.object(downloader, "_batch_fetch", return_value={"600000.SH": recent}) as batch,\n            patch.object(downloader, "_save_cache"),\n        ):\n            result = downloader.download_batch([downloader.TickerInfo("600000.SH")])\n        batch.assert_called_once_with(["600000.SH"], downloader._INCREMENTAL_BARS)\n        self.assertEqual(str(result["600000.SH"].index[-1].date()), "2026-08-10")\n\n    def test_forward_adjustment_change_forces_full_rebuild(self):\n        stale = pd.DataFrame({\n            "Open": [10.0, 10.1], "High": [10.2, 10.3],\n            "Low": [9.9, 10.0], "Close": [10.1, 10.2],\n            "Volume": [1000, 1100],\n        }, index=pd.to_datetime(["2026-08-06", "2026-08-07"]))\n        rebased = stale.copy()\n        rebased[["Open", "High", "Low", "Close"]] *= 0.9\n        full = pd.DataFrame({\n            "Open": [9.0, 9.1, 9.2], "High": [9.2, 9.3, 9.4],\n            "Low": [8.9, 9.0, 9.1], "Close": [9.1, 9.2, 9.3],\n            "Volume": [1000, 1100, 1200],\n        }, index=pd.to_datetime(["2026-08-06", "2026-08-07", "2026-08-10"]))\n        with (\n            patch.object(downloader, "_load_cache", return_value=stale),\n            patch.object(downloader, "_cache_has_completed_daily_bar", return_value=False),\n            patch.object(\n                downloader, "_batch_fetch",\n                side_effect=[{"600000.SH": rebased}, {"600000.SH": full}],\n            ) as batch,\n            patch.object(downloader, "_save_cache"),\n        ):\n            result = downloader.download_batch([downloader.TickerInfo("600000.SH")])\n        self.assertEqual(batch.call_count, 2)\n        self.assertEqual(batch.call_args_list[0].args, (["600000.SH"], downloader._INCREMENTAL_BARS))\n        self.assertEqual(batch.call_args_list[1].args, (["600000.SH"],))\n        self.assertEqual(str(result["600000.SH"].index[-1].date()), "2026-08-10")\n'''
    provider_tests = provider_tests.rstrip() + "\n" + insert + "\n"
write("test_tickflow_provider.py", provider_tests)

# Self-clean one-shot patch machinery.
for relative in ("tools/patch_tickflow_ci.py", ".github/workflows/patch-tickflow-ci.yml"):
    (ROOT / relative).unlink(missing_ok=True)
