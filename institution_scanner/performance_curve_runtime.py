"""Runtime facade used by DAILY and Pages with no policy side effects."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from institution_scanner.performance_curve_integration import inject_into_html, safe_emit
from institution_scanner.performance_curve_web import write_performance_page
from performance_curve import curve_summary, write_performance_curve

logger = logging.getLogger("institution_scanner.performance_curve.runtime")


def after_history_refresh(history: pd.DataFrame) -> dict[str, Any]:
    return safe_emit(history)


def build_from_output_dir(output_dir: Path) -> dict[str, Any]:
    """Refresh curve artifacts from the persisted research ledger.

    This hook is intentionally non-fatal for Pages.  SignalHistory is already a
    published research ledger, so regenerating the diagnostic curve here avoids
    coupling production scoring or DAILY ranking to presentation code.
    """
    root = Path(output_dir)
    history_path = root / "SignalHistory.csv"
    if not history_path.is_file():
        return {"rows": 0, "status": "NO_HISTORY"}
    try:
        history = pd.read_csv(history_path, encoding="utf-8-sig", dtype={"Ticker": str})
        _, _, curve = write_performance_curve(
            history,
            csv_path=root / "PerformanceCurve.csv",
            json_path=root / "PerformanceCurve.json",
        )
        summary = curve_summary(curve)
        summary["status"] = "READY" if not curve.empty else "EMPTY"
        return summary
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, IndexError, pd.errors.ParserError) as exc:
        logger.warning("Performance curve refresh skipped during report build: %s", exc)
        return {"rows": 0, "status": "SKIPPED", "reason": str(exc)}


def after_page_build(page_path: Path, output_dir: Path) -> bool:
    return inject_into_html(Path(page_path), Path(output_dir) / "PerformanceCurve.json")


def build_detail_page(site_dir: Path, output_dir: Path) -> Path:
    return write_performance_page(
        Path(site_dir) / "performance.html",
        Path(output_dir) / "PerformanceCurve.json",
    )
