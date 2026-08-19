"""InstitutionScanner v72 CLI facade.

The stable command implementation lives in ``main_core``. This facade installs
execution-time contracts before importing that implementation so CLI, GUI
subprocesses and the daily pipeline share corrected backtest semantics. The
standalone ``report`` command is cache-only by design, so v72 applies the same
coherent market-date publication gate as GUI cache-first scans before it can
overwrite canonical outputs.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

import analytics as _analytics
from backtest_alignment import install_analytics_alignment
from pipeline_contracts import enforce_enrichment_contract
from publication_guard_v65 import enforce_cache_first_market_contract

install_analytics_alignment(_analytics)
_core = importlib.import_module("main_core")
_legacy_report_enrich = _core.enrich_results


def _guarded_report_enrich(
    results: list[Any],
    source: str,
    *args: Any,
    **kwargs: Any,
) -> None:
    """Make standalone cache-report publication obey canonical safety gates."""
    _legacy_report_enrich(results, source, *args, **kwargs)
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

if __name__ == "__main__":
    raise SystemExit(_core.main())

# When imported (not executed as a script), expose the real implementation
# module so daily_pipeline OUTPUT_DIR redirection mutates the globals used by
# cmd_scan/cmd_backtest rather than an inert wrapper namespace.
sys.modules[__name__] = _core
