"""InstitutionScanner canonical CLI integrity facade.

The stable scanner/report commands remain compatibility kernels. v106.6 adds two
engineering-only safeguards around the canonical backtest path: wide result
frames are consolidated before post-ranking mutation, and candidate views are
materialized once after all diagnostics are complete. Neither changes scoring,
ranking, eligibility or TradeReady semantics.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import workstation_runtime_v77 as _runtime

_runtime.configure_native_threads()

import analytics as _analytics  # noqa: E402
import backtest_command_v76 as _backtest_command  # noqa: E402
from backtest_alignment import install_analytics_alignment  # noqa: E402
from institution_scanner import export_batch as _export_batch  # noqa: E402
from institution_scanner import postprocess_performance as _postprocess_performance  # noqa: E402
from model_audit import run_audit  # noqa: E402
from pipeline_contracts import enforce_enrichment_contract  # noqa: E402
from publication_guard_v65 import enforce_cache_first_market_contract  # noqa: E402
from universe_snapshot_v82 import record_universe_snapshot_file  # noqa: E402

install_analytics_alignment(_analytics)
_backtest_command.install()
_postprocess_performance.install()
_export_batch.install(_backtest_command)
_core = importlib.import_module("main_core")
_legacy_report_enrich = _core.enrich_results
_legacy_cmd_report = _core.cmd_report
_core._legacy_report_enrich = _legacy_report_enrich
_core._legacy_cmd_report = _legacy_cmd_report


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


def _refresh_report_observability(args: Any) -> None:
    """Refresh diagnostics after a successful standalone report publication."""
    log = _core.logging.getLogger("institution_scanner")
    result_path = Path(_core.OUTPUT_DIR) / "AllResults.csv"
    if not result_path.exists():
        log.warning(
            "Standalone report completed but AllResults.csv is missing; v82 audit skipped."
        )
        return

    try:
        payload = run_audit(result_path, Path(_core.OUTPUT_DIR) / "audit")
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        log.warning("Standalone report ranking audit failed: %s", exc)
    else:
        log.info(
            "Standalone report ranking audit refreshed: rows=%s, stocks=%s, ETFs=%s.",
            payload.get("rows", 0),
            payload.get("stocks", 0),
            payload.get("etfs", 0),
        )

    # ``report`` always rebuilds the complete selected universe. When stocks
    # are included, record the stock portion; the snapshot writer explicitly
    # excludes ETF rows from a mixed stock+ETF report.
    if bool(getattr(args, "etfs_only", False)):
        return
    try:
        snapshot = record_universe_snapshot_file(result_path)
    except (OSError, ValueError, TypeError, ImportError) as exc:
        log.warning("Standalone report universe snapshot capture failed: %s", exc)
        return
    if snapshot is not None:
        log.info("Standalone report stock-universe snapshot recorded: %s", snapshot)


def cmd_report(args: Any) -> int:
    """Run the stable report command, then reconcile observational state."""
    status = int(_core._legacy_cmd_report(args))
    if status == 0:
        _refresh_report_observability(args)
    return status


_core.enrich_results = _guarded_report_enrich
_core.cmd_report = cmd_report
_core.CACHE_REPORT_PUBLICATION_VERSION = (
    "2026-08-21-v82-report-audit-snapshot-v1"
)
_core.CANDIDATE_EXPORT_BATCH_VERSION = _export_batch.CANDIDATE_EXPORT_BATCH_VERSION
_core.POSTPROCESS_FRAME_PERFORMANCE_VERSION = (
    _postprocess_performance.POSTPROCESS_FRAME_PERFORMANCE_VERSION
)
_core.BACKTEST_COMMAND_INTEGRITY_VERSION = (
    _export_batch.CANDIDATE_EXPORT_BATCH_VERSION
)
_core.PERFORMANCE_ENGINE_VERSION = (
    _postprocess_performance.POSTPROCESS_FRAME_PERFORMANCE_VERSION
)

if __name__ == "__main__":
    raise SystemExit(_core.main())

sys.modules[__name__] = _core
