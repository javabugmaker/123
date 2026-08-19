"""v74 crash recovery for the DAILY outer publication transaction.

The DAILY core already snapshots the previously published files before staging a
new run and rolls them back on caught exceptions.  A hard process termination,
however, skips that exception handler.  This module adds two narrow contracts:

* each new ``.daily_transactions/<run_id>`` journal records the producing PID
  and host after the existing backup is complete;
* before a new DAILY run starts, unfinished journals are inspected. A live
  producer fails closed; a dead producer is either committed (when LatestRun
  already points at that run) or rolled back to its saved previous file set.

This preserves the existing DAILY staging/quality logic and adds no market-data
or scoring behavior.
"""

from __future__ import annotations

import os
import socket
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import daily_pipeline_core as _core

_LEGACY_BEGIN_TRANSACTION = _core._begin_transaction
_LEGACY_RUN_DAILY_PIPELINE = _core.run_daily_pipeline
_INSTALLED = False


def _pid_alive(pid: int) -> bool | None:
    """Return process liveness without sending a terminating Windows signal."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            completed = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        output = completed.stdout.strip()
        return bool(output and f'"{pid}"' in output and "No tasks" not in output)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _begin_transaction(pipeline_run_id: str) -> tuple[Path, set[str]]:
    """Extend the existing durable backup journal with producer identity."""
    tx_dir, existing = _LEGACY_BEGIN_TRANSACTION(pipeline_run_id)
    payload = _core._read_json(tx_dir / "state.json")
    payload.update(
        {
            "run_id": pipeline_run_id,
            "producer_pid": os.getpid(),
            "producer_host": socket.gethostname(),
            "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(
                timespec="seconds"
            ),
        }
    )
    _core._atomic_write_json(tx_dir / "state.json", payload)
    return tx_dir, existing


def _cleanup_orphan_run(run_id: str, *, keep_archive: bool) -> None:
    shutil_targets = [
        _core.OUTPUT_DIR / ".staging" / run_id,
    ]
    if not keep_archive:
        shutil_targets.append(_core.OUTPUT_DIR / "runs" / run_id)
    import shutil

    for target in shutil_targets:
        shutil.rmtree(target, ignore_errors=True)


def recover_daily_transactions() -> dict[str, int]:
    """Resolve interrupted DAILY transactions before allowing a new run."""
    group = _core.OUTPUT_DIR / ".daily_transactions"
    if not group.is_dir():
        return {"rolled_back": 0, "committed": 0, "orphaned": 0}

    latest = _core._read_json(_core.OUTPUT_DIR / "LatestRun.json")
    latest_run_id = str(latest.get("run_id", "") or "")
    current_host = socket.gethostname()
    rolled_back = 0
    committed = 0
    orphaned = 0

    for tx_dir in sorted(path for path in group.iterdir() if path.is_dir()):
        run_id = tx_dir.name
        state_path = tx_dir / "state.json"
        state = _core._read_json(state_path)
        if not state_path.is_file():
            # _begin_transaction writes state before any staging publication;
            # an orphan without state never reached a target replacement.
            import shutil

            shutil.rmtree(tx_dir, ignore_errors=True)
            _cleanup_orphan_run(run_id, keep_archive=False)
            orphaned += 1
            continue

        pid = int(state.get("producer_pid", 0) or 0)
        host = str(state.get("producer_host", "") or "")
        if pid > 0 and (not host or host == current_host):
            alive = _pid_alive(pid)
            if alive is True:
                raise RuntimeError(
                    "DAILY_ALREADY_RUNNING: "
                    f"transaction {run_id} belongs to live PID {pid}"
                )
            if alive is None:
                raise RuntimeError(
                    "DAILY_RECOVERY_UNCERTAIN: "
                    f"cannot determine liveness of PID {pid} for {run_id}"
                )
        elif pid > 0 and host and host != current_host:
            raise RuntimeError(
                "DAILY_RECOVERY_UNCERTAIN: "
                f"transaction {run_id} belongs to host {host}"
            )

        raw_existing = state.get("existing", [])
        if not isinstance(raw_existing, list):
            raise RuntimeError(
                f"DAILY_RECOVERY_FAILED: invalid transaction state {state_path}"
            )
        existing = {str(value) for value in raw_existing if str(value).strip()}

        if latest_run_id == run_id:
            # Publication + LatestRun activation completed; only transaction
            # cleanup was interrupted. Keep the new result set and archive.
            _core._commit_transaction(tx_dir)
            _cleanup_orphan_run(run_id, keep_archive=True)
            committed += 1
        else:
            # LatestRun never switched to this run, so canonical files must be
            # restored to the complete pre-run snapshot.
            _core._rollback_transaction(tx_dir, existing)
            _cleanup_orphan_run(run_id, keep_archive=False)
            rolled_back += 1

    try:
        group.rmdir()
    except OSError:
        pass

    if rolled_back or committed or orphaned:
        _core.logger.warning(
            "DAILY crash recovery: rolled_back=%d, committed=%d, orphaned=%d.",
            rolled_back,
            committed,
            orphaned,
        )
        _core._atomic_write_json(
            _core.OUTPUT_DIR / "PublicationStatus.json",
            {
                "status": "recovered",
                "rolled_back": rolled_back,
                "committed": committed,
                "orphaned": orphaned,
                "latest_run_id": latest_run_id,
            },
        )
    return {
        "rolled_back": rolled_back,
        "committed": committed,
        "orphaned": orphaned,
    }


def run_daily_pipeline(*args: Any, **kwargs: Any) -> int:
    recover_daily_transactions()
    return int(_LEGACY_RUN_DAILY_PIPELINE(*args, **kwargs))


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _core._begin_transaction = _begin_transaction
    _core.recover_daily_transactions = recover_daily_transactions
    _core.run_daily_pipeline = run_daily_pipeline
    _core.DAILY_RECOVERY_INTEGRITY_VERSION = (
        "2026-08-19-v74-pid-aware-outer-transaction-recovery-v1"
    )
    _INSTALLED = True


install()
