"""v77 workstation runtime tuning for CPU-bound scan/backtest workloads.

InstitutionScanner parallelises at the ticker/process level.  Native BLAS/OpenMP
libraries spawning their own worker pools inside every scanner thread or
backtest process causes severe oversubscription on laptop CPUs.  Configure those
libraries to one native thread *before NumPy/SciPy import*, then size Python
parallelism for a 6-core/12-thread class workstation while keeping environment
variables available as explicit overrides.

The RTX 3060 is deliberately not used here: the current workload is thousands
of small, variable-length pandas frames with Python-heavy decision logic, where
host<->device transfer and per-frame kernel launch overhead would dominate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_NATIVE_THREAD_ENV = (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
)


def _positive_env(name: str, default: int) -> int:
    text = str(os.environ.get(name, "") or "").strip()
    if text:
        try:
            value = int(text)
        except ValueError:
            value = 0
        if value > 0:
            return value
    return max(1, int(default))


def configure_native_threads() -> None:
    """Prevent nested BLAS/OpenMP oversubscription without overriding user values."""
    for name in _NATIVE_THREAD_ENV:
        os.environ.setdefault(name, "1")


@dataclass(frozen=True)
class WorkstationRuntimeProfile:
    logical_cpus: int
    estimated_physical_cores: int
    scan_threads: int
    backtest_processes: int
    backtest_chunk_size: int
    backtest_fast_chunk_size: int
    backtest_incremental_tail_bars: int


def runtime_profile() -> WorkstationRuntimeProfile:
    logical = max(1, int(os.cpu_count() or 4))
    # Intel/AMD mobile workstations normally expose SMT/HT as two logical CPUs
    # per physical core.  Keep conservative fallbacks for tiny/non-SMT hosts.
    physical = max(1, logical // 2) if logical >= 8 and logical % 2 == 0 else max(1, logical - 1)

    scan_default = min(12, max(4, logical))
    process_default = min(8, physical)
    scan_threads = _positive_env("INSTITUTION_SCANNER_SCAN_THREADS", scan_default)
    backtest_processes = _positive_env(
        "INSTITUTION_SCANNER_BACKTEST_PROCESSES", process_default
    )
    exact_chunk = _positive_env(
        "INSTITUTION_SCANNER_BACKTEST_CHUNK_SIZE",
        6 if backtest_processes >= 4 else 4,
    )
    fast_chunk = _positive_env(
        "INSTITUTION_SCANNER_BACKTEST_FAST_CHUNK_SIZE",
        max(12, backtest_processes * 4),
    )
    incremental_tail = _positive_env(
        "INSTITUTION_SCANNER_BACKTEST_TAIL_BARS", 360
    )
    incremental_tail = max(300, incremental_tail)

    return WorkstationRuntimeProfile(
        logical_cpus=logical,
        estimated_physical_cores=physical,
        scan_threads=min(max(1, scan_threads), max(1, logical * 2)),
        backtest_processes=min(
            max(1, backtest_processes), max(1, logical), 12
        ),
        backtest_chunk_size=max(1, exact_chunk),
        backtest_fast_chunk_size=max(1, fast_chunk),
        backtest_incremental_tail_bars=incremental_tail,
    )


configure_native_threads()
