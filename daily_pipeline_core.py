"""One-click daily workflow with v29 transactional publication.

The canonical output set is snapshotted before a run.  A scan/backtest/data
quality failure restores that snapshot; a successful run is archived under a
RunId and only then advances LatestRun.json.  Scoring semantics are untouched.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import os
import shutil
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import main as scanner_cli
from config import (
    BACKTEST_MAX_PROCESSES,
    BACKTEST_STOCK_COMMISSION_RATE,
    DAILY_MIN_ETF_COUNT,
    DAILY_MIN_FRESH_RATIO,
    DAILY_MIN_STOCK_COUNT,
    DAILY_MIN_UNIVERSE_TOTAL,
    DAILY_MIN_VALID_ETF_RATIO,
    DAILY_MIN_VALID_STOCK_RATIO,
    DAILY_RELATIVE_UNIVERSE_FLOOR,
    DATA_FRESHNESS_STALE_TRADING_DAYS,
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
    "AllResults.csv",
    "AllResults.parquet",
    "DecisionResults.csv",
    "Top50.csv",
    "Top50Mixed.csv",
    "Top50Stocks.csv",
    "Top50ETF.csv",
    "Top50TradeReady.csv",
    "Top50SustainedSignals.csv",
    "Top50ValueTrapRisk.csv",
    "SignalHistory.csv",
    "SignalTracking.csv",
    "BacktestSummary.json",
    "ScanPerformance.json",
    "DailyRunSummary.json",
    "ScoreCalibration.json",
    "TierPerformanceReport.csv",
    "FactorICReport.csv",
)
_PUBLISH_PATTERNS = (
    "Top*.csv",
    "Top*.parquet",
    "AllResults.csv",
    "AllResults.parquet",
    "DecisionResults.csv",
    "BacktestSummary.json",
    "DailyRunSummary.json",
    "ScanPerformance.json",
    "ScoreCalibration.json",
    "TierPerformanceReport.csv",
    "FactorICReport.csv",
    "SignalHistory.csv",
    "SignalTracking.csv",
)

_STAGING_SEED_FILES = (
    "AllResults.parquet",
    "SignalHistory.csv",
    "SignalTracking.csv",
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
        "valid_rows": 0,
        "valid_stocks": 0,
        "valid_etfs": 0,
        "error_rows": 0,
        "valid_stock_ratio": 0.0,
        "valid_etf_ratio": 0.0,
        "fresh_rows": 0,
        "fresh_ratio": 0.0,
        "quality_applicable_stocks": 0,
        "quality_gate_passed_stocks": 0,
        "quality_gate_pass_rate": 0.0,
        "quality_hard_data_complete_stocks": 0,
        "quality_hard_data_complete_rate": 0.0,
        "run_ids": [],
    }
    if not path.exists():
        return profile
    run_ids: set[str] = set()
    rows = stocks = etfs = fresh = 0
    valid_rows = valid_stocks = valid_etfs = error_rows = 0
    quality_applicable_stocks = quality_gate_passed_stocks = 0
    quality_hard_data_complete_stocks = 0
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                if not str(row.get("Ticker", "")).strip():
                    continue
                rows += 1
                asset = str(row.get("AssetType", "")).strip().lower()
                row_is_etf = asset == "etf" or _truthy(row.get("IsETF", False))
                if row_is_etf:
                    etfs += 1
                else:
                    stocks += 1
                has_error = bool(str(row.get("Error", "")).strip())
                if has_error:
                    error_rows += 1
                else:
                    valid_rows += 1
                    if row_is_etf:
                        valid_etfs += 1
                    else:
                        valid_stocks += 1
                        quality_applicable = _truthy(
                            row.get("QualityApplicable", True)
                        )
                        if quality_applicable:
                            quality_applicable_stocks += 1
                            quality_gate_passed_stocks += int(
                                _truthy(row.get("QualityGate", False))
                            )
                            quality_hard_data_complete_stocks += int(
                                _truthy(row.get("QualityHardDataComplete", False))
                            )
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
            "valid_rows": valid_rows,
            "valid_stocks": valid_stocks,
            "valid_etfs": valid_etfs,
            "error_rows": error_rows,
            "valid_stock_ratio": round(valid_stocks / stocks, 4) if stocks else 0.0,
            "valid_etf_ratio": round(valid_etfs / etfs, 4) if etfs else 0.0,
            "fresh_rows": fresh,
            "fresh_ratio": round(fresh / rows, 4) if rows else 0.0,
            "quality_applicable_stocks": quality_applicable_stocks,
            "quality_gate_passed_stocks": quality_gate_passed_stocks,
            "quality_gate_pass_rate": round(
                quality_gate_passed_stocks / quality_applicable_stocks, 4
            )
            if quality_applicable_stocks
            else 0.0,
            "quality_hard_data_complete_stocks": quality_hard_data_complete_stocks,
            "quality_hard_data_complete_rate": round(
                quality_hard_data_complete_stocks / quality_applicable_stocks, 4
            )
            if quality_applicable_stocks
            else 0.0,
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
    valid_stocks = int(scan_profile.get("valid_stocks", stocks) or 0)
    valid_etfs = int(scan_profile.get("valid_etfs", etfs) or 0)
    valid_stock_ratio = float(
        scan_profile.get("valid_stock_ratio", valid_stocks / max(1, stocks)) or 0.0
    )
    valid_etf_ratio = float(
        scan_profile.get("valid_etf_ratio", valid_etfs / max(1, etfs)) or 0.0
    )
    fresh_ratio = float(scan_profile.get("fresh_ratio", 0.0) or 0.0)
    if total < int(DAILY_MIN_UNIVERSE_TOTAL):
        errors.append(f"有效标的仅 {total}，低于安全下限 {DAILY_MIN_UNIVERSE_TOTAL}")
    if stocks < int(DAILY_MIN_STOCK_COUNT):
        errors.append(f"股票仅 {stocks}，低于安全下限 {DAILY_MIN_STOCK_COUNT}")
    if etfs < int(DAILY_MIN_ETF_COUNT):
        errors.append(f"ETF仅 {etfs}，低于安全下限 {DAILY_MIN_ETF_COUNT}")
    if valid_stocks < int(DAILY_MIN_STOCK_COUNT):
        errors.append(
            f"有效股票仅 {valid_stocks}/{stocks}，低于安全下限 {DAILY_MIN_STOCK_COUNT}"
        )
    if stocks and valid_stock_ratio < float(DAILY_MIN_VALID_STOCK_RATIO):
        errors.append(
            f"股票有效率 {valid_stock_ratio:.1%}，低于 {DAILY_MIN_VALID_STOCK_RATIO:.0%}"
        )
    if valid_etfs < int(DAILY_MIN_ETF_COUNT):
        errors.append(
            f"有效ETF仅 {valid_etfs}/{etfs}，低于安全下限 {DAILY_MIN_ETF_COUNT}"
        )
    if etfs and valid_etf_ratio < float(DAILY_MIN_VALID_ETF_RATIO):
        errors.append(
            f"ETF有效率 {valid_etf_ratio:.1%}，低于 {DAILY_MIN_VALID_ETF_RATIO:.0%}"
        )
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
    expected_split_rows = {
        "Top50Mixed.csv": min(
            int(TOP_N_REPORT), int(scan_profile.get("valid_rows", 0) or 0)
        ),
        "Top50Stocks.csv": min(
            int(TOP_N_REPORT), int(scan_profile.get("valid_stocks", 0) or 0)
        ),
        "Top50ETF.csv": min(
            int(TOP_N_REPORT), int(scan_profile.get("valid_etfs", 0) or 0)
        ),
    }
    for name in FINAL_OUTPUTS:
        profile = profiles.get(name, {})
        rows = int(profile.get("rows", 0) or 0)
        if rows <= 0:
            errors.append(f"{name} 缺失或为空")
            continue
        expected_rows = expected_split_rows.get(name, 0)
        if quality_gates and expected_rows and rows < expected_rows:
            errors.append(
                f"{name} 仅 {rows} 条，当前有效标的足以生成 {expected_rows} 条"
            )
        if quality_gates:
            fresh_ratio = float(profile.get("fresh_ratio", 0.0) or 0.0)
            if fresh_ratio < float(DAILY_MIN_FRESH_RATIO):
                errors.append(f"{name} 最新交易日覆盖率仅 {fresh_ratio:.1%}")
            run_ids = list(profile.get("run_ids", []) or [])
            if data_run_id and run_ids != [data_run_id]:
                errors.append(f"{name} RunId={run_ids or 'missing'}，与 AllResults 不一致")
    return errors


def _published_files(directory: Path | None = None) -> dict[str, Path]:
    root = directory or OUTPUT_DIR
    files: dict[str, Path] = {}
    for pattern in _PUBLISH_PATTERNS:
        for path in root.glob(pattern):
            if path.is_file():
                files[path.name] = path
    return files


def _prepare_staging(pipeline_run_id: str) -> Path:
    stage_dir = OUTPUT_DIR / ".staging" / pipeline_run_id
    stage_dir.mkdir(parents=True, exist_ok=False)
    try:
        for name in _STAGING_SEED_FILES:
            source = OUTPUT_DIR / name
            if source.exists() and source.is_file():
                shutil.copy2(source, stage_dir / name)
    except Exception:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise
    return stage_dir


@contextmanager
def _runtime_output_directory(directory: Path):
    """Redirect all scan/backtest writers while keeping canonical files stable."""
    import analytics
    import report
    import scanner
    import signal_lifecycle_core

    modules = (scanner_cli, analytics, report, scanner, signal_lifecycle_core)
    previous: list[tuple[object, str, object]] = []
    for module in modules:
        if hasattr(module, "OUTPUT_DIR"):
            previous.append((module, "OUTPUT_DIR", getattr(module, "OUTPUT_DIR")))
            setattr(module, "OUTPUT_DIR", directory)
    for module, attribute, value in (
        (scanner, "_CHECKPOINT_PATH", directory / "_checkpoint.json"),
        (signal_lifecycle_core, "HISTORY_FILE", directory / "SignalHistory.csv"),
        (signal_lifecycle_core, "TRACKING_FILE", directory / "SignalTracking.csv"),
    ):
        previous.append((module, attribute, getattr(module, attribute)))
        setattr(module, attribute, value)
    try:
        yield
    finally:
        for module, attribute, value in reversed(previous):
            setattr(module, attribute, value)


def _publish_staging(stage_dir: Path) -> None:
    staged = _published_files(stage_dir)
    if not staged:
        raise ValueError("staging directory contains no publishable outputs")
    for name, source in staged.items():
        target = OUTPUT_DIR / name
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.publish.tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, target)


def _begin_transaction(pipeline_run_id: str) -> tuple[Path, set[str]]:
    tx_dir = OUTPUT_DIR / ".daily_transactions" / pipeline_run_id
    tx_dir.mkdir(parents=True, exist_ok=True)
    try:
        existing = _published_files()
        for name, path in existing.items():
            shutil.copy2(path, tx_dir / name)
        _atomic_write_json(tx_dir / "state.json", {"existing": sorted(existing)})
    except Exception:
        shutil.rmtree(tx_dir, ignore_errors=True)
        raise
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _activate_run(
    pipeline_run_id: str,
    run_dir: Path,
    payload: dict[str, object],
) -> None:
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


def _archive_run(
    pipeline_run_id: str,
    payload: dict[str, object],
    *,
    source_dir: Path | None = None,
    activate: bool = True,
) -> Path:
    """Create an immutable per-run snapshot; never overwrite an existing run id."""
    run_dir = OUTPUT_DIR / "runs" / pipeline_run_id
    if run_dir.exists():
        raise FileExistsError(f"run archive already exists: {pipeline_run_id}")
    run_dir.mkdir(parents=True, exist_ok=False)
    try:
        archive_hashes: dict[str, str] = {}
        source_root = source_dir or OUTPUT_DIR
        archive_names = set(_ARCHIVE_FILES) | set(_published_files(source_root))
        for name in sorted(archive_names):
            source = source_root / name
            if source.exists() and source.is_file():
                destination = run_dir / name
                shutil.copy2(source, destination)
                archive_hashes[name] = _sha256_file(destination)
        manifest_payload = dict(payload)
        manifest_payload["archive_hashes_sha256"] = archive_hashes
        manifest_payload["archive_immutable"] = True
        _atomic_write_json(run_dir / "RunManifest.json", manifest_payload)
        if activate:
            _activate_run(pipeline_run_id, run_dir, payload)
    except Exception:
        shutil.rmtree(run_dir, ignore_errors=True)
        raise
    return run_dir


def _cache_health(
    previous_summary: dict[str, object],
    current_rate: float,
    evaluations: int,
) -> dict[str, object]:
    previous_backtest = previous_summary.get("backtest", {})
    if not isinstance(previous_backtest, dict):
        previous_backtest = {}
    previous_rate = float(previous_backtest.get("cache_hit_rate", 0.0) or 0.0)
    previous_version = str(previous_summary.get("pipeline_version", "") or "")
    cold_start = bool(previous_version and previous_version != PIPELINE_VERSION) or not previous_version
    rate = float(max(0.0, min(1.0, current_rate)))
    if evaluations <= 0:
        status = "未知"
    elif cold_start:
        status = "冷启动"
    elif rate >= 0.70:
        status = "健康"
    elif rate >= 0.35:
        status = "偏低"
    else:
        status = "异常偏低"
    return {
        "status": status,
        "cold_start": cold_start,
        "current_rate": round(rate, 4),
        "previous_rate": round(previous_rate, 4),
        "delta": round(rate - previous_rate, 4),
        "warning": bool(status == "异常偏低" and evaluations >= 100),
    }


def _decision_snapshot(path: Path) -> dict[str, dict[str, object]]:
    if not path.exists():
        return {}
    result: dict[str, dict[str, object]] = {}
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as file:
            for row in csv.DictReader(file):
                ticker = str(row.get("Ticker", "")).strip().upper()
                if not ticker:
                    continue
                try:
                    score = float(row.get("RankingScore", 0.0) or 0.0)
                except (TypeError, ValueError):
                    score = 0.0
                if not math.isfinite(score):
                    score = 0.0
                result[ticker] = {
                    "eligibility": str(row.get("RankingEligibility", "") or "观察"),
                    "score": score,
                    "hard_gate": _truthy(row.get("HardGatePassed", False)),
                    "quality_applicable": _truthy(row.get("QualityApplicable", False)),
                    "quality_complete": _truthy(
                        row.get("QualityHardDataComplete", False)
                    ),
                    "backtest_requested": _truthy(
                        row.get("BacktestRequested", False)
                    ),
                    "backtest_eligible": _truthy(
                        row.get("BacktestEligibleForRanking", False)
                    ),
                    "hard_risk": _truthy(row.get("HardRiskFlag", False)),
                    "data_age": row.get("DataTradingAgeDays", ""),
                    "provider_error": bool(str(row.get("Error", "") or "").strip()),
                }
    except (OSError, UnicodeError, csv.Error):
        return {}
    return result


def _decision_health(path: Path) -> dict[str, object]:
    snapshot = _decision_snapshot(path)
    eligibility: dict[str, int] = {}
    blockers = {
        "hard_gate_failed": 0,
        "fundamental_hard_data_incomplete": 0,
        "hard_risk": 0,
        "local_backtest_not_rankable": 0,
        "stale_market_data": 0,
        "provider_error": 0,
    }
    decision_rows = 0
    for row in snapshot.values():
        provider_error = bool(row.get("provider_error", False))
        blockers["provider_error"] += int(provider_error)
        if provider_error:
            continue
        decision_rows += 1
        label = str(row.get("eligibility", "观察"))
        eligibility[label] = eligibility.get(label, 0) + 1
        blockers["hard_gate_failed"] += int(not bool(row.get("hard_gate", False)))
        blockers["fundamental_hard_data_incomplete"] += int(
            bool(row.get("quality_applicable", False))
            and not bool(row.get("quality_complete", False))
        )
        blockers["hard_risk"] += int(bool(row.get("hard_risk", False)))
        blockers["local_backtest_not_rankable"] += int(
            bool(row.get("backtest_requested", False))
            and not bool(row.get("backtest_eligible", False))
        )
        try:
            age = float(row.get("data_age", 0) or 0)
        except (TypeError, ValueError):
            age = 0.0
        blockers["stale_market_data"] += int(
            age > int(DATA_FRESHNESS_STALE_TRADING_DAYS)
        )
    top_blockers = sorted(blockers.items(), key=lambda item: (-item[1], item[0]))
    return {
        "rows": len(snapshot),
        "decision_rows": decision_rows,
        "eligibility": eligibility,
        "blockers": blockers,
        "top_blockers": [
            {"name": name, "count": count}
            for name, count in top_blockers
            if count > 0
        ][:5],
    }


def _run_diff(previous_path: Path, current_path: Path) -> dict[str, object]:
    previous = {
        ticker: row
        for ticker, row in _decision_snapshot(previous_path).items()
        if not bool(row.get("provider_error", False))
    }
    current = {
        ticker: row
        for ticker, row in _decision_snapshot(current_path).items()
        if not bool(row.get("provider_error", False))
    }
    priority = {"风险过滤": 0, "观察": 1, "谨慎候选": 2, "推荐": 3}
    shared = previous.keys() & current.keys()
    upgraded: list[str] = []
    downgraded: list[str] = []
    score_up: list[str] = []
    score_down: list[str] = []
    for ticker in shared:
        old = previous[ticker]
        new = current[ticker]
        old_rank = priority.get(str(old.get("eligibility", "观察")), 1)
        new_rank = priority.get(str(new.get("eligibility", "观察")), 1)
        if new_rank > old_rank:
            upgraded.append(ticker)
        elif new_rank < old_rank:
            downgraded.append(ticker)
        delta = float(new.get("score", 0.0)) - float(old.get("score", 0.0))
        if delta >= 5.0:
            score_up.append(ticker)
        elif delta <= -5.0:
            score_down.append(ticker)
    return {
        "previous_rows": len(previous),
        "current_rows": len(current),
        "added": len(current.keys() - previous.keys()),
        "removed": len(previous.keys() - current.keys()),
        "eligibility_upgraded": len(upgraded),
        "eligibility_downgraded": len(downgraded),
        "score_up_5_plus": len(score_up),
        "score_down_5_plus": len(score_down),
        "upgraded_examples": sorted(upgraded)[:10],
        "downgraded_examples": sorted(downgraded)[:10],
    }


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
    previous_summary: dict[str, object],
    result_dir: Path | None = None,
    previous_results_path: Path | None = None,
) -> dict[str, object]:
    root = result_dir or OUTPUT_DIR
    backtest = _read_json(root / "BacktestSummary.json")
    scan_performance = _read_json(root / "ScanPerformance.json")
    evaluations = int(backtest.get("total_ticker_evaluations", 0) or 0)
    cache_hits = int(backtest.get("cache_hits", 0) or 0)
    cache_hit_rate = float(backtest.get("cache_hit_rate", 0.0) or 0.0)
    if cache_hit_rate <= 0 and evaluations > 0:
        cache_hit_rate = cache_hits / evaluations
    cache_health = _cache_health(previous_summary, cache_hit_rate, evaluations)
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
        "quality_gate": {
            "applicable_stocks": int(
                scan_profile.get("quality_applicable_stocks", 0) or 0
            ),
            "passed_stocks": int(
                scan_profile.get("quality_gate_passed_stocks", 0) or 0
            ),
            "pass_rate": float(
                scan_profile.get("quality_gate_pass_rate", 0.0) or 0.0
            ),
            "hard_data_complete_stocks": int(
                scan_profile.get("quality_hard_data_complete_stocks", 0) or 0
            ),
            "hard_data_complete_rate": float(
                scan_profile.get("quality_hard_data_complete_rate", 0.0) or 0.0
            ),
        },
        "stage_seconds": {
            key: round(float(value), 3) for key, value in stage_seconds.items()
        },
        "scan_breakdown": {
            "total_seconds": round(float(scan_performance.get("total_seconds", stage_seconds.get("scan", 0.0)) or 0.0), 3),
            "universe_seconds": round(float(scan_performance.get("universe_seconds", 0.0) or 0.0), 3),
            "fundamentals_seconds": round(float(scan_performance.get("fundamentals_seconds", 0.0) or 0.0), 3),
            "download_seconds": round(float(scan_performance.get("download_seconds", 0.0) or 0.0), 3),
            "analysis_seconds": round(float(scan_performance.get("analysis_seconds", 0.0) or 0.0), 3),
            "enrichment_seconds": round(float(scan_performance.get("enrichment_seconds", 0.0) or 0.0), 3),
            "export_seconds": round(float(scan_performance.get("export_seconds", 0.0) or 0.0), 3),
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
            "cache_health": str(cache_health.get("status", "未知")),
            "cache_cold_start": bool(cache_health.get("cold_start", False)),
            "cache_hit_rate_delta": float(cache_health.get("delta", 0.0) or 0.0),
            "cache_warning": bool(cache_health.get("warning", False)),
            "elapsed_seconds": round(backtest_elapsed, 3),
            "fast_elapsed_seconds": round(max(0.0, backtest_elapsed - exact_elapsed), 3),
            "exact_elapsed_seconds": round(exact_elapsed, 3),
            "calibration_lookup_seconds": round(float(backtest.get("calibration_lookup_elapsed_seconds", 0.0) or 0.0), 3),
            "ranking_compute_seconds": round(float(backtest.get("ranking_compute_elapsed_seconds", 0.0) or 0.0), 3),
            "persistence_seconds": round(float(backtest.get("persistence_elapsed_seconds", 0.0) or 0.0), 3),
            "postprocess_seconds": round(
                max(
                    float(backtest.get("postprocess_elapsed_seconds", 0.0) or 0.0),
                    float(stage_seconds.get("backtest", 0.0) or 0.0) - backtest_elapsed,
                    0.0,
                ),
                3,
            ),
            "execution_model": backtest.get("execution_model", ""),
            "cost_parameters": backtest.get("cost_parameters", {}),
            "calibration_stability": backtest.get("calibration_stability", {}),
            "point_in_time_universe": backtest.get("point_in_time_universe", {}),
        },
        # v27 top-level compatibility keys.
        "backtest_engine": backtest.get("engine", ""),
        "backtest_workers": int(backtest.get("worker_count", 0) or 0),
        "backtest_cache_hits": cache_hits,
        "outputs": {
            name: _csv_row_count(root / name)
            for name in FINAL_OUTPUTS
        },
        "decision_health": _decision_health(root / "AllResults.csv"),
        "run_diff": _run_diff(
            previous_results_path or (OUTPUT_DIR / "AllResults.csv"),
            root / "AllResults.csv",
        ),
    }
    _atomic_write_json(root / "DailyRunSummary.json", payload)
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
    tx_dir: Path | None = None
    stage_dir: Path | None = None
    previous_results_path: Path | None = None
    previous_files: set[str] = set()
    stage_seconds: dict[str, float] = {}
    run_dir: Path | None = None

    try:
        tx_dir, previous_files = _begin_transaction(pipeline_run_id)
        stage_dir = _prepare_staging(pipeline_run_id)
        previous_results_path = tx_dir / "AllResults.csv"
        _atomic_write_json(
            OUTPUT_DIR / "PublicationStatus.json",
            {
                "status": "running",
                "run_id": pipeline_run_id,
                "started_at": started_at,
                "staging": str(stage_dir.relative_to(OUTPUT_DIR)),
                "latest_run_unchanged": True,
            },
        )
        with _runtime_output_directory(stage_dir):
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
                raise RuntimeError(f"scan stage failed with exit code {scan_code}")

            tickers = _current_result_tickers(stage_dir / "AllResults.csv")
            if not tickers:
                raise RuntimeError("AllResults.csv 没有可回测标的")

            logger.info("DAILY stage 2/4: 数据完整性 / 新鲜度 / Universe 安全闸门。")
            gate_started = time.perf_counter()
            scan_profile = _csv_profile(stage_dir / "AllResults.csv", expected_date)
            gate_errors = _quality_gate_errors(
                scan_profile, previous_summary, quality_gates=quality_gates
            )
            stage_seconds["quality_gate"] = time.perf_counter() - gate_started
            if gate_errors:
                raise ValueError("DAILY quality gate failed: " + "；".join(gate_errors))
            logger.info(
                "DAILY quality gate passed: universe=%d (stock=%d, ETF=%d), latest=%s %.1f%%.",
                int(scan_profile.get("rows", 0)),
                int(scan_profile.get("stocks", 0)),
                int(scan_profile.get("etfs", 0)),
                expected_date,
                float(scan_profile.get("fresh_ratio", 0.0)) * 100,
            )

            ticker_file = stage_dir / "BacktestDaily.txt"
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
                commission=BACKTEST_STOCK_COMMISSION_RATE,
                stamp_duty=0.0005,
                slippage=0.001,
                test_ratio=0.2,
                validation_ratio=0.2,
            )
            backtest_code = scanner_cli.cmd_backtest(backtest_args)
            stage_seconds["backtest"] = time.perf_counter() - backtest_started
            if backtest_code != 0:
                raise RuntimeError(
                    f"backtest stage failed with exit code {backtest_code}"
                )

            logger.info("DAILY stage 4/4: 校验同一 RunId 并原子发布最终榜单。")
            publish_started = time.perf_counter()
            scan_profile = _csv_profile(stage_dir / "AllResults.csv", expected_date)
            final_profiles = {
                name: _csv_profile(stage_dir / name, expected_date)
                for name in FINAL_OUTPUTS
            }
            final_errors = _final_output_errors(
                scan_profile, final_profiles, quality_gates=quality_gates
            )
            if final_errors:
                raise ValueError(
                    "DAILY publish gate failed: " + "；".join(final_errors)
                )

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
                previous_summary=previous_summary,
                result_dir=stage_dir,
                previous_results_path=previous_results_path,
            )

        run_dir = _archive_run(
            pipeline_run_id,
            payload,
            source_dir=stage_dir,
            activate=False,
        )
        _atomic_write_json(
            OUTPUT_DIR / "PublicationStatus.json",
            {"status": "publishing", "run_id": pipeline_run_id},
        )
        _publish_staging(stage_dir)
        _activate_run(pipeline_run_id, run_dir, payload)
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
        _atomic_write_json(
            OUTPUT_DIR / "PublicationStatus.json",
            {
                "status": "published",
                "run_id": pipeline_run_id,
                "finished_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(
                    timespec="seconds"
                ),
            },
        )
        return 0
    except Exception:
        staged_checkpoint_discarded = bool(
            stage_dir is not None and (stage_dir / "_checkpoint.json").is_file()
        )
        logger.exception("DAILY unexpected failure; restoring previous published result set.")
        if tx_dir is not None:
            _rollback_transaction(tx_dir, previous_files)
        if run_dir is not None:
            shutil.rmtree(run_dir, ignore_errors=True)
        if staged_checkpoint_discarded:
            logger.warning(
                "DAILY failed staging contains a scan checkpoint, but the isolated "
                "transaction will be discarded; the next DAILY run performs a "
                "clean full-market scan."
            )
        _atomic_write_json(
            OUTPUT_DIR / "PublicationStatus.json",
            {
                "status": "failed",
                "run_id": pipeline_run_id,
                "latest_run_unchanged": True,
                "scan_checkpoint_discarded": staged_checkpoint_discarded,
            },
        )
        return 2
    finally:
        if stage_dir is not None:
            shutil.rmtree(stage_dir, ignore_errors=True)


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
