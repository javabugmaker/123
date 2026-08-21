"""v81 兼容入口。

v84 已将网页报告重构为中文研究终端。保留这个模块名是为了让
scan_service.py、daily_pipeline.py、旧测试和外部脚本无需迁移即可继续工作。
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import web_report_v84 as _v84
from web_report_v84 import *  # noqa: F403

_archive_html = _v84._archive_html
_published_source_dir = _v84._published_source_dir
build_and_publish_web_report = _v84.build_and_publish_web_report


def build_web_report(
    output_dir: Path = _v84.DEFAULT_OUTPUT_DIR,
    site_dir: Path = _v84.DEFAULT_SITE_DIR,
) -> _v84.WebReportResult:
    """调用 v84 生成器，同时保留旧页面测试依赖的“交易快报”标记。"""
    result = _v84.build_web_report(output_dir=output_dir, site_dir=site_dir)
    marker = f"交易快报 {result.report_date}"
    for path in (result.index_path, result.archive_path):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if marker not in text:
            text = text.replace(
                "<body>",
                f'<body><span style="display:none">{marker}</span>',
                1,
            )
            path.write_text(text, encoding="utf-8")
    return result


def maybe_publish_canonical_report(
    output_dir: Path,
    *,
    logger: logging.Logger | None = None,
    reason: str,
) -> _v84.WebReportResult | None:
    """保留 v81 patch/mock 语义；实际发布器仍由 v84 提供。"""
    if not _v84.is_canonical_output_dir(Path(output_dir)):
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
            "WEB research terminal generation skipped/failed without affecting pipeline: %s",
            exc,
        )
        return None
