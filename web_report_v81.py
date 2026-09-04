"""Stable publication entry point backed by the canonical renderer.

The module name is retained for scanner, DAILY and external callers.  It no
longer composes the historical web-report overlay chain.
"""

from __future__ import annotations

import csv
import logging
import os
from pathlib import Path

from institution_scanner import backtest_web
from institution_scanner import report_terminal as _terminal
from institution_scanner.performance_curve_runtime import (
    after_page_build as _after_performance_page_build,
)
from institution_scanner.performance_curve_runtime import (
    build_detail_page as _build_performance_detail_page,
)
from institution_scanner.performance_curve_runtime import (
    build_from_output_dir as _build_performance_curve,
)
from institution_scanner.verify_output import verify_directory
from publication_freshness_v101 import (
    PublicationFreshness,
    validate_live_publication,
    write_publication_status,
)

DEFAULT_OUTPUT_DIR = _terminal.DEFAULT_OUTPUT_DIR
DEFAULT_SITE_DIR = _terminal.DEFAULT_SITE_DIR
WEB_REPORT_VERSION = _terminal.WEB_REPORT_VERSION
WebReportResult = _terminal.WebReportResult
build_canonical_web_report = _terminal.build_web_report
publish_site = _terminal.publish_site


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


def _assert_output_contract_ready(output_dir: Path) -> dict[str, object]:
    payload = verify_directory(Path(output_dir))
    if payload.get("status") != "PASS":
        raise RuntimeError(
            "Output reliability contract failed before publication: "
            f"{payload.get('issues', [])}"
        )
    return payload


def build_web_report(
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    site_dir: Path = DEFAULT_SITE_DIR,
) -> WebReportResult:
    """Build the compact report plus standalone performance audit pages."""
    output_dir = Path(output_dir)
    site_dir = Path(site_dir)
    _build_performance_curve(output_dir)
    result = build_canonical_web_report(output_dir=output_dir, site_dir=site_dir)
    log = logging.getLogger("institution_scanner")
    try:
        _build_performance_detail_page(site_dir, output_dir)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        log.warning("Forward performance detail page skipped: %s", exc)

    backtest_json = output_dir / "HistoricalBacktest.json"
    if backtest_json.is_file():
        try:
            backtest_web.write_backtest_page(
                site_dir / "backtest.html", backtest_json
            )
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            log.warning("Historical backtest detail page skipped: %s", exc)

    for path in (result.index_path, result.archive_path):
        _after_performance_page_build(path, output_dir)
    return result


def _publication_enabled() -> bool:
    raw = os.environ.get(_terminal.WEB_PUBLISH_ENV)
    return raw is None or raw.strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
    }


def build_and_publish_web_report(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    site_dir: Path = DEFAULT_SITE_DIR,
    logger: logging.Logger | None = None,
    reason: str = "run-complete",
) -> WebReportResult:
    """Publish only data that pass freshness and output contracts."""
    output_dir = Path(output_dir)
    site_dir = Path(site_dir)
    log = logger or logging.getLogger("institution_scanner")
    if _terminal.is_canonical_output_dir(output_dir):
        check = _assert_live_publication_ready(output_dir)
        log.info(
            "WEB publication freshness passed: expected=%s effective=%s ratio=%.1f%%.",
            check.expected_trading_date,
            check.effective_trading_date,
            check.all_results_fresh_ratio * 100,
        )
        verification = _assert_output_contract_ready(output_dir)
        log.info(
            "WEB output reliability contract passed: errors=%s warnings=%s.",
            verification.get("errors", 0),
            verification.get("warnings", 0),
        )

    built = build_web_report(output_dir=output_dir, site_dir=site_dir)
    log.info(
        "WEB research briefing generated: %s (%s).",
        built.archive_path,
        reason,
    )
    if not _publication_enabled():
        log.info("WEB publication disabled by %s.", _terminal.WEB_PUBLISH_ENV)
        return built
    try:
        return publish_site(
            site_dir,
            repo_root=_terminal.PROJECT_ROOT,
            report_date=built.report_date,
        )
    except Exception as exc:
        log.warning(
            "WEB report publication skipped/failed without affecting pipeline: %s",
            exc,
        )
        return WebReportResult(
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
) -> WebReportResult | None:
    """Preserve the historical API while enforcing canonical publication rules."""
    if not _terminal.is_canonical_output_dir(Path(output_dir)):
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


__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_SITE_DIR",
    "WEB_REPORT_VERSION",
    "WebReportResult",
    "build_and_publish_web_report",
    "build_web_report",
    "maybe_publish_canonical_report",
    "publish_site",
]
