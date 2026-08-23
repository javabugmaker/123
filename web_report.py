"""Canonical public A-share decision briefing and GitHub Pages publisher.

This is the single public entry point for PAGE generation.  It deliberately
keeps presentation separate from scoring/backtest math: the validated v85 base
renderer still owns the public field allowlist, historical chart cut-off and
GitHub Pages transaction, while this module owns the current information
architecture and directly exposes production backtest calibration plus the
ranking-neutral five-factor diagnostics.

The page is decision-first rather than metric-first: the first screen answers
whether the published run is healthy, which candidates are executable, why they
rank there, and what the main blockers are.  It remains a static, self-contained
report with no external JavaScript/CDN dependencies.
"""

from __future__ import annotations

import csv
import html
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import web_report_v85 as _base

WEB_REPORT_VERSION = "2026-08-23-v101-canonical-decision-briefing-v1"
WebReportResult = _base.WebReportResult
PROJECT_ROOT = _base.PROJECT_ROOT
DEFAULT_OUTPUT_DIR = _base.DEFAULT_OUTPUT_DIR
DEFAULT_SITE_DIR = _base.DEFAULT_SITE_DIR
WEB_PUBLISH_ENV = _base.WEB_PUBLISH_ENV
GH_PAGES_BRANCH = _base.GH_PAGES_BRANCH

# Stable public compatibility exports.  Callers should import this module rather
# than a numbered implementation layer.
_archive_html = _base._archive_html
_published_source_dir = _base._published_source_dir
is_canonical_output_dir = _base.is_canonical_output_dir
github_pages_url_from_remote = _base.github_pages_url_from_remote
publish_site = _base.publish_site


def _safe(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except (OSError, UnicodeError, csv.Error):
        return []


def _candidate_rows(source_dir: Path, output_dir: Path) -> list[dict[str, str]]:
    roots = (
        (source_dir,)
        if source_dir.resolve() == output_dir.resolve()
        else (source_dir, output_dir)
    )
    for root in roots:
        for name in (
            "AllResults.csv",
            "DecisionResults.csv",
            "Top50Mixed.csv",
            "Top50.csv",
        ):
            rows = _read_csv_rows(root / name)
            if rows:
                return rows
    return []


def _ready_rows(source_dir: Path, output_dir: Path, rows: list[dict[str, str]]) -> list[dict[str, str]]:
    roots = (
        (source_dir,)
        if source_dir.resolve() == output_dir.resolve()
        else (source_dir, output_dir)
    )
    for root in roots:
        ready = _read_csv_rows(root / "Top50TradeReady.csv")
        if ready:
            return ready
    return [
        row
        for row in rows
        if _base._state(row) in {"READY", "CAUTIOUS"}
    ]


def _fmt(value: object, digits: int = 1) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:.{digits}f}"


def _backtest_delta(row: dict[str, str]) -> float | None:
    composite = _number(row.get("CompositeScore"))
    raw = _number(row.get("FinalScore"))
    if raw is None:
        raw = _number(row.get("Score"))
    if composite is None or raw is None:
        return None
    return composite - raw


def _state_label(row: dict[str, str]) -> tuple[str, str]:
    state = _base._state(row)
    if state == "READY":
        return "可执行", "ready"
    if state == "CAUTIOUS":
        return "谨慎候选", "cautious"
    if state == "BLOCKED":
        return "阻断", "blocked"
    return "观察", "observe"


def _decision_summary(
    rows: list[dict[str, str]],
    ready_rows: list[dict[str, str]],
    daily: dict[str, Any],
    backtest: dict[str, Any],
) -> str:
    ready_count = sum(_base._state(row) == "READY" for row in rows)
    cautious_count = sum(_base._state(row) == "CAUTIOUS" for row in rows)
    new_count = sum(str(row.get("SignalStatus", "")).strip().upper() == "NEW" for row in rows)
    blocked_count = sum(_base._state(row) == "BLOCKED" for row in rows)

    freshness_raw = daily.get("freshness", {})
    freshness = freshness_raw if isinstance(freshness_raw, dict) else {}
    fresh_ratio = _number(freshness.get("all_results_ratio"))
    freshness_text = "待确认" if fresh_ratio is None else f"{fresh_ratio:.0%}"

    ranking_status = str(backtest.get("ranking_calibration_status", "") or "未生成")
    bt_mode = str(backtest.get("mode", "") or "—").upper()
    bt_samples = backtest.get("ranking_calibration_samples", backtest.get("samples", "—"))

    if ready_count > 0:
        headline = f"当前有 {ready_count} 个 READY 候选"
        tone = "positive"
        note = "先看执行条件与止损，不把研究排名当成无条件买入许可。"
    elif cautious_count > 0:
        headline = f"暂无 READY，{cautious_count} 个谨慎候选"
        tone = "warn"
        note = "研究价值存在，但执行条件尚未完全满足。"
    else:
        headline = "当前没有可执行候选"
        tone = "neutral"
        note = "保持观察，等待价格、质量或触发条件改善。"

    cards = (
        ("EXECUTION", str(ready_count), "READY", "positive" if ready_count else "neutral"),
        ("CAUTIOUS", str(cautious_count), "谨慎候选", "warn" if cautious_count else "neutral"),
        ("NEW SIGNAL", str(new_count), "本轮新信号", "accent" if new_count else "neutral"),
        ("BLOCKED", str(blocked_count), "风险 / 质量阻断", "risk" if blocked_count else "neutral"),
        ("DATA", freshness_text, "结果数据覆盖", "positive" if fresh_ratio is not None and fresh_ratio >= 0.98 else "warn"),
        ("BACKTEST", bt_mode, f"样本 {bt_samples}", "neutral"),
    )
    card_html = "".join(
        f'<article class="decision-kpi {tone}"><span>{_safe(label)}</span>'
        f'<strong>{_safe(value)}</strong><small>{_safe(sub)}</small></article>'
        for label, value, sub, tone in cards
    )
    return (
        f'<section id="today-decision" class="decision-hero {tone}">'
        '<div class="decision-copy"><span class="eyebrow">TODAY / 今日行动摘要</span>'
        f'<h1>{_safe(headline)}</h1><p>{_safe(note)}</p>'
        f'<div class="decision-meta"><span>回测校准：{_safe(ranking_status)}</span>'
        f'<span>研究池：{len(rows):,}</span><span>行动池：{len(ready_rows):,}</span></div></div>'
        f'<div class="decision-kpis">{card_html}</div></section>'
    )


def _action_cards(rows: list[dict[str, str]]) -> str:
    if not rows:
        return '<div class="empty-state">当前没有 READY / CAUTIOUS 候选，等待下一次有效触发。</div>'
    cards: list[str] = []
    for row in rows[:6]:
        ticker = str(row.get("Ticker", "") or "")
        name = str(row.get("Name", "") or "—")
        state, state_class = _state_label(row)
        signal = _base._v84._signal_label(row.get("EntrySignal", ""))
        alpha = _fmt(row.get("AlphaScore", row.get("DisplayAlpha", "")), 1)
        close = _fmt(row.get("Close"), 3 if str(row.get("AssetType", "")).upper() == "ETF" else 2)
        buy = str(row.get("ReferenceBuyPrice", "") or row.get("EntryZone", "") or "—")
        stop = _fmt(row.get("StopLoss"), 3 if str(row.get("AssetType", "")).upper() == "ETF" else 2)
        target = _fmt(row.get("ProjectedTarget"), 3 if str(row.get("AssetType", "")).upper() == "ETF" else 2)
        rr = _fmt(row.get("RiskRewardRatio"), 2)
        delta = _backtest_delta(row)
        delta_text = "—" if delta is None else f"{delta:+.1f}"
        delta_class = "up" if delta is not None and delta > 0.05 else "down" if delta is not None and delta < -0.05 else "flat"
        confidence = str(row.get("BacktestConfidenceTier", "") or "无本票可靠校准")
        cards.append(
            f'<article class="action-card" data-ticker="{_safe(ticker)}" tabindex="0">'
            f'<div class="action-top"><div><span class="action-code">{_safe(ticker)}</span>'
            f'<h3>{_safe(name)}</h3></div><span class="action-state {state_class}">{_safe(state)}</span></div>'
            f'<div class="action-signal"><strong>{_safe(signal)}</strong><span>ALPHA {_safe(alpha)} · 收盘 {_safe(close)}</span></div>'
            '<div class="action-levels">'
            f'<div><span>参考买点</span><strong>{_safe(buy)}</strong></div>'
            f'<div><span>止损</span><strong>{_safe(stop)}</strong></div>'
            f'<div><span>目标</span><strong>{_safe(target)}</strong></div>'
            f'<div><span>R/R</span><strong>{_safe(rr)}</strong></div></div>'
            f'<div class="action-foot"><span>回测校准 Δ <b class="{delta_class}">{_safe(delta_text)}</b></span>'
            f'<span>{_safe(confidence)}</span></div></article>'
        )
    return "".join(cards)


def _production_backtest(rows: list[dict[str, str]], backtest: dict[str, Any]) -> str:
    eligible = [
        row
        for row in rows
        if _number(row.get("BacktestScore")) is not None
        or _number(row.get("CompositeScore")) is not None
    ]
    meta = (
        f"模式 {str(backtest.get('mode', '') or '—').upper()} · "
        f"目标 {backtest.get('objective', '—')} · "
        f"校准状态 {backtest.get('ranking_calibration_status', '—')}"
    )
    if not eligible:
        table = '<div class="empty-state">当前发布结果尚未包含可展示的生产回测校准字段。</div>'
    else:
        body: list[str] = []
        for row in eligible[:24]:
            delta = _backtest_delta(row)
            delta_text = "—" if delta is None else f"{delta:+.1f}"
            cls = "up" if delta is not None and delta > 0.05 else "down" if delta is not None and delta < -0.05 else "flat"
            body.append(
                "<tr>"
                f"<td><strong>{_safe(row.get('Ticker', '—'))}</strong><small>{_safe(row.get('Name', ''))}</small></td>"
                f"<td>{_safe(_fmt(row.get('BacktestScore'), 1))}</td>"
                f"<td>{_safe(_fmt(row.get('BacktestAdjustedScore'), 1))}</td>"
                f"<td>{_safe(_fmt((_number(row.get('BacktestEffectiveWeight')) or 0.0) * 100.0, 1))}%</td>"
                f"<td>{_safe(_fmt(row.get('CompositeScore'), 1))}</td>"
                f'<td class="{cls}">{_safe(delta_text)}</td>'
                f"<td>{_safe(row.get('BacktestSamples', '—'))}</td>"
                f"<td>{_safe(row.get('BacktestConfidenceTier', '') or '—')}</td>"
                "</tr>"
            )
        table = (
            '<div class="compact-table"><table><thead><tr><th>标的</th><th>回测分</th><th>校准分</th>'
            '<th>权重</th><th>综合分</th><th>Δ</th><th>样本</th><th>可信度</th></tr></thead><tbody>'
            + "".join(body)
            + "</tbody></table></div>"
        )
    return (
        '<section id="production-backtest-calibration" class="final-panel">'
        '<div class="final-panel-head"><div><span class="eyebrow">MODEL EVIDENCE</span>'
        '<h2>生产回测校准</h2></div><span class="ranking-chip">参与当前排名</span></div>'
        f'<p class="panel-meta">{_safe(meta)}</p>{table}</section>'
    )


def _resonance(backtest: dict[str, Any]) -> str:
    raw = backtest.get("resonance_analysis", {})
    analysis = raw if isinstance(raw, dict) else {}
    status = str(analysis.get("status", "") or "NOT_EVALUATED")
    version = str(analysis.get("version", "") or "未生成")
    samples = analysis.get("samples", 0)
    groups = analysis.get("by_band", [])
    body: list[str] = []
    if isinstance(groups, list):
        for item in groups[:8]:
            if not isinstance(item, dict):
                continue
            body.append(
                '<div class="res-card">'
                f'<strong>{_safe(item.get("group", "—"))}</strong>'
                f'<span>样本 {_safe(item.get("samples", 0))}</span>'
                f'<span>20D净超额 {_safe(_fmt(item.get("average_net_excess_20d"), 2))}%</span>'
                f'<span>胜率 {_safe(_fmt((_number(item.get("net_excess_win_rate_20d")) or 0.0) * 100.0, 1))}%</span>'
                '</div>'
            )
    content = "".join(body) or '<div class="empty-state">暂无足够的完整五因子历史样本。</div>'
    return (
        '<section id="five-factor-resonance" class="final-panel secondary-panel">'
        '<div class="final-panel-head"><div><span class="eyebrow">DIAGNOSTIC ONLY</span>'
        '<h2>五因子共振回测</h2></div><span class="diagnostic-chip">不进入排名</span></div>'
        f'<p class="panel-meta">状态 {_safe(status)} · 完整样本 {_safe(samples)} · {_safe(version)} · MACD + KDJ + RSI + OBV + BOLL</p>'
        f'<div class="res-grid-final">{content}</div></section>'
    )


_FINAL_CSS = r"""
<style id="canonical-v101-style">
.final-nav{position:sticky;top:0;z-index:20;display:flex;gap:6px;align-items:center;padding:8px 10px;margin:0 0 12px;background:rgba(21,23,26,.96);backdrop-filter:blur(8px);overflow:auto;border-left:4px solid var(--red)}.final-nav a{color:#d9dde3;text-decoration:none;font:700 9px ui-monospace,Consolas,monospace;letter-spacing:.6px;padding:6px 8px;border:1px solid #454a50;white-space:nowrap}.final-nav a:hover{background:#fff;color:#15171a}.final-nav .history-link{margin-left:auto;border-color:var(--red);color:#fff}
.decision-hero{display:grid;grid-template-columns:minmax(300px,1.05fr) 1.5fr;gap:12px;background:#15171a;color:#fff;padding:18px;border-left:6px solid #6b7078;margin:0 0 12px}.decision-hero.positive{border-left-color:var(--red)}.decision-hero.warn{border-left-color:var(--amber)}.decision-copy{padding:6px 8px}.eyebrow{display:block;color:#9298a1;font:700 9px ui-monospace,Consolas,monospace;letter-spacing:1.4px}.decision-copy h1{font-size:25px;line-height:1.15;margin:7px 0 7px}.decision-copy p{margin:0;color:#c9cdd2;font-size:11px;line-height:1.7}.decision-meta{display:flex;flex-wrap:wrap;gap:6px 12px;margin-top:13px;color:#8f959d;font-size:9px}.decision-kpis{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}.decision-kpi{background:#21252a;border-top:3px solid #6b7078;padding:11px;min-height:82px}.decision-kpi.positive{border-top-color:var(--red)}.decision-kpi.warn{border-top-color:var(--amber)}.decision-kpi.risk{border-top-color:var(--green)}.decision-kpi.accent{border-top-color:var(--blue)}.decision-kpi span,.decision-kpi small{display:block;color:#959ba3;font:700 8px ui-monospace,Consolas,monospace}.decision-kpi strong{display:block;font:700 21px ui-monospace,Consolas,monospace;margin:6px 0 3px;color:#fff}
.action-section{margin-bottom:14px}.action-board{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.action-card{background:#fff;border:1px solid var(--line);border-top:3px solid var(--ink);padding:12px;cursor:pointer;transition:transform .12s ease,box-shadow .12s ease}.action-card:hover,.action-card:focus{transform:translateY(-1px);box-shadow:0 5px 16px rgba(21,23,26,.09);outline:none}.action-top{display:flex;justify-content:space-between;gap:10px}.action-code{font:700 10px ui-monospace,Consolas,monospace;color:var(--muted)}.action-top h3{font-size:13px;margin:3px 0}.action-state{height:max-content;padding:4px 6px;border-left:3px solid #999;background:#f4f5f6;font-size:9px;font-weight:700}.action-state.ready{border-color:var(--red);color:var(--red-dark)}.action-state.cautious{border-color:var(--amber);color:var(--amber)}.action-signal{display:flex;justify-content:space-between;gap:8px;align-items:end;padding:10px 0 8px;border-bottom:1px solid var(--line)}.action-signal strong{font:700 11px ui-monospace,Consolas,monospace}.action-signal span{color:var(--muted);font-size:9px}.action-levels{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line);margin-top:8px}.action-levels div{background:#fafafa;padding:7px}.action-levels span{display:block;color:var(--muted);font-size:8px}.action-levels strong{display:block;margin-top:3px;font:700 10px ui-monospace,Consolas,monospace}.action-foot{display:flex;justify-content:space-between;gap:8px;margin-top:8px;color:var(--muted);font-size:8px}.up{color:var(--red-dark)!important;font-weight:700}.down{color:var(--green)!important;font-weight:700}.flat{color:var(--muted)!important}
.final-panel{background:#fff;border:1px solid var(--line);margin-bottom:12px}.final-panel-head{display:flex;justify-content:space-between;gap:12px;align-items:center;padding:12px 13px;border-bottom:1px solid var(--line)}.final-panel-head h2{margin:2px 0 0;font-size:15px}.ranking-chip,.diagnostic-chip{font:700 9px ui-monospace,Consolas,monospace;padding:5px 7px}.ranking-chip{background:#15171a;color:#fff}.diagnostic-chip{border:1px solid var(--line);color:var(--muted)}.panel-meta{margin:0;padding:8px 13px;color:var(--muted);font-size:9px;background:#fafafa;border-bottom:1px solid var(--line)}.compact-table{overflow:auto;max-height:390px}.compact-table table{font-size:10px}.compact-table td{padding:7px 8px}.compact-table td:first-child{text-align:left}.compact-table td small{display:block;color:var(--muted);font-size:8px}.res-grid-final{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--line)}.res-card{background:#fff;padding:10px}.res-card strong,.res-card span{display:block}.res-card strong{font:700 11px ui-monospace,Consolas,monospace;margin-bottom:5px}.res-card span{color:var(--muted);font-size:9px;line-height:1.55}.empty-state{padding:18px;color:var(--muted);font-size:11px;background:#fafafa}
.system-fold{margin-bottom:12px;border:1px solid var(--line);background:#fff}.system-fold summary{cursor:pointer;padding:10px 12px;font-weight:700;font-size:10px}.system-fold .fold-note{padding:0 12px 10px;color:var(--muted);font-size:9px}
@media(max-width:1100px){.decision-hero{grid-template-columns:1fr}.action-board{grid-template-columns:repeat(2,1fr)}}
@media(max-width:700px){.final-nav{margin-left:-9px;margin-right:-9px;border-left:0}.final-nav .history-link{margin-left:0}.decision-hero{padding:12px;margin-left:-2px;margin-right:-2px}.decision-copy h1{font-size:20px}.decision-kpis{grid-template-columns:repeat(2,1fr)}.action-board{grid-template-columns:1fr}.action-levels{grid-template-columns:repeat(2,1fr)}.res-grid-final{grid-template-columns:repeat(2,1fr)}}
</style>
"""


def _enhance_page(
    path: Path,
    *,
    rows: list[dict[str, str]],
    ready_rows: list[dict[str, str]],
    daily: dict[str, Any],
    backtest: dict[str, Any],
    report_date: str,
) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return
    if 'id="canonical-v101-style"' in text:
        return

    if "</head>" in text:
        text = text.replace("</head>", _FINAL_CSS + "</head>", 1)

    history_href = "reports/index.html" if path.name == "index.html" and path.parent.name != "reports" else "index.html"
    nav = (
        '<nav class="final-nav" aria-label="报告导航">'
        '<a href="#today-decision">今日摘要</a><a href="#action-board">行动池</a>'
        '<a href="#production-backtest-calibration">回测校准</a>'
        '<a href="#five-factor-resonance">五因子诊断</a>'
        f'<a class="history-link" href="{_safe(history_href)}">历史报告 →</a></nav>'
    )
    hero = _decision_summary(rows, ready_rows, daily, backtest)
    actions = (
        '<section id="action-board" class="action-section">'
        '<div class="section-head"><h2>ACTION BOARD / 当前行动池</h2>'
        '<p>只优先展示 READY / CAUTIOUS；点击卡片查看日 K、买点、止损与诊断</p></div>'
        f'<div class="action-board">{_action_cards(ready_rows)}</div></section>'
    )

    first_section = '<section class="section" data-section='
    if first_section in text:
        text = text.replace(first_section, nav + hero + actions + first_section, 1)
    else:
        text = text.replace("<main class=\"shell\">", '<main class="shell">' + nav + hero + actions, 1)

    diagnostics = _production_backtest(rows, backtest) + _resonance(backtest)
    foot = '<div class="foot">'
    if foot in text:
        text = text.replace(foot, diagnostics + foot, 1)
    else:
        text = text.replace("</main>", diagnostics + "</main>", 1)

    hidden_marker = f"交易快报 {report_date}"
    if hidden_marker not in text:
        text = text.replace("<body>", f'<body><span style="display:none">{_safe(hidden_marker)}</span>', 1)
    text = text.replace(_base.WEB_REPORT_VERSION, WEB_REPORT_VERSION)
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        return


def build_web_report(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    site_dir: Path = DEFAULT_SITE_DIR,
) -> WebReportResult:
    output_dir = Path(output_dir)
    result = _base.build_web_report(output_dir=output_dir, site_dir=Path(site_dir))
    source_dir = _published_source_dir(output_dir)
    rows = _candidate_rows(source_dir, output_dir)
    ready = _ready_rows(source_dir, output_dir, rows)
    daily = _read_json(source_dir / "DailyRunSummary.json") or _read_json(
        output_dir / "DailyRunSummary.json"
    )
    backtest = _read_json(source_dir / "BacktestSummary.json") or _read_json(
        output_dir / "BacktestSummary.json"
    )
    for path in (result.index_path, result.archive_path):
        _enhance_page(
            path,
            rows=rows,
            ready_rows=ready,
            daily=daily,
            backtest=backtest,
            report_date=result.report_date,
        )
    return result


def build_and_publish_web_report(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    site_dir: Path = DEFAULT_SITE_DIR,
    logger: logging.Logger | None = None,
    reason: str = "run-complete",
) -> WebReportResult:
    log = logger or logging.getLogger("institution_scanner")
    built = build_web_report(output_dir=Path(output_dir), site_dir=Path(site_dir))
    log.info("Canonical WEB decision briefing generated: %s (%s).", built.archive_path, reason)
    raw = os.environ.get(WEB_PUBLISH_ENV)
    enabled = True if raw is None else raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}
    if not enabled:
        log.info("WEB publication disabled by %s.", WEB_PUBLISH_ENV)
        return built
    try:
        published = publish_site(
            Path(site_dir), repo_root=PROJECT_ROOT, report_date=built.report_date
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        log.warning("WEB report publication skipped/failed without affecting pipeline: %s", exc)
        return WebReportResult(
            report_date=built.report_date,
            index_path=built.index_path,
            archive_path=built.archive_path,
            publish_message=str(exc),
        )
    log.info("Canonical WEB decision briefing published: %s", published.page_url)
    return published


def maybe_publish_canonical_report(
    output_dir: Path,
    *,
    logger: logging.Logger | None = None,
    reason: str,
) -> WebReportResult | None:
    if not is_canonical_output_dir(Path(output_dir)):
        return None
    try:
        return build_and_publish_web_report(
            output_dir=Path(output_dir), logger=logger, reason=reason
        )
    except (OSError, RuntimeError, csv.Error) as exc:
        log = logger or logging.getLogger("institution_scanner")
        log.warning(
            "WEB decision briefing generation skipped/failed without affecting pipeline: %s",
            exc,
        )
        return None
