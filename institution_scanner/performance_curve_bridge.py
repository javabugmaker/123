"""Compatibility bridge for longitudinal model-health diagnostics.

Keeps the core analytics and public-report layers decoupled: the analytics run
can emit PerformanceCurve.{csv,json} whenever SignalHistory is refreshed, while
the web report remains free to consume or ignore that artifact.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from performance_curve import curve_summary, write_performance_curve

PERFORMANCE_CURVE_BRIDGE_VERSION = "2026-08-27-v1"


def emit_performance_curve(history: pd.DataFrame) -> dict[str, Any]:
    csv_path, json_path, curve = write_performance_curve(history)
    summary = curve_summary(curve)
    summary.update(
        {
            "csv": str(Path(csv_path).name),
            "json": str(Path(json_path).name),
            "bridge_version": PERFORMANCE_CURVE_BRIDGE_VERSION,
        }
    )
    return summary
