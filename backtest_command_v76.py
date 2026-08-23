"""v76 whole-command transaction for standalone backtests.

``analytics.apply_backtest_ranking`` already stages ranking outputs, but the
standalone CLI/GUI backtest also writes ``ScoreCalibration.json`` and an initial
then-final ``BacktestSummary.json`` around that ranking step.  Without an outer
command transaction those metadata files can belong to a newer run than the
canonical AllResults files when postprocessing fails.

This facade redirects the main/analytics/report output roots to one temporary
command stage, seeds the current AllResults inputs, runs the existing backtest
unchanged, and publishes the entire resulting file set with the v75 durable
journal only when the command returns success. DAILY safely nests this inside
its own outer staging directory.

v90 keeps the production ranking untouched and materializes the independent
MACD/KDJ/RSI/OBV/BOLL diagnostics inside the same transaction.  GUI, candidate
CSVs and the public briefing therefore see one consistent resonance snapshot
instead of a new BacktestSummary paired with stale candidate files.
"""

from __future__ import annotations

import logging
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

import analytics as _analytics
import main_core as _main
import report as _report
from resonance_reporting_v90 import materialize_resonance_outputs
from web_report_v81 import maybe_publish_canonical_report

_LEGACY_CMD_BACKTEST = _main.cmd_backtest
_COMMAND_LOCK = threading.Lock()
_INSTALLED = False
logger = logging.getLogger("institution_scanner.backtest_command")


def _seed_backtest_inputs(destination: Path, stage: Path) -> None:
    """Copy only canonical files the legacy backtest reads before rewriting."""
    for name in ("AllResults.csv", "AllResults.parquet"):
        source = destination / name
        if not source.is_file():
            continue
        target = stage / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _canonical_backtest_runtime() -> bool:
    """Return whether CLI execution still uses the installed production engines."""
    return (
        _main.run_historical_backtest is _analytics.run_historical_backtest
        and _main.apply_backtest_ranking is _analytics.apply_backtest_ranking
    )


def _materialize_resonance_stage(stage: Path) -> None:
    """Best-effort diagnostic publication; never alters backtest eligibility."""
    try:
        payload = materialize_resonance_outputs(stage)
    except (OSError, ValueError, TypeError, KeyError, ImportError, RuntimeError) as exc:
        logger.warning("Five-factor resonance output materialization skipped: %s", exc)
        return
    status = str(payload.get("status", "") or "")
    if status == "MATERIALIZED":
        logger.info(
            "Five-factor resonance diagnostics materialized: ticker_metrics=%s, groups=%s, candidate_exports=%s.",
            payload.get("ticker_metrics", 0),
            payload.get("diagnostic_groups", 0),
            payload.get("candidate_exports", "UNKNOWN"),
        )
    elif status:
        logger.info(
            "Five-factor resonance diagnostics not materialized: %s (%s).",
            status,
            payload.get("reason", "no reason"),
        )


def cmd_backtest(args: Any) -> int:
    """Run the complete legacy backtest against staging and publish on success."""
    with _COMMAND_LOCK:
        canonical_runtime = _canonical_backtest_runtime()
        destination = Path(_main.OUTPUT_DIR)
        _report.recover_publication_transactions(destination)
        transaction_root = (
            destination / ".backtest_publication_txn" / uuid.uuid4().hex
        )
        stage = transaction_root / "stage"
        backup = transaction_root / "backup"
        stage.mkdir(parents=True, exist_ok=True)
        _seed_backtest_inputs(destination, stage)

        original_main_output = _main.OUTPUT_DIR
        original_analytics_output = _analytics.OUTPUT_DIR
        original_report_output = _report.OUTPUT_DIR
        try:
            _main.OUTPUT_DIR = stage
            _analytics.OUTPUT_DIR = stage
            _report.OUTPUT_DIR = stage
            code = int(_LEGACY_CMD_BACKTEST(args))
            if code == 0 and canonical_runtime:
                _materialize_resonance_stage(stage)
        except BaseException:
            shutil.rmtree(transaction_root, ignore_errors=True)
            raise
        finally:
            _main.OUTPUT_DIR = original_main_output
            _analytics.OUTPUT_DIR = original_analytics_output
            _report.OUTPUT_DIR = original_report_output

        if code != 0:
            shutil.rmtree(transaction_root, ignore_errors=True)
            return code

        try:
            files = [path for path in stage.rglob("*") if path.is_file()]
            if not files:
                # The stable CLI has long supported injected runner/ranking
                # engines for compatibility tests and embedders.  Such an
                # injected no-op may intentionally return success without a
                # publication artifact.  The installed production runtime must
                # still fail closed if it ever reports success with no files.
                if not canonical_runtime:
                    return 0
                raise RuntimeError(
                    "BACKTEST_PUBLICATION_FAILED: successful command produced no files"
                )
            _report._publish_stage(stage, destination, backup)
            log = _main.logging.getLogger("institution_scanner")
            log.info(
                "Standalone backtest publication committed transactionally: %d files.",
                len(files),
            )
            maybe_publish_canonical_report(
                destination,
                logger=log,
                reason="backtest-complete",
            )
            return 0
        finally:
            shutil.rmtree(transaction_root, ignore_errors=True)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _main._legacy_cmd_backtest = _LEGACY_CMD_BACKTEST
    _main.cmd_backtest = cmd_backtest
    _main.BACKTEST_COMMAND_INTEGRITY_VERSION = (
        "2026-08-23-v90-whole-command-resonance-publication-v1"
    )
    _INSTALLED = True


install()
