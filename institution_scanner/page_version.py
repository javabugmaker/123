"""Public research-terminal engineering version marker.

This is presentation provenance only. It must not enter the production model
signature or cause score/rank migration attribution.
"""
from __future__ import annotations

import re
from typing import Final

PUBLIC_PAGE_VERSION: Final = "v106.8"
PUBLIC_PAGE_VERSION_ID: Final = (
    "2026-08-28-v106.8-forward-performance-audit-v1"
)

_PAGE_VERSION_RE = re.compile(
    r'(<span>页面版本</span><strong>)([^<]*)(</strong>)',
    flags=re.IGNORECASE,
)
_WEB_META_RE = re.compile(
    r'<meta name="web-report-version" content="[^"]*">',
    flags=re.IGNORECASE,
)


def apply_public_page_version_html(text: str) -> str:
    """Stamp engineering page provenance without touching model provenance."""
    if not text:
        return text

    text = _PAGE_VERSION_RE.sub(
        rf"\g<1>{PUBLIC_PAGE_VERSION}\g<3>",
        text,
        count=1,
    )
    web_meta = (
        '<meta name="web-report-version" '
        f'content="{PUBLIC_PAGE_VERSION_ID}">'
    )
    if _WEB_META_RE.search(text):
        text = _WEB_META_RE.sub(web_meta, text, count=1)
    elif "</head>" in text:
        text = text.replace("</head>", web_meta + "</head>", 1)

    marker = (
        '<meta name="public-page-version" '
        f'content="{PUBLIC_PAGE_VERSION}">'
    )
    if "public-page-version" not in text and "</head>" in text:
        text = text.replace("</head>", marker + "</head>", 1)
    return text
