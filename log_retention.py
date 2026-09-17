"""Retention for the per-run log files ``config_core.setup_logging`` creates.

``setup_logging`` names each run's file with a second-resolution timestamp, so
the log directory grows in file *count*, not in file size: before this module
existed it reached 11,826 files / 178 MB here, at roughly 600 new files a day
because a single process opens three loggers (``scanner_downloader``,
``scanner_score``, ``scanner_scanner``).

Rotation is the wrong fix for that shape -- the individual files never grow,
they are born at final size -- so this deletes by age instead.  It lives in its
own module rather than in ``config_core`` because that file is under a
shrink-only budget: adding ~600 bytes there would have tripped
``test_every_large_root_module_has_a_budget`` and bought a new ceiling for a
module the project is trying to make smaller.

Age is the only criterion; there is no "keep at least N" floor.  A floor reads
as a safety net but inverts into a bug on a small directory: with a floor of
50, five logs that are each a year old could never be deleted at all, which is
precisely the case a prune exists for.  Logs are regenerated on every run and
are not the record of anything, so deleting them all is the honest outcome when
they are all stale.

This module deliberately does not import ``config_core`` -- it takes the log
directory as an argument, keeping the dependency one-way
(``config_core`` -> ``log_retention``) and free of an import cycle.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Final

#: How long a ``*.log`` file survives before :func:`prune_old_logs` removes it.
#: Seven days, so a failure noticed on Monday can still be read off the run
#: that produced it on Friday.
LOG_RETENTION_DAYS: Final[int] = 7

#: Marker recording the last day the sweep ran, so it costs one directory
#: scan per day rather than one per ``setup_logging`` call.
_PRUNE_MARKER = ".last_log_prune"

_SECONDS_PER_DAY = 86_400


def prune_old_logs(
    log_dir: Path,
    retention_days: int = LOG_RETENTION_DAYS,
    now: float | None = None,
) -> int:
    """Delete ``*.log`` files older than ``retention_days`` from ``log_dir``.

    Only ``*.log`` at the top level is considered, so the sweep marker and any
    other artefact are left alone.  Returns the number deleted.  Errors are
    swallowed: housekeeping must never break the run that triggered it.
    """
    if not log_dir.is_dir():
        return 0
    cutoff = (now if now is not None else time.time()) - retention_days * _SECONDS_PER_DAY
    try:
        candidates = [p for p in log_dir.glob("*.log") if p.is_file()]
    except OSError:
        return 0
    deleted = 0
    for path in candidates:
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
        except OSError:
            continue
        deleted += 1
    return deleted


def prune_logs_once_per_day(log_dir: Path) -> int:
    """Run :func:`prune_old_logs` at most once per calendar day.

    Sweeping on every ``setup_logging`` call would be pure overhead -- a single
    process opens three loggers.  A marker file records the last day the sweep
    ran.
    """
    marker = log_dir / _PRUNE_MARKER
    today = time.strftime("%Y-%m-%d")
    try:
        if marker.exists() and marker.read_text(encoding="utf-8").strip() == today:
            return 0
    except OSError:
        return 0
    deleted = prune_old_logs(log_dir)
    try:
        marker.write_text(today, encoding="utf-8")
    except OSError:
        pass
    return deleted
