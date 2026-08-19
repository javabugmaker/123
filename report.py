"""v66 report provenance and transactional publication facade.

The v51 report contract remains in ``report_v51``. v52 corrected ETF market-cap
and price-limit provenance; v58 exposed execution-only liquidity/freshness
fields in DecisionResults. v66 writes the complete ordinary scan result set to
an internal staging directory first, including lifecycle/research artifacts,
then commits the staged files with rollback of prior files if any replacement
fails. Existing SignalHistory is seeded into staging so transactional publication
never resets lifecycle continuity. DAILY may wrap this with its outer staging
transaction; nested staging is deliberate and safe.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

import report_v51 as _core
from report_v51 import *  # noqa: F403
from tradeability import daily_limit_pct, price_limit_source

_legacy_results_to_dataframe = _core._results_to_dataframe
_legacy_export_all = _core.export_all
_PUBLICATION_LOCK = threading.Lock()

_DECISION_EXECUTION_DIAGNOSTICS = (
    "TradeLiquidityApplicable",
    "TradeLiquidityPassed",
    "TradeLiquidityStatus",
    "TradeLiquidityThresholdCNY",
    "TradeLiquidityAssumedNotionalCNY",
    "TradeLiquidityParticipationPct",
    "TradeLiquidityMaxParticipationPct",
    "TradeLiquidityReason",
    "TradeLiquidityGateApplied",
    "TradeFreshnessApplicable",
    "TradeFreshnessPassed",
    "TradeFreshnessStatus",
    "TradeFreshnessTradingDays",
    "TradeFreshnessMaxTradingDays",
    "TradeFreshnessReason",
    "TradeFreshnessGateApplied",
)


def _results_to_dataframe(results: list[Any]) -> pd.DataFrame:
    frame = _legacy_results_to_dataframe(results)
    if frame.empty:
        if "MarketCapApplicable" not in frame.columns:
            frame["MarketCapApplicable"] = pd.Series(dtype=bool)
        if "PriceLimitSource" not in frame.columns:
            frame["PriceLimitSource"] = pd.Series(dtype=object)
        return frame

    is_etf = (
        frame.get("IsETF", pd.Series(False, index=frame.index))
        .fillna(False)
        .astype(bool)
    )
    if "AssetType" in frame.columns:
        is_etf |= frame["AssetType"].fillna("").astype(str).str.lower().eq("etf")

    frame["MarketCapApplicable"] = ~is_etf
    etf_index = frame.index[is_etf]
    if len(etf_index):
        frame.loc[etf_index, "MarketCap"] = None
        if "MarketCapDataAvailable" in frame.columns:
            frame.loc[etf_index, "MarketCapDataAvailable"] = False
        if "MarketCapAvailable" in frame.columns:
            frame.loc[etf_index, "MarketCapAvailable"] = False
        if "MarketCapUnitInferred" in frame.columns:
            frame.loc[etf_index, "MarketCapUnitInferred"] = False
        if "MarketCapUnitAssumption" in frame.columns:
            frame.loc[etf_index, "MarketCapUnitAssumption"] = "not_applicable"
        if "MarketCapRawTotalShares" in frame.columns:
            frame.loc[etf_index, "MarketCapRawTotalShares"] = None
        if "MarketCapNormalizedTotalShares" in frame.columns:
            frame.loc[etf_index, "MarketCapNormalizedTotalShares"] = None
        if "MarketCapSanityPassed" in frame.columns:
            frame.loc[etf_index, "MarketCapSanityPassed"] = False

    limit_values: list[float] = []
    limit_sources: list[str] = []
    for index, ticker in frame["Ticker"].astype(str).items():
        etf = bool(is_etf.loc[index])
        limit_values.append(daily_limit_pct(ticker, is_etf=etf))
        limit_sources.append(price_limit_source(ticker, is_etf=etf))
    frame["PriceLimitPct"] = limit_values
    frame["PriceLimitSource"] = limit_sources
    return frame


def _staged_files(stage: Path) -> list[Path]:
    return sorted(
        (path for path in stage.rglob("*") if path.is_file()),
        key=lambda path: str(path.relative_to(stage)).casefold(),
    )


def _publish_stage(
    stage: Path,
    destination: Path,
    backup: Path,
    *,
    replace_fn: Callable[
        [
            str | bytes | os.PathLike[str] | os.PathLike[bytes],
            str | bytes | os.PathLike[str] | os.PathLike[bytes],
        ],
        None,
    ] = os.replace,
) -> list[Path]:
    """Commit every staged file, restoring the previous set on any failure."""
    files = _staged_files(stage)
    if not files:
        raise ValueError("REPORT_PUBLICATION_FAILED: staging directory is empty")

    moved_old: list[Path] = []
    installed: list[Path] = []
    try:
        for staged in files:
            relative = staged.relative_to(stage)
            target = destination / relative
            old = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                old.parent.mkdir(parents=True, exist_ok=True)
                replace_fn(target, old)
                moved_old.append(relative)
            replace_fn(staged, target)
            installed.append(relative)
    except Exception as publish_error:
        rollback_errors: list[str] = []
        for relative in reversed(installed):
            target = destination / relative
            try:
                target.unlink(missing_ok=True)
            except OSError as exc:
                rollback_errors.append(f"remove {relative}: {exc}")
        for relative in reversed(moved_old):
            old = backup / relative
            target = destination / relative
            if not old.exists():
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                replace_fn(old, target)
            except Exception as exc:
                rollback_errors.append(f"restore {relative}: {exc}")
        detail = "; ".join(rollback_errors)
        if detail:
            raise RuntimeError(
                "REPORT_PUBLICATION_ROLLBACK_FAILED: " + detail
            ) from publish_error
        raise
    return [destination / path.relative_to(stage) for path in files]


def _remap_staged_path(path: Path, stage: Path, destination: Path) -> Path:
    try:
        return destination / path.relative_to(stage)
    except ValueError:
        return path


def _seed_lifecycle_state(
    destination: Path,
    stage: Path,
    lifecycle_module: Any,
) -> tuple[Path, Path]:
    """Copy prior state needed to calculate SignalDays into the transaction."""
    del destination
    prior_history = Path(lifecycle_module.HISTORY_FILE)
    prior_tracking = Path(lifecycle_module.TRACKING_FILE)
    staged_history = stage / prior_history.name
    staged_tracking = stage / prior_tracking.name
    for source, target in (
        (prior_history, staged_history),
        (prior_tracking, staged_tracking),
    ):
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    return staged_history, staged_tracking


def export_all(
    results: list[Any],
    top_n_csv: int = _core.TOP_N_REPORT,
    top_n_parquet: int = _core.TOP_N_PARQUET,
    data_source: str = "tickflow",
):
    """Build the complete stateful report set off to the side, then publish."""
    with _PUBLICATION_LOCK:
        # Lazy imports avoid introducing a report<->analytics import cycle at
        # module import time. At export time both modules are already loaded.
        import analytics as analytics_module
        import signal_lifecycle as lifecycle_module

        destination = Path(_core.OUTPUT_DIR)
        transaction_root = destination / ".publication_txn" / uuid.uuid4().hex
        stage = transaction_root / "stage"
        backup = transaction_root / "backup"
        stage.mkdir(parents=True, exist_ok=True)

        staged_history, staged_tracking = _seed_lifecycle_state(
            destination, stage, lifecycle_module
        )
        original_report_output = _core.OUTPUT_DIR
        original_analytics_output = analytics_module.OUTPUT_DIR
        original_history = lifecycle_module.HISTORY_FILE
        original_tracking = lifecycle_module.TRACKING_FILE
        try:
            _core.OUTPUT_DIR = stage
            analytics_module.OUTPUT_DIR = stage
            lifecycle_module.HISTORY_FILE = staged_history
            lifecycle_module.TRACKING_FILE = staged_tracking
            staged_paths = _legacy_export_all(
                results,
                top_n_csv=top_n_csv,
                top_n_parquet=top_n_parquet,
                data_source=data_source,
            )
        except Exception:
            shutil.rmtree(transaction_root, ignore_errors=True)
            raise
        finally:
            _core.OUTPUT_DIR = original_report_output
            analytics_module.OUTPUT_DIR = original_analytics_output
            lifecycle_module.HISTORY_FILE = original_history
            lifecycle_module.TRACKING_FILE = original_tracking

        try:
            _publish_stage(stage, destination, backup)
            mapped = tuple(
                _remap_staged_path(Path(path), stage, destination)
                for path in staged_paths
            )
            return mapped
        finally:
            shutil.rmtree(transaction_root, ignore_errors=True)


if hasattr(_core, "DECISION_RESULT_COLUMNS"):
    existing = tuple(_core.DECISION_RESULT_COLUMNS)
    _core.DECISION_RESULT_COLUMNS = existing + tuple(
        column for column in _DECISION_EXECUTION_DIAGNOSTICS if column not in existing
    )

_core._results_to_dataframe = _results_to_dataframe
_core._publish_stage = _publish_stage
_core._seed_lifecycle_state = _seed_lifecycle_state
_core.export_all = export_all
sys.modules[__name__] = _core
