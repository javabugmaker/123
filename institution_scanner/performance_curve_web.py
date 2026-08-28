"""Dependency-free HTML/SVG renderer for forward performance diagnostics.

The public page deliberately distinguishes a forward research-cohort proxy from
a broker-replicable portfolio NAV. It never derives Sharpe or CAGR from
overlapping horizon labels, and it exposes sample maturity before showing a
performance conclusion.
"""
from __future__ import annotations

import html
import json
import os
import re
from contextlib import suppress
from pathlib import Path
from typing import Any


def _safe(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _num(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _read_payload(path: Path) -> tuple[str, list[dict[str, Any]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "", []
    if not isinstance(payload, dict):
        return "", []
    rows = payload.get("rows", [])
    if not isinstance(rows, list):
        return str(payload.get("version", "") or ""), []
    return (
        str(payload.get("version", "") or ""),
        [row for row in rows if isinstance(row, dict)],
    )


def _read(path: Path) -> list[dict[str, Any]]:
    return _read_payload(path)[1]


def _sample(
    rows: list[dict[str, Any]],
    max_points: int = 420,
) -> list[dict[str, Any]]:
    if len(rows) <= max_points:
        return rows
    stride = max(1, len(rows) // max_points)
    sampled = rows[::stride]
    if sampled[-1] is not rows[-1]:
        sampled.append(rows[-1])
    return sampled


def _metric(rows: list[dict[str, Any]], key: str) -> float | None:
    for row in reversed(rows):
        value = _num(row.get(key))
        if value is not None:
            return value
    return None


def _integer_metric(rows: list[dict[str, Any]], key: str) -> int:
    value = _metric(rows, key)
    return max(0, int(value)) if value is not None else 0


def _date_label(value: object, *, compact: bool = False) -> str:
    raw = str(value or "").strip()
    if len(raw) >= 10 and raw[4:5] == "-" and raw[7:8] == "-":
        return raw[5:10] if compact else raw[:10]
    return raw or "—"


def _fmt(value: float | None, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "pct":
        return f"{value:+.1f}%"
    if kind == "ic":
        return f"{value:+.3f}"
    return f"{value:.2f}"


def _tick(value: float, kind: str) -> str:
    if kind == "pct":
        return f"{value:.1f}%"
    if kind == "ic":
        return f"{value:+.2f}"
    return f"{value:.2f}"


def _coverage_state(rows: list[dict[str, Any]]) -> tuple[str, str, str]:
    matured = _integer_metric(rows, "MaturedDates20")
    rolling_ic = _metric(rows, "RollingRankIC60")
    drawdown = _metric(rows, "ResearchCohortDrawdown")
    if matured == 0:
        return (
            "WARM-UP",
            "warm",
            "尚无完成 20 个交易日观察窗的前瞻队列，当前不评价收益优劣。",
        )
    if matured < 20:
        return (
            "EARLY SAMPLE",
            "warm",
            f"已有 {matured} 个成熟信号日，曲线可观察但不足以做稳定性结论。",
        )
    if rolling_ic is not None and rolling_ic < 0.0:
        return (
            "IC DEGRADED",
            "risk",
            "滚动 Rank IC 低于零，近期排序力需要继续复核。",
        )
    if drawdown is not None and drawdown <= -10.0:
        return (
            "DRAWDOWN WATCH",
            "risk",
            "研究队列净值代理处于两位数回撤，先看失效来源与样本结构。",
        )
    return (
        "MONITORABLE",
        "good",
        "前瞻样本达到最低观察门槛；仍应结合基准、回撤与 IC，而不是只看净值。",
    )


def _analysis_text(rows: list[dict[str, Any]]) -> str:
    _label, _tone, explanation = _coverage_state(rows)
    matured_samples = _integer_metric(rows, "MaturedSamples20Cumulative")
    benchmark_available = any(
        _num(row.get("BenchmarkCohortReturn20")) is not None for row in rows
    )
    benchmark_note = (
        "沪深300同窗基准已接入。"
        if benchmark_available
        else "沪深300同窗结果尚未成熟，基准线暂不作比较。"
    )
    return f"{explanation} 已成熟个股样本 {matured_samples} 条；{benchmark_note}"


def _segments(
    rows: list[dict[str, Any]],
    key: str,
    *,
    x_at: Any,
    y_at: Any,
) -> list[str]:
    segments: list[list[str]] = []
    current: list[str] = []
    for index, row in enumerate(rows):
        value = _num(row.get(key))
        if value is None:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(f"{x_at(index):.1f},{y_at(value):.1f}")
    if current:
        segments.append(current)
    return [" ".join(segment) for segment in segments if len(segment) >= 2]


def _flag_rects(
    rows: list[dict[str, Any]],
    key: str,
    *,
    x_at: Any,
    plot_top: float,
    plot_height: float,
    plot_width: float,
) -> str:
    if not rows:
        return ""
    step = plot_width / max(len(rows), 1)
    output: list[str] = []
    for index, row in enumerate(rows):
        if bool(row.get(key, False)):
            output.append(
                f'<rect class="pc-risk-band" x="{x_at(index) - step / 2:.1f}" '
                f'y="{plot_top:.1f}" width="{max(step, 1.0):.1f}" '
                f'height="{plot_height:.1f}" fill="#e65c5c" opacity="0.10"/>'
            )
    return "".join(output)


def _chart_svg(
    rows: list[dict[str, Any]],
    series: tuple[tuple[str, str, str], ...],
    *,
    kind: str,
    anchor: float | None = None,
    flag_key: str = "",
    aria_label: str,
) -> str:
    width = 1000.0
    height = 280.0
    left, right, top, bottom = 68.0, 22.0, 18.0, 38.0
    plot_width = width - left - right
    plot_height = height - top - bottom
    values = [
        value
        for row in rows
        for key, _css_class, _label in series
        if (value := _num(row.get(key))) is not None
    ]
    if not values:
        return (
            f'<svg class="pc-chart" viewBox="0 0 {int(width)} {int(height)}" '
            f'role="img" aria-label="{_safe(aria_label)}">'
            '<text class="pc-empty" x="500" y="140" text-anchor="middle" '
            'fill="#6b7078">'
            "等待成熟样本 / WAITING FOR MATURE OUTCOMES</text></svg>"
        )
    if anchor is not None:
        values.append(anchor)

    lo, hi = min(values), max(values)
    pad = (
        max(abs(hi) * 0.04, 0.04 if kind != "ic" else 0.02)
        if abs(hi - lo) < 1e-9
        else (hi - lo) * 0.10
    )
    lo -= pad
    hi += pad

    def x_at(index: int) -> float:
        return left + index * plot_width / max(len(rows) - 1, 1)

    def y_at(value: float) -> float:
        return top + (hi - value) * plot_height / max(hi - lo, 1e-9)

    elements: list[str] = []
    if flag_key:
        elements.append(
            _flag_rects(
                rows,
                flag_key,
                x_at=x_at,
                plot_top=top,
                plot_height=plot_height,
                plot_width=plot_width,
            )
        )
    for tick_index in range(5):
        value = hi - tick_index * (hi - lo) / 4.0
        y = y_at(value)
        elements.append(
            f'<line class="pc-grid" x1="{left:.1f}" y1="{y:.1f}" '
            f'x2="{width - right:.1f}" y2="{y:.1f}" '
            f'stroke="#dfe3e8" stroke-width="1"/>'
            f'<text class="pc-axis" x="{left - 10:.1f}" y="{y + 4:.1f}" '
            f'text-anchor="end" fill="#6b7078">'
            f'{_safe(_tick(value, kind))}</text>'
        )
    if anchor is not None and lo <= anchor <= hi:
        y = y_at(anchor)
        elements.append(
            f'<line class="pc-zero" x1="{left:.1f}" y1="{y:.1f}" '
            f'x2="{width - right:.1f}" y2="{y:.1f}" stroke="#9aa1aa" '
            f'stroke-width="1" stroke-dasharray="5 5"/>'
        )
    stroke_colours = {
        "pc-strategy": "#b52b32",
        "pc-benchmark": "#4f5968",
        "pc-excess": "#2477b3",
        "pc-ic": "#2477b3",
        "pc-canary": "#c88719",
    }
    for key, css_class, _label in series:
        for points in _segments(rows, key, x_at=x_at, y_at=y_at):
            dash = ' stroke-dasharray="7 5"' if css_class == "pc-benchmark" else ""
            elements.append(
                f'<polyline class="pc-line {css_class}" points="{points}" '
                f'fill="none" stroke="{stroke_colours.get(css_class, "#15171a")}" '
                f'stroke-width="2.4" stroke-linecap="round" '
                f'stroke-linejoin="round"{dash}/>'
            )
    if rows:
        label_positions = sorted({0, len(rows) // 2, len(rows) - 1})
        elements.extend(
            (
                f'<text class="pc-axis" x="{x_at(index):.1f}" '
                f'y="{height - 10:.1f}" text-anchor="middle" fill="#6b7078">'
                f'{_safe(_date_label(rows[index].get("Date"), compact=True))}</text>'
            )
            for index in label_positions
        )
    return (
        f'<svg class="pc-chart" viewBox="0 0 {int(width)} {int(height)}" '
        f'role="img" aria-label="{_safe(aria_label)}">'
        + "".join(elements)
        + "</svg>"
    )


def _legend(items: tuple[tuple[str, str], ...]) -> str:
    return '<div class="pc-legend">' + "".join(
        f'<span><i class="{_safe(css_class)}"></i>{_safe(label)}</span>'
        for css_class, label in items
    ) + "</div>"


_SHARED_CSS = """
.pc2{--pc-accent:#b52b32;--pc-benchmark:#4f5968;--pc-blue:#2477b3;--pc-amber:#c88719}
.pc2 .pc-headrow{display:flex;justify-content:space-between;gap:16px;align-items:flex-start}
.pc2 .pc-open{white-space:nowrap;color:var(--pc-accent);font:700 10px ui-monospace,Consolas,monospace;text-decoration:none}
.pc2 .pc-status{display:flex;gap:10px;align-items:center;padding:10px 12px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);font-size:10px}
.pc2 .pc-status b{font:800 10px ui-monospace,Consolas,monospace;letter-spacing:.05em;padding:4px 7px;border:1px solid currentColor;border-radius:999px}
.pc2 .pc-status.warm b{color:#9a6a12}.pc2 .pc-status.risk b{color:#b52b32}.pc2 .pc-status.good b{color:#18734a}
.pc2 .pc-metrics{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));border-bottom:1px solid var(--line)}
.pc2 .pc-metric{padding:10px 12px;border-right:1px solid var(--line)}.pc2 .pc-metric:last-child{border-right:0}
.pc2 .pc-metric span{display:block;color:var(--muted);font-size:8px}.pc2 .pc-metric strong{font:800 15px ui-monospace,Consolas,monospace}
.pc2 .pc-panel{padding:12px;border-top:1px solid var(--line)}.pc2 .pc-panel:first-of-type{border-top:0}
.pc2 .pc-panel h3{margin:0;font:800 11px ui-monospace,Consolas,monospace}.pc2 .pc-panel p{margin:3px 0 8px;color:var(--muted);font-size:9px}
.pc2 .pc-chart{display:block;width:100%;height:auto;min-height:120px}.pc2 .pc-grid{stroke:var(--line);stroke-width:1}.pc2 .pc-zero{stroke:#9aa1aa;stroke-width:1;stroke-dasharray:5 5}.pc2 .pc-axis{fill:var(--muted);font:10px ui-monospace,Consolas,monospace}.pc2 .pc-empty{fill:var(--muted);font:700 13px ui-monospace,Consolas,monospace}
.pc2 .pc-line{fill:none;stroke-width:2.4;stroke-linecap:round;stroke-linejoin:round}.pc2 .pc-strategy{stroke:var(--pc-accent)}.pc2 .pc-benchmark{stroke:var(--pc-benchmark);stroke-dasharray:7 5}.pc2 .pc-excess{stroke:var(--pc-blue)}.pc2 .pc-ic{stroke:var(--pc-blue)}.pc2 .pc-canary{stroke:var(--pc-amber)}.pc2 .pc-risk-band{fill:#e65c5c;opacity:.10}
.pc2 .pc-legend{display:flex;gap:14px;flex-wrap:wrap;margin:0 0 4px;color:var(--muted);font-size:9px}.pc2 .pc-legend span{display:flex;align-items:center;gap:5px}.pc2 .pc-legend i{width:18px;height:2px;display:inline-block;background:currentColor}.pc2 .pc-legend i.pc-strategy{color:var(--pc-accent)}.pc2 .pc-legend i.pc-benchmark{color:var(--pc-benchmark);border-top:1px dashed currentColor;background:none}.pc2 .pc-legend i.pc-excess,.pc2 .pc-legend i.pc-ic{color:var(--pc-blue)}.pc2 .pc-legend i.pc-canary{color:var(--pc-amber)}
.pc2 .pc-progress{height:8px;border-radius:999px;background:var(--line);overflow:hidden;margin-top:8px}.pc2 .pc-progress i{display:block;height:100%;background:var(--pc-accent)}
@media(max-width:760px){.pc2 .pc-headrow{display:block}.pc2 .pc-open{display:inline-block;margin-top:6px}.pc2 .pc-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.pc2 .pc-metric{border-bottom:1px solid var(--line)}.pc2 .pc-status{align-items:flex-start}}
"""


def performance_curve_html(
    path: Path,
    *,
    detail_href: str = "performance.html",
) -> str:
    rows = _sample(_read(path))
    if not rows:
        return ""
    label, tone, explanation = _coverage_state(rows)
    matured_dates = _integer_metric(rows, "MaturedDates20")
    progress = min(100.0, matured_dates / 20.0 * 100.0)
    start = _date_label(rows[0].get("Date"))
    end = _date_label(rows[-1].get("Date"))
    chart_rows = rows if matured_dates else []
    nav = _metric(rows, "ResearchCohortNAV") if matured_dates else None
    benchmark = _metric(rows, "BenchmarkNAV") if matured_dates else None
    drawdown = _metric(rows, "ResearchCohortDrawdown") if matured_dates else None
    rolling_ic = _metric(rows, "RollingRankIC60")
    chart = _chart_svg(
        chart_rows,
        (
            ("ResearchCohortNAV", "pc-strategy", "研究队列NAV代理"),
            ("BenchmarkNAV", "pc-benchmark", "沪深300同窗基准"),
        ),
        kind="nav",
        anchor=1.0,
        aria_label="前瞻研究队列净值代理与沪深300同窗基准",
    )
    body = f"""<section id="performance-curves-v2" class="section card pc2"><div class="section-head pc-headrow"><div><h2>FORWARD PERFORMANCE / 前瞻绩效</h2><p>只使用发布后逐步成熟的 SignalHistory；不把历史筛选样本包装成可交易净值</p></div><a class="pc-open" href="{_safe(detail_href)}">OPEN FULL AUDIT →</a></div>
<div class="pc-status {_safe(tone)}"><b>{_safe(label)}</b><span>{_safe(explanation)}</span></div>
<div class="pc-metrics"><div class="pc-metric"><span>观察区间</span><strong>{_safe(start)}<br>{_safe(end)}</strong></div><div class="pc-metric"><span>成熟信号日</span><strong>{matured_dates}/20</strong><div class="pc-progress"><i style="width:{progress:.1f}%"></i></div></div><div class="pc-metric"><span>研究NAV代理</span><strong>{_safe(_fmt(nav, 'nav'))}</strong></div><div class="pc-metric"><span>沪深300同窗</span><strong>{_safe(_fmt(benchmark, 'nav'))}</strong></div><div class="pc-metric"><span>当前回撤</span><strong>{_safe(_fmt(drawdown, 'pct'))}</strong></div><div class="pc-metric"><span>60D Rolling IC</span><strong>{_safe(_fmt(rolling_ic, 'ic'))}</strong></div></div>
<div class="pc-panel"><h3>FORWARD COHORT NAV PROXY</h3><p>20D 已实现收益折算为日等效诊断序列；它是模型监控代理，不是券商账户收益。</p>{_legend((("pc-strategy", "研究队列NAV代理"), ("pc-benchmark", "沪深300同窗基准")))}{chart}</div></section>"""
    return f'<style id="performance-curve-v2-style">{_SHARED_CSS}</style>' + body


def performance_page_html(path: Path) -> str:
    version, raw_rows = _read_payload(path)
    rows = _sample(raw_rows, max_points=600)
    label, tone, explanation = _coverage_state(rows)
    start = _date_label(rows[0].get("Date")) if rows else "—"
    end = _date_label(rows[-1].get("Date")) if rows else "—"
    matured_dates = _integer_metric(rows, "MaturedDates20")
    matured_samples = _integer_metric(rows, "MaturedSamples20Cumulative")
    chart_rows = rows if matured_dates else []
    nav = _metric(rows, "ResearchCohortNAV") if matured_dates else None
    benchmark = _metric(rows, "BenchmarkNAV") if matured_dates else None
    excess = _metric(rows, "ResearchExcessNAV") if matured_dates else None
    progress = min(100.0, matured_dates / 20.0 * 100.0)

    nav_chart = _chart_svg(
        chart_rows,
        (
            ("ResearchCohortNAV", "pc-strategy", "研究队列NAV代理"),
            ("BenchmarkNAV", "pc-benchmark", "沪深300同窗基准"),
        ),
        kind="nav",
        anchor=1.0,
        aria_label="前瞻研究队列净值代理与沪深300同窗基准",
    )
    drawdown_chart = _chart_svg(
        chart_rows,
        (
            ("ResearchCohortDrawdown", "pc-strategy", "研究队列回撤"),
            ("BenchmarkDrawdown", "pc-benchmark", "沪深300回撤"),
        ),
        kind="pct",
        anchor=0.0,
        aria_label="研究队列与沪深300同窗回撤",
    )
    ic_chart = _chart_svg(
        chart_rows,
        (
            ("RollingRankIC60", "pc-ic", "60D Rolling Rank IC"),
            ("ICMedian", "pc-benchmark", "历史中位数"),
        ),
        kind="ic",
        anchor=0.0,
        flag_key="ICRiskFlag",
        aria_label="滚动Rank IC与负值风险区间",
    )
    canary_chart = _chart_svg(
        chart_rows,
        (("BetaCanaryNAV", "pc-canary", "高风险队列代理"),),
        kind="nav",
        anchor=1.0,
        flag_key="BetaRiskFlag",
        aria_label="高风险队列风险偏好代理",
    )
    analysis = (
        _analysis_text(rows)
        if rows
        else "尚未生成 SignalHistory 纵向诊断数据。"
    )

    page_css = f"""
:root{{--bg:#f1f2f4;--paper:#fff;--ink:#15171a;--muted:#6b7078;--line:#dfe3e8;--accent:#b52b32}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei UI",sans-serif}}
main{{max-width:1120px;margin:auto;padding:28px 20px 72px}}header{{display:flex;justify-content:space-between;gap:20px;align-items:flex-end;border-bottom:3px solid var(--accent);padding-bottom:14px;margin-bottom:18px}}h1{{margin:0;font:800 30px/1.1 ui-monospace,Consolas,monospace}}header p{{margin:7px 0 0;color:var(--muted)}}a{{color:var(--accent);text-decoration:none;font-weight:700}}.card{{background:var(--paper);border:1px solid var(--line);margin:14px 0}}.hero{{padding:18px}}.hero .pc-status{{border:0;padding:0 0 12px}}.analysis{{margin-top:12px;padding:12px 14px;border-left:3px solid var(--accent);background:#faf6f6}}.section-head{{padding:13px 14px;border-bottom:1px solid var(--line)}}.section-head h2{{margin:0;font:800 14px ui-monospace,Consolas,monospace}}.section-head p{{margin:4px 0 0;color:var(--muted);font-size:11px}}.method{{padding:14px 18px}}.method ol{{margin:0;padding-left:20px}}.method li{{margin:6px 0}}footer{{margin-top:22px;color:var(--muted);font-size:11px}}{_SHARED_CSS}
@media(max-width:760px){{header{{display:block}}header a{{display:inline-block;margin-top:10px}}h1{{font-size:24px}}}}
"""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="performance-curve-version" content="{_safe(version)}"><title>前瞻绩效 · A股研究终端</title><style>{page_css}</style></head><body><main><header><div><h1>FORWARD PERFORMANCE</h1><p>前瞻绩效与模型健康审计</p></div><a href="index.html">← 返回最新研究简报</a></header>
<section class="card hero pc2"><div class="pc-status {_safe(tone)}"><b>{_safe(label)}</b><span>{_safe(explanation)}</span></div><div class="pc-metrics"><div class="pc-metric"><span>观察区间</span><strong>{_safe(start)}<br>{_safe(end)}</strong></div><div class="pc-metric"><span>成熟信号日</span><strong>{matured_dates}/20</strong><div class="pc-progress"><i style="width:{progress:.1f}%"></i></div></div><div class="pc-metric"><span>成熟个股样本</span><strong>{matured_samples}</strong></div><div class="pc-metric"><span>研究NAV代理</span><strong>{_safe(_fmt(nav, 'nav'))}</strong></div><div class="pc-metric"><span>沪深300同窗</span><strong>{_safe(_fmt(benchmark, 'nav'))}</strong></div><div class="pc-metric"><span>相对净值代理</span><strong>{_safe(_fmt(excess, 'nav'))}</strong></div></div><div class="analysis"><strong>当前解读：</strong>{_safe(analysis)}</div></section>
<section class="card pc2"><div class="section-head"><h2>01 / FORWARD COHORT NAV PROXY</h2><p>前瞻队列净值代理 vs 沪深300同窗基准；线性坐标，不用对数轴放大视觉效果</p></div><div class="pc-panel">{_legend((("pc-strategy", "研究队列NAV代理"), ("pc-benchmark", "沪深300同窗基准")))}{nav_chart}</div></section>
<section class="card pc2"><div class="section-head"><h2>02 / DRAWDOWN</h2><p>从各自历史峰值回落；只对已成熟的前瞻结果负责</p></div><div class="pc-panel">{_legend((("pc-strategy", "研究队列回撤"), ("pc-benchmark", "沪深300回撤")))}{drawdown_chart}</div></section>
<section class="card pc2"><div class="section-head"><h2>03 / ROLLING RANK IC</h2><p>每日横截面 Spearman；红色带表示滚动 IC 低于零</p></div><div class="pc-panel">{_legend((("pc-ic", "60D Rolling Rank IC"), ("pc-benchmark", "历史中位数")))}{ic_chart}</div></section>
<section class="card pc2"><div class="section-head"><h2>04 / RISK-APPETITE CANARY</h2><p>由现有研究字段构造的高风险队列代理；不是传统市场 Beta，也不进入生产排序</p></div><div class="pc-panel">{_legend((("pc-canary", "高风险队列代理"),))}{canary_chart}</div></section>
<section class="card"><div class="section-head"><h2>METHODOLOGY / 口径</h2><p>为什么这页没有照搬夸张收益、Sharpe 与“冠军策略”标签</p></div><div class="method"><ol><li>仅使用每次公开扫描后写入的 SignalHistory，20D/60D 结果必须等观察窗真实走完。</li><li>同一信号日先做横截面等权聚合，再按持有期折算为诊断序列；不会把数千条重叠样本当成独立日收益。</li><li>沪深300采用相同起止交易日；基准未成熟时显示空缺，不用零收益代替。</li><li>当前仍是研究队列 NAV 代理。没有逐日持仓、换手与成交账本前，不发布 Sharpe、CAGR 或“可复制收益”。</li></ol></div></section>
<footer>本页用于量化研究与模型监控，不构成投资建议或收益承诺。 · {_safe(version or 'performance-curve')}</footer></main></body></html>"""


def write_performance_page(page_path: Path, curve_json: Path) -> Path:
    page_path = Path(page_path)
    page_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = page_path.with_name(f".{page_path.name}.tmp")
    try:
        temporary.write_text(performance_page_html(curve_json), encoding="utf-8")
        os.replace(temporary, page_path)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)
    return page_path


def inject_into_html(path: Path, curve_json: Path) -> bool:
    """Insert a compact audit card and link to the standalone performance page."""
    detail_href = (
        "../performance.html"
        if Path(path).parent.name == "reports"
        else "performance.html"
    )
    fragment = performance_curve_html(curve_json, detail_href=detail_href)
    if not fragment:
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    if 'id="performance-curves-v2"' in text:
        return True
    text = re.sub(
        r'<style id="performance-curve-v1-style">.*?</section>',
        "",
        text,
        count=1,
        flags=re.S,
    )
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
