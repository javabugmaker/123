"""Wide-frame postprocess performance safeguards.

The production result surface now contains hundreds of columns. Pandas may keep a
wide CSV in many internal blocks and repeated post-ranking column inserts can then
trigger ``PerformanceWarning: DataFrame is highly fragmented``. The warning is
not a correctness failure, but fragmentation makes later assignment and
serialization more expensive.

This module keeps the legacy compatibility kernels untouched. It installs a
module-local pandas proxy for postprocessors that read the canonical wide result
frame. Only wide ``read_csv`` results are consolidated with one copy; all other
pandas APIs are delegated unchanged. No score, rank, gate or value is modified.
"""
from __future__ import annotations

from types import ModuleType
from typing import Any, Final

import pandas as pd

POSTPROCESS_FRAME_PERFORMANCE_VERSION: Final = (
    "2026-08-25-v106.6-wide-frame-defragmentation-v1"
)
WIDE_FRAME_COLUMN_THRESHOLD: Final = 96

_INSTALLED = False


def defragment_wide_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a consolidated copy only when the frame is materially wide."""
    if len(frame.columns) < WIDE_FRAME_COLUMN_THRESHOLD:
        return frame
    return frame.copy()


class _PandasProxy:
    """Delegate pandas while consolidating wide CSV reads for one module."""

    def __init__(self, base: ModuleType) -> None:
        self._base = base

    def read_csv(self, *args: Any, **kwargs: Any) -> pd.DataFrame:
        frame = self._base.read_csv(*args, **kwargs)
        return defragment_wide_frame(frame)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def _install_proxy(module: ModuleType) -> None:
    current = getattr(module, "pd", None)
    if isinstance(current, _PandasProxy) or current is None:
        return
    setattr(module, "pd", _PandasProxy(pd))


def install() -> None:
    """Consolidate wide frames at the postprocess read boundary."""
    global _INSTALLED
    if _INSTALLED:
        return

    import calibration_governance_v102 as calibration_governance
    import calibration_semantics_v102_1 as calibration_semantics
    import resonance_reporting_v90 as resonance_reporting
    from institution_scanner import reliability

    for module in (
        calibration_governance,
        calibration_semantics,
        reliability,
        resonance_reporting,
    ):
        _install_proxy(module)
        setattr(
            module,
            "POSTPROCESS_FRAME_PERFORMANCE_VERSION",
            POSTPROCESS_FRAME_PERFORMANCE_VERSION,
        )

    _INSTALLED = True
