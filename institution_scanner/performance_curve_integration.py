"""Small integration helpers for DAILY and GitHub Pages publishing."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from institution_scanner.performance_curve_bridge import emit_performance_curve
from institution_scanner.performance_curve_web import performance_curve_html

logger = logging.getLogger("institution_scanner.performance_curve")


def safe_emit(history: pd.DataFrame) -> dict[str, Any]:
    """Emit diagnostics without making DAILY fatal on presentation failures."""
    try:
        return emit_performance_curve(history)
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        logger.warning("Performance curve generation skipped: %s", exc)
        return {"rows": 0, "status": "SKIPPED", "reason": str(exc)}


def inject_into_html(path: Path, curve_json: Path) -> bool:
    """Insert the model-health section before held-out calibration when present."""
    fragment = performance_curve_html(curve_json)
    if not fragment:
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    if 'id="performance-curves-v1"' in text:
        return True
    markers = (
        '<section id="score-bucket-calibration-v93"',
        '<section id="what-changed-v93"',
        "</main>",
        "</body>",
    )
    for marker in markers:
        position = text.find(marker)
        if position >= 0:
            text = text[:position] + fragment + text[position:]
            break
    else:
        text += fragment
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        return False
    return True
