"""Manual retry entry point for the canonical GitHub Pages research briefing."""

from __future__ import annotations

import logging

from web_report import build_and_publish_web_report


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    result = build_and_publish_web_report(reason="manual-publish")
    if result.page_url:
        print(result.page_url)
    else:
        print(result.index_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
