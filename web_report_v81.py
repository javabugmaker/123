"""Historical v81 entry point routed through the current v102 report stack.

The module name remains stable for scanner/daily/external callers. v101 owns
LIVE publication freshness; v102 owns ranking/display/calibration integrity.
"""
from __future__ import annotations

import csv
import logging
import os
from pathlib import Path

import web_report_v102 as _v85
from publication_freshness_v101 import (
    PublicationFreshness,
    validate_live_publication,
    write_publication_status,
)
from web_report_v102 import *  # noqa: F403

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
    """Build the v102 report while retaining the legacy hidden report marker."""
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
        text = text.replace("LIVE · 数据就绪", "PUBLISHED SNAPSHOT · 数据对齐")
        try:
            path.write_text(text, encoding="utf-8")
        except OSError:
            continue
    return result


def _publication_enabled() -> bool:
    raw = os.environ.get(_v85.WEB_PUBLISH_ENV)
    return raw is None or raw.strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
    }


def build_and_publish_web_report(
    *,
    output_dir: Path = _v85.DEFAULT_OUTPUT_DIR,
    site_dir: Path = _v85.DEFAULT_SITE_DIR,
    logger: logging.Logger | None = None,
    reason: str = "run-complete",
) -> _v85.WebReportResult:
    """Publish only canonical data aligned to the latest completed session."""
    output_dir = Path(output_dir)
    site_dir = Path(site_dir)
    log = logger or logging.getLogger("institution_scanner")
    if _v85.is_canonical_output_dir(output_dir):
        check = _assert_live_publication_ready(output_dir)
        log.info(
            "WEB publication freshness passed: expected=%s effective=%s ratio=%.1f%%.",
            check.expected_trading_date,
            check.effective_trading_date,
            check.all_results_fresh_ratio * 100,
        )
    built = build_web_report(output_dir=output_dir, site_dir=site_dir)
    log.info("WEB v102 Research Console generated: %s (%s).", built.archive_path, reason)
    if not _publication_enabled():
        log.info("WEB publication disabled by %s.", _v85.WEB_PUBLISH_ENV)
        return built
    try:
        return _v85.publish_site(
            site_dir,
            repo_root=_v85.PROJECT_ROOT,
            report_date=built.report_date,
        )
    except Exception as exc:
        log.warning(
            "WEB report publication skipped/failed without affecting pipeline: %s",
            exc,
        )
        return _v85.WebReportResult(
            report_date=built.report_date,
            index_path=built.index_path,
            archive_path=built.archive_path,
            publish_message=str(exc),
        )


def maybe_publish_canonical_report(
    output_dir: Path,
    *,
    logger: logging.Logger | None = None,
    reason: str,
) -> _v85.WebReportResult | None:
    """Preserve the v81 API while enforcing v101 + v102 publication contracts."""
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
