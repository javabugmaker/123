"""InstitutionScanner v78 CLI performance/integrity facade.

Native math-thread limits are installed before NumPy/SciPy enter through the
analytics stack, preventing each Python worker from creating another BLAS/OpenMP
pool. v78's vectorized FAST backtest, worker metadata caches and cache-aware
incremental maturity window are installed by the public analytics facade. All
v76 correctness/publication contracts remain unchanged.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

import workstation_runtime_v77 as _runtime

_runtime.configure_native_threads()

import analytics as _analytics  # noqa: E402
import backtest_command_v76 as _backtest_command  # noqa: E402
from backtest_alignment import install_analytics_alignment  # noqa: E402
from pipeline_contracts import enforce_enrichment_contract  # noqa: E402
from publication_guard_v65 import enforce_cache_first_market_contract  # noqa: E402

install_analytics_alignment(_analytics)
_backtest_command.install()
_core = importlib.import_module("main_core")
_legacy_report_enrich = _core.enrich_results
_core._legacy_report_enrich = _legacy_report_enrich


def _guarded_report_enrich(
    results: list[Any],
    source: str,
    *args: Any,
    **kwargs: Any,
) -> None:
    """Make standalone cache-report publication obey canonical safety gates."""
    _core._legacy_report_enrich(results, source, *args, **kwargs)
    enforce_enrichment_contract(results)
    health = enforce_cache_first_market_contract(results)
    _core.logging.getLogger("institution_scanner").info(
        "Standalone report market contract: %s, date=%s, lag=%d trading days, coherence=%.1f%%.",
        health["status"],
        health["dominant_date"],
        health["lag_trading_days"],
        float(health["dominant_ratio"]) * 100.0,
    )


_core.enrich_results = _guarded_report_enrich
_core.CACHE_REPORT_PUBLICATION_VERSION = "2026-08-19-v72-coherent-market-date-v1"
_core.BACKTEST_COMMAND_INTEGRITY_VERSION = (
    "2026-08-19-v76-whole-command-transaction-v1"
)
_core.PERFORMANCE_ENGINE_VERSION = (
    "2026-08-20-v78-vectorized-fast-backtest-io-cache-maturity-v2"
)

if __name__ == "__main__":
    raise SystemExit(_core.main())

sys.modules[__name__] = _core
