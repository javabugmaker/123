"""InstitutionScanner v76 CLI integrity facade.

The stable command implementation lives in ``main_core``. This facade installs
execution-time contracts before exposing it: aligned/cache-safe analytics,
whole-command transactional backtest publication, and the coherent market-date
gate for the cache-only ``report`` command. CLI, GUI subprocesses and DAILY
therefore share the same corrected entry semantics.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

import analytics as _analytics
import backtest_command_v76 as _backtest_command
from backtest_alignment import install_analytics_alignment
from pipeline_contracts import enforce_enrichment_contract
from publication_guard_v65 import enforce_cache_first_market_contract

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

if __name__ == "__main__":
    raise SystemExit(_core.main())

# When imported (not executed as a script), expose the real implementation
# module so DAILY output redirection mutates the globals used by command bodies.
sys.modules[__name__] = _core
