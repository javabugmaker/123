"""v92 public briefing adapter for backtest calibration and resonance diagnostics.

The existing v85 report owns the public allowlist, charts and interaction model.
v92 post-processes that validated document with two explicitly separated layers:
production backtest calibration (which participates in ranking) and five-factor
resonance diagnostics (which remain experimental and ranking-neutral).

The web publication boundary remains strictly fail-soft: a presentation-layer
fault must never roll back an otherwise valid DAILY result transaction.
"""

from __future__ import annotations

import csv
import html
import json
import logging
import os
from pathlib import Path
from typing import Any

import web_report_v85 as _v85
from web_report_v85 import *  # noqa: F403

WEB_REPORT_VERSION = "2026-08-23-v92-backtest-calibration-visibility-v1"
WebReportResult = _v85.WebReportResult
DEFAULT_OUTPUT_DIR = _v85.DEFAULT_OUTPUT_DIR
DEFAULT_SITE_DIR = _v85.DEFAULT_SITE_DIR
PROJECT_ROOT = _v85.PROJECT_ROOT
WEB_PUBLISH_ENV = _v85.WEB_PUBLISH_ENV
GH_PAGES_BRANCH = _v85.GH_PAGES_BRANCH

_archive_html = _v85._archive_html
_published_source_dir = _v85._published_source_dir
is_canonical_output_dir = _v85.is_canonical_output_dir
github_pages_url_from_remote = _v85.github_pages_url_from_remote
publish_site = _v85.publish_site

_RESONANCE_CSS = """
<style id="five-factor-resonance-style">
.resonance-v90 .res-meta,.production-backtest-v92 .res-meta{display:flex;gap:10px;flex-wrap:wrap;padding:10px 13px;border-bottom:1px solid var(--line);background:#fafafa;color:var(--muted);font-size:10px}
.resonance-v90 .res-grid{display:grid;grid-template-columns:1fr 1fr;gap:1px;background:var(--line)}
.resonance-v90 .res-panel,.production-backtest-v92 .res-panel{background:#fff;min-width:0}.resonance-v90 .res-panel h3{margin:0;padding:9px 11px;border-bottom:1px solid var(--line);font:700 10px ui-monospace,Consolas,monospace;letter-spacing:.6px}
.resonance-v90 table,.production-backtest-v92 table{font-size:10px}.resonance-v90 th,.production-backtest-v92 th{font-size:8px}.resonance-v90 td,.production-backtest-v92 td{padding:7px 8px}.resonance-v90 .positive-number,.production-backtest-v92 .positive-number{color:var(--red-dark);font-weight:700}.resonance-v90 .negative-number,.production-backtest-v92 .negative-number{color:var(--green);font-weight:700}
.production-backtest-v92 .score-up{color:var(--red-dark);font-weight:700}.production-backtest-v92 .score-down{color:var(--green);font-weight:700}.production-backtest-v92 .score-flat{color:var(--muted)}
@media(max-width:900px){.resonance-v90 .res-grid{grid-template-columns:1fr}}
</style>
"""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except (OSError, UnicodeError, csv.Error):
        return []


def _backtest_candidate_rows(source_dir: Path, output_dir: Path) -> list[dict[str, str]]:
    roots = (source_dir,) if source_dir.resolve() == output_dir.resolve() else (source_dir, output_dir)
    for root in roots:
        for name in ("Top50Mixed.csv", "Top50Stocks.csv", "DecisionResults.csv", "AllResults.csv"):
            rows = _read_csv_rows(root / name)
            if rows:
                return rows
    return []


def _safe(value: object) -> str:
    """Escape diagnostic text without relying on private compatibility helpers."""
    return html.escape("" if value is None else str(value), quote=True)


def _truthy_env(name: str, default: bool = True) -> bool:
    """Read one boolean environment flag locally."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _number(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    return result


def _pct_value(value: object, *, fraction: bool = False) -> str:
    number = _number(value)
    if number is None:
        return "—"
    if fraction:
        return f"{number:.1%}"
    return f"{number:+.2f}%"


def _metric_class(value: object) -> str:
    number = _number(value)
    if number is None or number == 0:
        return ""
    return "positive-number" if number > 0 else "negative-number"


def _score(value: object) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:.1f}"


def _weight(value: object) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:.0%}"


def _delta(row: dict[str, str]) -> float | None:
    composite = _number(row.get("CompositeScore"))
    raw = _number(row.get("FinalScore"))
    if raw is None:
        raw = _number(row.get("Score"))
    if composite is None or raw is None:
        return None
    return composite - raw


def _delta_text(value: float | None) -> tuple[str, str]:
    if value is None:
        return "—", "score-flat"
    if value > 0.05:
        return f"{value:+.1f}", "score-up"
    if value < -0.05:
        return f"{value:+.1f}", "score-down"
    return f"{value:+.1f}", "score-flat"


def _production_backtest_table(rows: list[dict[str, str]]) -> str:
    eligible = [
        row for row in rows
        if _number(row.get("BacktestScore")) is not None
        or _number(row.get("CompositeScore")) is not None
    ]
    if not eligible:
        return (
            '<div class="res-meta">当前已发布结果尚未包含个股回测校准字段。'
            '如果回测仍在运行，排名与这些字段会在回测完成并原子发布后一起刷新。</div>'
        )
    body: list[str] = []
    for row in eligible[:30]:
        delta, delta_class = _delta_text(_delta(row))
        samples = _number(row.get("BacktestSamples"))
        effective = _number(row.get("BacktestEffectiveSamples"))
        sample_text = "—" if samples is None else str(round(samples))
        if effective is not None:
            sample_text += f" / {effective:.1f}"
        rank = (
            row.get("ResearchRank", "")
            or row.get("OverallRank", "")
            or row.get("TradeRank", "")
            or "—"
        )
        body.append(
            "<tr>"
            f'<td class="number">{_safe(rank)}</td>'
            f"<td><strong>{_safe(row.get('Ticker', '—'))}</strong><br><small>{_safe(row.get('Name', ''))}</small></td>"
            f'<td class="number">{_safe(_score(row.get("BacktestScore")))}</td>'
            f'<td class="number">{_safe(_score(row.get("BacktestAdjustedScore")))}</td>'
            f'<td class="number">{_safe(_weight(row.get("BacktestEffectiveWeight")))}</td>'
            f'<td class="number">{_safe(_score(row.get("CompositeScore")))}</td>'
            f'<td class="number {delta_class}">{_safe(delta)}</td>'
            f'<td class="number">{_safe(sample_text)}</td>'
            f"<td>{_safe(row.get('BacktestConfidenceTier', '') or '—')}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        '<th>当前排名</th><th>标的</th><th>回测分</th><th>校准分</th><th>有效权重</th>'
        '<th>回测后综合分</th><th>直接Δ</th><th>样本 / 有效</th><th>可信度</th>'
        '</tr></thead><tbody>' + "".join(body) + "</tbody></table></div>"
    )


def _production_backtest_block(rows: list[dict[str, str]], backtest: dict[str, object]) -> str:
    requested = backtest.get("ranking_eligible_ticker_count", 0)
    mode = str(backtest.get("mode", "") or "—")
    objective = str(backtest.get("objective", "") or "—")
    return f"""
<section id="production-backtest-calibration" class="section card production-backtest-v92">
  <div class="section-head"><h2>PRODUCTION BACKTEST CALIBRATION / 个股回测校准</h2><p>这条链路参与生产评分；回测完成后统一重算并发布，不在运行中增量改排名</p></div>
  <div class="res-meta"><span>模式 {_safe(mode)}</span><span>·</span><span>目标 {_safe(objective)}</span><span>·</span><span>可用于排名的标的 {_safe(requested)}</span><span>· 回测分→可靠性收缩→有效权重→CompositeScore</span></div>
  {_production_backtest_table(rows)}
</section>
"""


def _group_table(groups: object) -> str:
    if not isinstance(groups, list) or not groups:
        return '<div class="res-meta">暂无足够的完整五因子样本。</div>'
    body: list[str] = []
    for raw in groups:
        if not isinstance(raw, dict):
            continue
        avg20 = raw.get("average_net_excess_20d")
        avg60 = raw.get("average_net_excess_60d")
        drawdown = raw.get("max_drawdown_60d")
        body.append(
            "<tr>"
            f"<td><strong>{_safe(raw.get('group', '—'))}</strong></td>"
            f'<td class="number">{_safe(raw.get("samples", 0))}</td>'
            f'<td class="number">{_safe(_pct_value(raw.get("net_excess_win_rate_20d"), fraction=True))}</td>'
            f'<td class="number {_metric_class(avg20)}">{_safe(_pct_value(avg20))}</td>'
            f'<td class="number {_metric_class(avg60)}">{_safe(_pct_value(avg60))}</td>'
            f'<td class="number {_metric_class(drawdown)}">{_safe(_pct_value(drawdown))}</td>'
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr>'
        "<th>状态</th><th>样本</th><th>20D净超额胜率</th><th>20D净超额</th>"
        "<th>60D净超额</th><th>60D最大回撤</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _resonance_block(backtest: dict[str, object]) -> str:
    analysis = backtest.get("resonance_analysis", {})
    if not isinstance(analysis, dict):
        analysis = {}
    version = str(analysis.get("version", "") or "未生成")
    status = str(analysis.get("status", "") or "NOT_EVALUATED")
    samples = analysis.get("samples", 0)
    by_band = analysis.get("by_band", [])
    by_transition = analysis.get("by_transition", [])
    by_count = analysis.get("by_count", [])
    return f"""
<section id="five-factor-resonance" class="section card resonance-v90">
  <div class="section-head"><h2>FIVE-FACTOR RESONANCE / 五因子共振回测</h2><p>MACD + KDJ + RSI + OBV + BOLL · 仅作独立诊断，不进入当前排名</p></div>
  <div class="res-meta"><span>状态 {_safe(status)}</span><span>·</span><span>完整样本 {_safe(samples)}</span><span>·</span><span>{_safe(version)}</span><span>· 信号日收盘快照，无次日数据前视</span></div>
  <div class="res-grid">
    <article class="res-panel"><h3>VOTE BAND / 票数分层</h3>{_group_table(by_band)}</article>
    <article class="res-panel"><h3>TRANSITION / 票数变化</h3>{_group_table(by_transition)}</article>
  </div>
  <details class="res-panel"><summary style="padding:10px 12px;cursor:pointer;font-weight:700">展开 0/5–5/5 明细</summary>{_group_table(by_count)}</details>
</section>
"""


def _inject_backtest_sections(
    path: Path,
    backtest: dict[str, object],
    rows: list[dict[str, str]],
) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return
    if 'id="production-backtest-calibration"' in text:
        return
    blocks = _production_backtest_block(rows, backtest) + _resonance_block(backtest)
    if "</head>" in text and 'id="five-factor-resonance-style"' not in text:
        text = text.replace("</head>", _RESONANCE_CSS + "</head>", 1)
    anchor = '<div class="foot">'
    if anchor in text:
        text = text.replace(anchor, blocks + anchor, 1)
    else:
        text = text.replace("</main>", blocks + "</main>", 1)
    text = text.replace(_v85.WEB_REPORT_VERSION, WEB_REPORT_VERSION)
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        return


def build_web_report(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    site_dir: Path = DEFAULT_SITE_DIR,
) -> WebReportResult:
    result = _v85.build_web_report(
        output_dir=Path(output_dir), site_dir=Path(site_dir)
    )
    source_dir = _published_source_dir(Path(output_dir))
    backtest = _read_json(source_dir / "BacktestSummary.json") or _read_json(
        Path(output_dir) / "BacktestSummary.json"
    )
    rows = _backtest_candidate_rows(source_dir, Path(output_dir))
    for path in (result.index_path, result.archive_path):
        _inject_backtest_sections(path, backtest, rows)
    return result


def build_and_publish_web_report(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    site_dir: Path = DEFAULT_SITE_DIR,
    logger: logging.Logger | None = None,
    reason: str = "run-complete",
) -> WebReportResult:
    log = logger or logging.getLogger("institution_scanner")
    built = build_web_report(output_dir=output_dir, site_dir=site_dir)
    log.info(
        "WEB v92 research briefing generated: %s (%s).", built.archive_path, reason
    )
    if not _truthy_env(WEB_PUBLISH_ENV, True):
        log.info("WEB publication disabled by %s.", WEB_PUBLISH_ENV)
        return built
    try:
        published = publish_site(
            Path(site_dir),
            repo_root=PROJECT_ROOT,
            report_date=built.report_date,
        )
    except Exception as exc:
        log.warning(
            "WEB report publication skipped/failed without affecting pipeline: %s",
            exc,
        )
        return WebReportResult(
            report_date=built.report_date,
            index_path=built.index_path,
            archive_path=built.archive_path,
            publish_message=str(exc),
        )
    log.info("WEB v92 research briefing published: %s", published.page_url)
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
            output_dir=Path(output_dir),
            logger=logger,
            reason=reason,
        )
    except Exception as exc:
        log = logger or logging.getLogger("institution_scanner")
        log.warning(
            "WEB v92 research briefing generation skipped/failed without affecting pipeline: %s",
            exc,
        )
        return None
