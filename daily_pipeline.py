from __future__ import annotations

"""One-click daily InstitutionScanner workflow.

The daily path deliberately refreshes incrementally instead of force-downloading
all historical bars: every symbol is re-analysed, TickFlow fills the newest
missing daily data, then the current result universe receives a FAST backtest.
The existing backtest ranking layer performs Exact refinement for the strongest
candidates and republishes all GUI-facing Top50 lists afterwards.
"""

import argparse
import csv
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import main as scanner_cli
from config import BACKTEST_MAX_PROCESSES, OUTPUT_DIR, TOP_N_PARQUET, TOP_N_REPORT

logger = logging.getLogger("institution_scanner.daily")

FINAL_OUTPUTS = (
    "Top50Mixed.csv",
    "Top50Stocks.csv",
    "Top50ETF.csv",
)


def _current_result_tickers(path: Path | None = None) -> list[str]:
    source = path or (OUTPUT_DIR / "AllResults.csv")
    if not source.exists():
        return []
    try:
        with source.open("r", encoding="utf-8-sig", newline="") as file:
            return list(
                dict.fromkeys(
                    str(row.get("Ticker", "")).strip().upper()
                    for row in csv.DictReader(file)
                    if str(row.get("Ticker", "")).strip()
                )
            )
    except (OSError, UnicodeError, csv.Error):
        return []


def _csv_row_count(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            reader = csv.reader(file)
            next(reader, None)
            return sum(1 for row in reader if row)
    except (OSError, UnicodeError, csv.Error):
        return 0


def _default_workers() -> int:
    cpu = max(1, (os.cpu_count() or 2) - 1)
    return max(1, min(int(BACKTEST_MAX_PROCESSES), cpu))


def _write_manifest(
    *,
    started_at: str,
    elapsed_seconds: float,
    ticker_count: int,
    workers: int,
    mode: str,
) -> None:
    summary_path = OUTPUT_DIR / "BacktestSummary.json"
    backtest: dict[str, object] = {}
    if summary_path.exists():
        try:
            backtest = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            backtest = {}
    payload = {
        "started_at": started_at,
        "finished_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "elapsed_seconds": round(float(elapsed_seconds), 3),
        "ticker_count": int(ticker_count),
        "requested_workers": int(workers),
        "requested_mode": str(mode).upper(),
        "backtest_engine": backtest.get("engine", ""),
        "backtest_workers": backtest.get("worker_count", 0),
        "backtest_cache_hits": backtest.get("cache_hits", 0),
        "outputs": {
            name: _csv_row_count(OUTPUT_DIR / name)
            for name in FINAL_OUTPUTS
        },
    }
    temporary = OUTPUT_DIR / ".DailyRunSummary.json.tmp"
    target = OUTPUT_DIR / "DailyRunSummary.json"
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)


def run_daily_pipeline(
    *,
    data_source: str = "tickflow",
    workers: int | None = None,
    refresh_fundamentals: bool = False,
    backtest_mode: str = "fast",
) -> int:
    started = time.perf_counter()
    started_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    worker_count = int(workers) if workers is not None else _default_workers()
    worker_count = max(1, worker_count)

    logger.info(
        "DAILY stage 1/3: 获取今日最新行情并重新扫描全市场（股票 + ETF）。"
    )
    scan_args = argparse.Namespace(
        stocks_only=False,
        etfs_only=False,
        tickers="",
        force_download=False,
        # A daily run must re-analyse every current symbol. Market caches remain
        # incremental, so this does not re-download ten years of history.
        no_resume=True,
        cache_first=False,
        refresh_fundamentals=bool(refresh_fundamentals),
        data_source=data_source,
        top=TOP_N_REPORT,
        top_parquet=TOP_N_PARQUET,
    )
    scan_code = scanner_cli.cmd_scan(scan_args)
    if scan_code != 0:
        logger.error("DAILY failed: 全市场扫描失败，停止后续回测。")
        return int(scan_code)

    tickers = _current_result_tickers()
    if not tickers:
        logger.error("DAILY failed: AllResults.csv 没有可回测标的。")
        return 2

    ticker_file = OUTPUT_DIR / "BacktestDaily.txt"
    ticker_file.write_text("\n".join(tickers) + "\n", encoding="utf-8")
    logger.info(
        "DAILY stage 2/3: 回测本轮 %d 个有效标的（mode=%s, workers=%d）。",
        len(tickers),
        backtest_mode.upper(),
        worker_count,
    )
    logger.info(
        "DAILY backtest policy: FAST 全量筛选 + 现有 EXACT 候选精炼；持久缓存开启时仅增量重算。"
    )
    backtest_args = argparse.Namespace(
        all_results=False,
        tickers_file=ticker_file,
        tickers=None,
        data_source=data_source,
        mode=backtest_mode,
        workers=worker_count,
        objective="net_excess_return_20d",
        benchmark="沪深300",
        commission=0.0003,
        stamp_duty=0.0005,
        slippage=0.001,
        test_ratio=0.2,
        validation_ratio=0.2,
    )
    backtest_code = scanner_cli.cmd_backtest(backtest_args)
    if backtest_code != 0:
        logger.error("DAILY failed: 历史回测失败；扫描结果已保留，但未发布最终回测排名。")
        return int(backtest_code)

    logger.info("DAILY stage 3/3: 校验并发布最终 Top50 榜单。")
    missing = [name for name in FINAL_OUTPUTS if _csv_row_count(OUTPUT_DIR / name) <= 0]
    if missing:
        logger.error("DAILY failed: 最终榜单缺失或为空：%s", ", ".join(missing))
        return 2

    elapsed = time.perf_counter() - started
    _write_manifest(
        started_at=started_at,
        elapsed_seconds=elapsed,
        ticker_count=len(tickers),
        workers=worker_count,
        mode=backtest_mode,
    )
    counts = ", ".join(
        f"{name}={_csv_row_count(OUTPUT_DIR / name)}"
        for name in FINAL_OUTPUTS
    )
    logger.info("DAILY complete: %s · elapsed=%.1fs", counts, elapsed)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="InstitutionScanner 一键今日更新")
    parser.add_argument("--data-source", default="tickflow", choices=("tickflow",))
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--refresh-fundamentals", action="store_true")
    parser.add_argument(
        "--backtest-mode",
        default="fast",
        choices=("fast", "exact", "auto"),
        help="日常默认 fast；FAST 完成后现有排名逻辑会 Exact 精炼强候选。",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    scanner_cli._configure_logging(bool(args.verbose))
    return run_daily_pipeline(
        data_source=args.data_source,
        workers=args.workers,
        refresh_fundamentals=bool(args.refresh_fundamentals),
        backtest_mode=args.backtest_mode,
    )


if __name__ == "__main__":
    raise SystemExit(main())
