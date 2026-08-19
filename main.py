"""InstitutionScanner v56 entry facade.

With no CLI arguments the canonical entry point now opens the v56 decision GUI,
including local TickFlow API credential controls.  Existing scan/report/
download/backtest/clean subcommands continue to use ``main_core`` unchanged.
"""

from __future__ import annotations

import importlib
import sys
from typing import Any

import analytics as _analytics
from backtest_alignment import install_analytics_alignment
from pipeline_contracts import enforce_enrichment_contract

install_analytics_alignment(_analytics)
_core = importlib.import_module("main_core")
_legacy_report_enrich = _core.enrich_results


def _guarded_report_enrich(
    results: list[Any],
    source: str,
    *args: Any,
    **kwargs: Any,
) -> None:
    """Make the standalone report command obey the canonical enrichment gate."""
    _legacy_report_enrich(results, source, *args, **kwargs)
    enforce_enrichment_contract(results)


_core.enrich_results = _guarded_report_enrich

if __name__ == "__main__":
    if len(sys.argv) == 1:
        from gui_v56 import main as gui_main

        gui_main()
        raise SystemExit(0)
    raise SystemExit(_core.main())

# When imported (not executed as a script), expose the real implementation
# module so daily_pipeline OUTPUT_DIR redirection mutates the globals used by
# cmd_scan/cmd_backtest rather than an inert wrapper namespace.
sys.modules[__name__] = _core
