"""v102 integrity overlay for the public A-share research briefing.

This module leaves production scoring untouched. It fixes the public contract:
CandidateViewRank drives mixed display, ACTIONABLE NOW reads TradeReady without
padding, model migrations suppress false day-over-day attribution, unstable
calibration is labelled diagnostic-only, and freshness exceptions are explicit.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Iterable

import web_report_v84 as _v84
import web_report_v93 as _v93
from web_report_v93 import *  # noqa: F403

WEB_REPORT_VERSION = "2026-08-24-v102-ranking-calibration-integrity-v1"
WebReportResult = _v93.WebReportResult
DEFAULT_OUTPUT_DIR = _v93.DEFAULT_OUTPUT_DIR
DEFAULT_SITE_DIR = _v93.DEFAULT_SITE_DIR
PROJECT_ROOT = _v93.PROJECT_ROOT
WEB_PUBLISH_ENV = _v93.WEB_PUBLISH_ENV
GH_PAGES_BRANCH = _v93.GH_PAGES_BRANCH
_archive_html = _v93._archive_html
_published_source_dir = _v93._published_source_dir
is_canonical_output_dir = _v93.is_canonical_output_dir
github_pages_url_from_remote = _v93.github_pages_url_from_remote
publish_site = _v93.publish_site

_BUILD_LOCK = threading.RLock()
_ACTIVE_TRADE_ROWS: list[dict[str, str]] = []
_MODEL_COMPARABLE = True
_CURRENT_MODEL_SIGNATURE = ""
_PREVIOUS_MODEL_SIGNATURE = ""

_SIGNATURE_FIELDS = (
    "ModelVersion",
    "PipelineVersion",
    "ModelWeightSignature",
    "OutputContractVersion",
    "DecisionIntegrityVersion",
    "FundamentalGateVersion",
    "DecisionPolicySignature",
    "RankingArchitectureVersion",
    "SmoothTriggerVersion",
    "CalibrationMathVersion",
    "CalibrationGovernanceVersion",
    "BacktestResonanceVersion",
)

_EXTRA_V93_FIELDS = (
    "TradeRank",
    "CandidateView",
    "ResearchAssetClass",
    "BacktestEligibleForRanking",
    "GlobalCalibrationStability",
    "GlobalCalibrationGovernanceStatus",
    "GlobalCalibrationApplied",
    "CalibrationGovernanceVersion",
    *_SIGNATURE_FIELDS,
)


def _num(value: object) -> float | None:
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _safe(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _read_raw_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [
                {str(key): str(value or "").strip() for key, value in raw.items()}
                for raw in csv.DictReader(handle)
                if str(raw.get("Ticker", "") or "").strip()
            ]
    except (OSError, UnicodeError, csv.Error):
        return []


def _first_rows(source: Path, output: Path, names: Iterable[str]) -> list[dict[str, str]]:
    roots = (source,) if source.resolve() == output.resolve() else (source, output)
    for root in roots:
        for name in names:
            rows = _read_raw_csv(root / name)
            if rows:
                return rows
    return []


def _trade_ready_rows(source: Path, output: Path) -> list[dict[str, str]]:
    rows = _first_rows(source, output, ("Top50TradeReady.csv",))
    accepted: list[dict[str, str]] = []
    for row in rows:
        state = str(row.get("ExecutionState") or row.get("RankingEligibility") or "").upper()
        quality = str(row.get("QualityLayerStatus", "") or "").upper()
        signal = str(row.get("EntrySignal", "") or "").upper()
        if (
            state in {"READY", "CAUTIOUS", "推荐", "谨慎候选"}
            and quality not in {"POLICY_FAIL", "DATA_INCOMPLETE"}
            and signal != "AVOID"
        ):
            accepted.append(row)
    return accepted[:10]


def _report_date_hint(source: Path, output: Path, rows: list[dict[str, str]]) -> str:
    for root in (source, output):
        path = root / "DailyRunSummary.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            for key in ("effective_trading_date", "expected_trading_date"):
                value = str(payload.get(key, "") or "").strip()
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                    return value
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get("DataAsOf", "") or "").strip()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            counts[value] = counts.get(value, 0) + 1
    return max(counts, key=lambda item: (counts[item], item)) if counts else ""


def _model_signature(rows: list[dict[str, str]]) -> tuple[str, dict[str, str]]:
    if not rows:
        return "", {}
    values: dict[str, str] = {}
    for field in _SIGNATURE_FIELDS:
        observed = sorted(
            {
                str(row.get(field, "") or "").strip()
                for row in rows[:250]
                if str(row.get(field, "") or "").strip()
            }
        )
        if observed:
            values[field] = " || ".join(observed)
    if not values:
        return "", {}
    canonical = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16], values


def _previous_signature(site_dir: Path, report_date: str) -> tuple[str, str]:
    reports = site_dir / "reports"
    if not reports.is_dir() or not report_date:
        return "", ""
    candidates = sorted(
        (
            path
            for path in reports.glob("????-??-??.html")
            if path.is_file() and path.stem < report_date
        ),
        key=lambda path: path.stem,
        reverse=True,
    )
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        match = re.search(
            r'<meta\s+name="model-signature-v102"\s+content="([^"]*)"', text
        )
        return path.stem, (match.group(1).strip() if match else "")
    return "", ""


def _candidate_rank(row: dict[str, str]) -> float:
    for key in (
        "CandidateViewRank",
        "TradeRank",
        "ResearchRank",
        "ResearchPoolRank",
        "OverallRank",
    ):
        value = _num(row.get(key))
        if value is not None and value > 0:
            return value
    return 1_000_000_000.0


def _v84_sort_key(row: dict[str, str]) -> tuple[float, float, str]:
    rank = _candidate_rank(row)
    alpha = _num(row.get("AlphaScore") or row.get("FinalScore"))
    return rank, -(alpha if alpha is not None else -1_000_000.0), row.get("Ticker", "")


def _decorate_rows(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    output = _ORIGINAL_V84_DECORATE(rows)
    for position, row in enumerate(output, start=1):
        candidate = _num(row.get("CandidateViewRank"))
        if candidate is not None and candidate > 0:
            row["DisplayResearchRank"] = str(int(candidate))
        elif not row.get("DisplayResearchRank"):
            row["DisplayResearchRank"] = str(position)
    return output


def _opportunities(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    if _ACTIVE_TRADE_ROWS:
        return [dict(row) for row in _ACTIVE_TRADE_ROWS]
    return [
        row
        for row in rows
        if _v93._state(row) in {"READY", "CAUTIOUS"}
        and _v93._quality(row) not in {"POLICY_FAIL", "DATA_INCOMPLETE"}
        and _v93._signal(row) != "AVOID"
    ][:10]


def _changed_html(
    rows: list[dict[str, str]],
    previous: dict[str, Any],
    prev_date: str,
    daily: dict[str, Any],
) -> str:
    if previous and not _MODEL_COMPARABLE:
        return f'''<section id="what-changed-v93" class="section card console-v93"><div class="section-head"><h2>MODEL MIGRATION / 本期不可直接同比</h2><p>模型契约变化时暂停 ticker 级排名/Alpha 归因，避免把代码变化误判成市场变化</p></div><div class="meta93"><span>比较基准 <strong>{_safe(prev_date or '上一期')}</strong></span><span>· 上期签名 <strong>{_safe(_PREVIOUS_MODEL_SIGNATURE or 'LEGACY / UNKNOWN')}</strong></span><span>· 本期签名 <strong>{_safe(_CURRENT_MODEL_SIGNATURE or 'UNKNOWN')}</strong></span></div><div class="grid93"><article class="card93"><span>可比性</span><strong>BLOCKED</strong><small>关键模型签名不一致</small></article><article class="card93"><span>资格变化</span><strong>暂停归因</strong><small>DailyRunSummary 原始统计仍保留在运行文件</small></article><article class="card93"><span>排名变化</span><strong>暂停归因</strong><small>避免模型迁移污染市场解释</small></article><article class="card93"><span>执行变化</span><strong>暂停归因</strong><small>下一期同签名后恢复逐票比较</small></article></div></section>'''
    return _ORIGINAL_CHANGED_HTML(rows, previous, prev_date, daily)


def _calibration_state(backtest: dict[str, Any]) -> tuple[bool, str]:
    stability_raw = backtest.get("calibration_stability", {})
    stability = stability_raw if isinstance(stability_raw, dict) else {}
    status = str(stability.get("status", "") or "").upper()
    h20 = _num(backtest.get("monotonicity_high_low_20d"))
    h60 = _num(backtest.get("monotonicity_high_low_60d"))
    ic20 = _num(backtest.get("rank_ic_20d"))
    ic60 = _num(backtest.get("rank_ic_60d"))
    passed = (
        status == "STABLE"
        and h20 is not None and h20 > 0
        and h60 is not None and h60 > 0
        and ic20 is not None and ic20 > 0
        and ic60 is not None and ic60 > 0
    )
    if passed:
        return True, "held-out ordering + 20D/60D Rank IC + walk-forward stability all passed"
    failures: list[str] = []
    if status != "STABLE":
        failures.append(f"walk-forward={status or 'MISSING'}")
    if h20 is None or h20 <= 0:
        failures.append("20D high-low<=0")
    if h60 is None or h60 <= 0:
        failures.append("60D high-low<=0")
    if ic20 is None or ic20 <= 0:
        failures.append("20D RankIC<=0")
    if ic60 is None or ic60 <= 0:
        failures.append("60D RankIC<=0")
    return False, ", ".join(failures)


def _calibration_html(backtest: dict[str, Any]) -> str:
    base = _ORIGINAL_CALIBRATION_HTML(backtest)
    active, reason = _calibration_state(backtest)
    status = "ON · 可参与生产" if active else "OFF · 仅诊断"
    cls = "good93" if active else "bad93"
    banner = (
        f'<div class="meta93"><span>生产校准治理 <strong class="{cls}">{_safe(status)}</strong></span>'
        f'<span>· {_safe(reason)}</span></div>'
    )
    marker = '<div class="section-head"><h2>HELD-OUT SCORE CALIBRATION / 测试集评分分桶</h2><p>生产回测 held-out test set；不重拟合、不回灌当前排名</p></div>'
    return base.replace(marker, marker + banner, 1)


def _freshness_exceptions(source: Path, output: Path, report_date: str) -> str:
    rows = _first_rows(source, output, ("AllResults.csv", "DecisionResults.csv"))
    if not rows:
        return ""
    bad: list[dict[str, str]] = []
    for row in rows:
        asof = str(row.get("DataAsOf", "") or "").strip()
        status = str(row.get("DataFreshnessStatus", "") or "").strip().upper()
        if (report_date and asof and asof != report_date) or status in {
            "STALE",
            "过期",
            "PROVIDER_LAG",
            "FUTURE",
            "MISSING",
        }:
            bad.append(row)
    if not bad:
        return ""
    examples = "".join(
        f'<div><b>{_safe(row.get("Ticker", "—"))}</b><em>{_safe(row.get("DataAsOf", "—"))} · {_safe(row.get("DataFreshnessStatus", "异常"))} · {_safe(row.get("DataFreshnessReason", ""))}</em></div>'
        for row in bad[:8]
    )
    return f'''<section id="freshness-exceptions-v102" class="section card console-v93"><div class="section-head"><h2>DATA FRESHNESS EXCEPTIONS / 时效例外</h2><p>数据新鲜度百分比之外，明确列出未对齐标的</p></div><div class="meta93"><span>异常标的 <strong>{len(bad)}</strong></span><span>· 报告日 <strong>{_safe(report_date or '—')}</strong></span></div><article class="list93">{examples}</article></section>'''


def _postprocess(path: Path, *, signature: str, freshness_html: str) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return

    if signature and 'name="model-signature-v102"' not in text:
        text = text.replace(
            "</head>",
            f'<meta name="model-signature-v102" content="{_safe(signature)}"><meta name="web-report-version" content="{WEB_REPORT_VERSION}"></head>',
            1,
        )

    text = text.replace("LIVE · 数据就绪", "PUBLISHED SNAPSHOT · 数据对齐")
    text = text.replace("● 数据已就绪", "● 已发布快照")
    text = text.replace("TOP OPPORTUNITIES / 今日优先研究", "ACTIONABLE NOW / 当前可执行")
    text = text.replace(
        "保留生产 ResearchRank；只做展示筛选，不对子集重新排名",
        "直接读取 TradeReady 生产候选；不足 10 只不补位，不绕过主题、质量与执行闸门",
    )
    text = text.replace(
        '<thead><tr><th>研究#</th><th>标的</th><th>收盘</th><th>SIGNAL</th>',
        '<thead><tr><th>候选#</th><th>标的</th><th>收盘</th><th>SIGNAL</th>',
        1,
    )
    text = text.replace("TOP OPPORTUNITIES / 今日机会", "RESEARCH RANK / 混合研究排序")
    text = text.replace(
        "研究排名不等于即时执行许可",
        "CandidateViewRank 是跨资产展示顺序；ResearchRank 仅用于股票/ETF 各自内部研究排序",
    )
    text = text.replace("SECTOR ROTATION / 行业轮动", "SECTOR HEAT / 研究池行业热度")
    text = text.replace(
        "按研究池前 400 名聚合，不新增模型权重",
        "按研究池前 400 名主题聚合；表示模型候选热度，不等于资金轮动",
    )
    text = text.replace(
        "<span>页面版本</span><strong>v87</strong>",
        "<span>页面版本</span><strong>v102</strong>",
    )

    if freshness_html and 'id="freshness-exceptions-v102"' not in text:
        market = re.search(
            r'(<section class="section" data-section="market_state">.*?</section>)',
            text,
            re.S,
        )
        if market:
            text = text[: market.end()] + freshness_html + text[market.end() :]

    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        return


_ORIGINAL_V84_SORT = _v84._sort_key
_ORIGINAL_V84_DECORATE = _v84._decorate_rows
_ORIGINAL_V93_RANK = _v93._rank
_ORIGINAL_OPPORTUNITIES = _v93._opportunities
_ORIGINAL_CHANGED_HTML = _v93._changed_html
_ORIGINAL_CALIBRATION_HTML = _v93._calibration_html


def build_web_report(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    site_dir: Path = DEFAULT_SITE_DIR,
) -> WebReportResult:
    global _ACTIVE_TRADE_ROWS, _MODEL_COMPARABLE
    global _CURRENT_MODEL_SIGNATURE, _PREVIOUS_MODEL_SIGNATURE

    output_dir = Path(output_dir)
    site_dir = Path(site_dir)
    source = _published_source_dir(output_dir)
    signature_rows = _first_rows(source, output_dir, ("AllResults.csv", "Top50Mixed.csv"))
    report_date = _report_date_hint(source, output_dir, signature_rows)
    current_signature, _ = _model_signature(signature_rows)
    _, previous_signature = _previous_signature(site_dir, report_date)
    trade_rows = _trade_ready_rows(source, output_dir)
    freshness_html = _freshness_exceptions(source, output_dir, report_date)

    with _BUILD_LOCK:
        _ACTIVE_TRADE_ROWS = trade_rows
        _CURRENT_MODEL_SIGNATURE = current_signature
        _PREVIOUS_MODEL_SIGNATURE = previous_signature
        _MODEL_COMPARABLE = bool(
            previous_signature and current_signature and previous_signature == current_signature
        )

        fields = tuple(dict.fromkeys((*_v93._FIELDS, *_EXTRA_V93_FIELDS)))
        old_fields = _v93._FIELDS
        _v93._FIELDS = fields
        _v84._sort_key = _v84_sort_key
        _v84._decorate_rows = _decorate_rows
        _v93._rank = _candidate_rank
        _v93._opportunities = _opportunities
        _v93._changed_html = _changed_html
        _v93._calibration_html = _calibration_html
        try:
            result = _v93.build_web_report(output_dir=output_dir, site_dir=site_dir)
        finally:
            _v93._FIELDS = old_fields
            _v84._sort_key = _ORIGINAL_V84_SORT
            _v84._decorate_rows = _ORIGINAL_V84_DECORATE
            _v93._rank = _ORIGINAL_V93_RANK
            _v93._opportunities = _ORIGINAL_OPPORTUNITIES
            _v93._changed_html = _ORIGINAL_CHANGED_HTML
            _v93._calibration_html = _ORIGINAL_CALIBRATION_HTML
            _ACTIVE_TRADE_ROWS = []

    for path in (result.index_path, result.archive_path):
        _postprocess(path, signature=current_signature, freshness_html=freshness_html)
    return result


def _publication_enabled() -> bool:
    raw = os.environ.get(WEB_PUBLISH_ENV)
    return raw is None or raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def build_and_publish_web_report(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    site_dir: Path = DEFAULT_SITE_DIR,
    logger: logging.Logger | None = None,
    reason: str = "run-complete",
) -> WebReportResult:
    log = logger or logging.getLogger("institution_scanner")
    built = build_web_report(output_dir=output_dir, site_dir=site_dir)
    log.info("WEB v102 integrity report generated: %s (%s).", built.archive_path, reason)
    if not _publication_enabled():
        return built
    try:
        return publish_site(site_dir, repo_root=PROJECT_ROOT, report_date=built.report_date)
    except Exception as exc:
        log.warning("WEB v102 publication skipped/failed: %s", exc)
        return WebReportResult(
            report_date=built.report_date,
            index_path=built.index_path,
            archive_path=built.archive_path,
            publish_message=str(exc),
        )
