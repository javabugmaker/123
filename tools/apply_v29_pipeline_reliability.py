from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"missing anchor: {label}")
    if text.count(old) != 1:
        raise RuntimeError(f"ambiguous anchor {label}: {text.count(old)} matches")
    return text.replace(old, new, 1)


def patch_config() -> None:
    path = ROOT / "config.py"
    text = path.read_text(encoding="utf-8")
    old = 'SCORING_VERSION: str = "2026-08-09-v24-decision-integrity"\n'
    new = '''SCORING_VERSION: str = "2026-08-09-v24-decision-integrity"
# Engineering versions are intentionally separate from the scoring model.
PIPELINE_VERSION: str = "2026-08-09-v29-reliable-daily"
GUI_VERSION: str = "2026-08-09-v29-workstation"
BACKTEST_PROVENANCE_VERSION: str = "2026-08-09-v29"

# Daily publication quality gates.  They protect the canonical result set from
# provider truncation or a stale daily-bar snapshot without changing scoring.
DAILY_MIN_UNIVERSE_TOTAL: Final[int] = 2000
DAILY_MIN_STOCK_COUNT: Final[int] = 1500
DAILY_MIN_ETF_COUNT: Final[int] = 150
DAILY_MIN_FRESH_RATIO: Final[float] = 0.90
DAILY_RELATIVE_UNIVERSE_FLOOR: Final[float] = 0.60
'''
    text = replace_once(text, old, new, "config engineering versions")
    path.write_text(text, encoding="utf-8")


def patch_analytics() -> None:
    path = ROOT / "analytics.py"
    text = path.read_text(encoding="utf-8")

    old = '    fast_exact_bridge: dict[str, Any] = field(default_factory=dict)\n'
    new = '''    fast_exact_bridge: dict[str, Any] = field(default_factory=dict)
    # v29 run-level provenance / observability.  requested_tickers is populated
    # by the CLI before ranking so manual subset backtests never mark unrelated
    # AllResults rows as if they had been evaluated.
    requested_tickers: list[str] = field(default_factory=list)
    fast_screen_ticker_count: int = 0
    exact_refinement_count: int = 0
    exact_refinement_tickers: list[str] = field(default_factory=list)
    exact_refinement_elapsed_seconds: float = 0.0
    exact_worker_count: int = 0
    total_ticker_evaluations: int = 0
    signal_sample_ticker_count: int = 0
    no_signal_ticker_count: int = 0
    ranking_eligible_ticker_count: int = 0
    cache_hit_rate: float = 0.0
'''
    text = replace_once(text, old, new, "analytics summary fields")

    old = '''    original_mode = str(summary.mode or "").strip().lower()
    if original_mode == "fast" and BACKTEST_AUTO_EXACT_REFINEMENT:
'''
    new = '''    original_mode = str(summary.mode or "").strip().lower()
    summary.fast_screen_ticker_count = int(getattr(summary, "ticker_count", 0) or 0)
    summary.exact_refinement_count = 0
    summary.exact_refinement_tickers = []
    summary.exact_refinement_elapsed_seconds = 0.0
    summary.exact_worker_count = 0
    if original_mode == "fast" and BACKTEST_AUTO_EXACT_REFINEMENT:
'''
    text = replace_once(text, old, new, "analytics refinement counters init")

    old = '            exact_rows = list(exact.by_ticker or [])\n'
    new = '''            exact_rows = list(exact.by_ticker or [])
            summary.exact_refinement_count = len(refine_tickers)
            summary.exact_refinement_tickers = list(refine_tickers)
            summary.exact_refinement_elapsed_seconds = float(getattr(exact, "elapsed_seconds", 0.0) or 0.0)
            summary.exact_worker_count = int(getattr(exact, "worker_count", 0) or 0)
            summary.elapsed_seconds = float(getattr(summary, "elapsed_seconds", 0.0) or 0.0) + summary.exact_refinement_elapsed_seconds
            summary.cache_hits = int(getattr(summary, "cache_hits", 0) or 0) + int(getattr(exact, "cache_hits", 0) or 0)
            summary.cache_hit_tickers = sorted(
                set(getattr(summary, "cache_hit_tickers", []) or [])
                | set(getattr(exact, "cache_hit_tickers", []) or [])
            )
'''
    text = replace_once(text, old, new, "analytics exact stats")

    marker = '\ndef apply_backtest_ranking(summary: BacktestSummary, top_n: int = 50) -> None:\n'
    if marker not in text:
        raise RuntimeError("missing analytics apply_backtest_ranking marker")
    helper = r'''

def _apply_backtest_provenance(
    frame: pd.DataFrame,
    summary: BacktestSummary,
    observed: pd.Series,
) -> pd.DataFrame:
    """Separate run-level HYBRID provenance from per-ticker execution state.

    A HYBRID run means the task used FAST screening plus selective EXACT
    refinement.  It does *not* mean every ticker was evaluated in HYBRID mode.
    Manual subset backtests also leave unrelated AllResults rows explicitly
    NOT_EVALUATED instead of fabricating a zero-sample result.
    """
    frame = frame.copy()
    ticker_text = frame.get("Ticker", pd.Series("", index=frame.index)).fillna("").astype(str)
    requested = {
        str(value).strip()
        for value in (getattr(summary, "requested_tickers", []) or [])
        if str(value).strip()
    }
    if not requested:
        requested = {
            str(row.get("ticker", "")).strip()
            for row in (getattr(summary, "by_ticker", []) or [])
            if str(row.get("ticker", "")).strip()
        }
    if not requested and int(getattr(summary, "ticker_count", 0) or 0) >= len(frame):
        requested = set(ticker_text)
    requested_mask = ticker_text.isin(requested)

    run_mode = str(getattr(summary, "mode", "") or "").strip().upper() or "UNKNOWN"
    run_engine = str(getattr(summary, "engine", "") or "").strip()
    screen_mode = "FAST" if run_mode == "HYBRID" else run_mode
    screen_engine = run_engine.split("+exact:", 1)[0] if run_engine else ""

    raw_mode = frame.get("BacktestMode", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip().str.upper()
    inferred_mode = pd.Series(
        np.where(requested_mask, screen_mode, "NONE"), index=frame.index, dtype=object
    )
    ticker_mode = raw_mode.where(raw_mode.ne(""), inferred_mode)
    ticker_mode = ticker_mode.where(requested_mask, "NONE")

    raw_engine = frame.get("BacktestEngine", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip()
    ticker_engine = raw_engine.where(raw_engine.ne(""), screen_engine)
    ticker_engine = ticker_engine.where(requested_mask, "")

    raw_stage = frame.get("BacktestStage", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip().str.upper()
    default_stage = pd.Series(
        np.where(
            ~requested_mask,
            "NOT_EVALUATED",
            np.where(
                ticker_mode.eq("EXACT"),
                "EXACT_REFINEMENT" if run_mode == "HYBRID" else "EXACT",
                "FAST_SCREEN",
            ),
        ),
        index=frame.index,
        dtype=object,
    )
    stage = raw_stage.where(raw_stage.ne(""), default_stage)
    stage = stage.where(requested_mask, "NOT_EVALUATED")

    numeric_observed = pd.to_numeric(observed, errors="coerce").fillna(0.0).clip(lower=0.0)
    frame["BacktestSamples"] = numeric_observed.round().astype(int)
    if "BacktestEffectiveSamples" in frame:
        frame["BacktestEffectiveSamples"] = (
            pd.to_numeric(frame["BacktestEffectiveSamples"], errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .fillna(0.0)
            .clip(lower=0.0)
        )
    frame["BacktestRunMode"] = run_mode
    frame["BacktestRunEngine"] = run_engine
    frame["BacktestRequested"] = requested_mask.astype(bool)
    frame["BacktestMode"] = ticker_mode
    frame["BacktestEngine"] = ticker_engine
    frame["BacktestStage"] = stage
    frame["BacktestStatus"] = np.select(
        [~requested_mask, numeric_observed.gt(0.0)],
        ["SKIPPED", "SAMPLES"],
        default="NO_SIGNAL_SAMPLES",
    )
    frame["BacktestEligibleForRanking"] = (
        requested_mask & numeric_observed.ge(BACKTEST_MIN_SAMPLES_FOR_RANKING)
    )

    minimum_fast = _minimum_fast_samples_for_exact_refinement()
    frame["BacktestSkipReason"] = np.select(
        [
            ~requested_mask,
            requested_mask & ticker_mode.eq("FAST") & numeric_observed.eq(0.0),
            requested_mask & ticker_mode.eq("EXACT") & numeric_observed.eq(0.0),
            requested_mask & ticker_mode.eq("FAST") & numeric_observed.gt(0.0) & numeric_observed.lt(minimum_fast) & pd.Series(run_mode == "HYBRID", index=frame.index),
            requested_mask & ticker_mode.eq("FAST") & numeric_observed.ge(minimum_fast) & pd.Series(run_mode == "HYBRID", index=frame.index),
            requested_mask & numeric_observed.lt(BACKTEST_MIN_SAMPLES_FOR_RANKING),
        ],
        [
            "不在本次回测范围",
            "FAST无历史信号样本",
            "EXACT无历史信号样本",
            "FAST样本不足，跳过EXACT",
            "未进入EXACT候选池",
            "历史样本不足，不参与排名",
        ],
        default="",
    )
    return frame
'''
    text = text.replace(marker, helper + marker, 1)

    old = '''    observed = pd.to_numeric(frame["BacktestSamples"], errors="coerce").fillna(0.0)
    frame["BacktestMode"] = (
        frame.get("BacktestMode", pd.Series("", index=frame.index))
        .fillna("").astype(str).str.strip().replace("", str(summary.mode).upper())
    )
    frame["BacktestEngine"] = (
        frame.get("BacktestEngine", pd.Series("", index=frame.index))
        .fillna("").astype(str).str.strip().replace("", str(summary.engine))
    )
    frame["BacktestCacheHit"] = frame.get(
        "BacktestCacheHit", pd.Series(False, index=frame.index)
    ).fillna(False).astype(bool)
    frame["BacktestStatus"] = np.where(observed.gt(0.0), "SAMPLES", "NO_SIGNAL_SAMPLES")
    if "BacktestStage" not in frame:
        frame["BacktestStage"] = np.where(
            frame["BacktestMode"].astype(str).str.upper().eq("EXACT"),
            "EXACT",
            "FAST_SCREEN",
        )
'''
    new = '''    observed = pd.to_numeric(frame["BacktestSamples"], errors="coerce").fillna(0.0)
    frame = _apply_backtest_provenance(frame, summary, observed)
    observed = pd.to_numeric(frame["BacktestSamples"], errors="coerce").fillna(0.0)
    frame["BacktestCacheHit"] = frame.get(
        "BacktestCacheHit", pd.Series(False, index=frame.index)
    ).fillna(False).astype(bool)
'''
    text = replace_once(text, old, new, "analytics provenance block")

    old = '''        "BacktestStatus",
        "BacktestStage",
        "GlobalCalibrationScore",
'''
    new = '''        "BacktestStatus",
        "BacktestStage",
        "BacktestRunMode",
        "BacktestRunEngine",
        "BacktestRequested",
        "BacktestEligibleForRanking",
        "BacktestSkipReason",
        "GlobalCalibrationScore",
'''
    text = replace_once(text, old, new, "analytics legacy provenance columns")

    old = '''    frame = finalize_signal_ranking(frame)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
'''
    new = '''    requested_mask = frame.get(
        "BacktestRequested", pd.Series(False, index=frame.index)
    ).fillna(False).astype(bool)
    requested_count = int(requested_mask.sum())
    sample_count = int(
        frame.loc[
            requested_mask
            & pd.to_numeric(frame["BacktestSamples"], errors="coerce").fillna(0).gt(0),
            "Ticker",
        ].astype(str).nunique()
    )
    ranking_eligible_count = int(
        frame.loc[
            requested_mask
            & frame.get(
                "BacktestEligibleForRanking", pd.Series(False, index=frame.index)
            ).fillna(False).astype(bool),
            "Ticker",
        ].astype(str).nunique()
    )
    summary.signal_sample_ticker_count = sample_count
    summary.no_signal_ticker_count = max(0, requested_count - sample_count)
    summary.ranking_eligible_ticker_count = ranking_eligible_count
    summary.total_ticker_evaluations = int(getattr(summary, "fast_screen_ticker_count", 0) or 0) + int(getattr(summary, "exact_refinement_count", 0) or 0)
    summary.cache_hit_rate = round(
        float(getattr(summary, "cache_hits", 0) or 0)
        / max(1, int(summary.total_ticker_evaluations or requested_count)),
        4,
    )

    frame = finalize_signal_ranking(frame)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
'''
    text = replace_once(text, old, new, "analytics summary observability")

    path.write_text(text, encoding="utf-8")


def patch_main() -> None:
    path = ROOT / "main.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "import argparse\nimport csv\nimport logging\nimport sys\n",
        "import argparse\nimport csv\nimport json\nimport logging\nimport os\nimport sys\n",
        "main imports",
    )
    old = '''    summary = run_historical_backtest(unique_tickers, **backtest_kwargs)
    if all_results:
'''
    new = '''    summary = run_historical_backtest(unique_tickers, **backtest_kwargs)
    summary.requested_tickers = list(unique_tickers)
    if all_results:
'''
    text = replace_once(text, old, new, "main requested tickers")
    old = '''    apply_backtest_ranking(summary, top_n=TOP_N_REPORT)
    logger.info(
'''
    new = '''    apply_backtest_ranking(summary, top_n=TOP_N_REPORT)
    # run_historical_backtest writes an initial summary before EXACT refinement.
    # Persist it again after ranking so HYBRID/provenance/performance counters are
    # the final values consumed by the daily manifest and GUI.
    summary_path = OUTPUT_DIR / "BacktestSummary.json"
    temporary_summary = summary_path.with_name(".BacktestSummary.json.tmp")
    temporary_summary.write_text(
        json.dumps(summary.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary_summary, summary_path)
    logger.info(
'''
    text = replace_once(text, old, new, "main final backtest summary")
    path.write_text(text, encoding="utf-8")


def patch_report() -> None:
    path = ROOT / "report.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "    OUTPUT_DIR,\n    TOP_N_PARQUET,\n",
        "    OUTPUT_DIR,\n    PIPELINE_VERSION,\n    TOP_N_PARQUET,\n",
        "report pipeline import",
    )
    text = replace_once(
        text,
        '                "ModelVersion": SCORING_VERSION,\n                "IndicatorCacheVersion": INDICATOR_CACHE_VERSION,\n',
        '                "ModelVersion": SCORING_VERSION,\n                "PipelineVersion": PIPELINE_VERSION,\n                "IndicatorCacheVersion": INDICATOR_CACHE_VERSION,\n',
        "report pipeline version row",
    )

    marker = '\ndef refresh_candidate_exports(\n'
    if marker not in text:
        raise RuntimeError("missing report refresh marker")
    helper = r'''

DECISION_RESULT_COLUMNS: tuple[str, ...] = (
    "Ticker", "Name", "Sector", "Industry", "ETFTheme", "IsETF", "AssetType",
    "ModelClassification", "ThemeCluster", "Close", "EntrySignal", "SignalStatus",
    "SignalDays", "EntryZone", "BreakoutBuyPrice", "StopLoss", "RankingEligibility",
    "RankingScore", "ResearchPoolRank", "OverallRank", "InstitutionalTier",
    "InstitutionalScore", "TradeReadinessReason", "RankingReason", "DecisionState",
    "BacktestRunMode", "BacktestMode", "BacktestStage", "BacktestSamples",
    "BacktestStatus", "BacktestConfidenceTier", "BacktestRequested",
    "BacktestEligibleForRanking", "BacktestSkipReason", "BacktestCacheHit",
    "DataAsOf", "DataTradingAgeDays", "RunId", "ModelVersion", "PipelineVersion",
)


def _decision_projection(frame: pd.DataFrame) -> pd.DataFrame:
    working = frame.copy()
    if "ETFTheme" not in working:
        working["ETFTheme"] = working.apply(_etf_theme_key, axis=1)
    else:
        missing_theme = working["ETFTheme"].fillna("").astype(str).str.strip().eq("")
        if missing_theme.any():
            working.loc[missing_theme, "ETFTheme"] = working.loc[missing_theme].apply(
                _etf_theme_key, axis=1
            )
    return working.reindex(columns=DECISION_RESULT_COLUMNS)


def write_decision_results(
    frame: pd.DataFrame, output_dir: Path | None = None
) -> Path:
    destination = output_dir if output_dir is not None else OUTPUT_DIR
    path = destination / "DecisionResults.csv"
    decision = _decision_projection(frame)
    _atomic_write_csv(decision, path)
    logger.info(
        "Exported lightweight decision surface: %d rows / %d columns to %s",
        len(decision), len(decision.columns), path,
    )
    return path
'''
    text = text.replace(marker, helper + marker, 1)

    old = '''    ranked["BacktestCacheVersion"] = ranked.get("BacktestCacheVersion", pd.Series(BACKTEST_CACHE_VERSION, index=ranked.index)).replace("", BACKTEST_CACHE_VERSION).fillna(BACKTEST_CACHE_VERSION)
    if "BacktestStage" in ranked:
'''
    new = '''    ranked["BacktestCacheVersion"] = ranked.get("BacktestCacheVersion", pd.Series(BACKTEST_CACHE_VERSION, index=ranked.index)).replace("", BACKTEST_CACHE_VERSION).fillna(BACKTEST_CACHE_VERSION)
    ranked["PipelineVersion"] = ranked.get("PipelineVersion", pd.Series(PIPELINE_VERSION, index=ranked.index)).replace("", PIPELINE_VERSION).fillna(PIPELINE_VERSION)
    if "BacktestStage" in ranked:
'''
    text = replace_once(text, old, new, "report ranked pipeline version")

    old = '''    csv_path = destination / f"Top{top_n_csv}.csv"
    research_pool = _diversify_ranked_candidates(ranked, top_n_csv)
'''
    new = '''    # The GUI's all-results view reads this compact projection instead of the
    # 200+ column research audit CSV.  AllResults.parquet remains the complete
    # machine-readable research artifact.
    write_decision_results(ranked, destination)

    csv_path = destination / f"Top{top_n_csv}.csv"
    research_pool = _diversify_ranked_candidates(ranked, top_n_csv)
'''
    text = replace_once(text, old, new, "report decision export")

    old = '''        for name in (
            f"Top{top_n_csv}Mixed.csv",
'''
    new = '''        write_decision_results(df, OUTPUT_DIR)
        for name in (
            f"Top{top_n_csv}Mixed.csv",
'''
    text = replace_once(text, old, new, "report empty decision export")
    path.write_text(text, encoding="utf-8")


def rewrite_daily_pipeline() -> None:
    path = ROOT / "daily_pipeline.py"
    content = r'''from __future__ import annotations

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
'''
    path.write_text(content, encoding="utf-8")


def patch_gui() -> None:
    path = ROOT / "gui.py"
    text = path.read_text(encoding="utf-8")
    text = replace_once(text, "import csv\nimport re\n", "import csv\nimport json\nimport re\n", "gui json import")
    text = replace_once(
        text,
        '    "all": "AllResults.csv",\n',
        '    "all": "DecisionResults.csv",\n',
        "gui lightweight all nav",
    )
    old = '''        "GlobalCalibrationLevel": "全局校准层级",
    }
)
'''
    new = '''        "GlobalCalibrationLevel": "全局校准层级",
        "BacktestRunMode": "本轮回测模式",
        "BacktestStage": "回测阶段",
        "BacktestEligibleForRanking": "回测参与排名",
        "BacktestSkipReason": "回测说明",
    }
)
'''
    text = replace_once(text, old, new, "gui provenance labels")

    old = '''        self.card_total = tk.StringVar(master=root, value="0")
        self.detail_title = tk.StringVar(master=root, value="选择一个标的")
'''
    new = '''        self.card_total = tk.StringVar(master=root, value="0")
        self.run_quality = tk.StringVar(master=root, value="运行质量：尚无本轮数据")
        self.detail_title = tk.StringVar(master=root, value="选择一个标的")
'''
    text = replace_once(text, old, new, "gui run quality var")

    old = '''        for key, shortcut in zip(("mixed", "stocks", "etf", "ready", "new", "all"), "123456"):
            self.root.bind(f"<Control-Key-{shortcut}>", lambda _event, nav_key=key: self._load_navigation(nav_key))
'''
    new = '''        for key, shortcut in zip(("mixed", "stocks", "etf", "ready", "new", "all"), "123456"):
            self.root.bind(f"<Control-Key-{shortcut}>", lambda _event, nav_key=key: self._load_navigation(nav_key))
        self.root.after(80, self._update_run_quality_summary)
'''
    text = replace_once(text, old, new, "gui initial quality refresh")

    old = '''    self.progress = ttk.Progressbar(footer, mode="indeterminate", length=220)
    self.progress.pack(side=tk.LEFT, padx=14, pady=9)
    ctk.CTkLabel(footer, textvariable=self.page_summary, text_color="#64748b").pack(side=tk.RIGHT, padx=(10, 6), pady=9)
'''
    new = '''    self.progress = ttk.Progressbar(footer, mode="indeterminate", length=180)
    self.progress.pack(side=tk.LEFT, padx=12, pady=9)
    ctk.CTkLabel(
        footer,
        textvariable=self.run_quality,
        text_color="#52677d",
        font=("Microsoft YaHei UI", 9),
    ).pack(side=tk.LEFT, padx=(2, 8), pady=9)
    ctk.CTkLabel(footer, textvariable=self.page_summary, text_color="#64748b").pack(side=tk.RIGHT, padx=(10, 6), pady=9)
'''
    text = replace_once(text, old, new, "gui footer run quality")

    old = '''    def _load_navigation(self, key: str) -> None:
        self._new_signal_only = key == "new"
        if key == "new":
            filename = "AllResults.csv"
        else:
            filename = NAV_FILES[key]
        if self.load_csv(filename, preserve_new_signal=(key == "new")):
            self._set_active_nav(key)
'''
    new = '''    def _load_navigation(self, key: str) -> None:
        self._new_signal_only = key == "new"
        if key == "new":
            filename = "DecisionResults.csv" if self._csv_has_results("DecisionResults.csv") else "AllResults.csv"
        else:
            filename = NAV_FILES[key]
            if filename == "DecisionResults.csv" and not self._csv_has_results(filename):
                filename = "AllResults.csv"
        if self.load_csv(filename, preserve_new_signal=(key == "new")):
            self._set_active_nav(key)
'''
    text = replace_once(text, old, new, "gui nav fallback")

    old = '''    def _load_best_available_results(self) -> bool:
        for filename in ("Top50Mixed.csv", "Top50Stocks.csv", "Top50ETF.csv", "Top50.csv", "AllResults.csv"):
'''
    new = '''    def _load_best_available_results(self) -> bool:
        for filename in ("Top50Mixed.csv", "Top50Stocks.csv", "Top50ETF.csv", "Top50.csv", "DecisionResults.csv", "AllResults.csv"):
'''
    text = replace_once(text, old, new, "gui best result decision")

    marker = '    # Dashboard cards / decision card ----------------------------------------\n'
    if marker not in text:
        raise RuntimeError("missing gui dashboard marker")
    method = r'''    def _update_run_quality_summary(self) -> None:
        path = OUTPUT_DIR / "DailyRunSummary.json"
        if not path.exists() or not hasattr(self, "run_quality"):
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        expected = str(payload.get("expected_trading_date", "") or "-")
        stages = payload.get("stage_seconds", {}) if isinstance(payload.get("stage_seconds", {}), dict) else {}
        backtest = payload.get("backtest", {}) if isinstance(payload.get("backtest", {}), dict) else {}
        scan_seconds = float(stages.get("scan", 0.0) or 0.0)
        backtest_seconds = float(stages.get("backtest", 0.0) or 0.0)
        exact = int(backtest.get("exact_refinement_tickers", 0) or 0)
        cache = float(backtest.get("cache_hit_rate", 0.0) or 0.0)
        self.run_quality.set(
            f"最新 {expected} · 扫描 {scan_seconds:.0f}s · 回测 {backtest_seconds:.0f}s · EXACT {exact} · Cache {cache:.0%}"
        )

'''
    text = text.replace(marker, method + marker, 1)

    old = '''        mode = str(data.get("BacktestMode", "") or "").strip().upper()
        samples_value = self._numeric_value(data.get("BacktestSamples", ""))
        samples = int(samples_value) if samples_value is not None else 0
        confidence = str(data.get("BacktestConfidenceTier", "") or "").strip() or "未评估"
        backtest_parts = [value for value in (mode, f"{samples}样本", confidence) if value]
        self.detail_backtest.set(" · ".join(backtest_parts) or "-")
        reason = data.get("TradeReadinessReason", "") or data.get("RankingReason", "") or "暂无额外执行说明。"
        if confidence == "样本不足":
            reason = f"{reason}\\n\\n历史样本不足，回测暂不作为主要排序依据。"
'''
    new = '''        mode = str(data.get("BacktestMode", "") or "").strip().upper()
        samples_value = self._numeric_value(data.get("BacktestSamples", ""))
        samples = int(samples_value) if samples_value is not None else 0
        confidence = str(data.get("BacktestConfidenceTier", "") or "").strip() or "未评估"
        ranking_enabled = str(data.get("BacktestEligibleForRanking", "")).strip().lower() in {"true", "1", "yes", "y", "是"}
        if mode in {"", "NONE"}:
            backtest_parts = ["未评估"]
        else:
            backtest_parts = [mode, f"{samples}样本", confidence]
            if not ranking_enabled:
                backtest_parts.append("不参与排名")
        self.detail_backtest.set(" · ".join(value for value in backtest_parts if value) or "-")
        reason = data.get("TradeReadinessReason", "") or data.get("RankingReason", "") or "暂无额外执行说明。"
        skip_reason = str(data.get("BacktestSkipReason", "") or "").strip()
        if skip_reason:
            reason = f"{reason}\\n\\n回测：{skip_reason}。"
        elif confidence == "样本不足":
            reason = f"{reason}\\n\\n历史样本不足，回测暂不作为主要排序依据。"
'''
    text = replace_once(text, old, new, "gui provenance detail")

    old = '''            if code == 0:
                self.load_csv("Top50Mixed.csv")
                self.status.set("今日全流程完成 · 综合/股票/ETF Top50 已更新")
'''
    new = '''            if code == 0:
                self.load_csv("Top50Mixed.csv")
                self._update_run_quality_summary()
                self.status.set("今日全流程完成 · 数据闸门通过 · Top50 已发布")
'''
    text = replace_once(text, old, new, "gui daily finish quality")

    old = '''        if "DAILY stage 1/3" in text:
            self.status.set("今日全流程 1/3 · 获取最新行情并扫描")
        elif "DAILY stage 2/3" in text:
            self.status.set("今日全流程 2/3 · 全量回测与候选精炼")
        elif "DAILY stage 3/3" in text:
            self.status.set("今日全流程 3/3 · 生成最终 Top50")
'''
    new = '''        if "DAILY stage 1/4" in text:
            self.status.set("今日全流程 1/4 · 获取最新行情并扫描")
        elif "DAILY stage 2/4" in text:
            self.status.set("今日全流程 2/4 · 数据完整性与新鲜度校验")
        elif "DAILY stage 3/4" in text:
            self.status.set("今日全流程 3/4 · FAST回测与EXACT精炼")
        elif "DAILY stage 4/4" in text:
            self.status.set("今日全流程 4/4 · 同RunId校验与发布")
'''
    text = replace_once(text, old, new, "gui daily stages v29")
    path.write_text(text, encoding="utf-8")


def patch_old_daily_tests() -> None:
    path = ROOT / "test_daily_pipeline_v27.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace("daily_pipeline.run_daily_pipeline(workers=4)", "daily_pipeline.run_daily_pipeline(workers=4, quality_gates=False)")
    text = text.replace("daily_pipeline.run_daily_pipeline(workers=2)", "daily_pipeline.run_daily_pipeline(workers=2, quality_gates=False)")
    text = text.replace("daily_pipeline.run_daily_pipeline(workers=1)", "daily_pipeline.run_daily_pipeline(workers=1, quality_gates=False)")
    path.write_text(text, encoding="utf-8")


def write_v29_tests() -> None:
    path = ROOT / "test_v29_pipeline_reliability.py"
    content = r'''from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import analytics
import config
import daily_pipeline
import gui
import report


class V29PipelineReliabilityTests(unittest.TestCase):
    def test_scoring_version_is_unchanged_but_pipeline_version_is_v29(self):
        self.assertEqual(config.SCORING_VERSION, "2026-08-09-v24-decision-integrity")
        self.assertIn("v29", config.PIPELINE_VERSION)

    def test_hybrid_run_does_not_pollute_per_ticker_mode(self):
        frame = pd.DataFrame(
            {
                "Ticker": ["A", "B", "C"],
                "BacktestMode": ["FAST", "", ""],
                "BacktestEngine": ["process", "", ""],
                "BacktestStage": ["FAST_SCREEN", "", ""],
                "BacktestEffectiveSamples": [1.0, np.nan, np.nan],
            }
        )
        summary = analytics.BacktestSummary(
            ticker_count=2, mode="hybrid", engine="process+exact:sequential"
        )
        summary.requested_tickers = ["A", "B"]
        result = analytics._apply_backtest_provenance(
            frame, summary, pd.Series([1.0, 0.0, 0.0])
        )
        self.assertTrue((result["BacktestRunMode"] == "HYBRID").all())
        self.assertEqual(result.loc[0, "BacktestMode"], "FAST")
        self.assertEqual(result.loc[1, "BacktestMode"], "FAST")
        self.assertEqual(result.loc[1, "BacktestStatus"], "NO_SIGNAL_SAMPLES")
        self.assertEqual(result.loc[2, "BacktestMode"], "NONE")
        self.assertEqual(result.loc[2, "BacktestStage"], "NOT_EVALUATED")
        self.assertEqual(result.loc[2, "BacktestStatus"], "SKIPPED")
        self.assertEqual(result.loc[1, "BacktestSamples"], 0)

    def test_decision_projection_is_lightweight_and_keeps_decision_fields(self):
        frame = pd.DataFrame(
            [
                {
                    "Ticker": "159915.SZ", "Name": "ETF", "IsETF": True,
                    "AssetType": "etf", "Industry": "", "Sector": "ETF",
                    "ModelClassification": "消费", "RankingScore": 42,
                    "EntrySignal": "WAIT_PULLBACK", "BacktestMode": "FAST",
                    "BacktestEligibleForRanking": False, "RunId": "run-1",
                }
            ]
        )
        projected = report._decision_projection(frame)
        self.assertLessEqual(len(projected.columns), 45)
        self.assertIn("BacktestSkipReason", projected.columns)
        self.assertIn("RunId", projected.columns)
        self.assertEqual(projected.loc[0, "ETFTheme"], "消费")

    def test_transaction_rollback_restores_previous_canonical_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            old = output / "Top50Mixed.csv"
            old.write_text("old", encoding="utf-8")
            with patch.object(daily_pipeline, "OUTPUT_DIR", output):
                tx, existing = daily_pipeline._begin_transaction("test-run")
                old.write_text("new", encoding="utf-8")
                (output / "Top50Stocks.csv").write_text("new-stock", encoding="utf-8")
                daily_pipeline._rollback_transaction(tx, existing)
            self.assertEqual(old.read_text(encoding="utf-8"), "old")
            self.assertFalse((output / "Top50Stocks.csv").exists())

    def test_quality_gate_detects_collapsed_universe_and_stale_data(self):
        profile = {
            "rows": 100,
            "stocks": 80,
            "etfs": 20,
            "fresh_ratio": 0.50,
        }
        errors = daily_pipeline._quality_gate_errors(
            profile, {"universe": {"rows": 6000, "stocks": 5000, "etfs": 1000}},
            quality_gates=True,
        )
        joined = " ".join(errors)
        self.assertIn("低于安全下限", joined)
        self.assertIn("覆盖率", joined)
        self.assertIn("异常降至", joined)

    def test_csv_profile_tracks_run_id_asset_mix_and_freshness(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "AllResults.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=["Ticker", "AssetType", "IsETF", "DataAsOf", "RunId"],
                )
                writer.writeheader()
                writer.writerow({"Ticker": "000001.SZ", "AssetType": "stock", "DataAsOf": "2026-08-07", "RunId": "r1"})
                writer.writerow({"Ticker": "159915.SZ", "AssetType": "etf", "IsETF": True, "DataAsOf": "2026-08-07", "RunId": "r1"})
            profile = daily_pipeline._csv_profile(path, "2026-08-07")
        self.assertEqual(profile["rows"], 2)
        self.assertEqual(profile["stocks"], 1)
        self.assertEqual(profile["etfs"], 1)
        self.assertEqual(profile["fresh_ratio"], 1.0)
        self.assertEqual(profile["run_ids"], ["r1"])

    def test_gui_all_results_prefers_lightweight_decision_surface(self):
        self.assertEqual(gui.NAV_FILES["all"], "DecisionResults.csv")
        self.assertIn("BacktestEligibleForRanking", gui.COLUMN_NAMES)


if __name__ == "__main__":
    unittest.main()
'''
    path.write_text(content, encoding="utf-8")


def main() -> None:
    patch_config()
    patch_analytics()
    patch_main()
    patch_report()
    rewrite_daily_pipeline()
    patch_gui()
    patch_old_daily_tests()
    write_v29_tests()
    print("v29 reliability patch applied")


if __name__ == "__main__":
    main()
