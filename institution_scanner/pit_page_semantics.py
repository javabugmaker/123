"""Public-page semantics for point-in-time held-out calibration.

This module changes presentation only. It never modifies a score, rank, gate,
backtest sample, calibration weight, or publication eligibility.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

PIT_PAGE_SEMANTICS_VERSION = "2026-08-24-v106.3-pit-warmup-page-semantics-v1"

_HELDOUT_HEADING = "HELD-OUT SCORE CALIBRATION / 测试集评分分桶"


def _safe(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _integer(value: object) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def read_backtest_summary(output_dir: Path) -> dict[str, Any]:
    path = Path(output_dir) / "BacktestSummary.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _pit_counts(summary: dict[str, Any]) -> tuple[int, int, int]:
    raw = _integer(summary.get("heldout_raw_test_samples"))
    verified = _integer(summary.get("heldout_verified_test_samples"))
    unverified = _integer(summary.get("heldout_unverified_test_samples"))
    if raw <= 0 and verified + unverified > 0:
        raw = verified + unverified
    return raw, verified, unverified


def _warmup_section(summary: dict[str, Any]) -> str:
    raw, verified, unverified = _pit_counts(summary)
    status = str(
        summary.get("heldout_point_in_time_status", "PIT_WARMUP")
        or "PIT_WARMUP"
    ).strip().upper()
    label = (
        "PIT WARM-UP · 历史校准冷启动"
        if status == "PIT_WARMUP"
        else "PIT INSUFFICIENT · 时点样本不足"
    )
    warning = str(summary.get("heldout_metric_warning", "") or "").strip()
    detail = warning or (
        "前瞻式历史股票池快照尚未积累足够成熟 outcome；"
        "未认证历史证据仅保留审计记录，不参与生产评分。"
    )
    return (
        '<section id="pit-heldout-v1063" class="section card console-v93">'
        '<div class="section-head">'
        "<h2>PIT HELD-OUT CALIBRATION / 时点回测验证</h2>"
        "<p>区分“验证失败”和“验证样本尚未成熟”；冷启动不会阻断生产扫描。</p>"
        "</div>"
        '<div class="meta93">'
        f'<span>状态 <strong>{_safe(label)}</strong></span>'
        f'<span>· Raw held-out <strong>{raw}</strong></span>'
        f'<span>· PIT verified <strong>{verified}</strong></span>'
        f'<span>· Unverified <strong>{unverified}</strong></span>'
        '<span>· 历史校准 <strong>OFF</strong></span>'
        '<span>· 生产评分 <strong>正常运行</strong></span>'
        "</div>"
        f"<p>{_safe(detail)}</p>"
        "<p>当前 peer / local 历史校准权重保持 0；"
        "Champion、CandidateViewRank 与 TradeReady 继续按生产契约运行。</p>"
        "</section>"
    )


def _verified_scope_banner(summary: dict[str, Any]) -> str:
    raw, verified, unverified = _pit_counts(summary)
    status = str(
        summary.get("heldout_point_in_time_status", "") or ""
    ).strip().upper()
    if not status:
        return ""
    return (
        '<div class="meta93" data-pit-heldout-scope="v106.3">'
        f'<span>PIT scope <strong>{_safe(status)}</strong></span>'
        f'<span>· Raw <strong>{raw}</strong></span>'
        f'<span>· Verified <strong>{verified}</strong></span>'
        f'<span>· Unverified <strong>{unverified}</strong></span>'
        "</div>"
    )


def _replace_heldout_section(text: str, replacement: str) -> str:
    heading = text.find(_HELDOUT_HEADING)
    if heading < 0:
        return text
    start = text.rfind("<section", 0, heading)
    end = text.find("</section>", heading)
    if start < 0 or end < 0:
        return text
    end += len("</section>")
    return text[:start] + replacement + text[end:]


def apply_pit_page_semantics_html(
    text: str,
    summary: dict[str, Any],
) -> str:
    """Align public held-out wording with v106.2 PIT evidence semantics."""
    if not text or not summary:
        return text

    status = str(
        summary.get("heldout_point_in_time_status", "") or ""
    ).strip().upper()
    metric_available = bool(summary.get("heldout_metric_available", False))

    if status in {"PIT_WARMUP", "INSUFFICIENT_VERIFIED_TEST"} and not metric_available:
        text = _replace_heldout_section(text, _warmup_section(summary))
    elif status in {"VERIFIED_SUBSET", "VERIFIED_ONLY"}:
        banner = _verified_scope_banner(summary)
        marker = '<div class="section-head"><h2>' + _HELDOUT_HEADING + "</h2>"
        if banner and marker in text and 'data-pit-heldout-scope="v106.3"' not in text:
            close = text.find("</div>", text.find(marker))
            if close >= 0:
                close += len("</div>")
                text = text[:close] + banner + text[close:]

    meta = (
        '<meta name="pit-page-semantics-version" '
        f'content="{PIT_PAGE_SEMANTICS_VERSION}">'
    )
    if "pit-page-semantics-version" not in text and "</head>" in text:
        text = text.replace("</head>", meta + "</head>", 1)
    return text
