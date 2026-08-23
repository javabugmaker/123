"""v81 compatibility entry for the current public research briefing.

Keep this historical module name so scan_service.py, daily_pipeline.py, old
tests and external scripts continue to work while v90 layers five-factor
resonance diagnostics on top of the validated v85 presentation.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

# Keep the historical ``_v85`` alias because older tests/extensions patch it.
# The implementation now points at v90, which itself delegates the base page to
# v85 and only appends aggregate public-safe resonance diagnostics.
import web_report_v90 as _v85
from web_report_v90 import *  # noqa: F403

_archive_html = _v85._v85._archive_html
_published_source_dir = _v85._published_source_dir
build_and_publish_web_report = _v85.build_and_publish_web_report


def build_web_report(
    output_dir: Path = _v85.DEFAULT_OUTPUT_DIR,
    site_dir: Path = _v85.DEFAULT_SITE_DIR,
) -> _v85.WebReportResult:
    """Call v90 while retaining the hidden legacy report marker."""
    result = _v85.build_web_report(output_dir=output_dir, site_dir=site_dir)
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
) -> _v85.WebReportResult | None:
    """Preserve v81 patch/mock semantics while v90 performs publication."""
    if not _v85.is_canonical_output_dir(Path(output_dir)):
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
