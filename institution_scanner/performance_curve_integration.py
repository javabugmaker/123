"""Small integration helpers for DAILY and GitHub Pages publishing."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from institution_scanner.performance_curve_bridge import emit_performance_curve
from institution_scanner.performance_curve_web import (
    inject_into_html as _inject_performance_html,
)

logger = logging.getLogger("institution_scanner.performance_curve")


def safe_emit(history: pd.DataFrame) -> dict[str, Any]:
    """Emit diagnostics without making DAILY fatal on presentation failures."""
    try:
        return emit_performance_curve(history)
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        logger.warning("Performance curve generation skipped: %s", exc)
        return {"rows": 0, "status": "SKIPPED", "reason": str(exc)}


def inject_into_html(path: Path, curve_json: Path) -> bool:
    """Insert the forward-performance card before held-out calibration."""
    return _inject_performance_html(Path(path), Path(curve_json))
