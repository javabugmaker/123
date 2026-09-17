"""Retention for the per-run log files ``setup_logging`` creates.

``setup_logging`` names each run's file with a second-resolution timestamp, so
the log directory grows in file *count*, not in file size: left alone it
reached 11,826 files / 178 MB here, at roughly 600 new files a day.  Rotation
is the wrong fix for that shape -- the individual files never grow -- so
``log_retention.prune_old_logs`` deletes by age instead.

Age is the only criterion; there is no "keep at least N" floor.  A floor reads
as a safety net but inverts into a bug on a small directory: with a floor of
50, five logs that are each a year old could never be deleted at all, which is
precisely the case a prune exists for.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from config_core import setup_logging
from log_retention import LOG_RETENTION_DAYS, prune_logs_once_per_day, prune_old_logs

_DAY = 86_400


def _touch(path: Path, age_days: float, now: float) -> Path:
    path.write_text("log line\n", encoding="utf-8")
    stamp = now - age_days * _DAY
    os.utime(path, (stamp, stamp))
    return path


def test_a_file_past_the_retention_window_is_deleted(tmp_path: Path) -> None:
    now = time.time()
    stale = _touch(tmp_path / "scanner_20260101_000000.log", LOG_RETENTION_DAYS + 1, now)
    assert prune_old_logs(tmp_path, now=now) == 1
    assert not stale.exists()


def test_a_file_inside_the_retention_window_survives(tmp_path: Path) -> None:
    now = time.time()
    fresh = _touch(tmp_path / "scanner_20260917_000000.log", LOG_RETENTION_DAYS - 1, now)
    assert prune_old_logs(tmp_path, now=now) == 0
    assert fresh.exists()


def test_a_mixed_directory_keeps_only_the_recent_files(tmp_path: Path) -> None:
    now = time.time()
    old = _touch(tmp_path / "scanner_old.log", LOG_RETENTION_DAYS + 1, now)
    recent = _touch(tmp_path / "scanner_recent.log", LOG_RETENTION_DAYS - 1, now)
    assert prune_old_logs(tmp_path, now=now) == 1
    assert not old.exists()
    assert recent.exists()


def test_pruning_is_by_age_and_not_by_count(tmp_path: Path) -> None:
    """A small directory of stale logs is still emptied -- no floor protects
    them just for being few."""
    now = time.time()
    for i in range(5):
        _touch(tmp_path / f"scanner_{i:06d}.log", 400, now)
    assert prune_old_logs(tmp_path, now=now) == 5
    assert list(tmp_path.glob("*.log")) == []


def test_non_log_files_are_left_alone(tmp_path: Path) -> None:
    now = time.time()
    notes = _touch(tmp_path / "notes.txt", 400, now)
    marker = _touch(tmp_path / ".last_log_prune", 400, now)
    assert prune_old_logs(tmp_path, now=now) == 0
    assert notes.exists()
    assert marker.exists()


def test_a_missing_directory_is_not_an_error(tmp_path: Path) -> None:
    assert prune_old_logs(tmp_path / "does_not_exist") == 0


def test_the_daily_sweep_runs_only_once_a_day(tmp_path: Path) -> None:
    now = time.time()
    _touch(tmp_path / "scanner_old.log", 400, now)
    assert prune_logs_once_per_day(tmp_path) == 1
    survivor = _touch(tmp_path / "scanner_old_2.log", 400, now)
    assert prune_logs_once_per_day(tmp_path) == 0
    assert survivor.exists()


def test_the_daily_sweep_reruns_when_the_marker_is_stale(tmp_path: Path) -> None:
    (tmp_path / ".last_log_prune").write_text("2020-01-01", encoding="utf-8")
    _touch(tmp_path / "scanner_old.log", 400, time.time())
    assert prune_logs_once_per_day(tmp_path) == 1


def test_setup_logging_actually_sweeps_the_directory_it_writes_to(
    tmp_path: Path,
) -> None:
    """Without this the prune is unreachable code that nothing ever calls."""
    log_dir = tmp_path / "logs"
    setup_logging("test.log_retention_probe", log_to_file=True, log_dir=log_dir)
    assert (log_dir / ".last_log_prune").exists()
