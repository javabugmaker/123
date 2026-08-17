"""v51 scan-service facade with fail-closed enrichment publication.

The stable application service remains in ``scan_service_core``. Canonical
CLI/GUI execution wraps only the export boundary: material enrichment loss
raises before any result artifact is written, while dependency-injected test
executors preserve their existing semantics.
"""

from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

import scan_service_core as _core
from pipeline_contracts import enforce_enrichment_contract
from scan_service_core import *  # noqa: F403

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
    """Execute a scan and fail closed before canonical export if enrichment broke."""
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
