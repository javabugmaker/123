"""v85 public-safe A-share research briefing and GitHub Pages publisher.

This layer reuses v84's validated data allowlist, historical cut-off, chart
payload and publication boundaries.  It adds an original editorial layout,
multi-view research table, sector breadth, run changes and risk diagnostics;
it does not alter scores or execution decisions.
"""

from __future__ import annotations

import csv
import logging
import os
import shutil
import subprocess
import tempfile
from collections import OrderedDict, defaultdict
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path

import web_report_v84 as _v84
from v85_terminal_config import (
    BRAND_LABEL,
    HOME_SECTIONS,
    PAGE_LABEL,
    SECTION_TITLES,
)

PROJECT_ROOT = _v84.PROJECT_ROOT
DEFAULT_OUTPUT_DIR = _v84.DEFAULT_OUTPUT_DIR
DEFAULT_SITE_DIR = _v84.DEFAULT_SITE_DIR
WEB_PUBLISH_ENV = _v84.WEB_PUBLISH_ENV
GH_PAGES_BRANCH = _v84.GH_PAGES_BRANCH
WEB_REPORT_VERSION = "2026-08-21-v87-directional-execution-diagnostics-v1"
WebReportResult = _v84.WebReportResult

# Compatibility exports used by old callers/tests.
_published_source_dir = _v84._published_source_dir
is_canonical_output_dir = _v84.is_canonical_output_dir
github_pages_url_from_remote = _v84.github_pages_url_from_remote


def _gate_label(value: object, reason: object = "") -> str:
    """Return a public tri-state label without treating missing data as pass."""
    normalized = str(value or "").strip().lower()
    if not normalized:
        return ""
    reason_text = str(reason or "")
    if "不适用" in reason_text or "沿用旧执行语义" in reason_text:
        return "不适用"
    if normalized in {"1", "true", "yes", "y", "是", "pass", "passed"}:
        return "通过"
    if normalized in {"0", "false", "no", "n", "否", "fail", "failed"}:
        return "未通过"
    return ""


def _details_payload(rows: list[dict[str, str]]) -> dict[str, dict[str, object]]:
    """Extend v84's fixed payload with explicitly allowlisted v87 audit fields."""
    payload = _v84._details_payload(rows)
    rows_by_ticker = {
        row.get("Ticker", ""): row
        for row in rows[: _v84._max_rows()]
        if row.get("Ticker", "")
    }
    for ticker, details in payload.items():
        row = rows_by_ticker.get(ticker, {})
        directional_reason = row.get("DirectionalResearchReason", "")
        breakout_reason = row.get("BreakoutPriceGateReason", "")
        economics_reason = row.get("TradeEconomicsReason", "")
        directional_gate = _gate_label(
            row.get("DirectionalResearchEligible", ""), directional_reason
        )
        breakout_gate = _gate_label(
            row.get("BreakoutPriceGatePassed", ""), breakout_reason
        )
        economics_gate = _gate_label(
            row.get("TradeEconomicsPassed", ""), economics_reason
        )
        details.update(
            {
                "directionalGate": directional_gate,
                "directionalReason": directional_reason,
                "breakoutConfirmation": (
                    _v84._number(row.get("BreakoutPriceConfirmationScore", ""))
                    if breakout_gate not in {"", "不适用"}
                    else None
                ),
                "breakoutGate": breakout_gate,
                "breakoutReason": breakout_reason,
                "roundTripCostPct": (
                    _v84._number(row.get("TradeEstimatedRoundTripCostPct", ""))
                    if economics_gate not in {"", "不适用"}
                    else None
                ),
                "targetCostMultiple": (
                    _v84._number(row.get("TradeTargetCostMultiple", ""))
                    if economics_gate not in {"", "不适用"}
                    else None
                ),
                "economicsGate": economics_gate,
                "economicsReason": economics_reason,
            }
        )
    return payload


def _read_view(source_dir: Path, output_dir: Path, names: Iterable[str]) -> list[dict[str, str]]:
    roots = (source_dir,) if source_dir.resolve() == output_dir.resolve() else (source_dir, output_dir)
    for root in roots:
        for name in names:
            rows = _v84._read_csv(root / name)
            if rows:
                return rows
    return []


def _state(row: dict[str, str]) -> str:
    value = (row.get("ExecutionState", "") or row.get("RankingEligibility", "")).strip().upper()
    return {
        "推荐": "READY",
        "谨慎候选": "CAUTIOUS",
        "观察": "OBSERVE",
        "风险过滤": "BLOCKED",
    }.get(value, value or "OBSERVE")


def _is_new(row: dict[str, str]) -> bool:
    return row.get("SignalStatus", "").strip().upper() == "NEW"


def _is_sustained(row: dict[str, str]) -> bool:
    return row.get("SignalStatus", "").strip().upper() in {"ACTIVE", "CONFIRMED", "STRENGTHEN"}


def _is_risk(row: dict[str, str]) -> bool:
    return _state(row) == "BLOCKED" or row.get("QualityLayerStatus", "").strip().upper() in {
        "POLICY_FAIL",
        "DATA_INCOMPLETE",
    }


def _build_views(
    *,
    source_dir: Path,
    output_dir: Path,
    all_rows: list[dict[str, str]],
    mixed_rows: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    stocks = _read_view(source_dir, output_dir, ("Top50Stocks.csv",))
    etfs = _read_view(source_dir, output_dir, ("Top50ETF.csv",))
    ready = _read_view(source_dir, output_dir, ("Top50TradeReady.csv",))
    risk = _read_view(source_dir, output_dir, ("Top50ValueTrapRisk.csv",))
    if not stocks:
        stocks = [row for row in all_rows if _v84._asset_type(row) == "股票"]
    if not etfs:
        etfs = [row for row in all_rows if _v84._asset_type(row) == "ETF"]
    if not ready:
        ready = [row for row in all_rows if _state(row) in {"READY", "CAUTIOUS"}]
    if not risk:
        risk = [row for row in all_rows if _is_risk(row)]
    raw = {
        "mixed": mixed_rows,
        "stocks": stocks,
        "etf": etfs,
        "ready": ready,
        "new": [row for row in all_rows if _is_new(row)],
        "sustained": [row for row in all_rows if _is_sustained(row)],
        "risk": risk,
    }
    return {key: _v84._decorate_rows(rows) for key, rows in raw.items()}


def _merge_view_rows(
    views: dict[str, list[dict[str, str]]],
) -> tuple[list[dict[str, str]], dict[str, set[str]]]:
    ordered: OrderedDict[str, dict[str, str]] = OrderedDict()
    memberships: dict[str, set[str]] = defaultdict(set)
    primary = ("mixed", "stocks", "etf", "ready", "new", "sustained", "risk")
    quota = max(12, _v84._max_rows() // len(primary))
    for view in primary:
        for row in views.get(view, ())[:quota]:
            ticker = row.get("Ticker", "")
            if not ticker:
                continue
            ordered.setdefault(ticker, dict(row))
            memberships[ticker].add(view)
    for ticker in ordered:
        memberships[ticker].add("all")
    return list(ordered.values()), memberships


def _sector_rotation(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    groups: dict[str, dict[str, object]] = {}
    candidates = _v84._decorate_rows(rows)[:400]
    for row in candidates:
        topic = row.get("IndustryTopic", "") or "其他"
        alpha = _v84._number(row.get("DisplayAlpha", ""))
        item = groups.setdefault(
            topic,
            {"topic": topic, "count": 0, "alpha_sum": 0.0, "alpha_count": 0, "ready": 0, "new": 0, "leader": "—"},
        )
        item["count"] = int(item["count"]) + 1
        if alpha is not None:
            item["alpha_sum"] = float(item["alpha_sum"]) + alpha
            item["alpha_count"] = int(item["alpha_count"]) + 1
        item["ready"] = int(item["ready"]) + int(_state(row) in {"READY", "CAUTIOUS"})
        item["new"] = int(item["new"]) + int(_is_new(row))
        if item["leader"] == "—":
            item["leader"] = f"{row.get('Ticker', '')} {row.get('Name', '')}".strip() or "—"
    result: list[dict[str, object]] = []
    for item in groups.values():
        count = max(1, int(item["alpha_count"]))
        item["average_alpha"] = float(item["alpha_sum"]) / count
        result.append(item)
    result.sort(
        key=lambda item: (
            -int(item["ready"]),
            -float(item["average_alpha"]),
            -int(item["new"]),
            -int(item["count"]),
            str(item["topic"]),
        )
    )
    return result[:8]


def _risk_items(rows: list[dict[str, str]], daily: dict[str, object]) -> list[dict[str, object]]:
    blocked = sum(_state(row) == "BLOCKED" for row in rows)
    incomplete = sum(row.get("QualityLayerStatus", "").strip().upper() == "DATA_INCOMPLETE" for row in rows)
    policy_fail = sum(row.get("QualityLayerStatus", "").strip().upper() == "POLICY_FAIL" for row in rows)
    missing_stop = sum(_v84._number(row.get("StopLoss", "")) is None for row in rows)
    items: list[dict[str, object]] = [
        {"label": "执行阻断", "value": blocked, "note": "BLOCKED / 风险过滤", "tone": "danger"},
        {"label": "质量数据不完整", "value": incomplete, "note": "关键基本面字段缺失", "tone": "warn"},
        {"label": "质量策略未通过", "value": policy_fail, "note": "仅作研究观察", "tone": "warn"},
        {"label": "止损字段缺失", "value": missing_stop, "note": "当前公开候选范围", "tone": "neutral"},
    ]
    health = daily.get("decision_health", {})
    if isinstance(health, dict):
        blockers = health.get("top_blockers", [])
        if isinstance(blockers, list):
            for blocker in blockers[:2]:
                if not isinstance(blocker, dict):
                    continue
                items.append(
                    {
                        "label": str(blocker.get("name", "主要阻断项") or "主要阻断项"),
                        "value": int(blocker.get("count", 0) or 0),
                        "note": "DailyRunSummary 决策健康统计",
                        "tone": "neutral",
                    }
                )
    return items[:6]


def _run_changes(daily: dict[str, object]) -> list[dict[str, object]]:
    raw = daily.get("run_diff", {})
    diff = raw if isinstance(raw, dict) else {}
    upgraded = diff.get("upgraded_examples", [])
    downgraded = diff.get("downgraded_examples", [])
    up_examples = "、".join(str(item) for item in upgraded[:4]) if isinstance(upgraded, list) else ""
    down_examples = "、".join(str(item) for item in downgraded[:4]) if isinstance(downgraded, list) else ""
    return [
        {
            "label": "资格上调",
            "value": int(diff.get("eligibility_upgraded", 0) or 0),
            "note": up_examples or "本轮暂无样例",
            "tone": "positive",
        },
        {
            "label": "资格下调",
            "value": int(diff.get("eligibility_downgraded", 0) or 0),
            "note": down_examples or "本轮暂无样例",
            "tone": "negative",
        },
        {
            "label": "评分上升 ≥5",
            "value": int(diff.get("score_up_5_plus", 0) or 0),
            "note": f"新增 {int(diff.get('added', 0) or 0)} 只",
            "tone": "positive",
        },
        {
            "label": "评分下降 ≥5",
            "value": int(diff.get("score_down_5_plus", 0) or 0),
            "note": f"移出 {int(diff.get('removed', 0) or 0)} 只",
            "tone": "negative",
        },
    ]


def _metric_cards(rows: list[dict[str, str]], charts: dict[str, object], daily: dict[str, object]) -> str:
    counts = _v84._summary_counts(rows)
    freshness_raw = daily.get("freshness", {})
    freshness = freshness_raw if isinstance(freshness_raw, dict) else {}
    quality_raw = daily.get("quality_gate", {})
    quality = quality_raw if isinstance(quality_raw, dict) else {}
    backtest_raw = daily.get("backtest", {})
    backtest = backtest_raw if isinstance(backtest_raw, dict) else {}
    cards = (
        ("全市场标的", f"{counts['total']:,}", f"股票 {counts['stocks']:,} · ETF {counts['etfs']:,}", "red"),
        ("可执行", f"{counts['ready']:,}", "READY / 推荐", "ink"),
        ("谨慎候选", f"{counts['cautious']:,}", "CAUTIOUS", "ink"),
        ("新信号", f"{counts['new']:,}", "SignalStatus = NEW", "ink"),
        ("数据新鲜度", _v84._fmt_percent(freshness.get("all_results_ratio", "")), "完整市场结果", "green"),
        ("质量通过率", _v84._fmt_percent(quality.get("pass_rate", "")), "适用股票", "amber"),
        ("图表覆盖", f"{len(charts):,}", "最近 120 个交易日", "ink"),
        ("回测缓存", _v84._fmt_percent(backtest.get("cache_hit_rate", "")), str(backtest.get("cache_health", "—") or "—"), "ink"),
    )
    return "".join(
        '<article class="metric {tone}"><span>{title}</span><strong>{value}</strong><small>{note}</small></article>'.format(
            tone=_v84._safe(tone),
            title=_v84._safe(title),
            value=_v84._safe(value),
            note=_v84._safe(note),
        )
        for title, value, note, tone in cards
    )


def _top_rows_html(rows: list[dict[str, str]], spark: dict[str, list[float]]) -> str:
    body: list[str] = []
    for row in rows[:10]:
        ticker = row.get("Ticker", "")
        status, css_class = _v84._execution_label(row.get("DisplayExecution", ""))
        body.append(
            f'<tr data-ticker="{_v84._safe(ticker)}">'
            f'<td class="rank">{_v84._safe(row.get("DisplayResearchRank", "—"))}</td>'
            f'<td class="security"><strong>{_v84._safe(ticker)}</strong><span>{_v84._safe(row.get("Name", "—"))} · {_v84._safe(row.get("IndustryTopic", "—"))}</span></td>'
            f'<td class="number">{_v84._safe(_v84._fmt_number(row.get("Close", ""), 3 if row.get("AssetType") == "ETF" else 2))}</td>'
            f'<td class="number alpha">{_v84._safe(_v84._fmt_number(row.get("DisplayAlpha", ""), 1))}</td>'
            f'<td><span class="status {css_class}">{_v84._safe(status)}</span></td>'
            f'<td class="number">{_v84._safe(row.get("ReferenceBuyPrice", "—"))}</td>'
            f'<td class="number">{_v84._safe(_v84._fmt_number(row.get("StopLoss", ""), 3 if row.get("AssetType") == "ETF" else 2))}</td>'
            f'<td class="number">{_v84._safe(_v84._fmt_number(row.get("ProjectedTarget", ""), 3 if row.get("AssetType") == "ETF" else 2))}</td>'
            f'<td class="trend">{_v84._sparkline_svg(spark.get(ticker, []), width=88, height=26)}</td>'
            "</tr>"
        )
    return "\n".join(body)


def _research_rows_html(
    rows: list[dict[str, str]],
    memberships: dict[str, set[str]],
    spark: dict[str, list[float]],
) -> str:
    body: list[str] = []
    order = {name: index for index, name in enumerate(("mixed", "stocks", "etf", "ready", "new", "sustained", "risk", "all"))}
    for row in rows:
        ticker = row.get("Ticker", "")
        status, css_class = _v84._execution_label(row.get("DisplayExecution", ""))
        views = " ".join(sorted(memberships.get(ticker, {"all"}), key=lambda item: order.get(item, 99)))
        search = f"{ticker} {row.get('Name', '')} {row.get('IndustryTopic', '')}".casefold()
        body.append(
            f'<tr class="research-row" data-ticker="{_v84._safe(ticker)}" data-views="{_v84._safe(views)}" '
            f'data-asset="{_v84._safe(row.get("AssetType", ""))}" data-execution="{_v84._safe(status)}" '
            f'data-search="{_v84._safe(search)}">'
            f'<td class="rank">{_v84._safe(row.get("DisplayResearchRank", "—"))}</td>'
            f'<td class="rank secondary">{_v84._safe(row.get("DisplayTradeRank", "—"))}</td>'
            f'<td class="security"><strong>{_v84._safe(ticker)}</strong><span>{_v84._safe(row.get("Name", "—"))} · {_v84._safe(row.get("IndustryTopic", "—"))}</span></td>'
            f'<td class="number">{_v84._safe(_v84._fmt_number(row.get("Close", ""), 3 if row.get("AssetType") == "ETF" else 2))}</td>'
            f'<td class="number alpha">{_v84._safe(_v84._fmt_number(row.get("DisplayAlpha", ""), 1))}</td>'
            f'<td><span class="status {css_class}">{_v84._safe(status)}</span></td>'
            f'<td>{_v84._safe(_v84._signal_label(row.get("EntrySignal", "")))}</td>'
            f'<td class="number">{_v84._safe(row.get("ReferenceBuyPrice", "—"))}</td>'
            f'<td class="number">{_v84._safe(_v84._fmt_number(row.get("StopLoss", ""), 3 if row.get("AssetType") == "ETF" else 2))}</td>'
            f'<td class="number">{_v84._safe(_v84._fmt_number(row.get("ProjectedTarget", ""), 3 if row.get("AssetType") == "ETF" else 2))}</td>'
            f'<td class="trend">{_v84._sparkline_svg(spark.get(ticker, []), width=80, height=24)}</td>'
            "</tr>"
        )
    return "\n".join(body)


def _sector_html(items: list[dict[str, object]]) -> str:
    return "".join(
        '<article class="sector-card"><div><span>{topic}</span><strong>{alpha}</strong></div>'
        '<p>候选 {count} · 可执行/谨慎 {ready} · 新信号 {new}</p><small>{leader}</small></article>'.format(
            topic=_v84._safe(item["topic"]),
            alpha=_v84._safe(_v84._fmt_number(item["average_alpha"], 1)),
            count=_v84._safe(item["count"]),
            ready=_v84._safe(item["ready"]),
            new=_v84._safe(item["new"]),
            leader=_v84._safe(item["leader"]),
        )
        for item in items
    )


def _diagnostic_html(items: list[dict[str, object]]) -> str:
    return "".join(
        '<article class="diagnostic {tone}"><span>{label}</span><strong>{value}</strong><small>{note}</small></article>'.format(
            tone=_v84._safe(item.get("tone", "neutral")),
            label=_v84._safe(item.get("label", "—")),
            value=_v84._safe(item.get("value", "—")),
            note=_v84._safe(item.get("note", "—")),
        )
        for item in items
    )


_CSS = r"""
:root{--bg:#f1f2f4;--paper:#fff;--ink:#15171a;--muted:#6b7078;--line:#d9dde3;--soft:#eef0f3;--red:#e33d3d;--red-dark:#b52b32;--green:#197a55;--amber:#b56a13;--blue:#1769aa;--violet:#6955b8}
*{box-sizing:border-box}html{background:var(--bg);scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font-family:"Microsoft YaHei UI","PingFang SC",system-ui,sans-serif;padding:24px 28px 48px;-webkit-font-smoothing:antialiased}.shell{max-width:1720px;margin:auto}.mono,.number,.rank{font-family:ui-monospace,"SFMono-Regular",Consolas,monospace;font-variant-numeric:tabular-nums}
.masthead{position:relative;display:grid;grid-template-columns:auto 1fr auto;align-items:end;gap:24px;border-bottom:1px solid var(--ink);padding-bottom:18px;margin-bottom:16px}.masthead:after{content:"";position:absolute;right:0;bottom:-6px;width:12px;height:12px;background:var(--red)}.brand strong{display:block;font:700 13px ui-monospace,Consolas,monospace;letter-spacing:2px}.brand span{display:block;margin-top:3px;color:var(--muted);font-size:11px;font-weight:700}.date{font:700 43px ui-monospace,Consolas,monospace;letter-spacing:-2px;line-height:.9}.live{background:var(--ink);color:#fff;padding:7px 10px;font:700 10px ui-monospace,Consolas,monospace;letter-spacing:1px}.meta{display:flex;gap:10px;align-items:center;flex-wrap:wrap;color:var(--muted);font-size:11px;margin-bottom:16px}.meta a{color:var(--ink);font-weight:700;text-decoration:none;border-bottom:1px solid var(--red)}
.section{margin-bottom:16px}.section-head{display:flex;align-items:center;gap:12px;background:var(--ink);color:#fff;border-left:5px solid var(--red);padding:10px 13px;min-height:43px}.section-head h2{margin:0;font:700 12px ui-monospace,Consolas,monospace;letter-spacing:1px}.section-head p{margin:0;color:#c8cdd2;font-size:10px}.section-head .controls{margin-left:auto;display:flex;gap:6px;align-items:center;flex-wrap:wrap}.section-head input,.section-head select{height:29px;border:1px solid #464b50;background:#24272b;color:#fff;padding:0 8px;font-size:10px;outline:none}.section-head input{min-width:205px}.card{background:var(--paper);border:1px solid var(--line);box-shadow:0 2px 0 rgba(21,23,26,.03)}
.metrics{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:8px}.metric{background:var(--paper);border:1px solid var(--line);border-top:3px solid var(--ink);padding:11px 12px;min-height:78px}.metric.red{border-top-color:var(--red)}.metric.green{border-top-color:var(--green)}.metric.amber{border-top-color:var(--amber)}.metric span,.metric small{display:block;color:var(--muted);font-size:9px}.metric strong{display:block;margin:4px 0 2px;font:700 22px ui-monospace,Consolas,monospace}.metric small{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.table-wrap{overflow:auto;max-height:66vh}table{width:100%;border-collapse:collapse;white-space:nowrap;font-size:12px}th{position:sticky;top:0;z-index:2;background:#1e2125;color:#fff;padding:9px 10px;text-align:center;font:700 9px ui-monospace,Consolas,monospace;letter-spacing:.7px;border-right:1px solid rgba(255,255,255,.08)}td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:center}tbody tr[data-ticker]{cursor:pointer}tbody tr[data-ticker]:hover td{background:#f7f8fa}.security{text-align:left;min-width:185px}.security strong{display:block;font:700 12px ui-monospace,Consolas,monospace}.security span{display:block;color:var(--muted);font-size:10px;margin-top:2px;max-width:230px;overflow:hidden;text-overflow:ellipsis}.rank{font-weight:700}.rank.secondary{color:var(--muted)}.alpha{font-weight:700}.trend{width:104px;padding:4px 8px}.趋势图{display:block;margin:auto}.status{display:inline-block;padding:3px 6px;border-left:3px solid;background:#f5f6f7;font-size:10px;font-weight:700}.status.ready{color:var(--red-dark);border-color:var(--red)}.status.cautious{color:var(--amber);border-color:var(--amber)}.status.observe{color:#5f666e;border-color:#a0a5ab}.status.blocked{color:var(--green);border-color:var(--green)}
.subhead{display:flex;align-items:center;border-top:1px solid var(--line);border-bottom:1px solid var(--line);background:#f8f8f8;padding:8px 10px}.subhead strong{font:700 10px ui-monospace,Consolas,monospace;letter-spacing:.7px}.tabs{display:flex;gap:4px;margin-left:12px;flex-wrap:wrap}.tab{border:1px solid var(--line);background:#fff;color:var(--ink);padding:5px 9px;font-size:10px;font-weight:700;cursor:pointer}.tab.active{background:var(--red);border-color:var(--red);color:#fff}.visible-count{margin-left:auto;color:var(--muted);font:700 10px ui-monospace,Consolas,monospace}
.sector-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:var(--line)}.sector-card{background:#fff;padding:12px 13px;min-height:92px}.sector-card div{display:flex;justify-content:space-between;gap:8px}.sector-card span{font-size:11px;font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.sector-card strong{color:var(--red-dark);font:700 15px ui-monospace,Consolas,monospace}.sector-card p{margin:8px 0 5px;color:var(--muted);font-size:10px}.sector-card small{display:block;color:var(--ink);font:700 9px ui-monospace,Consolas,monospace;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.split{display:grid;grid-template-columns:1fr 1fr;gap:12px}.diagnostic-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:1px;background:var(--line)}.diagnostic{background:#fff;padding:12px 13px;min-height:83px;border-left:3px solid transparent}.diagnostic.danger,.diagnostic.negative{border-left-color:var(--green)}.diagnostic.warn{border-left-color:var(--amber)}.diagnostic.positive{border-left-color:var(--red)}.diagnostic span,.diagnostic small{display:block}.diagnostic span{color:var(--muted);font-size:9px;font-weight:700}.diagnostic strong{display:block;margin:4px 0 3px;font:700 20px ui-monospace,Consolas,monospace}.diagnostic small{color:var(--muted);font-size:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.run-grid{display:grid;grid-template-columns:repeat(7,1fr);gap:1px;background:var(--line)}.run-item{background:#fff;padding:12px}.run-item span{display:block;color:var(--muted);font-size:9px}.run-item strong{display:block;margin-top:4px;font:700 12px ui-monospace,Consolas,monospace}.foot{color:var(--muted);font-size:9px;line-height:1.65;border-top:1px solid var(--line);padding-top:11px;margin-top:15px}
.drawer-mask{position:fixed;inset:0;background:rgba(0,0,0,.28);display:none;z-index:30}.drawer-mask.open{display:block}.drawer{position:absolute;right:0;top:0;height:100%;width:min(980px,95vw);background:#f6f7f8;border-left:1px solid #999;overflow:auto;padding:18px 20px 28px;box-shadow:-12px 0 35px rgba(0,0,0,.18)}.drawer-head{display:flex;align-items:flex-start;border-bottom:1px solid var(--ink);padding-bottom:11px;margin-bottom:11px}.drawer-head h3{margin:0;font:700 20px ui-monospace,Consolas,monospace}.drawer-head p{margin:4px 0 0;color:var(--muted);font-size:11px}.close{margin-left:auto;border:1px solid var(--ink);background:#fff;width:33px;height:33px;cursor:pointer;font-size:17px}.detail-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:6px;margin:9px 0 11px}.detail-item{background:#fff;border:1px solid var(--line);padding:8px 9px;min-height:55px}.detail-item span{display:block;color:var(--muted);font-size:9px}.detail-item strong{display:block;margin-top:4px;font:700 11px ui-monospace,Consolas,monospace}.chart-frame{background:#fff;border:1px solid var(--line);padding:7px}#日K图{width:100%;height:auto;display:block;min-height:420px}.explanation{background:#fff;border:1px solid var(--line);padding:11px;margin-top:8px;font-size:11px;line-height:1.65}.hidden{display:none!important}
@media(max-width:1280px){body{padding:18px 16px 40px}.metrics{grid-template-columns:repeat(4,1fr)}.sector-grid{grid-template-columns:repeat(2,1fr)}.run-grid{grid-template-columns:repeat(4,1fr)}}
@media(max-width:760px){body{padding:14px 9px 34px}.masthead{grid-template-columns:1fr auto;align-items:start;gap:10px}.date{grid-column:1/-1;grid-row:2;font-size:31px}.live{grid-column:2}.metrics{grid-template-columns:repeat(2,1fr)}.split{grid-template-columns:1fr}.detail-grid{grid-template-columns:repeat(3,1fr)}.section-head{align-items:flex-start;flex-wrap:wrap}.section-head .controls{margin-left:0;width:100%}.section-head input{min-width:140px;flex:1}.run-grid{grid-template-columns:repeat(2,1fr)}}
"""


_JS = r"""
const 图表=JSON.parse(document.getElementById('图表数据').textContent||'{}');
const 详情=JSON.parse(document.getElementById('详情数据').textContent||'{}');
const 研究表=document.getElementById('研究表');
const 搜索=document.getElementById('搜索'),类型=document.getElementById('类型'),执行=document.getElementById('执行');
const 计数=document.getElementById('可见计数');let 当前视图='mixed';
function 过滤(){let n=0;const q=(搜索.value||'').trim().toLowerCase(),a=类型.value,s=执行.value;for(const r of 研究表.querySelectorAll('tr')){const views=(r.dataset.views||'').split(' ');const ok=views.includes(当前视图)&&(!q||(r.dataset.search||'').includes(q))&&(!a||r.dataset.asset===a)&&(!s||r.dataset.execution===s);r.classList.toggle('hidden',!ok);if(ok)n++}计数.textContent=`${n} ROWS`}
for(const button of document.querySelectorAll('.tab'))button.addEventListener('click',()=>{当前视图=button.dataset.view;for(const item of document.querySelectorAll('.tab'))item.classList.toggle('active',item===button);过滤()});
搜索.addEventListener('input',过滤);类型.addEventListener('change',过滤);执行.addEventListener('change',过滤);过滤();
const 遮罩=document.getElementById('遮罩');document.getElementById('关闭').onclick=()=>遮罩.classList.remove('open');遮罩.addEventListener('click',e=>{if(e.target===遮罩)遮罩.classList.remove('open')});document.addEventListener('keydown',e=>{if(e.key==='Escape')遮罩.classList.remove('open')});
function 文本(v,d='—'){return v===null||v===undefined||v===''?d:String(v)}
function 安全(v){return 文本(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#39;')}
function 数字(v,n=2){if(v===null||v===undefined||v==='')return '—';const x=Number(v);return Number.isFinite(x)?x.toFixed(n):'—'}
function 详情项(k,v){return `<div class="detail-item"><span>${安全(k)}</span><strong>${安全(v)}</strong></div>`}
function 有值(v){return v!==null&&v!==undefined&&v!==''}
function 可选详情项(k,v){return 有值(v)?详情项(k,v):''}
function 百分比(v,n=3){return 有值(v)?`${数字(v,n)}%`:''}
function 倍数(v){return 有值(v)?`${数字(v,2)}×`:''}
function 突破诊断(d){if(!有值(d.breakoutGate))return '';const score=有值(d.breakoutConfirmation)?`${数字(d.breakoutConfirmation,1)} / 100 · `:'';return `${score}${d.breakoutGate}`}
function 诊断说明(d){const values=[d.reason,d.directionalGate==='未通过'?d.directionalReason:'',d.breakoutGate==='未通过'?d.breakoutReason:'',d.economicsGate==='未通过'?d.economicsReason:''].filter(v=>有值(v)&&v!=='—');return [...new Set(values)].join('；')||'—'}
function 线(points,color,width=1.5){return `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="${width}" stroke-linejoin="round" stroke-linecap="round"/>`}
function 画K线(ticker,d){const svg=document.getElementById('日K图'),x=图表[ticker];if(!x||!x.c||x.c.length<2){svg.innerHTML='<text x="470" y="250" text-anchor="middle" fill="#6b7078" font-size="14">暂无本地日 K 缓存</text>';return}const n=x.c.length,W=940,H=520,left=52,right=72,top=24,priceBottom=390,volTop=414,volBottom=490;let vals=[...x.h,...x.l,...x.e20,...x.e50,...x.e200].filter(Number.isFinite);for(const k of ['buy','breakout','stop','target']){const v=Number(d[k]);if(Number.isFinite(v))vals.push(v)}let lo=Math.min(...vals),hi=Math.max(...vals),pad=Math.max((hi-lo)*.06,hi*.005,1e-6);lo-=pad;hi+=pad;const pw=W-left-right,step=pw/n,cw=Math.max(1.4,Math.min(7,step*.58));const X=i=>left+(i+.5)*step,Y=v=>top+(hi-v)/(hi-lo)*(priceBottom-top),vmax=Math.max(...x.v,1),V=v=>volBottom-v/vmax*(volBottom-volTop);let out=`<rect x="0" y="0" width="${W}" height="${H}" fill="#fff"/>`;for(let i=0;i<5;i++){const yy=top+i*(priceBottom-top)/4,price=hi-i*(hi-lo)/4;out+=`<line x1="${left}" x2="${W-right}" y1="${yy}" y2="${yy}" stroke="#eceef1"/><text x="${W-right+8}" y="${yy+4}" fill="#6b7078" font-size="10">${price.toFixed(2)}</text>`}for(let i=0;i<n;i++){const up=x.c[i]>=x.o[i],color=up?'#E33D3D':'#197A55',xx=X(i),yo=Y(x.o[i]),yc=Y(x.c[i]),yh=Y(x.h[i]),yl=Y(x.l[i]);out+=`<line x1="${xx}" x2="${xx}" y1="${yh}" y2="${yl}" stroke="${color}"/><rect x="${xx-cw/2}" y="${Math.min(yo,yc)}" width="${cw}" height="${Math.max(1,Math.abs(yc-yo))}" fill="${color}"/><rect x="${xx-cw/2}" y="${V(x.v[i])}" width="${cw}" height="${volBottom-V(x.v[i])}" fill="${color}" opacity=".35"/>`}for(const [arr,color] of [[x.e20,'#1769AA'],[x.e50,'#B56A13'],[x.e200,'#6955B8']]){const pts=arr.map((v,i)=>Number.isFinite(v)?`${X(i).toFixed(1)},${Y(v).toFixed(1)}`:null).filter(Boolean).join(' ');out+=线(pts,color,1.35)}const levels=[['buy','买点','#1769AA'],['breakout','突破','#B56A13'],['stop','止损','#197A55'],['target','目标','#E33D3D']];for(const [k,label,color] of levels){const v=Number(d[k]);if(!Number.isFinite(v)||v<lo||v>hi)continue;const yy=Y(v);out+=`<line x1="${left}" x2="${W-right}" y1="${yy}" y2="${yy}" stroke="${color}" stroke-dasharray="5 4" opacity=".75"/><text x="${left+4}" y="${yy-4}" fill="${color}" font-size="10" font-weight="700">${label} ${v.toFixed(2)}</text>`}out+=`<text x="${left}" y="${H-8}" fill="#6b7078" font-size="10">${x.d[0]}</text><text x="${W-right}" y="${H-8}" text-anchor="end" fill="#6b7078" font-size="10">${x.d[n-1]}</text><text x="${left}" y="${top-7}" fill="#1769AA" font-size="10">EMA20</text><text x="${left+48}" y="${top-7}" fill="#B56A13" font-size="10">EMA50</text><text x="${left+96}" y="${top-7}" fill="#6955B8" font-size="10">EMA200</text>`;svg.innerHTML=out}
function 打开(ticker){const d=详情[ticker];if(!d)return;document.getElementById('详情标题').textContent=`${d.ticker} · ${d.name||''}`;document.getElementById('详情副标题').textContent=`${d.asset||''} · ${d.topic||''} · 数据日 ${d.asof||'—'}`;document.getElementById('详情格').innerHTML=详情项('研究排名','#'+文本(d.researchRank))+详情项('交易排名','#'+文本(d.tradeRank))+详情项('Alpha',数字(d.alpha,1))+详情项('执行状态',d.execution)+详情项('技术信号',d.signal)+详情项('质量层',d.quality)+详情项('收盘',数字(d.close,3))+详情项('参考买点',d.buyText)+详情项('止损',数字(d.stop,3))+详情项('目标',数字(d.target,3))+详情项('盈亏比',数字(d.rr,2))+详情项('平滑触发',数字(d.smoothTrigger,1))+可选详情项('方向性研究准入',d.directionalGate)+可选详情项('突破价格确认',突破诊断(d))+可选详情项('估算往返成本',百分比(d.roundTripCostPct))+可选详情项('目标 / 成本',倍数(d.targetCostMultiple))+可选详情项('执行经济性',d.economicsGate);document.getElementById('解释').textContent=诊断说明(d);画K线(ticker,d);遮罩.classList.add('open')}
for(const row of document.querySelectorAll('[data-ticker]'))row.addEventListener('click',()=>打开(row.dataset.ticker));
"""


def _render_html(
    *,
    report_date: str,
    all_rows: list[dict[str, str]],
    views: dict[str, list[dict[str, str]]],
    merged_rows: list[dict[str, str]],
    memberships: dict[str, set[str]],
    daily: dict[str, object],
    backtest: dict[str, object],
    history_href: str,
) -> str:
    charts, spark = _v84._chart_payload(merged_rows, report_date)
    details = _details_payload(merged_rows)
    top_rows = views.get("mixed", merged_rows)
    sector_items = _sector_rotation(all_rows)
    risk_items = _risk_items(all_rows, daily)
    change_items = _run_changes(daily)
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    stage_raw = daily.get("stage_seconds", {})
    stages = stage_raw if isinstance(stage_raw, dict) else {}
    daily_backtest_raw = daily.get("backtest", {})
    daily_backtest = daily_backtest_raw if isinstance(daily_backtest_raw, dict) else {}
    mode = str(backtest.get("mode", "") or daily_backtest.get("run_mode", "") or daily_backtest.get("mode", "") or "—").upper()
    samples = backtest.get("samples", daily_backtest.get("signal_sample_tickers", "—"))
    run_items = (
        ("总耗时", _v84._duration(daily.get("elapsed_seconds", ""))),
        ("扫描", _v84._duration(stages.get("scan", ""))),
        ("回测", _v84._duration(stages.get("backtest", ""))),
        ("回测模式", mode),
        ("信号样本标的", samples or "—"),
        ("发布状态", str(daily.get("publish_status", "已生成") or "已生成")),
        ("页面版本", "v87"),
    )
    run_html = "".join(
        f'<div class="run-item"><span>{_v84._safe(label)}</span><strong>{_v84._safe(value)}</strong></div>'
        for label, value in run_items
    )
    tabs = (
        ("mixed", "综合"),
        ("stocks", "股票"),
        ("etf", "ETF"),
        ("ready", "可执行"),
        ("new", "新信号"),
        ("sustained", "持续"),
        ("risk", "风险"),
        ("all", "全部"),
    )
    tab_html = "".join(
        f'<button class="tab{" active" if key == "mixed" else ""}" data-view="{key}">{_v84._safe(label)}</button>'
        for key, label in tabs
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="description" content="{_v84._safe(PAGE_LABEL)} {report_date}"><title>{_v84._safe(report_date)} · {_v84._safe(PAGE_LABEL)}</title><style>{_CSS}</style></head>
<body><main class="shell">
<header class="masthead"><div class="brand"><strong>{_v84._safe(BRAND_LABEL)}</strong><span>{_v84._safe(PAGE_LABEL)}</span></div><div class="date">{_v84._safe(report_date)}</div><div class="live">LIVE · 数据就绪</div></header>
<div class="meta"><span>生成 {_v84._safe(generated)}</span><span>·</span><span>行情严格截止 {_v84._safe(report_date)}</span><span>·</span><a href="{_v84._safe(history_href)}">历史报告</a><span>· 点击标的查看日 K 与研究详情</span></div>
<section class="section" data-section="{HOME_SECTIONS[0]}"><div class="section-head"><h2>{_v84._safe(SECTION_TITLES[HOME_SECTIONS[0]])}</h2><p>研究价值、执行状态与数据质量分层展示</p></div><div class="metrics">{_metric_cards(all_rows, charts, daily)}</div></section>
<section class="section card" data-section="{HOME_SECTIONS[1]}"><div class="section-head"><h2>{_v84._safe(SECTION_TITLES[HOME_SECTIONS[1]])}</h2><p>研究排名不等于即时执行许可</p></div><div class="table-wrap"><table><thead><tr><th>研究#</th><th>代码 / 名称</th><th>收盘</th><th>ALPHA</th><th>执行状态</th><th>参考买点</th><th>止损</th><th>目标</th><th>TREND</th></tr></thead><tbody>{_top_rows_html(top_rows, spark)}</tbody></table></div>
<div class="subhead"><strong>RESEARCH UNIVERSE / 研究池</strong><div class="tabs">{tab_html}</div><span id="可见计数" class="visible-count">0 ROWS</span></div>
<div class="section-head"><p>只发布显式白名单字段</p><div class="controls"><input id="搜索" placeholder="搜索代码 / 名称 / 行业"><select id="类型"><option value="">全部类型</option><option>股票</option><option>ETF</option></select><select id="执行"><option value="">全部状态</option><option>可执行</option><option>谨慎</option><option>观察</option><option>阻断</option></select></div></div>
<div class="table-wrap"><table><thead><tr><th>研究#</th><th>交易#</th><th>代码 / 名称</th><th>收盘</th><th>ALPHA</th><th>执行状态</th><th>技术信号</th><th>参考买点</th><th>止损</th><th>目标</th><th>TREND</th></tr></thead><tbody id="研究表">{_research_rows_html(merged_rows, memberships, spark)}</tbody></table></div></section>
<section class="section card" data-section="{HOME_SECTIONS[2]}"><div class="section-head"><h2>{_v84._safe(SECTION_TITLES[HOME_SECTIONS[2]])}</h2><p>按研究池前 400 名聚合，不新增模型权重</p></div><div class="sector-grid">{_sector_html(sector_items)}</div></section>
<div class="split"><section class="section card" data-section="{HOME_SECTIONS[3]}"><div class="section-head"><h2>{_v84._safe(SECTION_TITLES[HOME_SECTIONS[3]])}</h2><p>展示阻断和数据完整性</p></div><div class="diagnostic-grid">{_diagnostic_html(risk_items)}</div></section>
<section class="section card" data-section="{HOME_SECTIONS[4]}"><div class="section-head"><h2>{_v84._safe(SECTION_TITLES[HOME_SECTIONS[4]])}</h2><p>与上一份已发布结果比较</p></div><div class="diagnostic-grid">{_diagnostic_html(change_items)}</div></section></div>
<section class="section card" data-section="{HOME_SECTIONS[5]}"><div class="section-head"><h2>{_v84._safe(SECTION_TITLES[HOME_SECTIONS[5]])}</h2><p>仅公开运行摘要，不公开日志、路径和缓存</p></div><div class="run-grid">{run_html}</div></section>
<div class="foot">本页用于量化研究、数据分析与策略复盘，不构成投资建议或收益承诺。历史报告的 K 线固定截断到对应报告日。 · {_v84._safe(WEB_REPORT_VERSION)}</div>
</main><div id="遮罩" class="drawer-mask"><aside class="drawer" role="dialog" aria-modal="true"><div class="drawer-head"><div><h3 id="详情标题">标的详情</h3><p id="详情副标题"></p></div><button id="关闭" class="close" aria-label="关闭">×</button></div><div id="详情格" class="detail-grid"></div><div class="chart-frame"><svg id="日K图" viewBox="0 0 940 520" preserveAspectRatio="xMidYMid meet"></svg></div><div id="解释" class="explanation"></div></aside></div>
<script id="图表数据" type="application/json">{_v84._json_for_script(charts)}</script><script id="详情数据" type="application/json">{_v84._json_for_script(details)}</script><script>{_JS}</script></body></html>"""


def _archive_html(site_dir: Path) -> str:
    report_dir = Path(site_dir) / "reports"
    pages = sorted(
        (path for path in report_dir.glob("????-??-??.html") if path.is_file()),
        key=lambda path: path.stem,
        reverse=True,
    )
    items = "".join(
        f'<li><a href="{_v84._safe(path.name)}"><span>{_v84._safe(path.stem)}</span><b>OPEN →</b></a></li>'
        for path in pages
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>历史研究简报</title><style>body{{margin:0;background:#f1f2f4;color:#15171a;font-family:"Microsoft YaHei UI",system-ui,sans-serif;padding:28px}}main{{max-width:920px;margin:auto}}header{{display:flex;align-items:end;gap:18px;border-bottom:1px solid #15171a;padding-bottom:14px}}h1{{margin:0;font:700 28px ui-monospace,Consolas,monospace}}p a{{color:#15171a;text-decoration:none;border-bottom:1px solid #e33d3d}}ul{{list-style:none;padding:0;background:#fff;border:1px solid #d9dde3}}li{{border-bottom:1px solid #d9dde3}}li:last-child{{border:0}}li a{{display:flex;justify-content:space-between;padding:14px 16px;color:#15171a;text-decoration:none;font-family:ui-monospace,Consolas,monospace}}li a:hover{{background:#f7f8fa;color:#b52b32}}</style></head><body><main><header><h1>HISTORY / 历史研究简报</h1></header><p><a href="../index.html">← 返回最新报告</a></p><ul>{items or '<li><a><span>暂无历史报告</span></a></li>'}</ul></main></body></html>"""


def build_web_report(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    site_dir: Path = DEFAULT_SITE_DIR,
) -> WebReportResult:
    output_dir = Path(output_dir)
    source_dir = _published_source_dir(output_dir)
    all_rows = _read_view(source_dir, output_dir, ("AllResults.csv", "DecisionResults.csv"))
    mixed_rows = _read_view(source_dir, output_dir, ("Top50Mixed.csv", "Top50.csv")) or all_rows
    if not all_rows and mixed_rows:
        all_rows = mixed_rows
    if not all_rows:
        raise RuntimeError("WEB_REPORT_NO_RESULTS: no published CSV results found")
    daily = _v84._read_json(source_dir / "DailyRunSummary.json") or _v84._read_json(output_dir / "DailyRunSummary.json")
    backtest = _v84._read_json(source_dir / "BacktestSummary.json") or _v84._read_json(output_dir / "BacktestSummary.json")
    report_date = _v84._report_date(all_rows, daily)
    views = _build_views(source_dir=source_dir, output_dir=output_dir, all_rows=all_rows, mixed_rows=mixed_rows)
    merged_rows, memberships = _merge_view_rows(views)
    site_dir = Path(site_dir)
    report_dir = site_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    latest = _render_html(
        report_date=report_date,
        all_rows=all_rows,
        views=views,
        merged_rows=merged_rows,
        memberships=memberships,
        daily=daily,
        backtest=backtest,
        history_href="reports/index.html",
    )
    archive = _render_html(
        report_date=report_date,
        all_rows=all_rows,
        views=views,
        merged_rows=merged_rows,
        memberships=memberships,
        daily=daily,
        backtest=backtest,
        history_href="index.html",
    )
    archive_path = report_dir / f"{report_date}.html"
    index_path = site_dir / "index.html"
    archive_path.write_text(archive, encoding="utf-8")
    index_path.write_text(latest, encoding="utf-8")
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    (report_dir / "index.html").write_text(_archive_html(site_dir), encoding="utf-8")
    return WebReportResult(report_date=report_date, index_path=index_path, archive_path=archive_path)


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
    remote = _run_git(["-C", str(repo_root), "remote", "get-url", "origin"]).stdout.strip()
    page_url = github_pages_url_from_remote(remote)
    if not page_url:
        raise RuntimeError("WEB_REPORT_UNSUPPORTED_REMOTE: origin is not github.com")
    exists = (
        _run_git(
            ["-C", str(repo_root), "ls-remote", "--exit-code", "--heads", "origin", branch],
            allow=(0, 2),
        ).returncode
        == 0
    )
    with tempfile.TemporaryDirectory(prefix="institution-web-") as temp_dir:
        worktree = Path(temp_dir) / "site"
        if exists:
            _run_git(
                ["clone", "--quiet", "--depth", "1", "--branch", branch, "--single-branch", remote, str(worktree)],
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
        publish_paths = ["index.html", ".nojekyll", "reports"]
        performance_page = site_dir / "performance.html"
        if performance_page.is_file():
            shutil.copy2(performance_page, worktree / "performance.html")
            publish_paths.append("performance.html")
        (worktree / "reports" / "index.html").write_text(_archive_html(worktree), encoding="utf-8")
        _run_git(["add", "--", *publish_paths], cwd=worktree)
        diff = _run_git(["diff", "--cached", "--quiet"], cwd=worktree, allow=(0, 1))
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
                    f"report: research briefing {stamp}",
                ],
                cwd=worktree,
            )
            _run_git(["push", "origin", f"HEAD:{branch}"], cwd=worktree, timeout=90)
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
    log.info("WEB research briefing generated: %s (%s).", built.archive_path, reason)
    if not _v84._truthy_env(WEB_PUBLISH_ENV, True):
        log.info("WEB publication disabled by %s.", WEB_PUBLISH_ENV)
        return built
    try:
        published = publish_site(site_dir, repo_root=PROJECT_ROOT, report_date=built.report_date)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        log.warning("WEB report publication skipped/failed without affecting pipeline: %s", exc)
        return WebReportResult(
            report_date=built.report_date,
            index_path=built.index_path,
            archive_path=built.archive_path,
            publish_message=str(exc),
        )
    log.info("WEB research briefing published: %s", published.page_url)
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
        return build_and_publish_web_report(output_dir=Path(output_dir), logger=logger, reason=reason)
    except (OSError, RuntimeError, csv.Error) as exc:
        log = logger or logging.getLogger("institution_scanner")
        log.warning("WEB research briefing generation skipped/failed without affecting pipeline: %s", exc)
        return None
