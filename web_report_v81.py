"""v81 compatibility entry for the current public research briefing.

Keep this historical module name so scan_service.py, daily_pipeline.py, old
tests and external scripts continue to work while v93 layers the Research
Console on top of the validated v92/v90 + v85 presentation stack.
"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from publication_freshness_v101 import (
    PublicationFreshness,
    validate_live_publication,
    write_publication_status,
)

# Keep the historical ``_v85`` alias because older tests/extensions patch it.
# The implementation now points at v93, which preserves the production v92
# backtest/resonance layer and adds public-safe decision-console diagnostics.
import web_report_v93 as _v85
from web_report_v93 import *  # noqa: F403

_archive_html = _v85._archive_html
_published_source_dir = _v85._published_source_dir


def _assert_live_publication_ready(output_dir: Path) -> PublicationFreshness:
    check = validate_live_publication(Path(output_dir))
    try:
        write_publication_status(Path(output_dir), check)
    except OSError:
        pass
    if not check.ready:
        raise RuntimeError(
            f"LIVE publication blocked [{check.status}]: {check.reason}"
        )
    return check


def build_web_report(
    output_dir: Path = _v85.DEFAULT_OUTPUT_DIR,
    site_dir: Path = _v85.DEFAULT_SITE_DIR,
) -> _v85.WebReportResult:
    """Call v93 while retaining the hidden legacy report marker."""
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
        text = text.replace("● 数据已就绪", "● 已发布快照")
        try:
            path.write_text(text, encoding="utf-8")
        except OSError:
            continue
    return result


def build_and_publish_web_report(
    *,
    output_dir: Path = _v85.DEFAULT_OUTPUT_DIR,
    site_dir: Path = _v85.DEFAULT_SITE_DIR,
    logger: logging.Logger | None = None,
    reason: str = "run-complete",
) -> _v85.WebReportResult:
    """Generate/publish only when canonical data matches the completed session."""
    output_dir = Path(output_dir)
    log = logger or logging.getLogger("institution_scanner")
    if _v85.is_canonical_output_dir(output_dir):
        check = _assert_live_publication_ready(output_dir)
        log.info(
            "WEB LIVE freshness passed: expected=%s effective=%s ratio=%.1f%%.",
            check.expected_trading_date,
            check.effective_trading_date,
            check.all_results_fresh_ratio * 100,
        )
    return _v85.build_and_publish_web_report(
        output_dir=output_dir,
        site_dir=Path(site_dir),
        logger=logger,
        reason=reason,
    )


def maybe_publish_canonical_report(
    output_dir: Path,
    *,
    logger: logging.Logger | None = None,
    reason: str,
) -> _v85.WebReportResult | None:
    """Preserve v81 patch/mock semantics while enforcing v101 LIVE freshness."""
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
            "WEB research terminal publication blocked/skipped without affecting pipeline: %s",
            exc,
        )
        return None
