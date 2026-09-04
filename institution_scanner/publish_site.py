"""Publish an already-built, verified static site artifact."""

from __future__ import annotations

import argparse
from pathlib import Path

from .pages_publisher import publish_site_files
from .publication_renderer import archive_index_html


def _report_date(site_dir: Path) -> str:
    reports = sorted(
        path.stem
        for path in (site_dir / "reports").glob("????-??-??.html")
        if path.is_file()
    )
    if not reports:
        raise RuntimeError("WEB_REPORT_ARCHIVE_MISSING: no dated report found")
    return reports[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish a prebuilt InstitutionScanner Pages artifact."
    )
    parser.add_argument("site_dir", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--branch", default="gh-pages")
    args = parser.parse_args(argv)
    site_dir = args.site_dir.resolve()
    result = publish_site_files(
        site_dir,
        repo_root=args.repo_root.resolve(),
        branch=args.branch,
        report_date=_report_date(site_dir),
        archive_renderer=archive_index_html,
    )
    print(result.page_url)
    print(result.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
