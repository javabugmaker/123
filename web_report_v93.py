"""v93 public Research Console layered on the validated v92/v90 report.

Presentation-only adapter: production scores, eligibility and ranks are never
recomputed. It adds Top Opportunities, Signal/Confidence/Risk axes, exact
published-pool deltas, deterministic explanations, and held-out/walk-forward
calibration visibility.
"""
from __future__ import annotations

import csv
import html
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import web_report_v90 as _v92
from web_report_v90 import *  # noqa: F403

WEB_REPORT_VERSION = '2026-08-23-v93-research-console-v1'
WebReportResult = _v92.WebReportResult
DEFAULT_OUTPUT_DIR = _v92.DEFAULT_OUTPUT_DIR
DEFAULT_SITE_DIR = _v92.DEFAULT_SITE_DIR
PROJECT_ROOT = _v92.PROJECT_ROOT
WEB_PUBLISH_ENV = _v92.WEB_PUBLISH_ENV
GH_PAGES_BRANCH = _v92.GH_PAGES_BRANCH
_archive_html = _v92._archive_html
_published_source_dir = _v92._published_source_dir
is_canonical_output_dir = _v92.is_canonical_output_dir
github_pages_url_from_remote = _v92.github_pages_url_from_remote
publish_site = _v92.publish_site
_FILES = ('AllResults.csv', 'DecisionResults.csv', 'Top50Mixed.csv', 'Top50Stocks.csv')
_FIELDS = ('Ticker', 'Name', 'Close', 'ResearchRank', 'CandidateViewRank', 'ResearchPoolRank', 'OverallRank', 'RankingScore', 'CompositeScore', 'AlphaScore', 'FinalScore', 'SmoothTriggerScore', 'SmoothAlphaScore', 'ScoreConfidence', 'EntrySignal', 'SignalStatus', 'SignalDays', 'ExecutionState', 'RankingEligibility', 'QualityLayerStatus', 'InstitutionalTier', 'BacktestConfidenceTier', 'BacktestSamples', 'BacktestWinRate20D', 'BacktestWinRate60D', 'StopLoss', 'BreakoutBuyPrice', 'RewardRiskRatio', 'TradeReadinessReason', 'DecisionReason', 'RankingReason', 'DirectionalResearchEligible', 'DirectionalResearchReason', 'BreakoutPriceGatePassed', 'BreakoutPriceGateReason', 'TradeEconomicsPassed', 'TradeEconomicsReason', 'DataAsOf', 'TrendScore', 'AccumulationScore', 'IndustryRelativeStrength', 'SignalCount', 'BreakoutQualityFactor', 'SectorConfirmationFactor', 'FailureSignalFactor', 'ChaseRiskScore', 'ChaseRiskLevel', 'ChaseRiskReason', 'DataFreshnessStatus', 'DataFreshnessReason', 'TradeLiquidityPassed', 'TradeLiquidityReason', 'ScoreCoverage', 'QualityDataCompleteness', 'BreakoutVolumeRatio', 'BreakoutVolumeConfirmed', 'BreakoutFlowConfirmed', 'MarketRegime', 'MarketRegimeFast', 'MarketRegimeSlow', 'MarketRegimeConfidence')
_SIG = {'BUY_NOW': '回调可买', 'BREAKOUT_CONFIRM': '突破确认', 'WAIT_PULLBACK': '等待回调', 'PRICE_BREAKOUT': '价格突破待放量', 'WAIT_VOLUME_CONFIRM': '等待量能确认', 'HOLD_WAIT': '继续观察', 'AVOID': '回避'}
_CSS = '<style id="research-console-v93-style">\n.console-v93{margin-bottom:16px}.console-v93 .meta93{display:flex;gap:8px;flex-wrap:wrap;padding:8px 12px;border-bottom:1px solid var(--line);background:#fafafa;color:var(--muted);font-size:9px}.console-v93 .meta93 strong{color:var(--ink)}\n.console-v93 td{vertical-align:middle}.console-v93 .axis93 b{font:700 11px ui-monospace,Consolas,monospace}.console-v93 .axis93 small{display:block;color:var(--muted);font-size:8px}.console-v93 .why93{max-width:340px;white-space:normal;text-align:left;line-height:1.45;font-size:10px}.spark93{display:block;margin:auto}\n.good93{color:var(--red-dark);font-weight:700}.mid93{color:var(--amber);font-weight:700}.bad93{color:var(--green);font-weight:700}.muted93{color:var(--muted)}\n.grid93{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:var(--line)}.card93{background:#fff;padding:10px 12px;min-height:76px}.card93 span,.card93 small{display:block;color:var(--muted);font-size:9px}.card93 strong{display:block;margin:4px 0;font:700 18px ui-monospace,Consolas,monospace}\n.lists93{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:var(--line);border-top:1px solid var(--line)}.list93{background:#fff;padding:10px 12px;min-height:104px}.list93 h3{margin:0 0 7px;font:700 9px ui-monospace,Consolas,monospace}.list93 div{font-size:9px;line-height:1.65;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.list93 em{font-style:normal;color:var(--muted);margin-left:5px}\n.cal93{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--line)}.cal93 div{background:#fff;padding:9px 11px}.cal93 span,.cal93 small{display:block;color:var(--muted);font-size:8px}.cal93 strong{display:block;margin:3px 0;font:700 12px ui-monospace,Consolas,monospace}\n.drawer93{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:8px}.drawer93 section{background:#fff;border:1px solid var(--line);padding:9px}.drawer93 h4{margin:0 0 5px;font:700 9px ui-monospace,Consolas,monospace}.drawer93 p{margin:0;color:var(--muted);font-size:10px;line-height:1.55}.drawer93 .invalid93{grid-column:1/-1;border-left:3px solid var(--green)}\n@media(max-width:900px){.grid93,.lists93{grid-template-columns:repeat(2,1fr)}.drawer93{grid-template-columns:1fr}.drawer93 .invalid93{grid-column:auto}}@media(max-width:620px){.grid93,.lists93{grid-template-columns:1fr}}\n</style>'
_JS = '<script id="research-console-v93-js">\nconst RC93=JSON.parse(document.getElementById(\'研究控制台数据\').textContent||\'{}\');\nif(typeof 打开===\'function\'){const old93=打开;打开=function(t){old93(t);const c=RC93[t];if(!c)return;const g=document.getElementById(\'详情格\');if(g){const s=c.s===null?\'—\':`${Number(c.s).toFixed(1)} / 100`;g.insertAdjacentHTML(\'beforeend\',详情项(\'SIGNAL / 信号强度\',s)+详情项(\'CONFIDENCE / 可信度\',c.c||\'UNCALIBRATED\')+详情项(\'RISK / 执行风险\',c.r||\'WATCH\')+可选详情项(\'较上期变化\',c.d||\'\')+可选详情项(\'信号持续\',c.p||\'\'));}const e=document.getElementById(\'解释\');if(e){const o=e.textContent||\'\';e.innerHTML=`<div class="drawer93"><section><h4>WHY NOW</h4><p>${安全(c.wn||\'—\')}</p></section><section><h4>WHY NOT</h4><p>${安全(c.wt||\'—\')}</p></section><section><h4>VALIDATION</h4><p>${安全(c.v||\'—\')}</p></section><section class="invalid93"><h4>INVALIDATION</h4><p>${安全(c.i||\'—\')}</p></section></div>${o&&o!==\'—\'?`<div style="margin-top:8px;color:#6b7078;font-size:9px">模型诊断：${安全(o)}</div>`:\'\'}`;}}}\n</script>'


def _safe(v: object) -> str:
    return html.escape('' if v is None else str(v), quote=True)


def _num(v: object) -> float | None:
    try:
        x = float(str(v).replace(',', '').strip())
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float('inf') else None


def _bool(v: object, yes: bool=True) -> bool:
    text = str(v or '').strip().lower()
    values = {'1', 'true', 'yes', 'y', '是', 'pass', 'passed'} if yes else {'0', 'false', 'no', 'n', '否', 'fail', 'failed'}
    return text in values


def _score(v: object) -> float | None:
    x = _num(v)
    if x is not None and 0 <= x <= 1:
        x *= 100
    return x if x is not None and 0 <= x <= 100 else None


def _pct_ratio(v: object) -> str:
    x = _num(v)
    if x is None:
        return '—'
    if abs(x) <= 1.5:
        x *= 100
    return f'{x:.1f}%'


def _pct_point(v: object) -> str:
    x = _num(v)
    return '—' if x is None else f'{x:+.2f}%'


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _embedded(text: str, element_id: str) -> dict[str, Any]:
    m = re.search(f'<script id="{re.escape(element_id)}"[^>]*>(.*?)</script>', text, re.S)
    if not m:
        return {}
    try:
        value = json.loads(m.group(1))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _rows(source: Path, output: Path) -> list[dict[str, str]]:
    roots = (source,) if source.resolve() == output.resolve() else (source, output)
    for root in roots:
        for name in _FILES:
            path = root / name
            if not path.is_file():
                continue
            try:
                with path.open('r', encoding='utf-8-sig', newline='') as handle:
                    result = []
                    for raw in csv.DictReader(handle):
                        ticker = str(raw.get('Ticker', '') or '').strip().upper()
                        if ticker:
                            row = {k: str(raw.get(k, '') or '').strip() for k in _FIELDS if k in raw}
                            row['Ticker'] = ticker
                            result.append(row)
                if result:
                    return result
            except (OSError, UnicodeError, csv.Error):
                continue
    return []


def _rank(row: dict[str, str]) -> float:
    for key in ('ResearchRank', 'CandidateViewRank', 'ResearchPoolRank', 'OverallRank'):
        x = _num(row.get(key))
        if x is not None and x > 0:
            return x
    return 1000000000.0


def _alpha(row: dict[str, str]) -> float | None:
    for key in ('AlphaScore', 'CompositeScore', 'RankingScore', 'FinalScore'):
        x = _num(row.get(key))
        if x is not None:
            return x
    return None


def _state(row: dict[str, str]) -> str:
    x = (row.get('ExecutionState') or row.get('RankingEligibility') or 'OBSERVE').strip().upper()
    return {'推荐': 'READY', '谨慎候选': 'CAUTIOUS', '观察': 'OBSERVE', '风险过滤': 'BLOCKED'}.get(x, x)


def _signal(row: dict[str, str]) -> str:
    return (row.get('EntrySignal') or 'HOLD_WAIT').strip().upper()


def _quality(row: dict[str, str]) -> str:
    return (row.get('QualityLayerStatus') or 'UNKNOWN').strip().upper()


def _strength(row: dict[str, str]) -> float | None:
    for key in ('SmoothTriggerScore', 'SmoothAlphaScore'):
        x = _score(row.get(key))
        if x is not None:
            return x
    return None


def _confidence(row: dict[str, str]) -> tuple[str, str]:
    if _quality(row) in {'POLICY_FAIL', 'DATA_INCOMPLETE'}:
        return ('LOW', 'bad93')
    x = _score(row.get('ScoreConfidence'))
    if x is not None:
        return (f'HIGH · {x:.0f}', 'good93') if x >= 75 else (f'MEDIUM · {x:.0f}', 'mid93') if x >= 50 else (f'LOW · {x:.0f}', 'bad93')
    tier = (row.get('BacktestConfidenceTier') or '').upper()
    if tier.startswith(('HIGH', 'FULL', 'STRONG')) or '高可信' in tier:
        return ('HIGH', 'good93')
    if tier.startswith(('MEDIUM', 'NORMAL', 'MODERATE')) or '中可信' in tier:
        return ('MEDIUM', 'mid93')
    if tier.startswith(('LOW', 'WEAK')) or '低可信' in tier:
        return ('LOW', 'bad93')
    return ('UNCALIBRATED', 'muted93')


def _risk(row: dict[str, str]) -> tuple[str, str]:
    if _state(row) == 'BLOCKED' or _quality(row) in {'POLICY_FAIL', 'DATA_INCOMPLETE'}:
        return ('BLOCKED', 'bad93')
    for key in (
        'DirectionalResearchEligible',
        'BreakoutPriceGatePassed',
        'TradeEconomicsPassed',
        'TradeLiquidityPassed',
    ):
        if row.get(key) and _bool(row[key], False):
            return ('HIGH', 'bad93')
    chase = _num(row.get('ChaseRiskScore'))
    rr = _num(row.get('RewardRiskRatio'))
    if (
        _state(row) == 'CAUTIOUS'
        or (chase is not None and chase >= 30.0)
        or (rr is not None and rr < 1.5)
    ):
        return ('MEDIUM', 'mid93')
    if _state(row) == 'READY' and _signal(row) in {'BUY_NOW', 'BREAKOUT_CONFIRM'}:
        return ('LOW', 'good93')
    return ('WATCH', 'muted93')


def _short(v: object, n: int=72) -> str:
    text = str(v or '').strip().replace('\n', ' ')
    text = re.split('[；;。]', text, 1)[0].strip()
    return text if len(text) <= n else text[:n - 1] + '…'


def _explain(row: dict[str, str]) -> dict[str, str]:
    sig = _signal(row)
    status = (row.get('SignalStatus') or '').upper()
    days = int(_num(row.get('SignalDays')) or 0)
    strength = _strength(row)
    now = [_SIG.get(sig, sig)]
    if status:
        now.append(f'{status} · {days}D' if days else status)
    if strength is not None:
        now.append(f'触发 {strength:.0f}/100')

    breakout_quality = _score(row.get('BreakoutQualityFactor'))
    if breakout_quality is not None and sig == 'BREAKOUT_CONFIRM':
        now.append(f'突破质量 {breakout_quality:.0f}/100')
    volume_ratio = _num(row.get('BreakoutVolumeRatio'))
    if volume_ratio is not None and volume_ratio > 0:
        now.append(f'突破量比 {volume_ratio:.2f}x')
    signal_count = _num(row.get('SignalCount'))
    if signal_count is not None and signal_count > 0:
        now.append(f'共振 {int(signal_count)}项')
    if row.get('InstitutionalTier'):
        now.append(f"机构层 {row['InstitutionalTier']}")

    blockers = []
    checks = (
        ('DirectionalResearchEligible', 'DirectionalResearchReason', '方向性研究准入未通过'),
        ('BreakoutPriceGatePassed', 'BreakoutPriceGateReason', '突破价格确认未通过'),
        ('TradeEconomicsPassed', 'TradeEconomicsReason', '执行经济性未通过'),
        ('TradeLiquidityPassed', 'TradeLiquidityReason', '执行流动性未通过'),
    )
    for flag, reason, fallback in checks:
        if row.get(flag) and _bool(row[flag], False):
            blockers.append(_short(row.get(reason)) or fallback)

    chase = _num(row.get('ChaseRiskScore'))
    if chase is not None and chase >= 30.0:
        blockers.append(_short(row.get('ChaseRiskReason')) or f'追高风险 {chase:.0f}/100')
    freshness = (row.get('DataFreshnessStatus') or '').strip()
    if freshness and freshness not in {'新鲜', 'FRESH', '同步'}:
        blockers.append(_short(row.get('DataFreshnessReason')) or f'行情时效 {freshness}')
    wait = {
        'WAIT_PULLBACK': '仍需等待回调',
        'WAIT_VOLUME_CONFIRM': '仍需等待量能确认',
        'PRICE_BREAKOUT': '仍缺量能确认',
        'HOLD_WAIT': '当前仅观察',
        'AVOID': '当前策略建议回避',
    }.get(sig)
    if wait:
        blockers.append(wait)
    if _state(row) == 'CAUTIOUS':
        reason = _short(
            row.get('TradeReadinessReason')
            or row.get('DecisionReason')
            or row.get('RankingReason')
        )
        if reason:
            blockers.append(reason)

    stop = _num(row.get('StopLoss'))
    breakout = _num(row.get('BreakoutBuyPrice'))
    if stop is not None:
        invalid = f'止损参考 {stop:g} 被触发，或执行状态降为 BLOCKED'
    elif sig == 'BREAKOUT_CONFIRM' and breakout is not None:
        invalid = f'突破确认价 {breakout:g} 失守，或执行状态降为 BLOCKED'
    else:
        invalid = '执行状态降为 BLOCKED、质量层失效或信号进入 FAILED / EXPIRED'

    confidence, _ = _confidence(row)
    validation = [f'Confidence {confidence}']
    samples = _num(row.get('BacktestSamples'))
    if samples is not None:
        validation.append(f'样本 {int(samples)}')
    for key, label in (
        ('BacktestWinRate20D', '20D'),
        ('BacktestWinRate60D', '60D'),
    ):
        if _num(row.get(key)) is not None:
            validation.append(f'{label}胜率 {_pct_ratio(row[key])}')
    return {
        'wn': ' · '.join(now[:6]),
        'wt': '；'.join(blockers[:3]) or '无新增硬阻断；仍需遵守执行价与风险边界',
        'i': invalid,
        'v': ' · '.join(validation),
        'p': f"{status or 'ACTIVE'} · {days}D" if days else status,
    }


def _pool(index_path: Path, all_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    try:
        details = _embedded(index_path.read_text(encoding='utf-8'), '详情数据')
    except (OSError, UnicodeError):
        details = {}
    if not details:
        return sorted(all_rows, key=lambda row: (_rank(row), row.get('Ticker', '')))
    members = {str(t).strip().upper() for t in details}
    return sorted((row for row in all_rows if row.get('Ticker') in members), key=lambda row: (_rank(row), row.get('Ticker', '')))


def _previous(site: Path, report_date: str) -> tuple[str, dict[str, Any]]:
    for path in sorted((p for p in (site / 'reports').glob('????-??-??.html') if p.is_file() and p.stem < report_date), key=lambda p: p.stem, reverse=True):
        try:
            details = _embedded(path.read_text(encoding='utf-8'), '详情数据')
        except (OSError, UnicodeError):
            continue
        if details:
            return (path.stem, details)
    return ('', {})


def _change(row: dict[str, str], previous: dict[str, Any]) -> dict[str, object]:
    prior = previous.get(row.get('Ticker', ''))
    if not isinstance(prior, dict):
        return {'new': True, 'rd': None, 'ad': None, 'ec': False, 'label': 'NEW'}
    pr, cr = (_num(prior.get('researchRank')), _rank(row))
    rd = pr - cr if pr is not None and pr > 0 and (cr < 1000000000.0) else None
    pa, ca = (_num(prior.get('alpha')), _alpha(row))
    ad = ca - pa if ca is not None and pa is not None else None
    old = str(prior.get('execution', '') or '').strip()
    new = {'READY': '可执行', 'CAUTIOUS': '谨慎', 'OBSERVE': '观察', 'BLOCKED': '阻断'}.get(_state(row), _state(row))
    ec = bool(old and old != new)
    parts = []
    if rd is not None and abs(rd) >= 1:
        parts.append(f"排名 {('↑' if rd > 0 else '↓')}{abs(int(rd))}")
    if ad is not None and abs(ad) >= 0.05:
        parts.append(f'Alpha {ad:+.1f}')
    if ec:
        parts.append(f'{old}→{new}')
    return {'new': False, 'rd': rd, 'ad': ad, 'ec': ec, 'label': ' · '.join(parts) or '持平'}


def _spark(values: object) -> str:
    if not isinstance(values, list):
        return '—'
    nums = [x for x in (_num(v) for v in values[-30:]) if x is not None]
    if len(nums) < 2:
        return '—'
    lo, hi = (min(nums), max(nums))
    spread = max(hi - lo, abs(hi) * 1e-06, 1e-09)
    pts = [f'{2 + i * 80 / max(1, len(nums) - 1):.1f},{21 - (v - lo) / spread * 18:.1f}' for i, v in enumerate(nums)]
    color = '#E33D3D' if nums[-1] >= nums[0] else '#197A55'
    return f'''<svg class="spark93" width="84" height="24" viewBox="0 0 84 24"><polyline points="{' '.join(pts)}" fill="none" stroke="{color}" stroke-width="1.7"/></svg>'''


def _opportunities(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    chosen = [r for r in rows if _state(r) in {'READY', 'CAUTIOUS'} and _quality(r) not in {'POLICY_FAIL', 'DATA_INCOMPLETE'} and (_signal(r) != 'AVOID')]
    if len(chosen) < 10:
        seen = {r['Ticker'] for r in chosen}
        chosen += [r for r in rows if r['Ticker'] not in seen and _state(r) != 'BLOCKED' and (_quality(r) not in {'POLICY_FAIL', 'DATA_INCOMPLETE'}) and (_signal(r) in {'BUY_NOW', 'BREAKOUT_CONFIRM', 'WAIT_PULLBACK', 'WAIT_VOLUME_CONFIRM'})]
    return chosen[:10]


def _top_html(rows: list[dict[str, str]], previous: dict[str, Any], charts: dict[str, Any], prev_date: str) -> tuple[str, dict[str, dict[str, object]]]:
    body, payload = ([], {})
    for row in _opportunities(rows):
        t = row['Ticker']
        ch = _change(row, previous)
        ex = _explain(row)
        c, cc = _confidence(row)
        r, rc = _risk(row)
        s = _strength(row)
        payload[t] = {'s': s, 'c': c, 'r': r, 'd': ch['label'], **ex}
        rank = '—' if _rank(row) >= 1000000000.0 else str(int(_rank(row)))
        price = _num(row.get('Close'))
        price_text = '—' if price is None else f'{price:.3f}'.rstrip('0').rstrip('.')
        delta_cls = 'good93' if ch['new'] or (_num(ch['rd']) or 0) > 0 or (_num(ch['ad']) or 0) > 0.05 else 'bad93' if (_num(ch['rd']) or 0) < 0 or (_num(ch['ad']) or 0) < -0.05 else 'muted93'
        chart = charts.get(t, {})
        closes = chart.get('c', []) if isinstance(chart, dict) else []
        body.append(f'''<tr data-ticker="{_safe(t)}"><td>{_safe(rank)}</td><td><strong>{_safe(t)}</strong><br><small>{_safe(row.get('Name') or '—')}</small></td><td>{_safe(price_text)}</td><td class="axis93"><b>{('—' if s is None else f'{s:.1f}')}</b><small>{_safe(_SIG.get(_signal(row), _signal(row)))}</small></td><td class="{cc}">{_safe(c)}</td><td class="{rc}">{_safe(r)}</td><td class="{delta_cls}">{_safe(ch['label'])}</td><td class="why93">{_safe(ex['wn'])}</td><td>{_spark(closes)}</td></tr>''')
    return (f"""<section id="top-opportunities-v93" class="section card console-v93"><div class="section-head"><h2>TOP OPPORTUNITIES / 今日优先研究</h2><p>保留生产 ResearchRank；只做展示筛选，不对子集重新排名</p></div><div class="meta93"><span>比较基准 <strong>{_safe(prev_date or '无可用上一期历史页')}</strong></span><span>· SIGNAL=既有触发分 · CONFIDENCE不是概率 · RISK=执行风险标签</span></div><div class="table-wrap"><table><thead><tr><th>研究#</th><th>标的</th><th>收盘</th><th>SIGNAL</th><th>CONFIDENCE</th><th>RISK</th><th>较上期</th><th>WHY NOW</th><th>TREND</th></tr></thead><tbody>{''.join(body) or '<tr><td colspan="9">暂无满足条件候选</td></tr>'}</tbody></table></div></section>""", payload)


def _changed_html(rows: list[dict[str, str]], previous: dict[str, Any], prev_date: str, daily: dict[str, Any]) -> str:
    items = [(r, _change(r, previous)) for r in rows]
    groups = [('NEW / 新进入发布研究池', [(r, c) for r, c in items if c['new']], '本期无新增'), ('RANK UP / 排名提升', sorted([(r, c) for r, c in items if (_num(c['rd']) or 0) >= 3], key=lambda x: -(_num(x[1]['rd']) or 0)), '没有 ≥3 名提升'), ('ALPHA UP / 评分提升', sorted([(r, c) for r, c in items if (_num(c['ad']) or 0) >= 2], key=lambda x: -(_num(x[1]['ad']) or 0)), '没有 ≥2 分提升'), ('EXECUTION / 执行状态变化', [(r, c) for r, c in items if c['ec']], '没有执行状态变化')]
    diff = daily.get('run_diff', {})
    diff = diff if isinstance(diff, dict) else {}
    cards = (('资格上调', diff.get('eligibility_upgraded', 0), '生产 DailyRunSummary'), ('资格下调', diff.get('eligibility_downgraded', 0), '生产 DailyRunSummary'), ('评分上升 ≥5', diff.get('score_up_5_plus', 0), f'发布池新增 {len(groups[0][1])}'), ('评分下降 ≥5', diff.get('score_down_5_plus', 0), f'执行变化 {len(groups[3][1])}'))
    card_html = ''.join((f'<article class="card93"><span>{_safe(a)}</span><strong>{_safe(b)}</strong><small>{_safe(c)}</small></article>' for a, b, c in cards))
    list_html = ''
    for title, values, empty in groups:
        lines = ''.join((f"<div><b>{_safe(r['Ticker'])}</b><em>{_safe(c['label'])}</em></div>" for r, c in values[:6])) or f'<div>{_safe(empty)}</div>'
        list_html += f'<article class="list93"><h3>{_safe(title)}</h3>{lines}</article>'
    return f"""<section id="what-changed-v93" class="section card console-v93"><div class="section-head"><h2>WHAT CHANGED TODAY / 今日发生了什么</h2><p>当前公开研究池 vs 最近一期历史报告</p></div><div class="meta93"><span>比较基准 <strong>{_safe(prev_date or '上一历史报告不可用')}</strong></span><span>· ticker级变化只比较实际公开研究池成员</span></div><div class="grid93">{card_html}</div><div class="lists93">{list_html}</div></section>"""


def _calibration_html(backtest: dict[str, Any]) -> str:
    raw = backtest.get('by_score_bucket', [])
    rows = []
    if isinstance(raw, list):
        for x in raw:
            if isinstance(x, dict) and _num(x.get('bucket')) is not None:
                rows.append({'b': int(_num(x['bucket']) or 0), 'n': int(_num(x.get('samples')) or 0), 'e': _num(x.get('effective_samples')), 'x20': _num(x.get('average_net_excess_return20')), 'x60': _num(x.get('average_net_excess_return60')), 'r20': _num(x.get('average_return20')), 'r60': _num(x.get('average_return60'))})
    rows.sort(key=lambda x: x['b'])

    def mono(key: str) -> tuple[str, str, int]:
        vals = [x[key] for x in rows if x[key] is not None]
        if len(vals) < 3:
            return ('INSUFFICIENT', 'muted93', 0)
        v = sum((b < a for a, b in zip(vals, vals[1:])))
        return ('PASS', 'good93', v) if v == 0 else ('MIXED', 'mid93', v) if v == 1 else ('FAIL', 'bad93', v)
    s20, c20, v20 = mono('x20')
    s60, c60, v60 = mono('x60')
    stab = backtest.get('calibration_stability', {})
    stab = stab if isinstance(stab, dict) else {}
    ss = str(stab.get('status', 'INSUFFICIENT_FOLDS') or 'INSUFFICIENT_FOLDS')
    sc = 'good93' if ss == 'STABLE' else 'mid93' if ss == 'UNSTABLE' else 'muted93'
    body = ''.join((f"""<tr><td><strong>Q{x['b']}</strong><small>{(' · 最高分' if x['b'] == rows[-1]['b'] else ' · 最低分' if x['b'] == rows[0]['b'] else '')}</small></td><td>{x['n']}</td><td>{('—' if x['e'] is None else f"{x['e']:.1f}")}</td><td>{_pct_point(x['x20'])}</td><td>{_pct_point(x['x60'])}</td><td>{_pct_point(x['r20'])}</td><td>{_pct_point(x['r60'])}</td></tr>""" for x in reversed(rows))) if rows else '<tr><td colspan="7">BacktestSummary.by_score_bucket 不可用</td></tr>'
    spread20 = _pct_point(backtest.get('monotonicity_high_low_20d'))
    spread60 = _pct_point(backtest.get('monotonicity_high_low_60d'))
    ic20 = _num(backtest.get('rank_ic_20d'))
    ic60 = _num(backtest.get('rank_ic_60d'))
    recent = _num(stab.get('recent_rank_ic'))
    ratio = _num(stab.get('stable_fold_ratio'))
    folds = int(_num(stab.get('fold_count')) or 0)
    return f'''<section id="score-bucket-calibration-v93" class="section card console-v93"><div class="section-head"><h2>HELD-OUT SCORE CALIBRATION / 测试集评分分桶</h2><p>生产回测 held-out test set；不重拟合、不回灌当前排名</p></div><div class="meta93"><span>模式 <strong>{_safe(str(backtest.get('mode', '—')).upper())}</strong></span><span>· 测试样本 <strong>{int(_num(backtest.get('samples')) or 0)}</strong></span><span>· Q1→Q5 为历史 score 五分位，Q5最高</span><span>· {_safe(backtest.get('ranking_calibration_status', '—'))}</span></div><div class="cal93"><div><span>20D 单调性 / HIGH-LOW</span><strong class="{c20}">{s20} · {spread20}</strong><small>{v20} 次逆序 · Rank IC {('—' if ic20 is None else f'{ic20:+.3f}')}</small></div><div><span>60D 单调性 / HIGH-LOW</span><strong class="{c60}">{s60} · {spread60}</strong><small>{v60} 次逆序 · Rank IC {('—' if ic60 is None else f'{ic60:+.3f}')}</small></div><div><span>EXPANDING WALK-FORWARD</span><strong class="{sc}">{_safe(ss)}</strong><small>{folds} folds · 稳定 {(_pct_ratio(ratio) if ratio is not None else '—')} · 最近IC {('—' if recent is None else f'{recent:+.3f}')}</small></div></div><div class="table-wrap"><table><thead><tr><th>Score Quintile</th><th>N</th><th>Effective N</th><th>20D净超额</th><th>60D净超额</th><th>20D总收益</th><th>60D总收益</th></tr></thead><tbody>{body}</tbody></table></div><div class="meta93"><span>by_score_bucket 来自生产 held-out test；walk-forward 使用已验证历史股票池样本做逐年 expanding window，且 60D 标签必须在年度边界前完整实现。</span></div></section>'''


def _inject(path: Path, rows: list[dict[str, str]], previous: dict[str, Any], prev_date: str, daily: dict[str, Any], backtest: dict[str, Any]) -> None:
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return
    if 'id="top-opportunities-v93"' in text:
        return
    charts = _embedded(text, '图表数据')
    top, payload = _top_html(rows, previous, charts, prev_date)
    changed = _changed_html(rows, previous, prev_date, daily)
    cal = _calibration_html(backtest)
    for row in rows[:1000]:
        t = row.get('Ticker')
        if t and t not in payload:
            c, _ = _confidence(row)
            r, _ = _risk(row)
            ch = _change(row, previous)
            payload[t] = {'s': _strength(row), 'c': c, 'r': r, 'd': ch['label'], **_explain(row)}
    data = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    text = text.replace('</head>', _CSS + '</head>', 1)
    anchor = '<section class="section card" data-section='
    text = text.replace(anchor, top + changed + anchor, 1) if anchor in text else text.replace('<main class="shell">', '<main class="shell">' + top + changed, 1)
    ca = '<section id="production-backtest-calibration"'
    if ca in text:
        text = text.replace(ca, cal + ca, 1)
    elif '<div class="foot">' in text:
        text = text.replace('<div class="foot">', cal + '<div class="foot">', 1)
    else:
        text = text.replace('</main>', cal + '</main>', 1)
    text = text.replace('</body>', f'<script id="研究控制台数据" type="application/json">{data}</script>' + _JS + '</body>', 1).replace(_v92.WEB_REPORT_VERSION, WEB_REPORT_VERSION)
    try:
        path.write_text(text, encoding='utf-8')
    except OSError:
        return


def build_web_report(output_dir: Path=DEFAULT_OUTPUT_DIR, site_dir: Path=DEFAULT_SITE_DIR) -> WebReportResult:
    output_dir, site_dir = (Path(output_dir), Path(site_dir))
    result = _v92.build_web_report(output_dir=output_dir, site_dir=site_dir)
    source = _published_source_dir(output_dir)
    rows = _pool(result.index_path, _rows(source, output_dir))
    daily = _read_json(source / 'DailyRunSummary.json') or _read_json(output_dir / 'DailyRunSummary.json')
    backtest = _read_json(source / 'BacktestSummary.json') or _read_json(output_dir / 'BacktestSummary.json')
    prev_date, previous = _previous(site_dir, result.report_date)
    if rows:
        for path in (result.index_path, result.archive_path):
            _inject(path, rows, previous, prev_date, daily, backtest)
    return result


def _env(name: str, default: bool=True) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() not in {'0', 'false', 'no', 'off', 'disabled'}


def build_and_publish_web_report(*, output_dir: Path=DEFAULT_OUTPUT_DIR, site_dir: Path=DEFAULT_SITE_DIR, logger: logging.Logger | None=None, reason: str='run-complete') -> WebReportResult:
    log = logger or logging.getLogger('institution_scanner')
    built = build_web_report(Path(output_dir), Path(site_dir))
    log.info('WEB v93 Research Console generated: %s (%s).', built.archive_path, reason)
    if not _env(WEB_PUBLISH_ENV, True):
        return built
    try:
        return publish_site(Path(site_dir), repo_root=PROJECT_ROOT, report_date=built.report_date)
    except Exception as exc:
        log.warning('WEB report publication skipped/failed without affecting pipeline: %s', exc)
        return WebReportResult(report_date=built.report_date, index_path=built.index_path, archive_path=built.archive_path, publish_message=str(exc))


def maybe_publish_canonical_report(output_dir: Path, *, logger: logging.Logger | None=None, reason: str) -> WebReportResult | None:
    if not is_canonical_output_dir(Path(output_dir)):
        return None
    try:
        return build_and_publish_web_report(output_dir=Path(output_dir), logger=logger, reason=reason)
    except Exception as exc:
        (logger or logging.getLogger('institution_scanner')).warning('WEB v93 Research Console generation skipped/failed without affecting pipeline: %s', exc)
        return None
