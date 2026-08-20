"""v78 profile-aware backtest process tuning.

A single BACKTEST_MAX_PROCESSES=6 cap was appropriate for Python-heavy EXACT
scoring but unnecessarily throttles the now-vectorized FAST screen.  On a 6C/12T
class workstation, FAST benefits from two additional processes to overlap
Parquet/gzip metadata I/O and NumPy work, while EXACT stays on physical cores to
avoid SMT contention and laptop thermal throttling.

An explicit --workers request or INSTITUTION_SCANNER_BACKTEST_PROCESSES override
still wins.
"""

from __future__ import annotations

import os
from typing import Any

import analytics_core as _core
from workstation_runtime_v77 import runtime_profile

_INSTALLED = False


def adaptive_worker_count(
    total: int,
    requested: int | None,
    profile: Any,
) -> int:
    total = max(1, int(total))
    runtime = runtime_profile()
    logical = max(1, int(runtime.logical_cpus))
    physical = max(1, int(runtime.estimated_physical_cores))
    cpu_limit = max(1, logical - 1)

    if requested is not None:
        return min(total, cpu_limit, max(1, int(requested)))

    explicit_env = str(
        os.environ.get("INSTITUTION_SCANNER_BACKTEST_PROCESSES", "") or ""
    ).strip()
    if explicit_env:
        try:
            configured = max(1, int(explicit_env))
        except ValueError:
            configured = max(1, int(runtime.backtest_processes))
        return min(total, cpu_limit, configured)

    mode = str(getattr(profile, "name", "exact") or "exact").lower()
    if mode == "fast":
        target = min(cpu_limit, physical + 2, 10)
    else:
        target = min(cpu_limit, physical, 8)
    return min(total, max(1, target))


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _core._adaptive_worker_count = adaptive_worker_count
    _INSTALLED = True


install()
