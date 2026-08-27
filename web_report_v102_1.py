"""v102.1 presentation semantics layered on the validated v102 report.

No score, rank, eligibility, calibration weight, or candidate list is recomputed.
This adapter only removes contradictory legacy labels revealed by the first real
v102 publication and publishes longitudinal model-health diagnostics from the
persisted SignalHistory research ledger.
"""
from __future__ import annotations

import re
from pathlib import Path

import web_report_v102 as _v102
from institution_scanner.performance_curve_runtime import (
    after_page_build as _after_performance_page_build,
    build_from_output_dir as _build_performance_curve,
)
from web_report_v102 import *  # noqa: F403

WEB_REPORT_VERSION = "2026-08-27-v102.2-model-health-curves-v1"
WebReportResult = _v102.WebReportResult
DEFAULT_OUTPUT_DIR = _v102.DEFAULT_OUTPUT_DIR
DEFAULT_SITE_DIR = _v102.DEFAULT_SITE_DIR
PROJECT_ROOT = _v102.PROJECT_ROOT
WEB_PUBLISH_ENV = _v102.WEB_PUBLISH_ENV
GH_PAGES_BRANCH = _v102.GH_PAGES_BRANCH
_archive_html = _v102._archive_html
_published_source_dir = _v102._published_source_dir
is_canonical_output_dir = _v102.is_canonical_output_dir
github_pages_url_from_remote = _v102.github_pages_url_from_remote
publish_site = _v102.publish_site


def _polish_freshness_section(match: re.Match[str]) -> str:
    section = match.group(0)
    section = section.replace(
        "异常标的 <strong>",
        "非同日报价 / 时效异常 <strong>",
    )
    section = section.replace(
        " · 新鲜 · 行情日期正常",
        " · 未同日 · 状态规则未判过期（可能停牌/无成交）",
    )
    return section


def _polish(path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return

    text = text.replace(
        "2026-08-24-v102-ranking-calibration-integrity-v1",
        WEB_REPORT_VERSION,
    )
    text = text.replace(
        "2026-08-24-v102.1-semantic-clarity-v1",
        WEB_REPORT_VERSION,
    )
    text = text.replace(
        '<div class="run-item"><span>页面版本</span><strong>v102</strong></div>',
        '<div class="run-item"><span>页面版本</span><strong>v102.2</strong></div>',
    )
    text = re.sub(
        r"<span>可执行</span><strong>(\d+)</strong>"
        r"<small>READY / 推荐</small>",
        (
            r"<span>全市场 READY</span><strong>\1</strong>"
            r"<small>TradeReady 另按候选视图精选</small>"
        ),
        text,
        count=1,
    )
    text = text.replace("信号样本标的", "回测测试样本")
    text = text.replace("<th>当前排名</th>", "<th>ResearchRank（资产内）</th>")
    text = text.replace("· 新信号 </p>", "· 新信号 0</p>")

    text = re.sub(
        r'<section id="freshness-exceptions-v102".*?</section>',
        _polish_freshness_section,
        text,
        count=1,
        flags=re.S,
    )

    if "OFF · 仅诊断" in text:
        text = text.replace(
            "这条链路参与生产评分；回测完成后统一重算并发布，不在运行中增量改排名",
            (
                "仅在回测证据通过生产治理时参与评分；"
                "当前页面同时展示实际有效权重"
            ),
        )
        text = text.replace(
            "回测分→可靠性收缩→有效权重→CompositeScore",
            "当前回测有效权重为 0%；证据仅诊断，不改生产评分",
        )

    if "MODEL MIGRATION / 本期不可直接同比" in text:
        text = re.sub(
            r'<section class="section card" data-section="model_changes">'
            r".*?</section>",
            "",
            text,
            count=1,
            flags=re.S,
        )
        text = text.replace(
            '<div class="split"><section class="section card" '
            'data-section="risk_radar">',
            '<div><section class="section card" data-section="risk_radar">',
            1,
        )

    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        return


def build_web_report(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    site_dir: Path = DEFAULT_SITE_DIR,
) -> WebReportResult:
    output = Path(output_dir)
    site = Path(site_dir)

    # Presentation-only longitudinal diagnostics.  Failure or an immature
    # SignalHistory ledger never changes scoring/ranking and never makes Pages
    # publication fatal.
    _build_performance_curve(output)

    result = _v102.build_web_report(
        output_dir=output,
        site_dir=site,
    )
    for path in (result.index_path, result.archive_path):
        _polish(path)
        _after_performance_page_build(path, output)
    return result
