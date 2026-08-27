"""Runtime facade used by DAILY and Pages with no policy side effects."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from institution_scanner.performance_curve_integration import inject_into_html, safe_emit


def after_history_refresh(history: pd.DataFrame) -> dict[str, Any]:
    return safe_emit(history)


def after_page_build(page_path: Path, output_dir: Path) -> bool:
    return inject_into_html(page_path, output_dir / "PerformanceCurve.json")
