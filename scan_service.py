"""v65 scan-service facade with fail-closed publication and safe inputs.

Before the stable application service is imported, the canonical path installs
snapshot-safe resume, non-OHLCV checkpoint fingerprints and the AkShare
freshness guard.  Canonical export then enforces both enrichment integrity and,
for cache-first scans, a bounded coherent market-date contract so an offline
stale cache cannot overwrite fresher published rankings.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

import scanner_resume_v59 as _resume_contract
import checkpoint_inputs_v59 as _checkpoint_inputs
import fundamental_refresh_v61 as _fundamental_refresh

_resume_contract.install()
_checkpoint_inputs.install()
_fundamental_refresh.install()

import scan_service_core as _core  # noqa: E402
from pipeline_contracts import enforce_enrichment_contract  # noqa: E402
from publication_guard_v65 import enforce_cache_first_market_contract  # noqa: E402
from scan_service_core import *  # noqa: E402,F403

_legacy_execute_scan = _core.execute_scan


def execute_scan(
    request: _core.ScanRequest,
    *,
    progress_callback: _core.ScanProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
    logger: logging.Logger | None = None,
    build_universe_fn: _core.BuildUniverseFn = _core.build_ticker_universe,
    run_scan_fn: _core.RunScanFn = _core.run_scan,
    export_all_fn: _core.ExportAllFn = _core.export_all,
    fundamental_path_fn: _core.FundamentalPathFn = _core.fundamental_data_path,
    refresh_fundamentals_fn: _core.RefreshFundamentalsFn = _core.refresh_fundamental_data,
    refresh_policy_fn: _core.RefreshPolicyFn | None = None,
) -> _core.ScanExecutionResult:
    """Execute a scan and fail closed before canonical export if inputs are unsafe."""
    log = logger or logging.getLogger("institution_scanner")
    canonical_execution = run_scan_fn is _core.run_scan
    selected_export = export_all_fn

    if canonical_execution:

        def _guarded_export(
            results: list[object],
            *,
            top_n_csv: int,
            top_n_parquet: int,
            data_source: str,
        ) -> tuple[Path, Path, Path, Path]:
            health = enforce_enrichment_contract(results, logger=log)
            log.info(
                "Enrichment contract: %s, %d/%d complete (%.1f%%).",
                health.status,
                health.complete_rows,
                health.successful_rows,
                health.complete_ratio * 100.0,
            )
            if request.cache_first:
                market_health = enforce_cache_first_market_contract(results)
                log.info(
                    "Cache-first market contract: %s, date=%s, lag=%d trading days, coherence=%.1f%%.",
                    market_health["status"],
                    market_health["dominant_date"],
                    market_health["lag_trading_days"],
                    float(market_health["dominant_ratio"]) * 100.0,
                )
            return export_all_fn(
                results,
                top_n_csv=top_n_csv,
                top_n_parquet=top_n_parquet,
                data_source=data_source,
            )

        selected_export = _guarded_export

    return _legacy_execute_scan(
        request,
        progress_callback=progress_callback,
        cancel_event=cancel_event,
        logger=logger,
        build_universe_fn=build_universe_fn,
        run_scan_fn=run_scan_fn,
        export_all_fn=selected_export,
        fundamental_path_fn=fundamental_path_fn,
        refresh_fundamentals_fn=refresh_fundamentals_fn,
        refresh_policy_fn=refresh_policy_fn,
    )


_core.execute_scan = execute_scan
sys.modules[__name__] = _core
