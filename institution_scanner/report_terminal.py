"""Canonical public-report facade.

Presentation now has one view model and one renderer.  The historical
``web_report_vXX`` modules remain import-compatible archives but are no longer
composed on the production publication path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from . import pages_publisher
from .publication_renderer import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SITE_DIR,
    PUBLIC_PAGE_VERSION_ID,
    WebReportResult,
    archive_index_html,
    build_web_report,
    published_source_dir,
)

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
WEB_REPORT_VERSION: Final = PUBLIC_PAGE_VERSION_ID
WEB_PUBLISH_ENV: Final = "INSTITUTION_SCANNER_WEB_PUBLISH"
GH_PAGES_BRANCH: Final = "gh-pages"


def is_canonical_output_dir(output_dir: Path) -> bool:
    try:
        return Path(output_dir).resolve() == DEFAULT_OUTPUT_DIR.resolve()
    except OSError:
        return False


# Compatibility names consumed by the stable root facade.
_archive_html = archive_index_html
_published_source_dir = published_source_dir
github_pages_url_from_remote = pages_publisher.github_pages_url


def publish_site(
    site_dir: Path,
    *,
    repo_root: Path = PROJECT_ROOT,
    branch: str = GH_PAGES_BRANCH,
    report_date: str = "",
) -> WebReportResult:
    """Publish the canonical site through the HTTPS-first Pages transport."""
    result = pages_publisher.publish_site_files(
        Path(site_dir),
        repo_root=Path(repo_root),
        branch=branch,
        report_date=report_date,
        archive_renderer=archive_index_html,
    )
    return WebReportResult(
        report_date=result.report_date,
        index_path=Path(site_dir) / "index.html",
        archive_path=Path(site_dir) / "reports" / f"{result.report_date}.html",
        page_url=result.page_url,
        published=True,
        publish_message=result.message,
    )


__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_SITE_DIR",
    "GH_PAGES_BRANCH",
    "PROJECT_ROOT",
    "WEB_PUBLISH_ENV",
    "WEB_REPORT_VERSION",
    "WebReportResult",
    "build_web_report",
    "github_pages_url_from_remote",
    "is_canonical_output_dir",
    "publish_site",
]
