'''Canonical research-terminal presentation layer.

The legacy web_report_vXX modules remain compatibility kernels. This module is
the canonical forward path for new UI features: reliability health, WHY NOT NOW,
candidate evidence and recent signal trajectory.
'''
from __future__ import annotations

import csv
import html
import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import web_report_v102_1 as _base
from web_report_v102_1 import *  # noqa: F403

from . import pages_publisher as _pages_publisher
from .contracts import CHALLENGER_CONTRACT, PRODUCTION_CONTRACT

WEB_REPORT_VERSION = "2026-08-24-v105-reliability-research-terminal-v1"
WebReportResult = _base.WebReportResult
DEFAULT_OUTPUT_DIR = _base.DEFAULT_OUTPUT_DIR
DEFAULT_SITE_DIR = _base.DEFAULT_SITE_DIR
PROJECT_ROOT = _base.PROJECT_ROOT
WEB_PUBLISH_ENV = _base.WEB_PUBLISH_ENV
GH_PAGES_BRANCH = _base.GH_PAGES_BRANCH
_archive_html = _base._archive_html
_published_source_dir = _base._published_source_dir
is_canonical_output_dir = _base.is_canonical_output_dir
github_pages_url_from_remote = _base.github_pages_url_from_remote


def publish_site(
    site_dir: Path,
    *,
    repo_root: Path = PROJECT_ROOT,
    branch: str = GH_PAGES_BRANCH,
    report_date: str = "",
) -> WebReportResult:
    """Publish through the canonical HTTPS-first transport."""
    result = _pages_publisher.publish_site_files(
        Path(site_dir),
        repo_root=Path(repo_root),
        branch=branch,
        report_date=report_date,
        archive_renderer=_archive_html,
    )
    return WebReportResult(
        report_date=result.report_date,
        index_path=Path(site_dir) / "index.html",
        archive_path=Path(site_dir) / "reports" / f"{result.report_date}.html",
        page_url=result.page_url,
        published=True,
        publish_message=result.message,
    )


def _safe(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _num(value: object) -> float | None:
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [
                {str(key): str(value or "").strip() for key, value in row.items()}
                for row in csv.DictReader(handle)
                if str(row.get("Ticker", "") or "").strip()
            ]
    except (OSError, UnicodeError, csv.Error):
        return []


def _first_rows(
    source: Path,
    output: Path,
    names: Iterable[str],
) -> list[dict[str, str]]:
    roots = (source,) if source.resolve() == output.resolve() else (source, output)
    for root in roots:
        for name in names:
            rows = _read_csv(root / name)
            if rows:
                return rows
    return []


def _read_json(source: Path, output: Path, name: str) -> dict[str, Any]:
    roots = (source,) if source.resolve() == output.resolve() else (source, output)
    for root in roots:
        path = root / name
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _rank(row: dict[str, str]) -> float:
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


def _status_class(value: str) -> str:
    normalized = value.strip().upper()
    if normalized in {"READY", "PASS", "ACTIVE", "STABLE"}:
        return "good105"
    if normalized in {"CAUTIOUS", "WATCH", "DIAGNOSTIC_ONLY", "INSUFFICIENT"}:
        return "mid105"
    if normalized in {
        "BLOCKED",
        "FAIL",
        "POLICY_FAIL",
        "DATA_INCOMPLETE",
        "STALE",
        "过期",
    }:
        return "bad105"
    return "muted105"


def _reason_tokens(row: dict[str, str]) -> list[str]:
    reasons: list[str] = []
    quality = str(row.get("QualityLayerStatus", "") or "").upper()
    state = str(row.get("ExecutionState", "") or "").upper()
    signal = str(row.get("EntrySignal", "") or "").upper()
    freshness = str(row.get("DataFreshnessStatus", "") or "").upper()

    if quality == "POLICY_FAIL":
        reasons.append("质量策略未通过")
    elif quality == "DATA_INCOMPLETE":
        reasons.append("基本面数据不完整")

    if freshness in {"STALE", "过期", "PROVIDER_LAG", "MISSING"}:
        reasons.append("行情时效不足")

    if signal == "WAIT_PULLBACK":
        reasons.append("等待回踩")
    elif signal == "HOLD_WAIT":
        reasons.append("触发尚未确认")
    elif signal == "AVOID":
        reasons.append("当前信号回避")

    if state == "OBSERVE":
        reasons.append("执行状态=观察")
    elif state == "BLOCKED":
        reasons.append("执行闸门阻断")
    elif state == "CAUTIOUS":
        reasons.append("仅谨慎候选")

    for key in (
        "TradeReadinessReason",
        "DecisionReason",
        "ExecutionBlockReason",
        "QualityLayerReason",
    ):
        text = str(row.get(key, "") or "").strip()
        if not text:
            continue
        for part in re.split(r"[；;|]", text):
            cleaned = part.strip()
            if cleaned and cleaned not in reasons:
                reasons.append(cleaned)
            if len(reasons) >= 4:
                break
        if len(reasons) >= 4:
            break

    return reasons[:4] or ["未进入 TradeReady 精选视图"]


def _model_health_html(
    summary: dict[str, Any],
    verification: dict[str, Any],
) -> str:
    hierarchy_raw = summary.get("hierarchical_evidence", {})
    hierarchy = hierarchy_raw if isinstance(hierarchy_raw, dict) else {}
    verification_status = str(verification.get("status", "PENDING") or "PENDING")
    challenger_rows = summary.get("challenger_rows_scored", "—")
    hierarchy_rows = hierarchy.get("diagnostic_rows", "—")

    return f'''<section id="model-health-v105" class="section card console-v93">
<div class="section-head"><h2>MODEL HEALTH / 模型健康</h2><p>生产 Champion 锁定；Challenger 与分层历史证据仅影子运行</p></div>
<div class="health-grid105">
<article><span>PRODUCTION CHAMPION</span><strong>{_safe(PRODUCTION_CONTRACT.weights.signature())}</strong><small>Setup / Trigger / Execution · 生产锁定</small></article>
<article><span>SHADOW CHALLENGER</span><strong>{_safe(CHALLENGER_CONTRACT.weights.signature())}</strong><small>仅影子排名 · 不改生产分</small></article>
<article><span>CHALLENGER COVERAGE</span><strong>{_safe(challenger_rows)}</strong><small>已生成影子诊断的标的数</small></article>
<article><span>HIERARCHICAL EVIDENCE</span><strong>{_safe(hierarchy_rows)}</strong><small>DIAGNOSTIC_ONLY · 永不直接回灌</small></article>
<article><span>OUTPUT CONTRACT</span><strong class="{_status_class(verification_status)}">{_safe(verification_status)}</strong><small>DAILY 发布前 reliability verification</small></article>
</div></section>'''


def _evidence_html(trade_rows: list[dict[str, str]]) -> str:
    if not trade_rows:
        return ""
    cards: list[str] = []
    for row in trade_rows[:10]:
        ticker = row.get("Ticker", "—")
        name = row.get("Name", "")
        alpha = row.get("AlphaScore") or row.get("FinalScore") or "—"
        local = (
            "ACTIVE"
            if str(row.get("BacktestEligibleForRanking", "")).lower()
            in {"true", "1", "yes"}
            else "INSUFFICIENT"
        )
        peer = str(
            row.get("GlobalCalibrationGovernanceStatus", "DIAGNOSTIC_ONLY")
            or "DIAGNOSTIC_ONLY"
        ).upper()
        hierarchy = str(
            row.get("HierarchicalEvidenceStatus", "INSUFFICIENT") or "INSUFFICIENT"
        ).upper()
        h_score = _num(row.get("HierarchicalEvidenceScore"))
        h_n = _num(row.get("HierarchicalEvidenceEffectiveN"))
        hierarchy_detail = hierarchy
        if h_score is not None and h_n is not None:
            hierarchy_detail = f"{hierarchy} · {h_score:.1f} / N≈{h_n:.1f}"
        quality = str(row.get("QualityLayerStatus", "N/A") or "N/A").upper()
        execution = str(row.get("ExecutionState", "—") or "—").upper()
        cards.append(
            f'''<article class="evidence-card105">
<div class="evidence-title105"><strong>{_safe(ticker)}</strong><span>{_safe(name)}</span><b>ALPHA {_safe(alpha)}</b></div>
<div class="badge-row105"><span>LOCAL BT <b class="{_status_class(local)}">{_safe(local)}</b></span>
<span>PEER BT <b class="{_status_class(peer)}">{_safe(peer)}</b></span>
<span>HIER BT <b class="{_status_class(hierarchy)}">{_safe(hierarchy_detail)}</b></span>
<span>QUALITY <b class="{_status_class(quality)}">{_safe(quality)}</b></span>
<span>EXEC <b class="{_status_class(execution)}">{_safe(execution)}</b></span></div>
</article>'''
        )
    return f'''<section id="candidate-evidence-v105" class="section card">
<div class="section-head"><h2>EVIDENCE / 候选证据</h2><p>把模型分、历史证据、质量与执行许可分开；历史证据不足不包装成概率</p></div>
<div class="evidence-grid105">{''.join(cards)}</div></section>'''


def _why_not_html(
    mixed_rows: list[dict[str, str]],
    trade_rows: list[dict[str, str]],
) -> str:
    trade_tickers = {row.get("Ticker", "") for row in trade_rows}
    candidates = [
        row for row in mixed_rows if row.get("Ticker", "") not in trade_tickers
    ]
    candidates.sort(key=_rank)
    candidates = candidates[:6]
    if not candidates:
        return ""

    body = []
    for row in candidates:
        reasons = " · ".join(_reason_tokens(row))
        row_rank = _rank(row)
        rank_text = str(int(row_rank)) if row_rank < 1_000_000_000 else "—"
        body.append(
            "<tr>"
            f"<td>{rank_text}</td>"
            f"<td class=\"security\"><strong>{_safe(row.get('Ticker', '—'))}</strong>"
            f"<span>{_safe(row.get('Name', ''))}</span></td>"
            f"<td>{_safe(row.get('AlphaScore') or row.get('FinalScore') or '—')}</td>"
            f"<td>{_safe(row.get('ExecutionState') or row.get('RankingEligibility') or '—')}</td>"
            f"<td class=\"why105\">{_safe(reasons)}</td>"
            "</tr>"
        )

    return f'''<section id="why-not-now-v105" class="section card">
<div class="section-head"><h2>WHY NOT NOW / 为什么暂不执行</h2><p>展示研究排名靠前但没有进入 TradeReady 的直接阻断原因</p></div>
<div class="table-wrap"><table><thead><tr><th>Mixed#</th><th>标的</th><th>ALPHA</th><th>执行状态</th><th>主要原因</th></tr></thead>
<tbody>{''.join(body)}</tbody></table></div></section>'''


def _history_by_ticker(
    history_rows: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in history_rows:
        ticker = row.get("Ticker", "")
        date = row.get("TradeDate", "")
        if not ticker or not date:
            continue
        grouped.setdefault(ticker, []).append(row)
    for ticker, rows in grouped.items():
        rows.sort(key=lambda item: item.get("TradeDate", ""))
        deduped: dict[str, dict[str, str]] = {}
        for row in rows:
            deduped[row.get("TradeDate", "")] = row
        grouped[ticker] = list(deduped.values())[-5:]
    return grouped


def _trajectory_html(
    trade_rows: list[dict[str, str]],
    history_rows: list[dict[str, str]],
) -> str:
    if not trade_rows or not history_rows:
        return ""
    grouped = _history_by_ticker(history_rows)
    rows_html: list[str] = []
    for current in trade_rows[:10]:
        ticker = current.get("Ticker", "")
        trail = grouped.get(ticker, [])
        if not trail:
            continue
        cells = []
        for item in trail:
            score = item.get("OpportunityScore") or item.get("Score") or "—"
            status = item.get("SignalStatus") or item.get("Stage") or "—"
            date = item.get("TradeDate", "—")
            cells.append(
                f'<span><b>{_safe(date[5:] if len(date) >= 10 else date)}</b>'
                f'<em>{_safe(score)} · {_safe(status)}</em></span>'
            )
        row_rank = _rank(current)
        rank_text = str(int(row_rank)) if row_rank < 1_000_000_000 else "—"
        rows_html.append(
            f'''<article class="trail-card105"><div><strong>{_safe(ticker)}</strong>
<small>{_safe(current.get("Name", ""))} · 当前 Mixed#{rank_text}</small></div>
<div class="trail105">{''.join(cells)}</div></article>'''
        )
    if not rows_html:
        return ""
    return f'''<section id="trajectory-v105" class="section card">
<div class="section-head"><h2>5D TRAJECTORY / 最近五个交易日轨迹</h2><p>来自 SignalHistory；展示历史 Score 与信号生命周期，不把跨版本变化伪装成同模型 Alpha 变化</p></div>
<div class="trail-grid105">{''.join(rows_html)}</div></section>'''


_STYLE = r'''
<style id="research-terminal-v105-style">
.health-grid105{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:1px;background:var(--line)}
.health-grid105 article{background:#fff;padding:11px 12px;min-height:72px}
.health-grid105 span,.health-grid105 small{display:block;color:var(--muted);font-size:9px}
.health-grid105 strong{display:block;margin:4px 0;font:700 13px ui-monospace,Consolas,monospace}
.evidence-grid105{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--line)}
.evidence-card105{background:#fff;padding:10px 12px}
.evidence-title105{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.evidence-title105 strong{font:700 12px ui-monospace,Consolas,monospace}
.evidence-title105 span{color:var(--muted);font-size:10px}
.evidence-title105 b{margin-left:auto;font:700 10px ui-monospace,Consolas,monospace}
.badge-row105{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.badge-row105 span{border:1px solid var(--line);padding:4px 6px;font-size:8px;background:#fafafa}
.good105{color:var(--red-dark)}.mid105{color:var(--amber)}.bad105{color:var(--green)}.muted105{color:var(--muted)}
.why105{max-width:600px;white-space:normal;text-align:left;line-height:1.45}
.trail-grid105{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--line)}
.trail-card105{background:#fff;padding:10px 12px}.trail-card105>div:first-child{display:flex;gap:8px;align-items:baseline}
.trail-card105 strong{font:700 11px ui-monospace,Consolas,monospace}.trail-card105 small{color:var(--muted);font-size:9px}
.trail105{display:grid;grid-template-columns:repeat(5,1fr);gap:4px;margin-top:8px}
.trail105 span{border-top:2px solid var(--line);padding-top:4px;min-width:0}
.trail105 b,.trail105 em{display:block;font-style:normal;font-size:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.trail105 em{color:var(--muted);margin-top:2px}
@media(max-width:1050px){.health-grid105{grid-template-columns:repeat(2,1fr)}}
@media(max-width:760px){.evidence-grid105,.trail-grid105{grid-template-columns:1fr}.trail105{grid-template-columns:repeat(5,minmax(54px,1fr));overflow:auto}.health-grid105{grid-template-columns:1fr}}
</style>
'''


def _postprocess(
    path: Path,
    *,
    model_health: str,
    evidence: str,
    why_not: str,
    trajectory: str,
) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return

    if 'id="research-terminal-v105-style"' not in text:
        text = text.replace("</head>", _STYLE + "</head>", 1)

    text = re.sub(
        r'<meta name="web-report-version" content="[^"]*">',
        f'<meta name="web-report-version" content="{WEB_REPORT_VERSION}">',
        text,
        count=1,
    )
    text = text.replace(
        "<span>页面版本</span><strong>v102.1</strong>",
        "<span>页面版本</span><strong>v105</strong>",
    )

    if model_health and 'id="model-health-v105"' not in text:
        market = re.search(
            r'(<section class="section" data-section="market_state">.*?</section>)',
            text,
            re.S,
        )
        freshness = re.search(
            r'(<section id="freshness-exceptions-v102".*?</section>)',
            text,
            re.S,
        )
        insert_at = freshness.end() if freshness else (market.end() if market else -1)
        if insert_at >= 0:
            text = text[:insert_at] + model_health + text[insert_at:]

    additions = evidence + why_not + trajectory
    if additions and 'id="candidate-evidence-v105"' not in text:
        actionable = re.search(
            r'(<section id="top-opportunities-v93".*?</section>)',
            text,
            re.S,
        )
        if actionable:
            text = text[: actionable.end()] + additions + text[actionable.end() :]

    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        return


def build_web_report(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    site_dir: Path = DEFAULT_SITE_DIR,
) -> WebReportResult:
    output_dir = Path(output_dir)
    site_dir = Path(site_dir)
    source = _published_source_dir(output_dir)

    trade_rows = _first_rows(source, output_dir, ("Top50TradeReady.csv",))
    mixed_rows = _first_rows(
        source,
        output_dir,
        ("Top50Mixed.csv", "Top50.csv"),
    )
    history_rows = _first_rows(source, output_dir, ("SignalHistory.csv",))
    reliability = _read_json(source, output_dir, "ReliabilitySummary.json")
    verification = _read_json(source, output_dir, "ReliabilityVerification.json")

    result = _base.build_web_report(
        output_dir=output_dir,
        site_dir=site_dir,
    )

    model_health = _model_health_html(reliability, verification)
    evidence = _evidence_html(trade_rows)
    why_not = _why_not_html(mixed_rows, trade_rows)
    trajectory = _trajectory_html(trade_rows, history_rows)

    for path in (result.index_path, result.archive_path):
        _postprocess(
            path,
            model_health=model_health,
            evidence=evidence,
            why_not=why_not,
            trajectory=trajectory,
        )
    return result
