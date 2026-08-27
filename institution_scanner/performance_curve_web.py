"""HTML/SVG renderer for PerformanceCurve.json.

The renderer is deliberately dependency-free so GitHub Pages publishing does
not require matplotlib/plotly.  It consumes only the exported diagnostics and
never recomputes production ranks or eligibility.
"""
from __future__ import annotations

import html
import json
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


def _read(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    rows = payload.get("rows", []) if isinstance(payload, dict) else []
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _polyline(rows: list[dict[str, Any]], key: str, width: int = 900, height: int = 150) -> str:
    values = [(_num(row.get(key)), index) for index, row in enumerate(rows)]
    valid = [(value, index) for value, index in values if value is not None]
    if len(valid) < 2:
        return ""
    lo = min(value for value, _ in valid)
    hi = max(value for value, _ in valid)
    spread = max(hi - lo, 1e-9)
    denom = max(len(rows) - 1, 1)
    pts = []
    for value, index in valid:
        x = 10.0 + index * (width - 20.0) / denom
        y = 8.0 + (hi - value) * (height - 16.0) / spread
        pts.append(f"{x:.1f},{y:.1f}")
    return " ".join(pts)


def _flag_rects(rows: list[dict[str, Any]], key: str, width: int = 900, height: int = 150) -> str:
    if not rows:
        return ""
    n = len(rows)
    step = (width - 20.0) / max(n, 1)
    rects = []
    for index, row in enumerate(rows):
        if bool(row.get(key, False)):
            x = 10.0 + index * step
            rects.append(
                f'<rect x="{x:.1f}" y="4" width="{max(step, 1.0):.1f}" height="{height - 8}" fill="currentColor" opacity="0.10"/>'
            )
    return "".join(rects)


def _metric(rows: list[dict[str, Any]], key: str, fmt: str = ".3f") -> str:
    for row in reversed(rows):
        value = _num(row.get(key))
        if value is not None:
            return format(value, fmt)
    return "—"


def performance_curve_html(path: Path) -> str:
    rows = _read(path)
    if len(rows) < 2:
        return ""
    # Keep the public page lightweight while retaining enough history to expose
    # regime transitions.  Sampling is deterministic and always includes the
    # latest observation.
    max_points = 420
    if len(rows) > max_points:
        stride = max(1, len(rows) // max_points)
        sampled = rows[::stride]
        if sampled[-1] is not rows[-1]:
            sampled.append(rows[-1])
        rows = sampled

    nav = _polyline(rows, "ResearchCohortNAV")
    ic = _polyline(rows, "RollingRankIC60")
    median = _polyline(rows, "ICMedian")
    canary = _polyline(rows, "BetaCanaryNAV")
    ic_flags = _flag_rects(rows, "ICRiskFlag")
    beta_flags = _flag_rects(rows, "BetaRiskFlag")
    start = _safe(rows[0].get("Date", "—"))
    end = _safe(rows[-1].get("Date", "—"))
    nav_last = _metric(rows, "ResearchCohortNAV", ".2f")
    dd_last = _metric(rows, "ResearchCohortDrawdown", ".1f")
    ic_last = _metric(rows, "RollingRankIC60", "+.3f")
    canary_last = _metric(rows, "BetaCanarySpread20", "+.2f")

    css = '''<style id="performance-curve-v1-style">
.pc1{margin-bottom:16px}.pc1 .pc-meta{display:flex;gap:10px;flex-wrap:wrap;padding:8px 12px;border-bottom:1px solid var(--line);font-size:9px;color:var(--muted)}.pc1 .pc-meta strong{color:var(--ink)}
.pc-panel{padding:10px 12px;border-top:1px solid var(--line)}.pc-panel:first-of-type{border-top:0}.pc-panel h3{margin:0 0 4px;font:700 10px ui-monospace,Consolas,monospace}.pc-panel p{margin:0 0 7px;color:var(--muted);font-size:9px}.pc-svg{display:block;width:100%;height:auto;overflow:visible}.pc-grid{stroke:var(--line);stroke-width:1}.pc-line{fill:none;stroke:var(--ink);stroke-width:2}.pc-line-alt{fill:none;stroke:var(--muted);stroke-width:1.2;stroke-dasharray:5 4}.pc-risk{color:#d65a45}.pc-beta{color:#d5a33f}
</style>'''
    body = f'''<section id="performance-curves-v1" class="section card pc1"><div class="section-head"><h2>MODEL HEALTH CURVES / 模型健康度曲线</h2><p>SignalHistory 的 point-in-time 纵向诊断；不把重叠20D样本伪装成逐日可交易净值</p></div><div class="pc-meta"><span>区间 <strong>{start} → {end}</strong></span><span>研究队列NAV <strong>{nav_last}</strong></span><span>当前回撤 <strong>{dd_last}%</strong></span><span>60D Rolling IC <strong>{ic_last}</strong></span><span>Beta Canary Spread <strong>{canary_last}%</strong></span></div>
<div class="pc-panel"><h3>RESEARCH COHORT NAV / 研究队列净值代理</h3><p>按信号日横截面等权聚合，并将20D结果折算为日等效诊断收益；用于模型健康监控，不宣称为真实成交组合。</p><svg class="pc-svg" viewBox="0 0 900 150" role="img"><line class="pc-grid" x1="10" y1="75" x2="890" y2="75"/><polyline class="pc-line" points="{nav}"/></svg></div>
<div class="pc-panel pc-risk"><h3>ROLLING 60D RANK IC / 模型预测力</h3><p>红色带表示 Rolling IC &lt; 0；虚线为样本期历史中位数。</p><svg class="pc-svg" viewBox="0 0 900 150" role="img">{ic_flags}<line class="pc-grid" x1="10" y1="75" x2="890" y2="75"/><polyline class="pc-line-alt" points="{median}"/><polyline class="pc-line" points="{ic}"/></svg></div>
<div class="pc-panel pc-beta"><h3>BETA CANARY / 风险偏好代理</h3><p>当前版本使用现有研究字段构造高风险队列代理；橙色带表示20日滚动 Canary spread 为负。</p><svg class="pc-svg" viewBox="0 0 900 150" role="img">{beta_flags}<line class="pc-grid" x1="10" y1="75" x2="890" y2="75"/><polyline class="pc-line" points="{canary}"/></svg></div></section>'''
    return css + body
