"""Batch candidate-view exports across the canonical backtest transaction.

Historically every post-ranking overlay refreshed DecisionResults/Top50/Top200:
base ranking, calibration governance, narrative alignment, reliability, then
resonance. Each refresh was correct but redundant and caused several seconds of
extra CSV/Parquet I/O per DAILY run.

For the canonical backtest command this module defers intermediate candidate
refreshes while the legacy ranking stack mutates ``AllResults``. Resonance is
then materialized without refreshing candidates, and the final fully-annotated
``AllResults.csv`` is exported exactly once. Standalone/noncanonical test paths
keep their legacy behavior.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import pandas as pd

CANDIDATE_EXPORT_BATCH_VERSION: Final = (
    "2026-08-25-v106.6-single-final-candidate-export-v1"
)


def install(backtest_module: Any) -> None:
    """Install one-export semantics into ``backtest_command_v76``."""
    if getattr(
        backtest_module,
        "_CANDIDATE_EXPORT_BATCH_V1066_INSTALLED",
        False,
    ):
        return

    original_legacy = getattr(backtest_module, "_LEGACY_CMD_BACKTEST", None)
    original_materialize = getattr(
        backtest_module,
        "_materialize_resonance_stage",
        None,
    )
    canonical_runtime = getattr(
        backtest_module,
        "_canonical_backtest_runtime",
        None,
    )
    report_module = getattr(backtest_module, "_report", None)
    if not callable(original_legacy):
        return
    if not callable(original_materialize):
        return
    if not callable(canonical_runtime):
        return
    if report_module is None:
        return

    logger = backtest_module.logger

    def batched_legacy(args: Any) -> int:
        if not bool(canonical_runtime()):
            return int(original_legacy(args))

        original_refresh = report_module.refresh_candidate_exports
        deferred_calls = 0

        def deferred_refresh(*args: Any, **kwargs: Any) -> None:
            del args, kwargs
            nonlocal deferred_calls
            deferred_calls += 1

        report_module.refresh_candidate_exports = deferred_refresh
        try:
            return int(original_legacy(args))
        finally:
            report_module.refresh_candidate_exports = original_refresh
            if deferred_calls:
                logger.info(
                    "Deferred %d intermediate candidate export refreshes; "
                    "final views will be materialized once after resonance.",
                    deferred_calls,
                )

    def batched_materialize(stage: Path) -> None:
        root = Path(stage)
        payload: dict[str, Any] = {}
        try:
            payload = backtest_module.materialize_resonance_outputs(
                root,
                refresh_candidate_exports=False,
            )
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            ImportError,
            RuntimeError,
        ) as exc:
            logger.warning(
                "Five-factor resonance output materialization skipped: %s",
                exc,
            )
        else:
            status = str(payload.get("status", "") or "")
            if status == "MATERIALIZED":
                ticker_metrics = int(payload.get("ticker_metrics", 0) or 0)
                groups = int(payload.get("diagnostic_groups", 0) or 0)
                logger.info(
                    "Five-factor resonance diagnostics materialized: "
                    "ticker_metrics=%s, groups=%s, candidate_exports=DEFERRED.",
                    ticker_metrics,
                    groups,
                )
                if ticker_metrics == 0 or groups == 0:
                    logger.warning(
                        "Five-factor resonance diagnostics are empty after a "
                        "successful backtest; candidate publication remains valid."
                    )
            elif status:
                logger.info(
                    "Five-factor resonance diagnostics not materialized: %s (%s).",
                    status,
                    payload.get("reason", "no reason"),
                )

        results_path = root / "AllResults.csv"
        if not results_path.is_file():
            raise RuntimeError(
                "FINAL_CANDIDATE_EXPORT_FAILED: AllResults.csv missing after backtest"
            )
        results = pd.read_csv(
            results_path,
            encoding="utf-8-sig",
            low_memory=False,
        ).copy()
        report_module.refresh_candidate_exports(results, output_dir=root)
        logger.info(
            "Final candidate views materialized once from %d fully annotated rows.",
            len(results),
        )

    backtest_module._LEGACY_CMD_BACKTEST = batched_legacy
    backtest_module._materialize_resonance_stage = batched_materialize
    backtest_module.CANDIDATE_EXPORT_BATCH_VERSION = CANDIDATE_EXPORT_BATCH_VERSION
    backtest_module._CANDIDATE_EXPORT_BATCH_V1066_INSTALLED = True
