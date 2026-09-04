"""Single-pass static renderer for the public A-share research briefing."""

from __future__ import annotations

import csv
import html
import json
import os
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final
from zoneinfo import ZoneInfo

import pandas as pd

from downloader import _load_cache

from .page_version import PUBLIC_PAGE_VERSION, PUBLIC_PAGE_VERSION_ID
from .publication_assets import PUBLICATION_CSS, PUBLICATION_JS

PUBLIC_STYLE_ASSET: Final = f"report-{PUBLIC_PAGE_VERSION}.css"
PUBLIC_SCRIPT_ASSET: Final = f"report-{PUBLIC_PAGE_VERSION}.js"
DEFAULT_OUTPUT_DIR: Final = Path(__file__).resolve().parents[1] / "output"
DEFAULT_SITE_DIR: Final = DEFAULT_OUTPUT_DIR / "web_report"


@dataclass(frozen=True)
class WebReportResult:
    report_date: str
    index_path: Path
    archive_path: Path
    page_url: str = ""
    published: bool = False
    publish_message: str = ""


def _safe(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def published_source_dir(output_dir: Path) -> Path:
    """Resolve the immutable latest-run snapshot without escaping output_dir."""
    output_dir = Path(output_dir)
    latest = _read_json(output_dir / "LatestRun.json")
    relative = str(latest.get("run_dir", "") or "").strip()
    if not relative:
        return output_dir
    candidate = output_dir / relative
    try:
        candidate.resolve().relative_to(output_dir.resolve())
    except (OSError, ValueError):
        return output_dir
    if candidate.is_dir():
        return candidate
    return output_dir


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
    names: tuple[str, ...],
) -> list[dict[str, str]]:
    roots = (source,) if source.resolve() == output.resolve() else (source, output)
    for root in roots:
        for name in names:
            rows = _read_csv(root / name)
            if rows:
                return rows
    return []


def _first_json(source: Path, output: Path, name: str) -> dict[str, Any]:
    roots = (source,) if source.resolve() == output.resolve() else (source, output)
    for root in roots:
        payload = _read_json(root / name)
        if payload:
            return payload
    return {}


def _num(value: object) -> float | None:
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _fmt(value: object, decimals: int = 1) -> str:
    number = _num(value)
    return "—" if number is None else f"{number:.{decimals}f}"


def _cached_closes(
    ticker: str,
    report_date: str,
    *,
    limit: int = 30,
) -> list[float]:
    """Read a report-date-safe close series for a compact row sparkline."""
    if not ticker:
        return []
    try:
        frame = _load_cache(ticker)
    except (ImportError, OSError, TypeError, ValueError):
        return []
    if frame is None or frame.empty or "Close" not in frame.columns:
        return []

    try:
        stamps = pd.to_datetime(frame.index, errors="coerce", utc=True)
        cutoff = pd.Timestamp(report_date, tz="UTC").normalize()
    except (OverflowError, TypeError, ValueError):
        return []

    pairs: list[tuple[pd.Timestamp, float]] = []
    for stamp, value in zip(stamps, frame["Close"], strict=False):
        number = _num(value)
        if pd.isna(stamp) or number is None or stamp.normalize() > cutoff:
            continue
        pairs.append((stamp, number))
    pairs.sort(key=lambda item: item[0])
    return [value for _, value in pairs[-max(2, limit) :]]


def _sparkline_svg(
    closes: list[float],
    *,
    width: int = 88,
    height: int = 26,
) -> str:
    """Render the legacy 30-session TREND chart with A-share colors."""
    values = [number for value in closes if (number := _num(value)) is not None]
    if len(values) < 2:
        return '<span class="trend-missing" title="未找到截至报告日的本地价格序列">—</span>'

    low, high = min(values), max(values)
    spread = max(high - low, abs(high) * 1e-6, 1e-9)
    x_step = (width - 4.0) / (len(values) - 1)
    points = []
    for index, value in enumerate(values):
        x = 2.0 + index * x_step
        y = (height - 3.0) - (value - low) / spread * (height - 6.0)
        points.append((x, y))

    first, last = values[0], values[-1]
    if last > first:
        direction, trend_class = "上涨", "trend-up"
    elif last < first:
        direction, trend_class = "下跌", "trend-down"
    else:
        direction, trend_class = "持平", "trend-flat"
    change = None if first == 0 else (last / first - 1.0) * 100.0
    change_text = "" if change is None else f" {change:+.1f}%"
    label = f"近{len(values)}个交易日走势：{direction}{change_text}"
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    end_x, end_y = points[-1]
    baseline = height / 2.0
    return (
        f'<svg class="trend-chart {trend_class}" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-label="{_safe(label)}">'
        f"<title>{_safe(label)}</title>"
        f'<line class="trend-baseline" x1="2" y1="{baseline:.1f}" x2="{width - 2}" y2="{baseline:.1f}"/>'
        f'<polyline class="trend-line" points="{polyline}"/>'
        f'<circle class="trend-dot" cx="{end_x:.1f}" cy="{end_y:.1f}" r="2.1"/></svg>'
    )


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y", "是"}


def _first(row: dict[str, str], *names: str) -> str:
    for name in names:
        value = str(row.get(name, "") or "").strip()
        if value:
            return value
    return ""


def _state(row: dict[str, str]) -> str:
    raw = _first(row, "ExecutionState", "DecisionState", "RankingEligibility")
    upper = raw.upper()
    if upper == "READY" or raw == "推荐":
        return "READY"
    if upper == "CAUTIOUS" or raw == "谨慎候选":
        return "CAUTIOUS"
    if upper == "BLOCKED" or raw == "风险过滤":
        return "BLOCKED"
    return "OBSERVE"


def _reason(row: dict[str, str]) -> str:
    parts: list[str] = []
    for name in (
        "TradeReadinessReason",
        "DecisionReason",
        "RankingReason",
        "QualityLayerReason",
    ):
        for part in _first(row, name).replace("|", ";").replace("；", ";").split(";"):
            cleaned = part.strip()
            if cleaned and cleaned not in parts:
                parts.append(cleaned)
            if len(parts) == 2:
                return " · ".join(parts)
    fallbacks = {
        "READY": "已通过当前执行闸门",
        "CAUTIOUS": "仅谨慎候选，等待风险条件改善",
        "OBSERVE": "信号或执行条件尚未同时满足",
        "BLOCKED": "硬性风险闸门阻断",
    }
    return " · ".join(parts) or fallbacks[_state(row)]


def _report_date(
    rows: list[dict[str, str]],
    manifest: dict[str, Any],
    daily: dict[str, Any],
) -> str:
    values = (
        manifest.get("report_date"),
        daily.get("effective_trading_date"),
        daily.get("expected_trading_date"),
        *(row.get("DataAsOf") for row in rows),
    )
    for value in values:
        try:
            return date.fromisoformat(str(value or "")[:10]).isoformat()
        except ValueError:
            continue
    return date.today().isoformat()


def _nested(payload: dict[str, Any], *keys: str, default: object = "") -> object:
    current: object = payload
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key, default)
    return current


def _state_counts(
    rows: list[dict[str, str]], manifest: dict[str, Any]
) -> Counter[str]:
    counts = Counter(_state(row) for row in rows)
    manifest_counts = _nested(manifest, "execution", "state_counts", default={})
    if isinstance(manifest_counts, dict) and manifest_counts:
        counts = Counter(
            {
                str(key).upper(): int(value or 0)
                for key, value in manifest_counts.items()
            }
        )
    return counts


def _metric_card(label: str, value: object, note: str, tone: str = "") -> str:
    return (
        f'<article class="metric {tone}"><span>{_safe(label)}</span>'
        f"<strong>{_safe(value)}</strong><small>{_safe(note)}</small></article>"
    )


def _candidate_row(
    row: dict[str, str],
    rank: int,
    sparkline: str,
) -> str:
    ticker = _first(row, "Ticker") or "—"
    name = _first(row, "Name")
    state = _state(row)
    asset = "ETF" if _truthy(row.get("IsETF")) or _first(row, "AssetType").lower() == "etf" else "股票"
    alpha = _first(row, "AlphaScore", "FinalScore", "CompositeScore", "Score")
    entry = _first(row, "BreakoutBuyPrice", "EntryZone")
    target = _first(row, "ProjectedTarget", "ProfitTarget", "TargetPrice")
    search = " ".join(
        (ticker, name, _first(row, "Industry", "Sector", "ETFTheme"), asset, state)
    ).lower()
    return f'''<tr data-search="{_safe(search)}" data-state="{state}" data-asset="{asset}">
<td>{rank}</td><td class="security"><strong>{_safe(ticker)}</strong><span>{_safe(name)}</span></td>
<td><span class="asset">{asset}</span></td><td>{_safe(_first(row, "Industry", "Sector", "ETFTheme") or "—")}</td>
<td class="number">{_safe(_fmt(row.get("Close"), 2))}</td><td class="trend-cell">{sparkline}</td><td class="number score">{_safe(_fmt(alpha))}</td>
<td><span class="state {state.lower()}">{state}</span></td><td>{_safe(_first(row, "EntrySignal", "SignalStatus") or "—")}</td>
<td class="number">{_safe(entry or "—")}</td><td class="number">{_safe(_fmt(row.get("StopLoss"), 2))}</td>
<td class="number">{_safe(_fmt(target, 2))}</td><td class="reason">{_safe(_reason(row))}</td></tr>'''


def _table(rows: list[dict[str, str]], report_date: str) -> str:
    body = "".join(
        _candidate_row(
            row,
            index,
            _sparkline_svg(
                _cached_closes(_first(row, "Ticker"), report_date)
            ),
        )
        for index, row in enumerate(rows, 1)
    )
    return f'''<div class="table-wrap"><table><thead><tr><th>#</th><th>代码 / 名称</th><th>类型</th><th>行业 / 主题</th><th>收盘</th><th title="截至报告日最近30个交易日收盘走势">TREND</th><th>ALPHA</th><th>状态</th><th>信号</th><th>参考买点</th><th>止损</th><th>目标</th><th>当前解释</th></tr></thead>
<tbody id="candidate-rows">{body}</tbody></table></div>'''


def _actionable_cards(rows: list[dict[str, str]]) -> str:
    actionable = [row for row in rows if _state(row) == "READY"]
    if not actionable:
        return '<div class="empty">今日没有标的同时通过模型、数据与执行闸门。观察不等于遗漏，空仓也是有效决策。</div>'
    cards = [
            f'''<article class="action-card"><div><span>READY</span><strong>{_safe(_first(row, "Ticker"))}</strong><small>{_safe(_first(row, "Name"))}</small></div>
<dl><dt>ALPHA</dt><dd>{_safe(_fmt(_first(row, "AlphaScore", "FinalScore")))}</dd><dt>参考买点</dt><dd>{_safe(_first(row, "BreakoutBuyPrice", "EntryZone") or "—")}</dd><dt>止损</dt><dd>{_safe(_fmt(row.get("StopLoss"), 2))}</dd><dt>目标</dt><dd>{_safe(_fmt(_first(row, "ProjectedTarget", "TargetPrice"), 2))}</dd></dl></article>'''
        for row in actionable[:8]
    ]
    return '<div class="action-grid">' + "".join(cards) + "</div>"


def _sector_rows(rows: list[dict[str, str]]) -> str:
    groups: dict[str, list[float]] = {}
    for row in rows:
        group = _first(row, "Industry", "Sector", "ETFTheme") or "未分类"
        score = _num(_first(row, "AlphaScore", "FinalScore"))
        groups.setdefault(group, [])
        if score is not None:
            groups[group].append(score)
    ordered = sorted(
        groups.items(),
        key=lambda item: (len(item[1]), sum(item[1]) / max(len(item[1]), 1)),
        reverse=True,
    )[:8]
    return "".join(
        f'<div><span>{_safe(name)}</span><strong>{len(scores)}</strong><small>平均 ALPHA {_fmt(sum(scores) / len(scores)) if scores else "—"}</small></div>'
        for name, scores in ordered
    )


def _diagnostics(
    manifest: dict[str, Any],
    daily: dict[str, Any],
    rows: list[dict[str, str]],
) -> str:
    role = _nested(
        manifest, "versions", "production_model", "role", default="PRODUCTION_CHAMPION"
    )
    signature = _nested(
        manifest, "versions", "production_model", "weight_signature", default="0.6000:0.2500:0.1500"
    )
    policy = _nested(manifest, "policy", "decision_policy_hash", default="—")
    run_id = _nested(manifest, "run", "run_id", default=_first(rows[0], "RunId") if rows else "—")
    universe = _nested(daily, "universe", "rows", default="—")
    freshness = _nested(daily, "freshness", "all_results_ratio", default=None)
    freshness_text = "—" if _num(freshness) is None else f"{float(freshness) * 100:.1f}%"
    return f'''<details class="diagnostics"><summary>模型、数据与运行诊断 <span>默认收起</span></summary><div class="diag-grid">
<div><span>生产角色</span><strong>{_safe(role)}</strong></div><div><span>模型权重</span><strong>{_safe(signature)}</strong></div>
<div><span>决策策略</span><strong>{_safe(policy)}</strong></div><div><span>Run ID</span><strong>{_safe(run_id)}</strong></div>
<div><span>全市场扫描</span><strong>{_safe(universe)}</strong></div><div><span>数据新鲜率</span><strong>{_safe(freshness_text)}</strong></div>
</div><p>完整 400+ 列研究数据保留在 AllResults.parquet；本页只消费稳定的公开候选契约。历史回测和分层证据不足时仅作诊断，不进入生产排序。</p></details>'''


def _render_html(
    *,
    report_date: str,
    rows: list[dict[str, str]],
    manifest: dict[str, Any],
    daily: dict[str, Any],
    history_href: str,
    asset_prefix: str,
) -> str:
    generated = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
    counts = _state_counts(rows, manifest)
    market = str(_nested(manifest, "market", "regime", default="") or "基准不足")
    freshness = str(_nested(manifest, "market", "freshness", default="") or "待核验")
    notional = _nested(
        manifest, "execution", "assumed_order_notional_cny", default=50_000
    )
    notional_value = _num(notional) or 50_000.0
    metrics = "".join(
        (
            _metric_card("市场状态", market, "市场环境只影响解释与闸门", "risk" if "风险" in market or "RISK_OFF" in market.upper() else ""),
            _metric_card("READY", counts.get("READY", 0), "当前可执行候选", "good"),
            _metric_card("CAUTIOUS", counts.get("CAUTIOUS", 0), "谨慎候选"),
            _metric_card("OBSERVE", counts.get("OBSERVE", 0), "继续观察"),
            _metric_card("数据日期", report_date, freshness),
            _metric_card("假设订单", f"¥{notional_value:,.0f}", "用于容量与冲击成本检查"),
        )
    )
    table_rows = rows[:100]
    action = _actionable_cards(rows)
    sectors = _sector_rows(rows)
    diagnostics = _diagnostics(manifest, daily, rows)
    why_rows = [row for row in rows if _state(row) != "READY"][:8]
    why_items = "".join(
        f'<li><strong>{_safe(_first(row, "Ticker"))}</strong>'
        f'<span>{_safe(_reason(row))}</span></li>'
        for row in why_rows
    )
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="web-report-version" content="{PUBLIC_PAGE_VERSION_ID}"><meta name="public-page-version" content="{PUBLIC_PAGE_VERSION}">
<title>{_safe(report_date)} · A股研究简报</title><link rel="stylesheet" href="{_safe(asset_prefix)}assets/{PUBLIC_STYLE_ASSET}"></head><body>
<main class="shell"><header class="masthead"><div class="brand"><b>INSTITUTION SCANNER</b><span>A股研究简报</span></div><div class="asof"><span>AS OF</span><strong>{_safe(report_date)}</strong></div><nav><a href="{_safe(history_href)}">历史简报</a><a href="{_safe(asset_prefix)}performance.html">前瞻绩效</a><a href="{_safe(asset_prefix)}backtest.html">回测审计</a></nav></header>
<div class="snapshot"><span>PUBLISHED SNAPSHOT · 数据对齐</span><span>生成 {_safe(generated)} CST</span><span>页面版本</span><strong>{PUBLIC_PAGE_VERSION}</strong></div>
<section class="hero"><div><p>DAILY DECISION BOARD</p><h1>先看风险与执行许可，<br>再看研究排名。</h1><p class="lead">研究分数回答“值得研究吗”，READY 才回答“当前能执行吗”。</p></div><div class="state-stack"><span>READY <b>{counts.get("READY", 0)}</b></span><span>CAUTIOUS <b>{counts.get("CAUTIOUS", 0)}</b></span><span>OBSERVE <b>{counts.get("OBSERVE", 0)}</b></span><span>BLOCKED <b>{counts.get("BLOCKED", 0)}</b></span></div></section>
<section class="metrics">{metrics}</section>
<section class="section"><div class="section-head"><div><span>01 / ACTIONABLE</span><h2>今日可执行候选</h2></div><p>必须同时通过信号、质量、数据时效、流动性与风险收益闸门</p></div>{action}</section>
<section class="section"><div class="section-head"><div><span>02 / RESEARCH UNIVERSE</span><h2>研究候选池</h2></div><p><span id="visible-count">{len(table_rows)} ROWS</span> · TREND 为截至报告日最近 30 个交易日收盘走势</p></div><div class="filters"><input id="candidate-search" type="search" placeholder="搜索代码、名称、行业"><select id="candidate-state"><option value="">全部状态</option><option>READY</option><option>CAUTIOUS</option><option>OBSERVE</option><option>BLOCKED</option></select><select id="candidate-asset"><option value="">全部类型</option><option>股票</option><option>ETF</option></select></div>{_table(table_rows, report_date)}</section>
<section class="split"><div class="section"><div class="section-head"><div><span>03 / THEMES</span><h2>行业与主题密度</h2></div></div><div class="sector-grid">{sectors}</div></div><div class="section"><div class="section-head"><div><span>04 / WHY NOT NOW</span><h2>暂不执行的主要原因</h2></div></div><ol class="why-list">{why_items}</ol></div></section>
{diagnostics}
<footer><p>本页用于量化研究、数据分析与策略复盘，不构成投资建议或收益承诺。</p><p>{PUBLIC_PAGE_VERSION_ID}</p></footer></main>
<script src="{_safe(asset_prefix)}assets/{PUBLIC_SCRIPT_ASSET}" defer></script></body></html>'''


def archive_index_html(site_dir: Path) -> str:
    pages = sorted(
        (path for path in (Path(site_dir) / "reports").glob("????-??-??.html") if path.is_file()),
        key=lambda path: path.stem,
        reverse=True,
    )
    items = "".join(
        f'<li><a href="{_safe(path.name)}"><span>{_safe(path.stem)}</span><b>OPEN →</b></a></li>'
        for path in pages
    ) or "<li><span>暂无历史报告</span></li>"
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>历史研究简报</title><link rel="stylesheet" href="../assets/{PUBLIC_STYLE_ASSET}"></head><body><main class="history"><header><p>ARCHIVE</p><h1>历史研究简报</h1><a href="../index.html">← 返回最新报告</a></header><ol>{items}</ol></main></body></html>'''


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def build_web_report(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    site_dir: Path = DEFAULT_SITE_DIR,
) -> WebReportResult:
    output_dir = Path(output_dir)
    site_dir = Path(site_dir)
    source = published_source_dir(output_dir)
    rows = _first_rows(
        source,
        output_dir,
        ("PublicCandidates.csv", "Top50Mixed.csv", "Top50.csv", "DecisionResults.csv"),
    )
    if not rows:
        raise RuntimeError("WEB_REPORT_NO_RESULTS: no published candidate results found")
    manifest = _first_json(source, output_dir, "PublicationManifest.json")
    daily = _first_json(source, output_dir, "DailyRunSummary.json")
    report_date = _report_date(rows, manifest, daily)
    report_dir = site_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(site_dir / "assets" / PUBLIC_STYLE_ASSET, PUBLICATION_CSS)
    _atomic_write(site_dir / "assets" / PUBLIC_SCRIPT_ASSET, PUBLICATION_JS)
    _atomic_write(site_dir / ".nojekyll", "")
    index_path = site_dir / "index.html"
    archive_path = report_dir / f"{report_date}.html"
    _atomic_write(
        index_path,
        _render_html(
            report_date=report_date,
            rows=rows,
            manifest=manifest,
            daily=daily,
            history_href="reports/index.html",
            asset_prefix="",
        ),
    )
    _atomic_write(
        archive_path,
        _render_html(
            report_date=report_date,
            rows=rows,
            manifest=manifest,
            daily=daily,
            history_href="index.html",
            asset_prefix="../",
        ),
    )
    _atomic_write(report_dir / "index.html", archive_index_html(site_dir))
    return WebReportResult(
        report_date=report_date,
        index_path=index_path,
        archive_path=archive_path,
    )
