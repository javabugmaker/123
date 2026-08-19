"""v73 report provenance and crash-recoverable transactional publication facade.

The v51 report contract remains in ``report_v51``. v52 corrected ETF market-cap
and price-limit provenance; v58 exposed execution diagnostics; v66 staged the
stateful ordinary report set with in-process rollback. v73 adds a durable
transaction journal: all previous files are copied to backup before any target
is replaced, COMMITTING/COMMITTED state is atomically recorded, and the next
publication automatically rolls back any interrupted transaction.

Existing SignalHistory is seeded into staging so transactional publication never
resets lifecycle continuity. DAILY may wrap this with its outer staging
transaction; nested staging remains deliberate and safe.
"""

from __future__ import annotations

import json
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
_TRANSACTION_GROUPS = (".publication_txn", ".backtest_publication_txn")

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


def _write_transaction_state(root: Path, payload: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    state = root / "state.json"
    temporary = root / ".state.json.tmp"
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, state)
    finally:
        temporary.unlink(missing_ok=True)


def _read_transaction_state(root: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads((root / "state.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _recover_publication_transaction(
    root: Path,
    destination: Path,
    *,
    replace_fn: Callable[
        [
            str | bytes | os.PathLike[str] | os.PathLike[bytes],
            str | bytes | os.PathLike[str] | os.PathLike[bytes],
        ],
        None,
    ] = os.replace,
) -> bool:
    """Rollback a journaled PREPARED/COMMITTING transaction after interruption."""
    state = _read_transaction_state(root)
    if state is None:
        return False
    status = str(state.get("status", "")).upper()
    if status == "COMMITTED":
        shutil.rmtree(root, ignore_errors=True)
        return True
    if status not in {"PREPARED", "COMMITTING"}:
        return False

    raw_entries = state.get("entries", [])
    if not isinstance(raw_entries, list):
        raise RuntimeError(f"REPORT_PUBLICATION_RECOVERY_FAILED: invalid journal {root}")

    errors: list[str] = []
    backup_root = root / "backup"
    for raw in reversed(raw_entries):
        if not isinstance(raw, dict):
            errors.append("invalid journal entry")
            continue
        relative_text = str(raw.get("path", "") or "").strip()
        relative = Path(relative_text)
        if not relative_text or relative.is_absolute() or ".." in relative.parts:
            errors.append(f"invalid path {relative_text!r}")
            continue
        target = destination / relative
        existed = bool(raw.get("existed", False))
        try:
            if existed:
                backup = backup_root / relative
                if not backup.is_file():
                    raise FileNotFoundError(f"missing backup {backup}")
                target.parent.mkdir(parents=True, exist_ok=True)
                replace_fn(backup, target)
            else:
                target.unlink(missing_ok=True)
        except (OSError, ValueError) as exc:
            errors.append(f"{relative}: {exc}")

    if errors:
        raise RuntimeError(
            "REPORT_PUBLICATION_RECOVERY_FAILED: " + "; ".join(errors)
        )
    shutil.rmtree(root, ignore_errors=True)
    return True


def recover_publication_transactions(destination: Path) -> int:
    """Recover all durable report/backtest transactions before a new publication."""
    recovered = 0
    for group_name in _TRANSACTION_GROUPS:
        group = destination / group_name
        if not group.is_dir():
            continue
        for root in sorted(path for path in group.iterdir() if path.is_dir()):
            state = _read_transaction_state(root)
            if state is None:
                # Pre-v73 orphan or a crash before the journal became visible.
                # No v73 target replacement can occur before state.json exists,
                # so leave an unknown legacy orphan untouched rather than guess.
                continue
            if _recover_publication_transaction(root, destination):
                recovered += 1
        try:
            group.rmdir()
        except OSError:
            pass
    if recovered:
        _core.logger.warning(
            "Recovered %d interrupted result publication transaction(s).",
            recovered,
        )
    return recovered


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
    """Commit every staged file with a durable all-or-rollback journal."""
    files = _staged_files(stage)
    if not files:
        raise ValueError("REPORT_PUBLICATION_FAILED: staging directory is empty")

    root = backup.parent
    entries: list[dict[str, Any]] = []
    try:
        # Prepare every backup before replacing even the first canonical file.
        for staged in files:
            relative = staged.relative_to(stage)
            target = destination / relative
            existed = target.is_file()
            if existed:
                old = backup / relative
                old.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, old)
            entries.append({"path": relative.as_posix(), "existed": existed})

        journal = {"version": 1, "status": "PREPARED", "entries": entries}
        _write_transaction_state(root, journal)
        journal["status"] = "COMMITTING"
        _write_transaction_state(root, journal)

        for staged in files:
            relative = staged.relative_to(stage)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            replace_fn(staged, target)

        journal["status"] = "COMMITTED"
        _write_transaction_state(root, journal)
    except BaseException as publish_error:
        try:
            state = _read_transaction_state(root)
            if state is not None:
                _recover_publication_transaction(
                    root,
                    destination,
                    replace_fn=replace_fn,
                )
        except BaseException as rollback_error:
            raise RuntimeError(
                f"REPORT_PUBLICATION_ROLLBACK_FAILED: {rollback_error}"
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
        import analytics as analytics_module
        import signal_lifecycle as lifecycle_module

        destination = Path(_core.OUTPUT_DIR)
        recover_publication_transactions(destination)
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
        except BaseException:
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
_core._write_transaction_state = _write_transaction_state
_core._read_transaction_state = _read_transaction_state
_core._recover_publication_transaction = _recover_publication_transaction
_core.recover_publication_transactions = recover_publication_transactions
_core._publish_stage = _publish_stage
_core._seed_lifecycle_state = _seed_lifecycle_state
_core.export_all = export_all
_core.REPORT_PUBLICATION_INTEGRITY_VERSION = (
    "2026-08-19-v73-journaled-crash-recovery-v2"
)
sys.modules[__name__] = _core
