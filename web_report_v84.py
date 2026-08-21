"""v84 中文研究终端静态网页与 GitHub Pages 发布器。

目标：
- 保留扫描器/评分器为唯一事实来源；本模块只负责展示。
- 页面完全静态、自包含，不依赖 React/Vue/后端服务。
- 表格趋势图使用内嵌 SVG；详情日 K 使用浏览器原生 SVG。
- K 线只读取本地 TickFlow 日线缓存，并严格截断到报告日，避免历史报告混入未来数据。
- 仅允许公开研究字段进入 HTML；不会发布日志、密钥、账户信息或原始缓存文件。
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
from typing import Any

import numpy as np
import pandas as pd

from downloader import _load_cache

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output"
DEFAULT_SITE_DIR = DEFAULT_OUTPUT_DIR / "web_report"
WEB_REPORT_VERSION = "2026-08-21-v84-chinese-research-terminal-v1"
WEB_PUBLISH_ENV = "INSTITUTION_SCANNER_WEB_PUBLISH"
WEB_REPORT_ROWS_ENV = "INSTITUTION_SCANNER_WEB_REPORT_ROWS"
WEB_CHART_ROWS_ENV = "INSTITUTION_SCANNER_WEB_CHART_ROWS"
GH_PAGES_BRANCH = "gh-pages"

_PUBLIC_COLUMNS = (
    "OverallRank",
    "CandidateViewRank",
    "ResearchPoolRank",
    "ResearchRank",
    "ResearchPercentile",
    "AssetClassRank",
    "TradeRank",
    "Ticker",
    "Name",
    "AssetType",
    "IsETF",
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
    "AlphaScore",
    "ExecutionState",
    "ExecutionEligible",
    "QualityLayerStatus",
    "QualityPolicyGatePassed",
    "QualityDataIntegrityPassed",
    "SmoothTriggerScore",
    "SmoothAlphaScore",
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
    "DecisionReason",
    "RankingReason",
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


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(os.environ.get(name, str(default)))))
    except ValueError:
        return default


def _max_rows() -> int:
    return _bounded_env_int(WEB_REPORT_ROWS_ENV, 250, 25, 1000)


def _max_chart_rows() -> int:
    return _bounded_env_int(WEB_CHART_ROWS_ENV, 80, 10, 250)


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
    return parsed if np.isfinite(parsed) else None


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
        return f"{hours}小时{minute:02d}分"
    if minutes:
        return f"{minutes}分{sec:02d}秒"
    return f"{sec}秒"


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "是"}


def _asset_type(row: dict[str, str]) -> str:
    value = row.get("AssetType", "").strip().upper()
    if value == "ETF" or _truthy(row.get("IsETF", "")):
        return "ETF"
    code = row.get("Ticker", "").split(".", 1)[0]
    if code.startswith(("15", "16", "50", "51", "56", "58")):
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
    signal = row.get("EntrySignal", "").strip().upper()
    if signal == "BREAKOUT_CONFIRM" and row.get("BreakoutBuyPrice", ""):
        return row["BreakoutBuyPrice"]
    return row.get("EntryZone", "") or row.get("BreakoutBuyPrice", "") or "—"


def _rank_value(row: dict[str, str], key: str, fallback: float = 1_000_000.0) -> float:
    value = _number(row.get(key, ""))
    return value if value is not None and value > 0 else fallback


def _sort_key(row: dict[str, str]) -> tuple[float, float, str]:
    research = _rank_value(row, "ResearchRank")
    if research >= 1_000_000:
        research = min(
            _rank_value(row, "CandidateViewRank"),
            _rank_value(row, "ResearchPoolRank"),
            _rank_value(row, "OverallRank"),
        )
    alpha = _number(row.get("AlphaScore", "") or row.get("FinalScore", ""))
    return research, -(alpha if alpha is not None else -1_000_000.0), row.get("Ticker", "")


def _decorate_rows(rows: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    for position, source in enumerate(sorted(rows, key=_sort_key), start=1):
        row = dict(source)
        row["DisplayResearchRank"] = (
            row.get("ResearchRank", "")
            or row.get("CandidateViewRank", "")
            or row.get("ResearchPoolRank", "")
            or row.get("OverallRank", "")
            or str(position)
        )
        row["DisplayTradeRank"] = row.get("TradeRank", "") or "—"
        row["AssetType"] = _asset_type(row)
        row["IndustryTopic"] = _industry_topic(row)
        row["ReferenceBuyPrice"] = _reference_buy_price(row)
        row["DisplayAlpha"] = row.get("AlphaScore", "") or row.get("FinalScore", "") or ""
        row["DisplayExecution"] = row.get("ExecutionState", "") or row.get("RankingEligibility", "") or "观察"
        output.append(row)
    return output


def _summary_counts(rows: list[dict[str, str]]) -> dict[str, int]:
    counts = {"total": len(rows), "stocks": 0, "etfs": 0, "ready": 0, "cautious": 0, "new": 0}
    for row in rows:
        counts["etfs" if _asset_type(row) == "ETF" else "stocks"] += 1
        state = (row.get("ExecutionState", "") or "").strip().upper()
        eligibility = row.get("RankingEligibility", "")
        counts["ready"] += state == "READY" or eligibility == "推荐"
        counts["cautious"] += state == "CAUTIOUS" or eligibility == "谨慎候选"
        counts["new"] += row.get("SignalStatus", "").strip().upper() == "NEW"
    return counts


def _safe(text: object) -> str:
    return html.escape(str(text or ""), quote=True)


def _execution_label(value: str) -> tuple[str, str]:
    normalized = str(value or "").strip().upper()
    mapping = {
        "READY": ("可执行", "ready"),
        "CAUTIOUS": ("谨慎", "cautious"),
        "OBSERVE": ("观察", "observe"),
        "BLOCKED": ("阻断", "blocked"),
        "推荐": ("可执行", "ready"),
        "谨慎候选": ("谨慎", "cautious"),
        "观察": ("观察", "observe"),
        "风险过滤": ("阻断", "blocked"),
    }
    return mapping.get(normalized, (normalized or "观察", "observe"))


def _signal_label(value: str) -> str:
    return {
        "BUY_NOW": "回调可买",
        "BREAKOUT_CONFIRM": "突破确认",
        "WAIT_PULLBACK": "等待回调",
        "PRICE_BREAKOUT": "价格突破待放量",
        "WAIT_VOLUME_CONFIRM": "等待量能确认",
        "HOLD_WAIT": "继续观察",
        "AVOID": "回避",
    }.get(str(value or "").strip().upper(), str(value or "").strip() or "—")


def _quality_label(value: str) -> str:
    return {
        "PASS": "通过",
        "POLICY_FAIL": "质量策略未通过",
        "DATA_INCOMPLETE": "关键数据不完整",
        "NOT_APPLICABLE": "不适用",
    }.get(str(value or "").strip().upper(), str(value or "").strip() or "—")


def _parse_price_level(value: object) -> float | None:
    direct = _number(value)
    if direct is not None:
        return direct
    text = str(value or "")
    numbers = [float(part) for part in re.findall(r"-?\d+(?:\.\d+)?", text)]
    if not numbers:
        return None
    return float(sum(numbers) / len(numbers))


def _sparkline_svg(closes: list[float], width: int = 92, height: int = 28) -> str:
    values = np.asarray(closes[-30:], dtype=np.float64)
    values = values[np.isfinite(values)]
    if values.size < 2:
        return '<span class="无趋势">—</span>'
    low, high = float(values.min()), float(values.max())
    spread = max(high - low, abs(high) * 1e-6, 1e-9)
    x = np.linspace(2.0, width - 2.0, values.size)
    y = (height - 3.0) - (values - low) / spread * (height - 6.0)
    points = " ".join(f"{a:.1f},{b:.1f}" for a, b in zip(x, y, strict=True))
    color = "#E33D3D" if values[-1] >= values[0] else "#197A55"
    return (
        f'<svg class="趋势图" width="{width}" height="{height}" viewBox="0 0 {width} {height}" aria-hidden="true">'
        f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
        f'<circle cx="{x[-1]:.1f}" cy="{y[-1]:.1f}" r="2.2" fill="{color}"/></svg>'
    )


def _chart_frame(ticker: str, report_date: str, bars: int = 120) -> pd.DataFrame | None:
    try:
        frame = _load_cache(ticker)
    except (OSError, ValueError, TypeError, ImportError):
        return None
    if frame is None or frame.empty:
        return None
    result = frame.copy()
    index = pd.to_datetime(result.index, errors="coerce")
    result = result.loc[index.notna()].copy()
    result.index = pd.DatetimeIndex(index[index.notna()])
    cutoff = pd.Timestamp(report_date)
    result = result.loc[result.index.normalize() <= cutoff.normalize()].tail(max(30, bars))
    required = ["Open", "High", "Low", "Close", "Volume"]
    if result.empty or any(column not in result.columns for column in required):
        return None
    for column in required:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    if result.empty:
        return None
    close = result["Close"]
    result["EMA20"] = close.ewm(span=20, adjust=False, min_periods=1).mean()
    result["EMA50"] = close.ewm(span=50, adjust=False, min_periods=1).mean()
    result["EMA200"] = close.ewm(span=200, adjust=False, min_periods=1).mean()
    return result.tail(bars)


def _chart_payload(rows: list[dict[str, str]], report_date: str) -> tuple[dict[str, object], dict[str, list[float]]]:
    charts: dict[str, object] = {}
    spark: dict[str, list[float]] = {}
    for row in rows[: _max_chart_rows()]:
        ticker = row.get("Ticker", "")
        if not ticker:
            continue
        frame = _chart_frame(ticker, report_date)
        if frame is None:
            continue
        spark[ticker] = [round(float(value), 6) for value in frame["Close"].tail(30)]
        charts[ticker] = {
            "d": [stamp.strftime("%Y-%m-%d") for stamp in frame.index],
            "o": np.round(frame["Open"].to_numpy(dtype=float), 6).tolist(),
            "h": np.round(frame["High"].to_numpy(dtype=float), 6).tolist(),
            "l": np.round(frame["Low"].to_numpy(dtype=float), 6).tolist(),
            "c": np.round(frame["Close"].to_numpy(dtype=float), 6).tolist(),
            "v": np.round(frame["Volume"].to_numpy(dtype=float), 2).tolist(),
            "e20": np.round(frame["EMA20"].to_numpy(dtype=float), 6).tolist(),
            "e50": np.round(frame["EMA50"].to_numpy(dtype=float), 6).tolist(),
            "e200": np.round(frame["EMA200"].to_numpy(dtype=float), 6).tolist(),
        }
    return charts, spark


def _details_payload(rows: list[dict[str, str]]) -> dict[str, dict[str, object]]:
    payload: dict[str, dict[str, object]] = {}
    for row in rows[: _max_rows()]:
        ticker = row.get("Ticker", "")
        if not ticker:
            continue
        execution_label, _ = _execution_label(row.get("DisplayExecution", ""))
        payload[ticker] = {
            "ticker": ticker,
            "name": row.get("Name", ""),
            "asset": row.get("AssetType", ""),
            "topic": row.get("IndustryTopic", ""),
            "close": _number(row.get("Close", "")),
            "researchRank": row.get("DisplayResearchRank", "—"),
            "tradeRank": row.get("DisplayTradeRank", "—"),
            "alpha": _number(row.get("DisplayAlpha", "")),
            "ranking": _number(row.get("RankingScore", "")),
            "execution": execution_label,
            "signal": _signal_label(row.get("EntrySignal", "")),
            "signalStatus": row.get("SignalStatus", "") or "—",
            "signalDays": row.get("SignalDays", "") or "—",
            "quality": _quality_label(row.get("QualityLayerStatus", "")),
            "smoothTrigger": _number(row.get("SmoothTriggerScore", "")),
            "buyText": row.get("ReferenceBuyPrice", "") or "—",
            "buy": _parse_price_level(row.get("ReferenceBuyPrice", "")),
            "breakout": _number(row.get("BreakoutBuyPrice", "")),
            "stop": _number(row.get("StopLoss", "")),
            "target": _number(row.get("ProjectedTarget", "")),
            "rr": _number(row.get("RewardRiskRatio", "")),
            "reason": row.get("TradeReadinessReason", "") or row.get("DecisionReason", "") or row.get("RankingReason", "") or "—",
            "asof": row.get("DataAsOf", "") or "—",
        }
    return payload


def _table_html(rows: list[dict[str, str]], spark: dict[str, list[float]]) -> str:
    body: list[str] = []
    for row in rows[: _max_rows()]:
        ticker = row.get("Ticker", "")
        execution_label, execution_class = _execution_label(row.get("DisplayExecution", ""))
        name = row.get("Name", "") or "—"
        topic = row.get("IndustryTopic", "") or "—"
        trend = _sparkline_svg(spark.get(ticker, []))
        body.append(
            "<tr class=\"标的行\" "
            f'data-ticker="{_safe(ticker)}" data-asset="{_safe(row.get("AssetType", ""))}" '
            f'data-execution="{_safe(execution_label)}" data-search="{_safe((ticker + " " + name + " " + topic).casefold())}">'
            f'<td class="排名">{_safe(row.get("DisplayResearchRank", "—"))}</td>'
            f'<td class="排名 次级">{_safe(row.get("DisplayTradeRank", "—"))}</td>'
            f'<td class="证券"><strong>{_safe(ticker)}</strong><span>{_safe(name)} · {_safe(topic)}</span></td>'
            f'<td class="数字">{_safe(_fmt_number(row.get("Close", ""), 3 if row.get("AssetType") == "ETF" else 2))}</td>'
            f'<td class="数字 强调">{_safe(_fmt_number(row.get("DisplayAlpha", ""), 1))}</td>'
            f'<td><span class="状态 {execution_class}">{_safe(execution_label)}</span></td>'
            f'<td class="数字">{_safe(row.get("ReferenceBuyPrice", "—"))}</td>'
            f'<td class="数字">{_safe(_fmt_number(row.get("StopLoss", ""), 3 if row.get("AssetType") == "ETF" else 2))}</td>'
            f'<td class="数字">{_safe(_fmt_number(row.get("ProjectedTarget", ""), 3 if row.get("AssetType") == "ETF" else 2))}</td>'
            f'<td class="趋势">{trend}</td>'
            "</tr>"
        )
    return "\n".join(body)


def _json_for_script(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


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
    charts, spark = _chart_payload(display_rows, report_date)
    details = _details_payload(display_rows)
    rows_html = _table_html(display_rows, spark)
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    stages_raw = daily.get("stage_seconds", {})
    stages = stages_raw if isinstance(stages_raw, dict) else {}
    daily_backtest_raw = daily.get("backtest", {})
    daily_backtest = daily_backtest_raw if isinstance(daily_backtest_raw, dict) else {}
    mode = str(backtest.get("mode", "") or daily_backtest.get("mode", "") or "—").upper()
    samples = backtest.get("samples", daily_backtest.get("samples", ""))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="description" content="机构交易研究终端 {report_date}"><title>{report_date} · 机构交易研究终端</title>
<style>
:root{{--背景:#f1f2f4;--纸:#fff;--墨:#15171a;--次:#6b7078;--线:#d9dde3;--软:#eef0f3;--红:#e33d3d;--红深:#b52b32;--绿:#197a55;--黄:#b56a13;--蓝:#1769aa}}
*{{box-sizing:border-box}}html{{background:var(--背景)}}body{{margin:0;background:var(--背景);color:var(--墨);font-family:"Microsoft YaHei UI","PingFang SC",system-ui,sans-serif;padding:28px 30px 50px;-webkit-font-smoothing:antialiased}}
.终端{{max-width:1680px;margin:auto}}.页眉{{position:relative;display:flex;align-items:flex-end;gap:18px;border-bottom:1px solid var(--墨);padding-bottom:18px;margin-bottom:20px}}.页眉:after{{content:"";position:absolute;right:0;bottom:-6px;width:12px;height:12px;background:var(--红)}}
.品牌{{font:700 14px ui-monospace,"SFMono-Regular",Consolas,monospace;letter-spacing:2px}}.日期{{font:700 44px ui-monospace,"SFMono-Regular",Consolas,monospace;letter-spacing:-2px;line-height:.9}}.数据状态{{margin-left:auto;background:var(--墨);color:#fff;padding:7px 10px;font:700 11px ui-monospace,Consolas,monospace;letter-spacing:1px}}
.说明{{font-size:12px;color:var(--次);margin-top:-8px;margin-bottom:18px}}a{{color:var(--蓝);text-decoration:none}}
.概览{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-bottom:18px}}.概览卡{{background:var(--纸);border:1px solid var(--线);border-top:3px solid var(--墨);padding:14px 16px;min-height:84px}}.概览卡.红{{border-top-color:var(--红)}}.概览卡 .题{{font-size:11px;color:var(--次);font-weight:700;letter-spacing:1px}}.概览卡 .值{{font:700 25px ui-monospace,Consolas,monospace;margin-top:5px}}.概览卡 .注{{font-size:11px;color:var(--次);margin-top:3px}}
.卡片{{background:var(--纸);border:1px solid var(--线);margin-bottom:16px;box-shadow:0 2px 0 rgba(21,23,26,.03)}}.卡头{{display:flex;align-items:center;gap:12px;background:var(--墨);color:#fff;border-left:5px solid var(--红);padding:11px 14px;min-height:46px}}.卡头 h2{{margin:0;font:700 13px ui-monospace,Consolas,monospace;letter-spacing:1px}}.卡头 .副{{font-size:11px;color:#cfd3d8}}
.筛选{{margin-left:auto;display:flex;gap:7px;align-items:center;flex-wrap:wrap}}.筛选 input,.筛选 select{{height:30px;border:1px solid #444a50;background:#24272b;color:#fff;padding:0 9px;font-size:11px;outline:none}}.筛选 input{{min-width:220px}}
.表包{{overflow:auto;max-height:72vh}}table{{width:100%;border-collapse:collapse;white-space:nowrap;font-size:13px}}th{{position:sticky;top:0;z-index:2;background:#1f2226;color:#fff;padding:10px 11px;text-align:center;font:700 10px ui-monospace,Consolas,monospace;letter-spacing:.7px;border-right:1px solid rgba(255,255,255,.09)}}td{{padding:10px 11px;border-bottom:1px solid var(--线);text-align:center;font-variant-numeric:tabular-nums}}tbody tr{{cursor:pointer}}tbody tr:hover td{{background:#f7f8fa}}td.证券{{text-align:left;min-width:190px}}td.证券 strong{{display:block;font:700 13px ui-monospace,Consolas,monospace}}td.证券 span{{display:block;color:var(--次);font-size:11px;margin-top:2px;max-width:230px;overflow:hidden;text-overflow:ellipsis}}td.数字{{font-family:ui-monospace,Consolas,monospace}}td.强调{{font-weight:700}}td.排名{{font:700 12px ui-monospace,Consolas,monospace}}td.排名.次级{{color:var(--次)}}td.趋势{{width:118px;padding:5px 10px}}.趋势图{{display:block;margin:auto}}
.状态{{display:inline-block;padding:4px 7px;border-left:3px solid;font-size:11px;font-weight:700;background:#f5f6f7}}.状态.ready{{color:var(--红);border-color:var(--红)}}.状态.cautious{{color:var(--黄);border-color:var(--黄)}}.状态.observe{{color:#5e6670;border-color:#9aa0a6}}.状态.blocked{{color:var(--绿);border-color:var(--绿)}}
.运行格{{display:grid;grid-template-columns:repeat(6,1fr);gap:1px;background:var(--线)}}.运行项{{background:#fff;padding:13px 14px}}.运行项 span{{display:block;color:var(--次);font-size:10px}}.运行项 strong{{display:block;margin-top:4px;font:700 13px ui-monospace,Consolas,monospace}}
.抽屉遮罩{{position:fixed;inset:0;background:rgba(0,0,0,.28);display:none;z-index:20}}.抽屉遮罩.开{{display:block}}.抽屉{{position:absolute;right:0;top:0;height:100%;width:min(980px,94vw);background:#f6f7f8;border-left:1px solid #aaa;overflow:auto;padding:18px 20px 28px;box-shadow:-12px 0 35px rgba(0,0,0,.18)}}.抽屉头{{display:flex;align-items:flex-start;border-bottom:1px solid var(--墨);padding-bottom:12px;margin-bottom:12px}}.抽屉头 h3{{margin:0;font-size:21px}}.抽屉头 p{{margin:4px 0 0;color:var(--次);font-size:12px}}.关闭{{margin-left:auto;border:1px solid var(--墨);background:#fff;width:34px;height:34px;cursor:pointer;font-size:18px}}
.详情格{{display:grid;grid-template-columns:repeat(6,1fr);gap:7px;margin:10px 0 12px}}.详情项{{background:#fff;border:1px solid var(--线);padding:9px 10px;min-height:58px}}.详情项 span{{display:block;color:var(--次);font-size:10px}}.详情项 strong{{display:block;margin-top:4px;font:700 12px ui-monospace,Consolas,monospace}}.图框{{background:#fff;border:1px solid var(--线);padding:8px}}#日K图{{width:100%;height:auto;display:block;min-height:420px}}.解释{{background:#fff;border:1px solid var(--线);padding:12px;margin-top:9px;font-size:12px;line-height:1.65}}
.页脚{{color:var(--次);font-size:10px;line-height:1.65;border-top:1px solid var(--线);padding-top:12px;margin-top:16px}}.隐藏{{display:none!important}}
@media(max-width:900px){{body{{padding:18px 10px 36px}}.日期{{font-size:31px}}.概览{{grid-template-columns:repeat(2,1fr)}}.运行格{{grid-template-columns:repeat(3,1fr)}}.详情格{{grid-template-columns:repeat(3,1fr)}}.筛选 input{{min-width:150px}}}}@media(max-width:560px){{.页眉{{align-items:flex-start;flex-wrap:wrap}}.数据状态{{margin-left:0}}.概览{{grid-template-columns:1fr 1fr}}.运行格,.详情格{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main class="终端">
<header class="页眉"><div class="品牌">INSTITUTION SCANNER / 研究终端</div><div class="日期">{_safe(report_date)}</div><div class="数据状态">● 数据已就绪</div></header>
<div class="说明">报告生成 {_safe(generated)} · 行情严格截止 {_safe(report_date)} · <a href="{_safe(history_href)}">历史报告</a> · 点击任一标的查看日 K 与研究详情</div>
<section class="概览">
<div class="概览卡 红"><div class="题">全市场标的</div><div class="值">{counts['total']:,}</div><div class="注">股票 {counts['stocks']:,} · ETF {counts['etfs']:,}</div></div>
<div class="概览卡"><div class="题">可执行</div><div class="值">{counts['ready']:,}</div><div class="注">READY / 推荐</div></div>
<div class="概览卡"><div class="题">谨慎候选</div><div class="值">{counts['cautious']:,}</div><div class="注">CAUTIOUS</div></div>
<div class="概览卡"><div class="题">新信号</div><div class="值">{counts['new']:,}</div><div class="注">SignalStatus = NEW</div></div>
<div class="概览卡"><div class="题">图表覆盖</div><div class="值">{len(charts):,}</div><div class="注">最近 120 个交易日</div></div>
</section>
<section class="卡片"><div class="卡头"><h2>今日研究榜</h2><span class="副">研究排名与执行状态分离 · v83/v84</span><div class="筛选"><input id="搜索" placeholder="搜索代码 / 名称 / 行业"><select id="类型"><option value="">全部类型</option><option>股票</option><option>ETF</option></select><select id="执行"><option value="">全部状态</option><option>可执行</option><option>谨慎</option><option>观察</option><option>阻断</option></select></div></div>
<div class="表包"><table><thead><tr><th>研究#</th><th>交易#</th><th>代码 / 名称</th><th>收盘</th><th>ALPHA</th><th>执行状态</th><th>参考买点</th><th>止损</th><th>目标</th><th>TREND</th></tr></thead><tbody id="研究表">{rows_html}</tbody></table></div></section>
<section class="卡片"><div class="卡头"><h2>运行与回测</h2><span class="副">展示摘要，不公开日志与缓存</span></div><div class="运行格">
<div class="运行项"><span>总耗时</span><strong>{_safe(_duration(daily.get('elapsed_seconds', '')))}</strong></div><div class="运行项"><span>扫描</span><strong>{_safe(_duration(stages.get('scan', '')))}</strong></div><div class="运行项"><span>回测</span><strong>{_safe(_duration(stages.get('backtest', '')))}</strong></div><div class="运行项"><span>回测模式</span><strong>{_safe(mode)}</strong></div><div class="运行项"><span>样本</span><strong>{_safe(samples or '—')}</strong></div><div class="运行项"><span>页面版本</span><strong>v84</strong></div></div></section>
<div class="页脚">本页是模型研究与执行辅助界面，不构成收益承诺。历史报告的 K 线会固定截断到对应报告日，便于审计与复盘。 · {_safe(WEB_REPORT_VERSION)}</div>
</main>
<div id="遮罩" class="抽屉遮罩"><aside class="抽屉" role="dialog" aria-modal="true"><div class="抽屉头"><div><h3 id="详情标题">标的详情</h3><p id="详情副标题"></p></div><button id="关闭" class="关闭" aria-label="关闭">×</button></div><div id="详情格" class="详情格"></div><div class="图框"><svg id="日K图" viewBox="0 0 940 520" preserveAspectRatio="xMidYMid meet"></svg></div><div id="解释" class="解释"></div></aside></div>
<script id="图表数据" type="application/json">{_json_for_script(charts)}</script><script id="详情数据" type="application/json">{_json_for_script(details)}</script>
<script>
const 图表=JSON.parse(document.getElementById('图表数据').textContent||'{{}}');const 详情=JSON.parse(document.getElementById('详情数据').textContent||'{{}}');
const 表=document.getElementById('研究表'),搜索=document.getElementById('搜索'),类型=document.getElementById('类型'),执行=document.getElementById('执行');
function 过滤(){{const q=(搜索.value||'').trim().toLowerCase(),a=类型.value,s=执行.value;for(const r of 表.querySelectorAll('tr')){{const ok=(!q||(r.dataset.search||'').includes(q))&&(!a||r.dataset.asset===a)&&(!s||r.dataset.execution===s);r.classList.toggle('隐藏',!ok)}}}}搜索.addEventListener('input',过滤);类型.addEventListener('change',过滤);执行.addEventListener('change',过滤);
const 遮罩=document.getElementById('遮罩');document.getElementById('关闭').onclick=()=>遮罩.classList.remove('开');遮罩.addEventListener('click',e=>{{if(e.target===遮罩)遮罩.classList.remove('开')}});document.addEventListener('keydown',e=>{{if(e.key==='Escape')遮罩.classList.remove('开')}});
function 文本(v,d='—'){{return v===null||v===undefined||v===''?d:String(v)}}function 数字(v,n=2){{const x=Number(v);return Number.isFinite(x)?x.toFixed(n):'—'}}
function 详情项(k,v){{return `<div class="详情项"><span>${{k}}</span><strong>${{文本(v)}}</strong></div>`}}
function 线(points,color,width=1.5){{return `<polyline points="${{points}}" fill="none" stroke="${{color}}" stroke-width="${{width}}" stroke-linejoin="round" stroke-linecap="round"/>`}}
function 画K线(ticker,d){{const svg=document.getElementById('日K图'),x=图表[ticker];if(!x||!x.c||x.c.length<2){{svg.innerHTML='<text x="470" y="250" text-anchor="middle" fill="#6b7078" font-size="14">暂无本地日 K 缓存</text>';return}}const n=x.c.length,W=940,H=520,left=52,right=72,top=24,priceBottom=390,volTop=414,volBottom=490;let vals=[...x.h,...x.l,...x.e20,...x.e50,...x.e200].filter(Number.isFinite);for(const k of ['buy','breakout','stop','target']){{const v=Number(d[k]);if(Number.isFinite(v))vals.push(v)}}let lo=Math.min(...vals),hi=Math.max(...vals),pad=Math.max((hi-lo)*.06,hi*.005,1e-6);lo-=pad;hi+=pad;const pw=W-left-right,step=pw/n,cw=Math.max(1.4,Math.min(7,step*.58));const X=i=>left+(i+.5)*step,Y=v=>top+(hi-v)/(hi-lo)*(priceBottom-top),vmax=Math.max(...x.v,1),V=v=>volBottom-v/vmax*(volBottom-volTop);let out=`<rect x="0" y="0" width="${{W}}" height="${{H}}" fill="#fff"/>`;for(let i=0;i<5;i++){{const yy=top+i*(priceBottom-top)/4,price=hi-i*(hi-lo)/4;out+=`<line x1="${{left}}" x2="${{W-right}}" y1="${{yy}}" y2="${{yy}}" stroke="#eceef1"/><text x="${{W-right+8}}" y="${{yy+4}}" fill="#6b7078" font-size="10">${{price.toFixed(2)}}</text>`}}for(let i=0;i<n;i++){{const up=x.c[i]>=x.o[i],color=up?'#E33D3D':'#197A55',xx=X(i),yo=Y(x.o[i]),yc=Y(x.c[i]),yh=Y(x.h[i]),yl=Y(x.l[i]);out+=`<line x1="${{xx}}" x2="${{xx}}" y1="${{yh}}" y2="${{yl}}" stroke="${{color}}"/><rect x="${{xx-cw/2}}" y="${{Math.min(yo,yc)}}" width="${{cw}}" height="${{Math.max(1,Math.abs(yc-yo))}}" fill="${{color}}"/><rect x="${{xx-cw/2}}" y="${{V(x.v[i])}}" width="${{cw}}" height="${{volBottom-V(x.v[i])}}" fill="${{color}}" opacity=".35"/>`}}for(const [arr,color] of [[x.e20,'#1769AA'],[x.e50,'#B56A13'],[x.e200,'#6955B8']]){{const pts=arr.map((v,i)=>Number.isFinite(v)?`${{X(i).toFixed(1)}},${{Y(v).toFixed(1)}}`:null).filter(Boolean).join(' ');out+=线(pts,color,1.35)}}const levels=[['buy','买点','#1769AA'],['breakout','突破','#B56A13'],['stop','止损','#197A55'],['target','目标','#E33D3D']];for(const [k,label,color] of levels){{const v=Number(d[k]);if(!Number.isFinite(v)||v<lo||v>hi)continue;const yy=Y(v);out+=`<line x1="${{left}}" x2="${{W-right}}" y1="${{yy}}" y2="${{yy}}" stroke="${{color}}" stroke-dasharray="5 4" opacity=".75"/><text x="${{left+4}}" y="${{yy-4}}" fill="${{color}}" font-size="10" font-weight="700">${{label}} ${{v.toFixed(2)}}</text>`}}out+=`<text x="${{left}}" y="${{H-8}}" fill="#6b7078" font-size="10">${{x.d[0]}}</text><text x="${{W-right}}" y="${{H-8}}" text-anchor="end" fill="#6b7078" font-size="10">${{x.d[n-1]}}</text><text x="${{left}}" y="${{top-7}}" fill="#1769AA" font-size="10">EMA20</text><text x="${{left+48}}" y="${{top-7}}" fill="#B56A13" font-size="10">EMA50</text><text x="${{left+96}}" y="${{top-7}}" fill="#6955B8" font-size="10">EMA200</text>`;svg.innerHTML=out}}
function 打开(ticker){{const d=详情[ticker];if(!d)return;document.getElementById('详情标题').textContent=`${{d.ticker}} · ${{d.name||''}}`;document.getElementById('详情副标题').textContent=`${{d.asset||''}} · ${{d.topic||''}} · 数据日 ${{d.asof||'—'}}`;document.getElementById('详情格').innerHTML=详情项('研究排名','#'+文本(d.researchRank))+详情项('交易排名','#'+文本(d.tradeRank))+详情项('Alpha',数字(d.alpha,1))+详情项('执行状态',d.execution)+详情项('技术信号',d.signal)+详情项('质量层',d.quality)+详情项('收盘',数字(d.close,3))+详情项('参考买点',d.buyText)+详情项('止损',数字(d.stop,3))+详情项('目标',数字(d.target,3))+详情项('盈亏比',数字(d.rr,2))+详情项('平滑触发',数字(d.smoothTrigger,1));document.getElementById('解释').textContent=d.reason||'—';画K线(ticker,d);遮罩.classList.add('开')}}for(const r of 表.querySelectorAll('tr'))r.addEventListener('click',()=>打开(r.dataset.ticker));
</script></body></html>"""


def _archive_html(site_dir: Path) -> str:
    report_dir = Path(site_dir) / "reports"
    pages = sorted(
        (path for path in report_dir.glob("????-??-??.html") if path.is_file()),
        key=lambda path: path.stem,
        reverse=True,
    )
    items = "".join(f'<li><a href="{_safe(path.name)}">{_safe(path.stem)}</a></li>' for path in pages)
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>历史研究报告</title><style>body{{margin:0;background:#f1f2f4;color:#15171a;font-family:"Microsoft YaHei UI",system-ui,sans-serif;padding:30px}}main{{max-width:900px;margin:auto}}h1{{border-bottom:1px solid #15171a;padding-bottom:14px}}ul{{list-style:none;padding:0;background:#fff;border:1px solid #d9dde3}}li{{border-bottom:1px solid #d9dde3}}li:last-child{{border:0}}a{{display:block;padding:14px 16px;color:#15171a;text-decoration:none;font-family:ui-monospace,Consolas,monospace}}a:hover{{background:#f7f8fa;color:#e33d3d}}</style></head><body><main><h1>历史研究报告</h1><p><a href="../index.html">← 返回最新报告</a></p><ul>{items or '<li><a>暂无历史报告</a></li>'}</ul></main></body></html>"""


def build_web_report(output_dir: Path = DEFAULT_OUTPUT_DIR, site_dir: Path = DEFAULT_SITE_DIR) -> WebReportResult:
    output_dir = Path(output_dir)
    source_dir = _published_source_dir(output_dir)
    all_rows = _read_csv(source_dir / "AllResults.csv") or _read_csv(source_dir / "DecisionResults.csv")
    display_rows = _read_csv(source_dir / "Top50Mixed.csv") or _read_csv(source_dir / "Top50.csv") or all_rows
    if not all_rows and display_rows:
        all_rows = display_rows
    if not all_rows:
        raise RuntimeError("WEB_REPORT_NO_RESULTS: no published CSV results found")
    daily = _read_json(source_dir / "DailyRunSummary.json") or _read_json(output_dir / "DailyRunSummary.json")
    backtest = _read_json(source_dir / "BacktestSummary.json") or _read_json(output_dir / "BacktestSummary.json")
    report_date = _report_date(all_rows, daily)
    decorated = _decorate_rows(display_rows)
    site_dir = Path(site_dir)
    report_dir = site_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    latest_page = _render_html(report_date=report_date, all_rows=all_rows, display_rows=decorated, daily=daily, backtest=backtest, history_href="reports/index.html")
    archive_page = _render_html(report_date=report_date, all_rows=all_rows, display_rows=decorated, daily=daily, backtest=backtest, history_href="index.html")
    archive_path = report_dir / f"{report_date}.html"
    index_path = site_dir / "index.html"
    archive_path.write_text(archive_page, encoding="utf-8")
    index_path.write_text(latest_page, encoding="utf-8")
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    (report_dir / "index.html").write_text(_archive_html(site_dir), encoding="utf-8")
    return WebReportResult(report_date=report_date, index_path=index_path, archive_path=archive_path)


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


def _run_git(args: list[str], *, cwd: Path | None = None, timeout: int = 60, allow: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    completed = subprocess.run(["git", *args], cwd=str(cwd) if cwd is not None else None, env=env, text=True, encoding="utf-8", errors="replace", capture_output=True, check=False, timeout=timeout)
    if completed.returncode not in allow:
        detail = (completed.stderr or completed.stdout or "git command failed").strip()
        raise RuntimeError(f"WEB_REPORT_GIT_FAILED: {' '.join(args)}: {detail}")
    return completed


def publish_site(site_dir: Path, *, repo_root: Path = PROJECT_ROOT, branch: str = GH_PAGES_BRANCH, report_date: str = "") -> WebReportResult:
    site_dir = Path(site_dir)
    if not (site_dir / "index.html").is_file():
        raise RuntimeError("WEB_REPORT_SITE_MISSING: index.html not found")
    remote = _run_git(["-C", str(repo_root), "remote", "get-url", "origin"]).stdout.strip()
    page_url = github_pages_url_from_remote(remote)
    if not page_url:
        raise RuntimeError("WEB_REPORT_UNSUPPORTED_REMOTE: origin is not github.com")
    exists = _run_git(["-C", str(repo_root), "ls-remote", "--exit-code", "--heads", "origin", branch], allow=(0, 2)).returncode == 0
    with tempfile.TemporaryDirectory(prefix="institution-web-") as temp_dir:
        worktree = Path(temp_dir) / "site"
        if exists:
            _run_git(["clone", "--quiet", "--depth", "1", "--branch", branch, "--single-branch", remote, str(worktree)], timeout=90)
        else:
            worktree.mkdir(parents=True, exist_ok=True)
            _run_git(["init", "--quiet"], cwd=worktree)
            _run_git(["remote", "add", "origin", remote], cwd=worktree)
            _run_git(["checkout", "--orphan", branch], cwd=worktree)
        shutil.copy2(site_dir / "index.html", worktree / "index.html")
        shutil.copy2(site_dir / ".nojekyll", worktree / ".nojekyll")
        shutil.copytree(site_dir / "reports", worktree / "reports", dirs_exist_ok=True)
        (worktree / "reports" / "index.html").write_text(_archive_html(worktree), encoding="utf-8")
        _run_git(["add", "--", "index.html", ".nojekyll", "reports"], cwd=worktree)
        diff = _run_git(["diff", "--cached", "--quiet"], cwd=worktree, allow=(0, 1))
        if diff.returncode == 1:
            stamp = report_date or date.today().isoformat()
            _run_git(["-c", "user.name=InstitutionScanner", "-c", "user.email=institution-scanner@users.noreply.github.com", "commit", "--quiet", "-m", f"report: research terminal {stamp}"], cwd=worktree)
            _run_git(["push", "origin", f"HEAD:{branch}"], cwd=worktree, timeout=90)
            message = f"published {stamp} to {branch}"
        else:
            message = "website already up to date"
    resolved_date = report_date or date.today().isoformat()
    return WebReportResult(report_date=resolved_date, index_path=site_dir / "index.html", archive_path=site_dir / "reports" / f"{resolved_date}.html", page_url=page_url, published=True, publish_message=message)


def build_and_publish_web_report(*, output_dir: Path = DEFAULT_OUTPUT_DIR, site_dir: Path = DEFAULT_SITE_DIR, logger: logging.Logger | None = None, reason: str = "run-complete") -> WebReportResult:
    log = logger or logging.getLogger("institution_scanner")
    built = build_web_report(output_dir=output_dir, site_dir=site_dir)
    log.info("WEB research terminal generated: %s (%s).", built.archive_path, reason)
    if not _truthy_env(WEB_PUBLISH_ENV, True):
        log.info("WEB publication disabled by %s.", WEB_PUBLISH_ENV)
        return built
    try:
        published = publish_site(site_dir, repo_root=PROJECT_ROOT, report_date=built.report_date)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        log.warning("WEB report publication skipped/failed without affecting pipeline: %s", exc)
        return WebReportResult(report_date=built.report_date, index_path=built.index_path, archive_path=built.archive_path, publish_message=str(exc))
    log.info("WEB research terminal published: %s", published.page_url)
    return published


def maybe_publish_canonical_report(output_dir: Path, *, logger: logging.Logger | None = None, reason: str) -> WebReportResult | None:
    if not is_canonical_output_dir(Path(output_dir)):
        return None
    try:
        return build_and_publish_web_report(output_dir=Path(output_dir), logger=logger, reason=reason)
    except (OSError, RuntimeError, csv.Error) as exc:
        log = logger or logging.getLogger("institution_scanner")
        log.warning("WEB research terminal generation skipped/failed without affecting pipeline: %s", exc)
        return None
