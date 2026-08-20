"""v81 public-safe static web report + gh-pages publisher.

The scanner remains the source of truth. This module only reads committed
output artifacts after a successful scan/backtest/daily publication, renders a
self-contained HTML research brief and, when enabled, pushes only website files
to the dedicated ``gh-pages`` branch.

Publication is best-effort by design: GitHub/network/authentication failures
must never turn a successful scan or backtest into a failed trading pipeline.
"""

from __future__ import annotations

import csv
import html
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"
DEFAULT_SITE_DIR = DEFAULT_OUTPUT_DIR / "web_report"
WEB_REPORT_VERSION = "2026-08-20-v81-github-pages-report-v1"
WEB_PUBLISH_ENV = "INSTITUTION_SCANNER_WEB_PUBLISH"
WEB_REPORT_ROWS_ENV = "INSTITUTION_SCANNER_WEB_REPORT_ROWS"
GH_PAGES_BRANCH = "gh-pages"

_PUBLIC_COLUMNS = (
    "OverallRank",
    "CandidateViewRank",
    "ResearchPoolRank",
    "Ticker",
    "Name",
    "AssetType",
    "Industry",
    "ETFTheme",
    "ModelClassification",
    "Close",
    "EntrySignal",
    "SignalStatus",
    "SignalDays",
    "EntryZone",
    "BreakoutBuyPrice",
    "StopLoss",
    "ProjectedTarget",
    "RewardRiskRatio",
    "RankingEligibility",
    "RankingScore",
    "InstitutionalTier",
    "InstitutionalScore",
    "FinalScore",
    "QualityGate",
    "QualityDataCompleteness",
    "BacktestMode",
    "BacktestSamples",
    "BacktestWinRate20D",
    "BacktestWinRate60D",
    "BacktestConfidenceTier",
    "BacktestFreshnessStatus",
    "DataAsOf",
    "TradeReadinessReason",
    "RankingReason",
)

_TABLE_COLUMNS = (
    ("OverallRank", "排名"),
    ("Ticker", "代码"),
    ("Name", "名称"),
    ("AssetType", "类型"),
    ("IndustryTopic", "行业 / 主题"),
    ("Close", "收盘"),
    ("EntrySignal", "技术信号"),
    ("ReferenceBuyPrice", "参考买点"),
    ("StopLoss", "止损"),
    ("RankingEligibility", "资格"),
    ("RankingScore", "排序分"),
    ("InstitutionalStrength", "机构强度"),
    ("BacktestEvidence", "回测"),
    ("DataAsOf", "数据日"),
)


@dataclass(frozen=True)
class WebReportResult:
    report_date: str
    index_path: Path
    archive_path: Path
    page_url: str = ""
    published: bool = False
    publish_message: str = ""


def _truthy_env(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _max_rows() -> int:
    raw = os.environ.get(WEB_REPORT_ROWS_ENV, "250")
    try:
        return max(25, min(1000, int(raw)))
    except ValueError:
        return 250


def is_canonical_output_dir(output_dir: Path) -> bool:
    try:
        return Path(output_dir).resolve() == DEFAULT_OUTPUT_DIR.resolve()
    except OSError:
        return False


def _read_json(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    rows: list[dict[str, str]] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                ticker = str(raw.get("Ticker", "") or "").strip().upper()
                if not ticker:
                    continue
                row = {
                    column: str(raw.get(column, "") or "").strip()
                    for column in _PUBLIC_COLUMNS
                    if column in raw
                }
                row["Ticker"] = ticker
                rows.append(row)
    except (OSError, UnicodeError, csv.Error):
        return []
    return rows


def _published_source_dir(output_dir: Path) -> Path:
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
    if candidate.is_dir() and any(
        (candidate / name).is_file()
        for name in ("AllResults.csv", "DecisionResults.csv", "Top50Mixed.csv")
    ):
        return candidate
    return output_dir


def _report_date(rows: list[dict[str, str]], daily: dict[str, object]) -> str:
    for key in ("effective_trading_date", "expected_trading_date"):
        text = str(daily.get(key, "") or "").strip()
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            pass
    counts: dict[str, int] = {}
    for row in rows:
        text = row.get("DataAsOf", "")
        try:
            parsed = date.fromisoformat(text).isoformat()
        except ValueError:
            continue
        counts[parsed] = counts.get(parsed, 0) + 1
    if counts:
        return max(counts.items(), key=lambda item: (item[1], item[0]))[0]
    return date.today().isoformat()


def _number(value: object) -> float | None:
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return parsed


def _fmt_number(value: object, decimals: int = 2) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:.{decimals}f}"


def _fmt_percent(value: object) -> str:
    number = _number(value)
    if number is None:
        return "—"
    if abs(number) <= 1.0:
        number *= 100.0
    return f"{number:.1f}%"


def _duration(value: object) -> str:
    number = _number(value)
    if number is None:
        return "—"
    seconds = max(0, round(number))
    minutes, sec = divmod(seconds, 60)
    hours, minute = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minute:02d}m"
    if minutes:
        return f"{minutes}m {sec:02d}s"
    return f"{sec}s"


def _asset_type(row: dict[str, str]) -> str:
    value = row.get("AssetType", "").strip().upper()
    ticker = row.get("Ticker", "")
    if value == "ETF" or ticker.startswith(("15", "16", "18", "51", "56", "58")):
        return "ETF"
    return "股票"


def _industry_topic(row: dict[str, str]) -> str:
    if _asset_type(row) == "ETF":
        return (
            row.get("ETFTheme", "")
            or row.get("ModelClassification", "")
            or row.get("Industry", "")
            or "—"
        )
    return row.get("Industry", "") or row.get("ModelClassification", "") or "—"


def _reference_buy_price(row: dict[str, str]) -> str:
    signal = row.get("EntrySignal", "").upper()
    if signal == "BREAKOUT_CONFIRM" and row.get("BreakoutBuyPrice", ""):
        return row["BreakoutBuyPrice"]
    return row.get("EntryZone", "") or row.get("BreakoutBuyPrice", "") or "—"


def _institutional_strength(row: dict[str, str]) -> str:
    tier = row.get("InstitutionalTier", "").strip()
    score = _fmt_number(row.get("InstitutionalScore", ""), 1)
    return " · ".join(value for value in (tier, score) if value and value != "—") or "—"


def _backtest_evidence(row: dict[str, str]) -> str:
    mode = row.get("BacktestMode", "").strip().upper()
    samples = _number(row.get("BacktestSamples", ""))
    confidence = row.get("BacktestConfidenceTier", "").strip()
    parts: list[str] = []
    if mode and mode != "NONE":
        parts.append(mode)
    if samples is not None:
        parts.append(f"{int(samples)}样本")
    if confidence:
        parts.append(confidence)
    return " · ".join(parts) or "—"


def _sort_key(row: dict[str, str]) -> tuple[float, float, str]:
    rank = _number(
        row.get("CandidateViewRank", "")
        or row.get("ResearchPoolRank", "")
        or row.get("OverallRank", "")
    )
    score = _number(row.get("RankingScore", ""))
    return (
        rank if rank is not None and rank > 0 else 1_000_000.0,
        -(score if score is not None else -1_000_000.0),
        row.get("Ticker", ""),
    )


def _decorate_rows(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for position, source in enumerate(sorted(rows, key=_sort_key), start=1):
        row = dict(source)
        row["OverallRank"] = (
            row.get("CandidateViewRank", "")
            or row.get("ResearchPoolRank", "")
            or row.get("OverallRank", "")
            or str(position)
        )
        row["AssetType"] = _asset_type(row)
        row["IndustryTopic"] = _industry_topic(row)
        row["ReferenceBuyPrice"] = _reference_buy_price(row)
        row["InstitutionalStrength"] = _institutional_strength(row)
        row["BacktestEvidence"] = _backtest_evidence(row)
        output.append(row)
    return output


def _summary_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    counts = {
        "total": len(rows),
        "stocks": 0,
        "etfs": 0,
        "recommended": 0,
        "cautious": 0,
        "new": 0,
        "buy_now": 0,
        "breakout": 0,
        "wait_pullback": 0,
    }
    for row in rows:
        counts["etfs" if _asset_type(row) == "ETF" else "stocks"] += 1
        eligibility = row.get("RankingEligibility", "")
        counts["recommended"] += eligibility == "推荐"
        counts["cautious"] += eligibility == "谨慎候选"
        counts["new"] += row.get("SignalStatus", "").upper() == "NEW"
        signal = row.get("EntrySignal", "").upper()
        counts["buy_now"] += signal == "BUY_NOW"
        counts["breakout"] += signal == "BREAKOUT_CONFIRM"
        counts["wait_pullback"] += signal == "WAIT_PULLBACK"
    return counts


def _safe(text: object) -> str:
    return html.escape(str(text or ""), quote=True)


def _signal_badge(signal: str) -> tuple[str, str]:
    normalized = signal.strip().upper()
    mapping = {
        "BUY_NOW": ("BUY NOW", "good"),
        "BREAKOUT_CONFIRM": ("BREAKOUT", "good"),
        "WAIT_PULLBACK": ("WAIT PULLBACK", "warn"),
        "PRICE_BREAKOUT": ("PRICE BREAKOUT", "warn"),
        "HOLD_WAIT": ("HOLD", "muted"),
        "AVOID": ("AVOID", "bad"),
    }
    return mapping.get(normalized, (normalized or "—", "muted"))


def _eligibility_class(value: str) -> str:
    return {
        "推荐": "good",
        "谨慎候选": "warn",
        "风险过滤": "bad",
        "观察": "muted",
    }.get(value, "muted")


def _table_html(rows: list[dict[str, str]]) -> str:
    body: list[str] = []
    for row in rows[: _max_rows()]:
        signal_label, signal_class = _signal_badge(row.get("EntrySignal", ""))
        eligibility = row.get("RankingEligibility", "") or "—"
        search = " ".join(
            (
                row.get("Ticker", ""),
                row.get("Name", ""),
                row.get("IndustryTopic", ""),
                eligibility,
                signal_label,
            )
        ).casefold()
        values = {
            "OverallRank": row.get("OverallRank", "—"),
            "Ticker": row.get("Ticker", "—"),
            "Name": row.get("Name", "—"),
            "AssetType": row.get("AssetType", "—"),
            "IndustryTopic": row.get("IndustryTopic", "—"),
            "Close": _fmt_number(row.get("Close", "")),
            "ReferenceBuyPrice": row.get("ReferenceBuyPrice", "—"),
            "StopLoss": _fmt_number(row.get("StopLoss", "")),
            "RankingScore": _fmt_number(row.get("RankingScore", ""), 1),
            "InstitutionalStrength": row.get("InstitutionalStrength", "—"),
            "BacktestEvidence": row.get("BacktestEvidence", "—"),
            "DataAsOf": row.get("DataAsOf", "—"),
        }
        cells: list[str] = []
        for key, _label in _TABLE_COLUMNS:
            if key == "EntrySignal":
                value = f'<span class="badge {signal_class}">{_safe(signal_label)}</span>'
            elif key == "RankingEligibility":
                value = (
                    f'<span class="badge {_eligibility_class(eligibility)}">'
                    f"{_safe(eligibility)}</span>"
                )
            else:
                value = _safe(values.get(key, "—"))
            cells.append(f"<td>{value}</td>")
        body.append(
            f'<tr data-search="{_safe(search)}" '
            f'data-signal="{_safe(row.get("EntrySignal", "").upper())}" '
            f'data-eligibility="{_safe(eligibility)}">{"".join(cells)}</tr>'
        )
    return "\n".join(body)


def _render_html(
    *,
    report_date: str,
    all_rows: list[dict[str, str]],
    display_rows: list[dict[str, str]],
    daily: dict[str, object],
    backtest: dict[str, object],
    history_href: str,
) -> str:
    counts = _summary_counts(all_rows)
    stages_raw = daily.get("stage_seconds", {})
    stages = stages_raw if isinstance(stages_raw, dict) else {}
    daily_backtest_raw = daily.get("backtest", {})
    daily_backtest = daily_backtest_raw if isinstance(daily_backtest_raw, dict) else {}
    mode = str(
        backtest.get("mode", "") or daily_backtest.get("mode", "") or "—"
    ).upper()
    samples = backtest.get("samples", daily_backtest.get("samples", ""))
    win20 = backtest.get("win_rate_20d", "")
    win60 = backtest.get("win_rate_60d", "")
    cache_rate = daily_backtest.get("cache_hit_rate", "")
    headers = "".join(f"<th>{_safe(label)}</th>" for _key, label in _TABLE_COLUMNS)
    rows_html = _table_html(display_rows)
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="InstitutionScanner 交易快报 {report_date}">
<title>交易快报 {report_date} · InstitutionScanner</title>
<style>
:root{{--bg:#07111f;--panel:#0d1b2a;--panel2:#102337;--line:#1f3650;--text:#edf6ff;--muted:#8fa9c3;--blue:#5aa9ff;--green:#37d69b;--yellow:#f4c95d;--red:#ff7a85;}}
*{{box-sizing:border-box}} body{{margin:0;background:radial-gradient(circle at top right,#102b46 0,#07111f 34rem);color:var(--text);font-family:Inter,"Microsoft YaHei UI",system-ui,-apple-system,sans-serif}}
a{{color:#83bfff;text-decoration:none}} .wrap{{max-width:1480px;margin:auto;padding:28px 22px 60px}}
.hero{{display:flex;justify-content:space-between;gap:18px;align-items:flex-end;margin-bottom:24px}} .eyebrow{{color:var(--blue);font-weight:800;letter-spacing:.12em;font-size:12px}}
h1{{margin:5px 0 7px;font-size:34px;letter-spacing:-.04em}} .sub{{color:var(--muted);font-size:13px}} .version{{text-align:right;color:var(--muted);font-size:12px}}
.grid{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin:18px 0}} .card{{background:linear-gradient(145deg,rgba(16,35,55,.94),rgba(10,24,39,.94));border:1px solid var(--line);border-radius:15px;padding:16px;min-height:92px;box-shadow:0 12px 35px rgba(0,0,0,.18)}} .card .k{{font-size:12px;color:var(--muted)}} .card .v{{font-size:25px;font-weight:800;margin-top:6px}} .card .x{{font-size:11px;color:var(--muted);margin-top:4px}}
.section{{background:rgba(9,23,37,.92);border:1px solid var(--line);border-radius:16px;margin-top:16px;overflow:hidden}} .section-head{{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:16px 18px;border-bottom:1px solid var(--line)}} .section-head h2{{margin:0;font-size:17px}}
.metrics{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:1px;background:var(--line)}} .metric{{background:var(--panel);padding:15px}} .metric span{{display:block;color:var(--muted);font-size:11px}} .metric strong{{display:block;margin-top:5px;font-size:16px}}
.controls{{display:flex;gap:8px;flex-wrap:wrap}} input,select{{background:#071522;color:var(--text);border:1px solid #29445f;border-radius:9px;padding:9px 11px;outline:none}} input{{min-width:240px}}
.table-wrap{{overflow:auto;max-height:72vh}} table{{border-collapse:collapse;width:100%;min-width:1220px;font-size:12px}} th{{position:sticky;top:0;z-index:2;background:#0c1d2e;color:#a8c1d8;text-align:left;padding:11px 10px;border-bottom:1px solid var(--line);white-space:nowrap}} td{{padding:10px;border-bottom:1px solid rgba(31,54,80,.62);white-space:nowrap}} tr:hover td{{background:#10283e}} .badge{{display:inline-flex;padding:4px 8px;border-radius:999px;font-weight:700;font-size:10px;border:1px solid currentColor}} .good{{color:var(--green);background:rgba(55,214,155,.08)}} .warn{{color:var(--yellow);background:rgba(244,201,93,.08)}} .bad{{color:var(--red);background:rgba(255,122,133,.08)}} .muted{{color:#9db1c3;background:rgba(157,177,195,.07)}}
.foot{{margin-top:18px;color:var(--muted);font-size:11px;line-height:1.7}} .hidden{{display:none}}
@media(max-width:1050px){{.grid{{grid-template-columns:repeat(3,1fr)}}.metrics{{grid-template-columns:repeat(3,1fr)}}}} @media(max-width:680px){{.wrap{{padding:18px 10px 40px}}.hero{{align-items:flex-start;flex-direction:column}}h1{{font-size:28px}}.version{{text-align:left}}.grid{{grid-template-columns:repeat(2,1fr)}}.metrics{{grid-template-columns:repeat(2,1fr)}}input{{min-width:100%;width:100%}}}}
</style>
</head>
<body><main class="wrap">
<header class="hero"><div><div class="eyebrow">INSTITUTIONSCANNER · DAILY RESEARCH BRIEF</div><h1>交易快报 {report_date}</h1><div class="sub">行情截止 {_safe(report_date)} · 生成于 {_safe(generated)} · <a href="{_safe(history_href)}">历史快报</a></div></div><div class="version">{WEB_REPORT_VERSION}<br>仅展示 public-safe 研究字段</div></header>
<section class="grid">
<div class="card"><div class="k">扫描标的</div><div class="v">{counts['total']:,}</div><div class="x">股票 {counts['stocks']:,} · ETF {counts['etfs']:,}</div></div>
<div class="card"><div class="k">推荐</div><div class="v">{counts['recommended']:,}</div><div class="x">谨慎候选 {counts['cautious']:,}</div></div>
<div class="card"><div class="k">新信号</div><div class="v">{counts['new']:,}</div><div class="x">本轮 SignalStatus=NEW</div></div>
<div class="card"><div class="k">BUY NOW</div><div class="v">{counts['buy_now']:,}</div><div class="x">回调可买</div></div>
<div class="card"><div class="k">BREAKOUT</div><div class="v">{counts['breakout']:,}</div><div class="x">突破确认</div></div>
<div class="card"><div class="k">WAIT PULLBACK</div><div class="v">{counts['wait_pullback']:,}</div><div class="x">等待回踩</div></div>
</section>
<section class="section"><div class="section-head"><h2>运行与回测</h2><span class="sub">本页只显示结果摘要，不公开原始缓存和运行日志</span></div><div class="metrics">
<div class="metric"><span>总耗时</span><strong>{_duration(daily.get('elapsed_seconds', ''))}</strong></div>
<div class="metric"><span>扫描阶段</span><strong>{_duration(stages.get('scan', ''))}</strong></div>
<div class="metric"><span>回测阶段</span><strong>{_duration(stages.get('backtest', ''))}</strong></div>
<div class="metric"><span>回测模式</span><strong>{_safe(mode)}</strong></div>
<div class="metric"><span>回测样本</span><strong>{_safe(samples or '—')}</strong></div>
<div class="metric"><span>Cache命中</span><strong>{_fmt_percent(cache_rate)}</strong></div>
<div class="metric"><span>20D胜率</span><strong>{_fmt_percent(win20)}</strong></div>
<div class="metric"><span>60D胜率</span><strong>{_fmt_percent(win60)}</strong></div>
<div class="metric"><span>FAST标的</span><strong>{_safe(daily_backtest.get('fast_screen_tickers', '—'))}</strong></div>
<div class="metric"><span>EXACT精炼</span><strong>{_safe(daily_backtest.get('exact_refinement_tickers', '—'))}</strong></div>
<div class="metric"><span>行情日期状态</span><strong>{_safe(daily.get('market_data_date_status', '—'))}</strong></div>
<div class="metric"><span>发布状态</span><strong>{_safe(daily.get('publish_status', '—'))}</strong></div>
</div></section>
<section class="section"><div class="section-head"><h2>候选观察表</h2><div class="controls"><input id="q" placeholder="搜索代码 / 名称 / 行业"><select id="signal"><option value="">全部信号</option><option>BUY_NOW</option><option>BREAKOUT_CONFIRM</option><option>WAIT_PULLBACK</option><option>PRICE_BREAKOUT</option><option>HOLD_WAIT</option></select><select id="elig"><option value="">全部资格</option><option>推荐</option><option>谨慎候选</option><option>观察</option><option>风险过滤</option></select></div></div>
<div class="table-wrap"><table id="t"><thead><tr>{headers}</tr></thead><tbody>{rows_html}</tbody></table></div></section>
<div class="foot">说明：网页是扫描器已发布结果的只读研究摘要，不会上传行情缓存、日志、本机路径、账户信息或未在白名单中的字段。表格最多展示 {_max_rows()} 行；完整研究仍以本地 AllResults / DecisionResults 为准。</div>
</main><script>
const q=document.getElementById('q'),s=document.getElementById('signal'),e=document.getElementById('elig');
function f(){{const x=q.value.trim().toLowerCase(),sv=s.value,ev=e.value;for(const r of document.querySelectorAll('#t tbody tr')){{const ok=(!x||r.dataset.search.includes(x))&&(!sv||r.dataset.signal===sv)&&(!ev||r.dataset.eligibility===ev);r.classList.toggle('hidden',!ok);}}}}q.addEventListener('input',f);s.addEventListener('change',f);e.addEventListener('change',f);
</script></body></html>"""


def _archive_html(site_dir: Path) -> str:
    report_dir = site_dir / "reports"
    dates = sorted(
        (path.stem for path in report_dir.glob("????-??-??.html")),
        reverse=True,
    )
    items = "".join(
        f'<li><a href="{_safe(day)}.html">交易快报 {_safe(day)}</a></li>'
        for day in dates
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>历史交易快报</title><style>body{{max-width:780px;margin:40px auto;padding:0 18px;background:#07111f;color:#edf6ff;font-family:system-ui,"Microsoft YaHei UI",sans-serif}}a{{color:#83bfff}}li{{padding:9px 0;border-bottom:1px solid #1f3650}}</style></head><body><h1>历史交易快报</h1><p><a href="../index.html">← 最新一期</a></p><ul>{items or '<li>暂无历史报告</li>'}</ul></body></html>"""


def build_web_report(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    site_dir: Path = DEFAULT_SITE_DIR,
) -> WebReportResult:
    output_dir = Path(output_dir)
    source_dir = _published_source_dir(output_dir)
    all_rows = _read_csv(source_dir / "AllResults.csv")
    if not all_rows:
        all_rows = _read_csv(source_dir / "DecisionResults.csv")
    display_rows = _read_csv(source_dir / "Top50Mixed.csv")
    if not display_rows:
        display_rows = _read_csv(source_dir / "Top50.csv")
    if not display_rows:
        display_rows = all_rows
    if not all_rows and display_rows:
        all_rows = display_rows
    if not all_rows:
        raise RuntimeError("WEB_REPORT_NO_RESULTS: no published CSV results found")

    daily = _read_json(source_dir / "DailyRunSummary.json")
    if not daily:
        daily = _read_json(output_dir / "DailyRunSummary.json")
    backtest = _read_json(source_dir / "BacktestSummary.json")
    if not backtest:
        backtest = _read_json(output_dir / "BacktestSummary.json")
    report_date = _report_date(all_rows, daily)
    decorated = _decorate_rows(display_rows)

    site_dir = Path(site_dir)
    report_dir = site_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    latest_page = _render_html(
        report_date=report_date,
        all_rows=all_rows,
        display_rows=decorated,
        daily=daily,
        backtest=backtest,
        history_href="reports/index.html",
    )
    archive_page = _render_html(
        report_date=report_date,
        all_rows=all_rows,
        display_rows=decorated,
        daily=daily,
        backtest=backtest,
        history_href="index.html",
    )
    archive_path = report_dir / f"{report_date}.html"
    index_path = site_dir / "index.html"
    archive_path.write_text(archive_page, encoding="utf-8")
    index_path.write_text(latest_page, encoding="utf-8")
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    (report_dir / "index.html").write_text(_archive_html(site_dir), encoding="utf-8")
    return WebReportResult(
        report_date=report_date,
        index_path=index_path,
        archive_path=archive_path,
    )


def github_pages_url_from_remote(remote: str) -> str:
    value = str(remote or "").strip()
    patterns = (
        r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?$",
        r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$",
        r"^ssh://git@github\.com/([^/]+)/([^/]+?)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.match(pattern, value, flags=re.IGNORECASE)
        if not match:
            continue
        owner, repo = match.group(1), match.group(2)
        if repo.lower() == f"{owner.lower()}.github.io":
            return f"https://{owner}.github.io/"
        return f"https://{owner}.github.io/{repo}/"
    return ""


def _run_git(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 60,
    allow: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        timeout=timeout,
    )
    if completed.returncode not in allow:
        detail = (completed.stderr or completed.stdout or "git command failed").strip()
        raise RuntimeError(f"WEB_REPORT_GIT_FAILED: {' '.join(args)}: {detail}")
    return completed


def publish_site(
    site_dir: Path,
    *,
    repo_root: Path = PROJECT_ROOT,
    branch: str = GH_PAGES_BRANCH,
    report_date: str = "",
) -> WebReportResult:
    site_dir = Path(site_dir)
    if not (site_dir / "index.html").is_file():
        raise RuntimeError("WEB_REPORT_SITE_MISSING: index.html not found")
    remote = _run_git(
        ["-C", str(repo_root), "remote", "get-url", "origin"]
    ).stdout.strip()
    page_url = github_pages_url_from_remote(remote)
    if not page_url:
        raise RuntimeError("WEB_REPORT_UNSUPPORTED_REMOTE: origin is not github.com")
    exists = (
        _run_git(
            [
                "-C",
                str(repo_root),
                "ls-remote",
                "--exit-code",
                "--heads",
                "origin",
                branch,
            ],
            allow=(0, 2),
        ).returncode
        == 0
    )

    with tempfile.TemporaryDirectory(prefix="institution-web-") as temp_dir:
        worktree = Path(temp_dir) / "site"
        if exists:
            _run_git(
                [
                    "clone",
                    "--quiet",
                    "--depth",
                    "1",
                    "--branch",
                    branch,
                    "--single-branch",
                    remote,
                    str(worktree),
                ],
                timeout=90,
            )
        else:
            worktree.mkdir(parents=True, exist_ok=True)
            _run_git(["init", "--quiet"], cwd=worktree)
            _run_git(["remote", "add", "origin", remote], cwd=worktree)
            _run_git(["checkout", "--orphan", branch], cwd=worktree)

        shutil.copy2(site_dir / "index.html", worktree / "index.html")
        shutil.copy2(site_dir / ".nojekyll", worktree / ".nojekyll")
        shutil.copytree(site_dir / "reports", worktree / "reports", dirs_exist_ok=True)
        (worktree / "reports" / "index.html").write_text(
            _archive_html(worktree), encoding="utf-8"
        )

        _run_git(
            ["add", "--", "index.html", ".nojekyll", "reports"],
            cwd=worktree,
        )
        diff = _run_git(
            ["diff", "--cached", "--quiet"], cwd=worktree, allow=(0, 1)
        )
        if diff.returncode == 1:
            stamp = report_date or date.today().isoformat()
            _run_git(
                [
                    "-c",
                    "user.name=InstitutionScanner",
                    "-c",
                    "user.email=institution-scanner@users.noreply.github.com",
                    "commit",
                    "--quiet",
                    "-m",
                    f"report: trading brief {stamp}",
                ],
                cwd=worktree,
            )
            _run_git(
                ["push", "origin", f"HEAD:{branch}"], cwd=worktree, timeout=90
            )
            message = f"published {stamp} to {branch}"
        else:
            message = "website already up to date"

    resolved_date = report_date or date.today().isoformat()
    return WebReportResult(
        report_date=resolved_date,
        index_path=site_dir / "index.html",
        archive_path=site_dir / "reports" / f"{resolved_date}.html",
        page_url=page_url,
        published=True,
        publish_message=message,
    )


def build_and_publish_web_report(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    site_dir: Path = DEFAULT_SITE_DIR,
    logger: logging.Logger | None = None,
    reason: str = "run-complete",
) -> WebReportResult:
    log = logger or logging.getLogger("institution_scanner")
    built = build_web_report(output_dir=output_dir, site_dir=site_dir)
    log.info("WEB report generated: %s (%s).", built.archive_path, reason)
    if not _truthy_env(WEB_PUBLISH_ENV, True):
        log.info("WEB publication disabled by %s.", WEB_PUBLISH_ENV)
        return built
    try:
        published = publish_site(
            site_dir,
            repo_root=PROJECT_ROOT,
            report_date=built.report_date,
        )
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
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
    log.info("WEB report published: %s", published.page_url)
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
    except (OSError, RuntimeError, csv.Error) as exc:
        log = logger or logging.getLogger("institution_scanner")
        log.warning(
            "WEB report generation skipped/failed without affecting pipeline: %s",
            exc,
        )
        return None
