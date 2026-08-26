"""Canonical scan-service facade with ranking audit + universe-history capture.

The existing snapshot, freshness and transactional publication contracts remain
unchanged. Recovery/bootstrap and versioned observability compatibility are
routed through ``institution_scanner.scan_runtime`` so the production entry
point no longer imports versioned root kernels directly.
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

import scanner as _scanner
from institution_scanner import scan_runtime as _scan_runtime

_scan_runtime.install()

import scan_service_core as _core  # noqa: E402
from model_audit import run_audit  # noqa: E402
from pipeline_contracts import enforce_enrichment_contract  # noqa: E402
from scan_service_core import *  # noqa: E402,F403

_legacy_execute_scan = _core.execute_scan
_core._legacy_execute_scan = _legacy_execute_scan


def _canonical_full_csv(execution: object) -> Path | None:
    """Return the published full-result path when this execution exposes one."""
    value = getattr(execution, "full_csv", None)
    if value is None:
        return None
    try:
        path = Path(value)
    except TypeError:
        return None
    return path if str(path) else None


def _record_full_market_snapshot(
    execution: _core.ScanExecutionResult,
    request: _core.ScanRequest,
    log: logging.Logger,
) -> None:
    """Persist only a complete stock-market scan; never a manual subset."""
    if request.tickers or not request.include_stocks:
        return
    full_csv = _canonical_full_csv(execution)
    if full_csv is None:
        return
    try:
        snapshot = _scan_runtime.record_universe_snapshot_file(full_csv)
    except (OSError, ValueError, TypeError, ImportError) as exc:
        log.warning("Historical universe snapshot capture failed: %s", exc)
        return
    if snapshot is not None:
        log.info("Historical universe snapshot recorded: %s", snapshot)


def _refresh_full_market_audit(
    execution: _core.ScanExecutionResult,
    request: _core.ScanRequest,
    log: logging.Logger,
) -> None:
    """Audit only an automatic market-wide scan, never a hand-picked subset."""
    if request.tickers:
        return
    full_csv = _canonical_full_csv(execution)
    if full_csv is None:
        return
    try:
        payload = run_audit(full_csv, Path(_scanner.OUTPUT_DIR) / "audit")
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        log.warning("Full-universe ranking audit failed: %s", exc)
        return
    log.info(
        "Full-universe ranking audit refreshed: rows=%s, stocks=%s, ETFs=%s.",
        payload.get("rows", 0),
        payload.get("stocks", 0),
        payload.get("etfs", 0),
    )


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
    """Execute a canonical scan and clear recovery state only after publication."""
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
                market_health = _scan_runtime.enforce_cache_first_market_contract(results)
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

    previous_defer = bool(
        getattr(_scanner, "_defer_checkpoint_clear_until_publish", False)
    )
    if canonical_execution:
        _scanner._defer_checkpoint_clear_until_publish = True

    try:
        execution = _core._legacy_execute_scan(
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
    except BaseException:
        raise
    else:
        if canonical_execution:
            _scanner.clear_checkpoint()
            log.info("Canonical publication committed; scan checkpoint cleared.")
            _record_full_market_snapshot(execution, request, log)
            _refresh_full_market_audit(execution, request, log)
            _scan_runtime.maybe_publish_canonical_report(
                Path(_scanner.OUTPUT_DIR),
                logger=log,
                reason="scan-complete",
            )
        return execution
    finally:
        if canonical_execution:
            _scanner._defer_checkpoint_clear_until_publish = previous_defer


_core._record_full_market_snapshot = _record_full_market_snapshot
_core._refresh_full_market_audit = _refresh_full_market_audit
_core.execute_scan = execute_scan
sys.modules[__name__] = _core