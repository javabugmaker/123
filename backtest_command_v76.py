"""Whole-command transaction for standalone backtests.

``analytics.apply_backtest_ranking`` stages ranking outputs, while the
standalone CLI/GUI backtest also writes model calibration and summary metadata.
This facade redirects the full command into one temporary stage and publishes
only after the command completes successfully.

v95/v96 activate canonical 504-bar score semantics, conditional WAIT_PULLBACK
fills and stationary calibration math.  The public analytics transaction stays
unchanged; calibration v96 wraps only its stable inner postprocess.
"""

from __future__ import annotations

import logging
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

import analytics as _analytics
import backtest_math_integrity_v94 as _math_integrity
import backtest_production_activation_v93 as _production_activation
import calibration_math_v96 as _calibration_math
import main_core as _main
import model_calibration as _model_calibration
import report as _report
import resonance_runtime_v91 as _resonance_runtime
from resonance_reporting_v90 import materialize_resonance_outputs
from web_report_v81 import maybe_publish_canonical_report

_resonance_runtime.install()
_production_activation.install(_analytics, _main)
_math_integrity.install(_analytics, _model_calibration)
_calibration_math.install(_analytics)
_main.apply_backtest_ranking = _analytics.apply_backtest_ranking

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
        ticker_metrics = int(payload.get("ticker_metrics", 0) or 0)
        groups = int(payload.get("diagnostic_groups", 0) or 0)
        logger.info(
            "Five-factor resonance diagnostics materialized: ticker_metrics=%s, "
            "groups=%s, candidate_exports=%s.",
            ticker_metrics,
            groups,
            payload.get("candidate_exports", "UNKNOWN"),
        )
        if ticker_metrics == 0 or groups == 0:
            logger.warning(
                "Five-factor resonance diagnostics are empty after a successful "
                "backtest. v91 parent recovery was installed, so this now means "
                "the held-out samples themselves lack full five-factor history "
                "rather than a worker-installation gap."
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
    _resonance_runtime.install()
    _production_activation.install(_analytics, _main)
    _math_integrity.install(_analytics, _model_calibration)
    _calibration_math.install(_analytics)
    _main.apply_backtest_ranking = _analytics.apply_backtest_ranking
    _main._legacy_cmd_backtest = _LEGACY_CMD_BACKTEST
    _main.cmd_backtest = cmd_backtest
    _main.BACKTEST_COMMAND_INTEGRITY_VERSION = (
        "2026-08-23-v96-scale-calibration-conditional-fill-v2"
    )
    _main.PRODUCTION_BACKTEST_ACTIVATION_VERSION = (
        _production_activation.PRODUCTION_BACKTEST_ACTIVATION_VERSION
    )
    _main.PRODUCTION_BACKTEST_MATH_VERSION = (
        _math_integrity.PRODUCTION_BACKTEST_MATH_VERSION
    )
    _main.CALIBRATION_MATH_VERSION = _calibration_math.CALIBRATION_MATH_VERSION
    _INSTALLED = True


install()
