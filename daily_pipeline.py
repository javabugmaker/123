from __future__ import annotations

"""One-click daily workflow with v29 transactional publication.

The canonical output set is snapshotted before a run.  A scan/backtest/data
quality failure restores that snapshot; a successful run is archived under a
RunId and only then advances LatestRun.json.  Scoring semantics are untouched.
"""

import argparse
import csv
import json
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import main as scanner_cli
from config import (
    BACKTEST_MAX_PROCESSES,
    DAILY_MIN_ETF_COUNT,
    DAILY_MIN_FRESH_RATIO,
    DAILY_MIN_STOCK_COUNT,
    DAILY_MIN_UNIVERSE_TOTAL,
    DAILY_RELATIVE_UNIVERSE_FLOOR,
    OUTPUT_DIR,
    PIPELINE_VERSION,
    TOP_N_PARQUET,
    TOP_N_REPORT,
)
from trading_calendar import latest_completed_trading_day

logger = logging.getLogger("institution_scanner.daily")

FINAL_OUTPUTS = (
    "Top50Mixed.csv",
    "Top50Stocks.csv",
    "Top50ETF.csv",
)
_ARCHIVE_FILES = (
    "AllResults.parquet",
    "DecisionResults.csv",
    "Top50.csv",
    "Top50Mixed.csv",
    "Top50Stocks.csv",
    "Top50ETF.csv",
    "Top50TradeReady.csv",
    "BacktestSummary.json",
    "DailyRunSummary.json",
)
_PUBLISH_PATTERNS = (
    "Top*.csv",
    "Top*.parquet",
    "AllResults.csv",
    "AllResults.parquet",
    "DecisionResults.csv",
    "BacktestSummary.json",
    "DailyRunSummary.json",
    "ScoreCalibration.json",
    "TierPerformanceReport.csv",
    "FactorICReport.csv",
)


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


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


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y", "是"}


def _csv_profile(path: Path, expected_date: str) -> dict[str, object]:
    profile: dict[str, object] = {
        "rows": 0,
        "stocks": 0,
        "etfs": 0,
        "fresh_rows": 0,
        "fresh_ratio": 0.0,
        "run_ids": [],
    }
    if not path.exists():
        return profile
    run_ids: set[str] = set()
    rows = stocks = etfs = fresh = 0
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                if not str(row.get("Ticker", "")).strip():
                    continue
                rows += 1
                asset = str(row.get("AssetType", "")).strip().lower()
                if asset == "etf" or _truthy(row.get("IsETF", False)):
                    etfs += 1
                else:
                    stocks += 1
                if str(row.get("DataAsOf", "")).strip() == expected_date:
                    fresh += 1
                run_id = str(row.get("RunId", "")).strip()
                if run_id:
                    run_ids.add(run_id)
    except (OSError, UnicodeError, csv.Error):
        return profile
    profile.update(
        {
            "rows": rows,
            "stocks": stocks,
            "etfs": etfs,
            "fresh_rows": fresh,
            "fresh_ratio": round(fresh / rows, 4) if rows else 0.0,
            "run_ids": sorted(run_ids),
        }
    )
    return profile


def _quality_gate_errors(
    scan_profile: dict[str, object],
    previous_summary: dict[str, object],
    *,
    quality_gates: bool,
) -> list[str]:
    if not quality_gates:
        return []
    errors: list[str] = []
    total = int(scan_profile.get("rows", 0) or 0)
    stocks = int(scan_profile.get("stocks", 0) or 0)
    etfs = int(scan_profile.get("etfs", 0) or 0)
    fresh_ratio = float(scan_profile.get("fresh_ratio", 0.0) or 0.0)
    if total < int(DAILY_MIN_UNIVERSE_TOTAL):
        errors.append(f"有效标的仅 {total}，低于安全下限 {DAILY_MIN_UNIVERSE_TOTAL}")
    if stocks < int(DAILY_MIN_STOCK_COUNT):
        errors.append(f"股票仅 {stocks}，低于安全下限 {DAILY_MIN_STOCK_COUNT}")
    if etfs < int(DAILY_MIN_ETF_COUNT):
        errors.append(f"ETF仅 {etfs}，低于安全下限 {DAILY_MIN_ETF_COUNT}")
    if fresh_ratio < float(DAILY_MIN_FRESH_RATIO):
        errors.append(
            f"最新交易日覆盖率 {fresh_ratio:.1%}，低于 {DAILY_MIN_FRESH_RATIO:.0%}"
        )

    previous_universe = previous_summary.get("universe", {})
    if isinstance(previous_universe, dict):
        for key, label, current in (
            ("rows", "总标的", total),
            ("stocks", "股票", stocks),
            ("etfs", "ETF", etfs),
        ):
            old = int(previous_universe.get(key, 0) or 0)
            if old >= 100 and current < old * float(DAILY_RELATIVE_UNIVERSE_FLOOR):
                errors.append(
                    f"{label}数量从上一轮 {old} 异常降至 {current}"
                )
    return errors


def _final_output_errors(
    scan_profile: dict[str, object],
    profiles: dict[str, dict[str, object]],
    *,
    quality_gates: bool,
) -> list[str]:
    errors: list[str] = []
    scan_ids = list(scan_profile.get("run_ids", []) or [])
    data_run_id = scan_ids[0] if len(scan_ids) == 1 else ""
    if quality_gates and len(scan_ids) != 1:
        errors.append(f"AllResults RunId 不唯一：{scan_ids or 'missing'}")
    for name in FINAL_OUTPUTS:
        profile = profiles.get(name, {})
        if int(profile.get("rows", 0) or 0) <= 0:
            errors.append(f"{name} 缺失或为空")
            continue
        if quality_gates:
            fresh_ratio = float(profile.get("fresh_ratio", 0.0) or 0.0)
            if fresh_ratio < float(DAILY_MIN_FRESH_RATIO):
                errors.append(f"{name} 最新交易日覆盖率仅 {fresh_ratio:.1%}")
            run_ids = list(profile.get("run_ids", []) or [])
            if data_run_id and run_ids != [data_run_id]:
                errors.append(f"{name} RunId={run_ids or 'missing'}，与 AllResults 不一致")
    return errors


def _published_files() -> dict[str, Path]:
    files: dict[str, Path] = {}
    for pattern in _PUBLISH_PATTERNS:
        for path in OUTPUT_DIR.glob(pattern):
            if path.is_file():
                files[path.name] = path
    return files


def _begin_transaction(pipeline_run_id: str) -> tuple[Path, set[str]]:
    tx_dir = OUTPUT_DIR / ".daily_transactions" / pipeline_run_id
    tx_dir.mkdir(parents=True, exist_ok=True)
    existing = _published_files()
    for name, path in existing.items():
        shutil.copy2(path, tx_dir / name)
    _atomic_write_json(tx_dir / "state.json", {"existing": sorted(existing)})
    return tx_dir, set(existing)


def _rollback_transaction(tx_dir: Path, existing: set[str]) -> None:
    for name, path in _published_files().items():
        if name not in existing:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    for name in existing:
        backup = tx_dir / name
        if not backup.exists():
            continue
        target = OUTPUT_DIR / name
        temporary = target.with_name(f".{target.name}.rollback.tmp")
        shutil.copy2(backup, temporary)
        os.replace(temporary, target)
    shutil.rmtree(tx_dir, ignore_errors=True)


def _commit_transaction(tx_dir: Path) -> None:
    shutil.rmtree(tx_dir, ignore_errors=True)


def _archive_run(pipeline_run_id: str, payload: dict[str, object]) -> Path:
    run_dir = OUTPUT_DIR / "runs" / pipeline_run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        for name in _ARCHIVE_FILES:
            source = OUTPUT_DIR / name
            if source.exists() and source.is_file():
                shutil.copy2(source, run_dir / name)
        _atomic_write_json(run_dir / "RunManifest.json", payload)
        _atomic_write_json(
            OUTPUT_DIR / "LatestRun.json",
            {
                "run_id": pipeline_run_id,
                "data_run_id": payload.get("data_run_id", ""),
                "expected_trading_date": payload.get("expected_trading_date", ""),
                "run_dir": str(run_dir.relative_to(OUTPUT_DIR)),
                "pipeline_version": PIPELINE_VERSION,
            },
        )
    except Exception:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    return run_dir


def _write_manifest(
    *,
    pipeline_run_id: str,
    data_run_id: str,
    started_at: str,
    elapsed_seconds: float,
    ticker_count: int,
    workers: int,
    mode: str,
    expected_date: str,
    scan_profile: dict[str, object],
    final_profiles: dict[str, dict[str, object]],
    stage_seconds: dict[str, float],
) -> dict[str, object]:
    backtest = _read_json(OUTPUT_DIR / "BacktestSummary.json")
    evaluations = int(backtest.get("total_ticker_evaluations", 0) or 0)
    cache_hits = int(backtest.get("cache_hits", 0) or 0)
    cache_hit_rate = float(backtest.get("cache_hit_rate", 0.0) or 0.0)
    if cache_hit_rate <= 0 and evaluations > 0:
        cache_hit_rate = cache_hits / evaluations
    exact_elapsed = float(backtest.get("exact_refinement_elapsed_seconds", 0.0) or 0.0)
    backtest_elapsed = float(backtest.get("elapsed_seconds", 0.0) or 0.0)
    payload: dict[str, object] = {
        "pipeline_version": PIPELINE_VERSION,
        "run_id": pipeline_run_id,
        "data_run_id": data_run_id,
        "publish_status": "published",
        "started_at": started_at,
        "finished_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "elapsed_seconds": round(float(elapsed_seconds), 3),
        "ticker_count": int(ticker_count),
        "requested_workers": int(workers),
        "requested_mode": str(mode).upper(),
        "expected_trading_date": expected_date,
        "universe": {
            "rows": int(scan_profile.get("rows", 0) or 0),
            "stocks": int(scan_profile.get("stocks", 0) or 0),
            "etfs": int(scan_profile.get("etfs", 0) or 0),
        },
        "freshness": {
            "all_results_ratio": float(scan_profile.get("fresh_ratio", 0.0) or 0.0),
            **{
                name: float(profile.get("fresh_ratio", 0.0) or 0.0)
                for name, profile in final_profiles.items()
            },
        },
        "stage_seconds": {
            key: round(float(value), 3) for key, value in stage_seconds.items()
        },
        "backtest": {
            "run_mode": str(backtest.get("mode", "")).upper(),
            "engine": backtest.get("engine", ""),
            "workers": int(backtest.get("worker_count", 0) or 0),
            "fast_screen_tickers": int(backtest.get("fast_screen_ticker_count", 0) or 0),
            "exact_refinement_tickers": int(backtest.get("exact_refinement_count", 0) or 0),
            "exact_workers": int(backtest.get("exact_worker_count", 0) or 0),
            "signal_sample_tickers": int(backtest.get("signal_sample_ticker_count", 0) or 0),
            "no_signal_tickers": int(backtest.get("no_signal_ticker_count", 0) or 0),
            "ranking_eligible_tickers": int(backtest.get("ranking_eligible_ticker_count", 0) or 0),
            "cache_hits": cache_hits,
            "cache_hit_rate": round(cache_hit_rate, 4),
            "elapsed_seconds": round(backtest_elapsed, 3),
            "fast_elapsed_seconds": round(max(0.0, backtest_elapsed - exact_elapsed), 3),
            "exact_elapsed_seconds": round(exact_elapsed, 3),
        },
        # v27 top-level compatibility keys.
        "backtest_engine": backtest.get("engine", ""),
        "backtest_workers": int(backtest.get("worker_count", 0) or 0),
        "backtest_cache_hits": cache_hits,
        "outputs": {
            name: _csv_row_count(OUTPUT_DIR / name)
            for name in FINAL_OUTPUTS
        },
    }
    _atomic_write_json(OUTPUT_DIR / "DailyRunSummary.json", payload)
    return payload


def run_daily_pipeline(
    *,
    data_source: str = "tickflow",
    workers: int | None = None,
    refresh_fundamentals: bool = False,
    backtest_mode: str = "fast",
    quality_gates: bool = True,
) -> int:
    started = time.perf_counter()
    started_at = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    pipeline_run_id = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S-%f")
    expected_date = latest_completed_trading_day().isoformat()
    worker_count = max(1, int(workers) if workers is not None else _default_workers())
    previous_summary = _read_json(OUTPUT_DIR / "DailyRunSummary.json")
    tx_dir, previous_files = _begin_transaction(pipeline_run_id)
    stage_seconds: dict[str, float] = {}

    try:
        logger.info("DAILY stage 1/4: 获取最新完整日K并重新扫描全市场（股票 + ETF）。")
        stage_started = time.perf_counter()
        scan_args = argparse.Namespace(
            stocks_only=False,
            etfs_only=False,
            tickers="",
            force_download=False,
            no_resume=True,
            cache_first=False,
            refresh_fundamentals=bool(refresh_fundamentals),
            data_source=data_source,
            top=TOP_N_REPORT,
            top_parquet=TOP_N_PARQUET,
        )
        scan_code = scanner_cli.cmd_scan(scan_args)
        stage_seconds["scan"] = time.perf_counter() - stage_started
        if scan_code != 0:
            logger.error("DAILY failed: 全市场扫描失败；恢复上一套已发布结果。")
            _rollback_transaction(tx_dir, previous_files)
            return int(scan_code)

        tickers = _current_result_tickers()
        if not tickers:
            logger.error("DAILY failed: AllResults.csv 没有可回测标的；恢复上一套结果。")
            _rollback_transaction(tx_dir, previous_files)
            return 2

        logger.info("DAILY stage 2/4: 数据完整性 / 新鲜度 / Universe 安全闸门。")
        gate_started = time.perf_counter()
        scan_profile = _csv_profile(OUTPUT_DIR / "AllResults.csv", expected_date)
        gate_errors = _quality_gate_errors(
            scan_profile, previous_summary, quality_gates=quality_gates
        )
        stage_seconds["quality_gate"] = time.perf_counter() - gate_started
        if gate_errors:
            logger.error("DAILY quality gate failed: %s", "；".join(gate_errors))
            _rollback_transaction(tx_dir, previous_files)
            return 2
        logger.info(
            "DAILY quality gate passed: universe=%d (stock=%d, ETF=%d), latest=%s %.1f%%.",
            int(scan_profile.get("rows", 0)),
            int(scan_profile.get("stocks", 0)),
            int(scan_profile.get("etfs", 0)),
            expected_date,
            float(scan_profile.get("fresh_ratio", 0.0)) * 100,
        )

        ticker_file = OUTPUT_DIR / "BacktestDaily.txt"
        ticker_file.write_text("\n".join(tickers) + "\n", encoding="utf-8")
        logger.info(
            "DAILY stage 3/4: 回测 %d 个有效标的（FAST筛选 + EXACT证据精炼, workers=%d）。",
            len(tickers), worker_count,
        )
        backtest_started = time.perf_counter()
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
        stage_seconds["backtest"] = time.perf_counter() - backtest_started
        if backtest_code != 0:
            logger.error("DAILY failed: 历史回测失败；恢复上一套已发布结果。")
            _rollback_transaction(tx_dir, previous_files)
            return int(backtest_code)

        logger.info("DAILY stage 4/4: 校验同一 RunId 并原子发布最终榜单。")
        publish_started = time.perf_counter()
        # Re-read AllResults because backtest ranking rewrites it with provenance.
        scan_profile = _csv_profile(OUTPUT_DIR / "AllResults.csv", expected_date)
        final_profiles = {
            name: _csv_profile(OUTPUT_DIR / name, expected_date)
            for name in FINAL_OUTPUTS
        }
        final_errors = _final_output_errors(
            scan_profile, final_profiles, quality_gates=quality_gates
        )
        if final_errors:
            logger.error("DAILY publish gate failed: %s", "；".join(final_errors))
            _rollback_transaction(tx_dir, previous_files)
            return 2

        scan_ids = list(scan_profile.get("run_ids", []) or [])
        data_run_id = scan_ids[0] if len(scan_ids) == 1 else ""
        stage_seconds["publish_validation"] = time.perf_counter() - publish_started
        elapsed = time.perf_counter() - started
        payload = _write_manifest(
            pipeline_run_id=pipeline_run_id,
            data_run_id=data_run_id,
            started_at=started_at,
            elapsed_seconds=elapsed,
            ticker_count=len(tickers),
            workers=worker_count,
            mode=backtest_mode,
            expected_date=expected_date,
            scan_profile=scan_profile,
            final_profiles=final_profiles,
            stage_seconds=stage_seconds,
        )
        run_dir = _archive_run(pipeline_run_id, payload)
        _commit_transaction(tx_dir)
        counts = ", ".join(
            f"{name}={_csv_row_count(OUTPUT_DIR / name)}" for name in FINAL_OUTPUTS
        )
        backtest = payload.get("backtest", {}) if isinstance(payload.get("backtest", {}), dict) else {}
        logger.info(
            "DAILY complete: %s · EXACT=%s · cache=%.1f%% · latest=%s · elapsed=%.1fs · archive=%s",
            counts,
            backtest.get("exact_refinement_tickers", 0),
            float(backtest.get("cache_hit_rate", 0.0) or 0.0) * 100,
            expected_date,
            elapsed,
            run_dir,
        )
        return 0
    except Exception:
        logger.exception("DAILY unexpected failure; restoring previous published result set.")
        _rollback_transaction(tx_dir, previous_files)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="InstitutionScanner 一键今日更新")
    parser.add_argument("--data-source", default="tickflow", choices=("tickflow",))
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--refresh-fundamentals", action="store_true")
    parser.add_argument(
        "--backtest-mode", default="fast", choices=("fast", "exact", "auto")
    )
    parser.add_argument("--no-quality-gates", action="store_true", help=argparse.SUPPRESS)
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
        quality_gates=not bool(args.no_quality_gates),
    )


if __name__ == "__main__":
    raise SystemExit(main())
