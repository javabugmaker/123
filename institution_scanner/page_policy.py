"""Public-page presentation policy for the canonical research terminal.

Freshness remains a production/publication contract. The public briefing does not
render the verbose per-symbol freshness exception section; stale data can still
block TradeReady and publication through the existing validators.
"""
from __future__ import annotations

import re

_FRESHNESS_EXCEPTIONS_RE = re.compile(
    r'<section\b[^>]*\bid=["\']freshness-exceptions-v102["\'][^>]*>.*?</section>',
    flags=re.IGNORECASE | re.DOTALL,
)


def polish_public_page_html(text: str, *, marker: str = "") -> str:
    """Apply stable presentation-only cleanup without weakening data governance."""
    if marker and marker not in text:
        text = text.replace(
            "<body>",
            f'<body><span style="display:none">{marker}</span>',
            1,
        )

    text = text.replace("● 数据已就绪", "● 已发布快照")
    text = text.replace("LIVE · 数据就绪", "PUBLISHED SNAPSHOT · 数据对齐")

    # User-facing clutter only. Freshness is still enforced by
    # publication_freshness_v101 and institution_scanner.verify_output.
    text = _FRESHNESS_EXCEPTIONS_RE.sub("", text, count=1)
    return text
