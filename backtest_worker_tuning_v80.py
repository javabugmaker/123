"""v80 workstation process/chunk tuning for the vectorised backtest engine.

Once FAST scoring and sample execution become ticker-vectorised, oversubscribing
SMT threads hurts more than it helps: workers compete for Parquet decompression,
memory bandwidth and Python allocator/GC time. v80 keeps physical cores busy,
allows one overlap worker for I/O, and increases chunk size so a full-market run
uses tens rather than hundreds of multiprocessing futures.

Explicit CLI/environment worker settings always win and no scoring semantics are
changed.
"""

from __future__ import annotations

import math
import os
from dataclasses import replace
from typing import Any

import analytics_core as _core
from workstation_runtime_v77 import runtime_profile

_LEGACY_RESOLVE_PROFILE = _core._resolve_backtest_profile
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
        # One extra process overlaps cache/parquet I/O without putting two
        # compute-heavy Python workers on every physical core.
        target = min(cpu_limit, physical + 1, 8)
    else:
        target = min(cpu_limit, physical, 8)
    return min(total, max(1, target))


def resolve_backtest_profile(mode: str | None, ticker_count: int):
    profile = _LEGACY_RESOLVE_PROFILE(mode, ticker_count)
    total = max(1, int(ticker_count))
    runtime = runtime_profile()
    physical = max(1, int(runtime.estimated_physical_cores))
    name = str(getattr(profile, "name", "exact") or "exact").lower()
    if name == "fast":
        # ~8-12 chunks/worker provides load balancing but sharply reduces
        # Windows spawn/future/IPC overhead on 5k-7k symbol universes.
        target = math.ceil(total / max(1, physical * 10))
        chunk_size = min(128, max(32, target))
    else:
        target = math.ceil(total / max(1, physical * 8))
        chunk_size = min(16, max(4, target))
    return replace(profile, chunk_size=chunk_size)


def install() -> None:
    global _INSTALLED
    _core._adaptive_worker_count = adaptive_worker_count
    _core._resolve_backtest_profile = resolve_backtest_profile
    _INSTALLED = True


install()
