"""Point-in-time backtest presentation for the public research console.

Renders a gross-of-cost Top-K historical backtest (strategy NAV vs CSI 300,
drawdown and annual return table) in the same visual language as the forward
performance page.  It reuses the dependency-free SVG chart renderer from
``performance_curve_web`` so the two diagnostics stay visually consistent.

This module is presentation-only: it reads the persisted
``HistoricalBacktest.json`` ledger and never changes scores, ranks, eligibility
or position logic.
"""

from __future__ import annotations

import html
import json
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

from institution_scanner.performance_curve_web import (  # noqa: F401
    _SHARED_CSS,
    _chart_svg,
    _date_label,
    _fmt,
    _legend,
    _metric,
    _num,
    _sample,
)


def _safe(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _read_payload(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _pct(value: object) -> str:
    number = _num(value)
    if number is None:
        return "—"
    return f"{number:+.1f}%"


def _nav(value: object) -> str:
    number = _num(value)
    if number is None:
        return "—"
    return f"{number:.2f}"


def _state(summary: dict[str, Any]) -> tuple[str, str, str]:
    nav = _num(summary.get("strategy_final_nav"))
    bench = _num(summary.get("benchmark_final_nav"))
    if nav is None or bench is None:
        return "NO DATA", "warm", "尚未生成历史回测数据。"
    excess = float(nav) - float(bench)
    mdd = _num(summary.get("strategy_max_drawdown_pct"))
    if excess > 0:
        return (
            "OUTPERFORM",
            "good",
            f"同期累计跑赢沪深300 {excess:+.2f} 个净值点；回测口径未计幸存者偏差消除，仅供研究参考。",
        )
    if mdd is not None and mdd <= -20.0:
        return (
            "UNDERPERFORM",
            "risk",
            "同期未跑赢沪深300且最大回撤偏深，先复核选股与换仓口径。",
        )
    return (
        "UNDERPERFORM",
        "warm",
        "同期未跑赢沪深300；结合回撤与年度明细再看有效性。",
    )


def _annual_table(summary: dict[str, Any]) -> str:
    annual = summary.get("annual", {})
    if not isinstance(annual, dict) or not annual:
        return ""
    rows = []
    for year in sorted(annual):
        item = annual[year]
        rows.append(
            "<tr>"
            f"<td>{_safe(year)}</td>"
            f"<td class=\"num\">{_pct(item.get('return_pct'))}</td>"
            f"<td class=\"num\">{_pct(item.get('benchmark_return_pct'))}</td>"
            f"<td class=\"num\">{_pct(_num(item.get('return_pct')) - _num(item.get('benchmark_return_pct')))}</td>"
            f"<td class=\"num\">{_pct(item.get('max_drawdown_pct'))}</td>"
            f"<td class=\"num\">{_safe(item.get('trading_days'))}</td>"
            "</tr>"
        )
    return (
        '<table class="bt-table"><thead><tr>'
        "<th>年份</th><th>策略收益</th><th>沪深300</th><th>超额</th><th>当年最大回撤</th><th>交易日</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def backtest_page_html(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    rows = payload.get("rows", [])
    version = str(payload.get("version", "") or "")
    rows = _sample([r for r in rows if isinstance(r, dict)], max_points=600)
    start = _date_label(summary.get("start"))
    end = _date_label(summary.get("end"))
    label, tone, explanation = _state(summary)

    nav_chart = _chart_svg(
        rows,
        (
            ("StrategyNAV", "pc-strategy", "策略净值(等权Top30)"),
            ("BenchmarkNAV", "pc-benchmark", "沪深300"),
        ),
        kind="nav",
        anchor=1.0,
        aria_label="策略净值与沪深300基准",
    )
    dd_chart = _chart_svg(
        rows,
        (
            ("StrategyDrawdown", "pc-strategy", "策略回撤"),
            ("BenchmarkDrawdown", "pc-benchmark", "沪深300回撤"),
        ),
        kind="pct",
        anchor=0.0,
        aria_label="策略与沪深300回撤",
    )

    page_css = f"""
:root{{--bg:#f1f2f4;--paper:#fff;--ink:#15171a;--muted:#6b7078;--line:#dfe3e8;--accent:#b52b32}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei UI",sans-serif}}
main{{max-width:1120px;margin:auto;padding:28px 20px 72px}}header{{display:flex;justify-content:space-between;gap:20px;align-items:flex-end;border-bottom:3px solid var(--accent);padding-bottom:14px;margin-bottom:18px}}h1{{margin:0;font:800 30px/1.1 ui-monospace,Consolas,monospace}}header p{{margin:7px 0 0;color:var(--muted)}}a{{color:var(--accent);text-decoration:none;font-weight:700}}.card{{background:var(--paper);border:1px solid var(--line);margin:14px 0}}.hero{{padding:18px}}.hero .pc-status{{border:0;padding:0 0 12px}}.section-head{{padding:13px 14px;border-bottom:1px solid var(--line)}}.section-head h2{{margin:0;font:800 14px ui-monospace,Consolas,monospace}}.section-head p{{margin:4px 0 0;color:var(--muted);font-size:11px}}.method{{padding:14px 18px}}.method ol{{margin:0;padding-left:20px}}.method li{{margin:6px 0}}footer{{margin-top:22px;color:var(--muted);font-size:11px}}
.bt-table{{width:100%;border-collapse:collapse;font:12px/1.6 ui-monospace,Consolas,monospace}}th,td{{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left}}th{{color:var(--muted);font-weight:600;font-size:10px;border-bottom:2px solid var(--line)}}td.num{{text-align:right;font-variant-numeric:tabular-nums}}tbody tr:hover{{background:#faf6f6}}
{_SHARED_CSS}
@media(max-width:760px){{header{{display:block}}header a{{display:inline-block;margin-top:10px}}h1{{font-size:24px}}}}
"""
    metrics = [
        ("观察区间", f"{start}<br>{end}"),
        ("最终净值", _nav(summary.get("strategy_final_nav"))),
        ("累计收益", _pct(summary.get("strategy_total_return_pct"))),
        ("沪深300累计", _pct(summary.get("benchmark_total_return_pct"))),
        ("最大回撤", _pct(summary.get("strategy_max_drawdown_pct"))),
        ("年化Sharpe", f"{_safe(summary.get('strategy_sharpe'))}"),
    ]
    metric_cells = "".join(
        f'<div class="pc-metric"><span>{_safe(title)}</span><strong>{value}</strong></div>'
        for title, value in metrics
    )
    annual = _annual_table(summary)

    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="historical-backtest-version" content="{_safe(version)}"><title>历史点状回测 · A股研究终端</title><style>{page_css}</style></head><body><main><header><div><h1>HISTORICAL BACKTEST</h1><p>模型评分的历史点状回测 · 2022 年以来</p></div><a href="index.html">← 返回最新研究简报</a></header>
<section class="card hero pc2"><div class="pc-status {_safe(tone)}"><b>{_safe(label)}</b><span>{_safe(explanation)}</span></div><div class="pc-metrics">{metric_cells}</div></section>
<section class="card pc2"><div class="section-head"><h2>01 / STRATEGY NAV vs CSI 300</h2><p>等权 Top{_safe(summary.get('top_k'))}、每 {_safe(summary.get('rebalance_days'))} 个交易日调仓；线性坐标，起点 1.0</p></div><div class="pc-panel">{_legend((("pc-strategy", "策略净值"), ("pc-benchmark", "沪深300")))}{nav_chart}</div></section>
<section class="card pc2"><div class="section-head"><h2>02 / DRAWDOWN</h2><p>从各自历史峰值回落（百分比）</p></div><div class="pc-panel">{_legend((("pc-strategy", "策略回撤"), ("pc-benchmark", "沪深300回撤")))}{dd_chart}</div></section>
<section class="card"><div class="section-head"><h2>03 / ANNUAL RETURNS</h2><p>按自然年汇总，含当年最大回撤与沪深300对照</p></div><div class="pc-panel">{annual or '<p style="color:var(--muted)">暂无年度数据。</p>'}</div></section>
<section class="card"><div class="section-head"><h2>METHODOLOGY / 口径</h2><p>为什么这张回测曲线不能当作可复制收益</p></div><div class="method"><ol><li>点状评分：每个调仓日只用当日及之前的K线，重新计算全市场评分；不读取未来数据。</li><li>所有指标均右对齐（rolling/ewm/cumsum）；唯一非因果项——成交量分布 HVN——在本回测口径中关闭，避免前视。</li><li>调仓日按最终分降序取等权 Top{_safe(summary.get('top_k'))}，持有至下一调仓日；每次调仓按约 {_safe(round(float(summary.get('cost_per_rebalance_pct', 0.25)), 2))}% 计入换手成本。</li><li>基准为沪深300（000300.SH），采用相同交易日与同一起点。</li><li>幸存者偏差：universe 来自当前缓存，可能缺失历史退市证券，此项偏差未消除、仅如实标注。</li><li>本页用于量化研究与模型监控，不构成投资建议或收益承诺。</li></ol></div></section>
<footer>本页用于量化研究与模型监控，不构成投资建议。 · {_safe(version or 'historical-backtest')}</footer></main></body></html>"""


def backtest_card_html(path: Path, *, detail_href: str = "backtest.html") -> str:
    summary = _read_payload(path).get("summary", {})
    if not summary:
        return ""
    rows = _sample(
        [r for r in _read_payload(path).get("rows", []) if isinstance(r, dict)],
        max_points=420,
    )
    label, tone, explanation = _state(summary)
    start = _date_label(summary.get("start"))
    end = _date_label(summary.get("end"))
    nav_chart = _chart_svg(
        rows,
        (
            ("StrategyNAV", "pc-strategy", "策略净值"),
            ("BenchmarkNAV", "pc-benchmark", "沪深300"),
        ),
        kind="nav",
        anchor=1.0,
        aria_label="历史回测策略净值与沪深300",
    ) if rows else ""
    body = f"""<section id="historical-backtest-v1" class="section card pc2"><div class="section-head pc-headrow"><div><h2>HISTORICAL BACKTEST / 历史点状回测</h2><p>2022 年以来模型评分的等权 Top{_safe(summary.get('top_k'))} 组合 vs 沪深300；研究口径，非券商账户收益</p></div><a class="pc-open" href="{_safe(detail_href)}">OPEN FULL BACKTEST →</a></div>
<div class="pc-status {_safe(tone)}"><b>{_safe(label)}</b><span>{_safe(explanation)}</span></div>
<div class="pc-metrics"><div class="pc-metric"><span>回测区间</span><strong>{_safe(start)}<br>{_safe(end)}</strong></div><div class="pc-metric"><span>策略累计</span><strong>{_pct(summary.get('strategy_total_return_pct'))}</strong></div><div class="pc-metric"><span>沪深300累计</span><strong>{_pct(summary.get('benchmark_total_return_pct'))}</strong></div><div class="pc-metric"><span>最终净值</span><strong>{_nav(summary.get('strategy_final_nav'))}</strong></div><div class="pc-metric"><span>最大回撤</span><strong>{_pct(summary.get('strategy_max_drawdown_pct'))}</strong></div><div class="pc-metric"><span>年化Sharpe</span><strong>{_safe(summary.get('strategy_sharpe'))}</strong></div></div>
<div class="pc-panel"><h3>STRATEGY NAV vs CSI 300</h3><p>等权 Top{_safe(summary.get('top_k'))}、{_safe(summary.get('rebalance_days'))} 交易日调仓；线性坐标。</p>{_legend((("pc-strategy", "策略净值"), ("pc-benchmark", "沪深300")))}{nav_chart}</div></section>"""
    return f'<style id="historical-backtest-v1-style">{_SHARED_CSS}</style>' + body


def write_backtest_page(page_path: Path, json_path: Path) -> Path:
    page_path = Path(page_path)
    page_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = page_path.with_name(f".{page_path.name}.tmp")
    try:
        temporary.write_text(backtest_page_html(_read_payload(json_path)), encoding="utf-8")
        os.replace(temporary, page_path)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
    return page_path


def inject_backtest_into_html(path: Path, json_path: Path) -> bool:
    detail_href = (
        "../backtest.html"
        if Path(path).parent.name == "reports"
        else "backtest.html"
    )
    fragment = backtest_card_html(json_path, detail_href=detail_href)
    if not fragment:
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    if 'id="historical-backtest-v1"' in text:
        return True
    markers = (
        '<section id="score-bucket-calibration-v93"',
        '<section id="what-changed-v93"',
        "</main>",
        "</body>",
    )
    for marker in markers:
        position = text.find(marker)
        if position >= 0:
            text = text[:position] + fragment + text[position:]
            break
    else:
        text += fragment
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        return False
    return True