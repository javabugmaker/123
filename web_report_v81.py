"""Stable publication entry point routed through the canonical research terminal.

The historical module name remains stable for scanner/daily/external callers.
Freshness and output integrity are enforced before publication; presentation
enhancements live in the canonical ``institution_scanner.report_terminal`` module.
"""
from __future__ import annotations

import csv
import logging
import os
from pathlib import Path

from institution_scanner import report_terminal as _v85
from institution_scanner.page_health_fallback import (
    apply_model_health_fallback_html,
)
from institution_scanner.page_policy import polish_public_page_html
from institution_scanner.page_version import apply_public_page_version_html
from institution_scanner.performance_curve_runtime import (
    after_page_build as _after_performance_page_build,
)
from institution_scanner.performance_curve_runtime import (
    build_detail_page as _build_performance_detail_page,
)
from institution_scanner.performance_curve_runtime import (
    build_from_output_dir as _build_performance_curve,
)
from institution_scanner.pit_counts import repair_summary_payload
from institution_scanner.pit_page_semantics import (
    apply_pit_page_semantics_html,
    read_backtest_summary,
)
from institution_scanner.report_terminal import *  # noqa: F403
from institution_scanner.verify_output import verify_directory
from publication_freshness_v101 import (
    PublicationFreshness,
    validate_live_publication,
    write_publication_status,
)

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


def _assert_output_contract_ready(output_dir: Path) -> dict[str, object]:
    payload = verify_directory(Path(output_dir))
    if payload.get("status") != "PASS":
        issues = payload.get("issues", [])
        raise RuntimeError(
            "Output reliability contract failed before publication: "
            f"{issues}"
        )
    return payload


def build_web_report(
    output_dir: Path = _v85.DEFAULT_OUTPUT_DIR,
    site_dir: Path = _v85.DEFAULT_SITE_DIR,
) -> _v85.WebReportResult:
    """Build the canonical report and apply the stable public-page policy."""
    output_dir = Path(output_dir)

    # Longitudinal diagnostics are presentation-only.  They are rebuilt from
    # the persisted SignalHistory ledger immediately before the page is built,
    # so failures cannot alter production score/rank/eligibility or DAILY.
    _build_performance_curve(output_dir)

    result = _v85.build_web_report(output_dir=output_dir, site_dir=site_dir)
    try:
        _build_performance_detail_page(Path(site_dir), output_dir)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        logging.getLogger("institution_scanner").warning(
            "Forward performance detail page skipped: %s",
            exc,
        )
    marker = f"交易快报 {result.report_date}"
    backtest_summary = repair_summary_payload(
        read_backtest_summary(output_dir)
    )
    for path in (result.index_path, result.archive_path):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        text = apply_pit_page_semantics_html(text, backtest_summary)
        text = apply_model_health_fallback_html(text, output_dir)
        text = apply_public_page_version_html(text)
        text = polish_public_page_html(text, marker=marker)
        try:
            path.write_text(text, encoding="utf-8")
        except OSError:
            continue
        _after_performance_page_build(path, output_dir)
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
    """Publish only canonical data that pass freshness and integrity contracts."""
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
        verification = _assert_output_contract_ready(output_dir)
        log.info(
            "WEB output reliability contract passed: errors=%s warnings=%s.",
            verification.get("errors", 0),
            verification.get("warnings", 0),
        )
    built = build_web_report(output_dir=output_dir, site_dir=site_dir)
    log.info(
        "WEB v106.8 forward-performance Research Terminal generated: %s (%s).",
        built.archive_path,
        reason,
    )
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
    """Preserve the historical API while enforcing canonical publication rules."""
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
            "WEB research terminal publication blocked/skipped without affecting "
            "pipeline: %s",
            exc,
        )
        return None
