"""Contract tests for the checkpoint helpers in ``scanner_core``.

These 45 lines are *taken over*, not dead: ``scanner_resume_v59`` reimplements
the resume loop but still reads and writes through ``_CHECKPOINT_PATH``, and the
on-disk schema is shared.  What is pinned here is therefore the **file
contract** -- the schema, the four invalidation rules and the failure handling --
rather than the bodies of ``save_checkpoint`` / ``load_checkpoint``.

Why this is worth its own file
------------------------------
A checkpoint is the one piece of scan state that survives a crash.  Two failure
modes are silent and expensive, and neither is visible in a green run:

* **over-invalidation** -- a checkpoint that never loads makes every restart
  rescan from zero;
* **under-invalidation** -- a stale checkpoint that *does* load makes a restart
  skip tickers under a scoring version whose numbers no longer apply.

Both are pinned below.

Everything is written to ``tmp_path``; no test touches the real checkpoint.
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import scanner_core as scanner

SHANGHAI = ZoneInfo("Asia/Shanghai")


@pytest.fixture
def checkpoint_path(tmp_path, monkeypatch) -> "object":
    """Redirect ``_CHECKPOINT_PATH`` so nothing writes to the real one."""
    target = tmp_path / "scan_checkpoint.json"
    monkeypatch.setattr(scanner, "_CHECKPOINT_PATH", target)
    monkeypatch.setattr(scanner, "ENABLE_CHECKPOINT", True)
    return target


def _write_checkpoint(path, **overrides: object) -> None:
    """A checkpoint that is valid as of *now*, with fields overridable."""
    payload = {
        "active": True,
        "processed": ["600000.SH", "000001.SZ"],
        "timestamp": datetime.now().isoformat(),
        "trade_date": scanner._checkpoint_trade_date(),
        "data_source": "tickflow",
        "scoring_version": scanner.SCORING_VERSION,
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# _checkpoint_trade_date
# ---------------------------------------------------------------------------


def test_a_naive_datetime_is_read_as_shanghai_time() -> None:
    """A bare datetime is localised, not converted (scanner_core:387-388)."""
    assert scanner._checkpoint_trade_date(datetime(2026, 9, 16, 8, 0)) == "2026-09-16"


def test_an_aware_datetime_is_converted_to_shanghai() -> None:
    """The date boundary is Shanghai's, not the machine's or UTC's.

    A UTC evening timestamp belongs to the *next* Shanghai day.  Getting this
    wrong shifts every checkpoint written after 16:00 UTC onto the wrong trade
    date, which silently discards the day's work at the next restart.
    """
    evening_utc = datetime(2026, 9, 16, 17, 30, tzinfo=ZoneInfo("UTC"))
    assert scanner._checkpoint_trade_date(evening_utc) == "2026-09-17"

    morning_utc = datetime(2026, 9, 16, 1, 0, tzinfo=ZoneInfo("UTC"))
    assert scanner._checkpoint_trade_date(morning_utc) == "2026-09-16"


def test_the_two_branches_agree_on_the_same_instant() -> None:
    """Naive and aware inputs denoting one instant give one date."""
    naive = datetime(2026, 9, 16, 12, 0)
    aware = datetime(2026, 9, 16, 4, 0, tzinfo=ZoneInfo("UTC"))
    assert scanner._checkpoint_trade_date(naive) == scanner._checkpoint_trade_date(aware)


# ---------------------------------------------------------------------------
# save / load round trip
# ---------------------------------------------------------------------------


def test_a_round_trip_preserves_the_processed_set(checkpoint_path) -> None:
    scanner.save_checkpoint({"600000.SH", "000001.SZ"})
    assert scanner.load_checkpoint() == {"600000.SH", "000001.SZ"}


def test_tickers_are_normalised_on_the_way_in(checkpoint_path) -> None:
    """Normalisation happens at write time too (scanner_core:400), not only on read.

    So the stored list is canonical even if a caller passed mixed case, and a
    checkpoint written by an older caller still matches a normalised lookup.
    """
    scanner.save_checkpoint({"600000.sh", " 000001.sz "})
    stored = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert stored["processed"] == ["000001.SZ", "600000.SH"], "not normalised and sorted"
    assert scanner.load_checkpoint() == {"600000.SH", "000001.SZ"}


def test_the_written_schema_carries_every_version_field(checkpoint_path) -> None:
    """The four fields ``load_checkpoint`` validates against, plus the payload."""
    scanner.save_checkpoint({"600000.SH"}, data_source="akshare")
    stored = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert stored["active"] is True
    assert stored["trade_date"] == scanner._checkpoint_trade_date()
    assert stored["scoring_version"] == scanner.SCORING_VERSION
    assert stored["data_source"] == "tickflow", "a legacy alias was not normalised on save"


def test_a_disabled_checkpoint_writes_nothing(checkpoint_path, monkeypatch) -> None:
    """``ENABLE_CHECKPOINT`` gates the write itself (scanner_core:395-396)."""
    monkeypatch.setattr(scanner, "ENABLE_CHECKPOINT", False)
    scanner.save_checkpoint({"600000.SH"})
    assert not checkpoint_path.exists()


# ---------------------------------------------------------------------------
# invalidation rules
# ---------------------------------------------------------------------------


def test_a_checkpoint_from_another_trade_date_is_discarded(checkpoint_path) -> None:
    """Rule 1 (scanner_core:418-419)."""
    _write_checkpoint(checkpoint_path, trade_date="2000-01-01")
    assert scanner.load_checkpoint() == set()


def test_a_checkpoint_from_another_scoring_version_is_discarded(checkpoint_path) -> None:
    """Rule 2 (scanner_core:420-421).

    The expensive one to get wrong: loading it would skip already-scored tickers
    while ranking them with a different scoring implementation.
    """
    _write_checkpoint(checkpoint_path, scoring_version="not-a-real-version")
    assert scanner.load_checkpoint() == set()


def test_an_inactive_checkpoint_is_discarded(checkpoint_path) -> None:
    """Rule 3 (scanner_core:416-417)."""
    _write_checkpoint(checkpoint_path, active=False)
    assert scanner.load_checkpoint() == set()


def test_a_mismatched_data_source_is_discarded(checkpoint_path) -> None:
    """Rule 4 (scanner_core:422-424)."""
    _write_checkpoint(checkpoint_path, data_source="sina")
    assert scanner.load_checkpoint(data_source="akshare") == set()


def test_an_empty_data_source_matches_any_checkpoint(checkpoint_path) -> None:
    """The other half of rule 4: no source requested means no source checked.

    Pinned because it is easy to "tighten" by accident, which would invalidate
    every checkpoint written by a caller that passes no source.
    """
    _write_checkpoint(checkpoint_path, data_source="tickflow")
    assert scanner.load_checkpoint() == {"600000.SH", "000001.SZ"}


def test_a_missing_file_is_an_empty_set(checkpoint_path) -> None:
    assert not checkpoint_path.exists()
    assert scanner.load_checkpoint() == set()


# ---------------------------------------------------------------------------
# failure handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    ["{ not json", "", "[1, 2, 3]", "null"],
    ids=["malformed", "empty", "wrong_shape", "null"],
)
def test_a_corrupt_checkpoint_never_raises(content: str, checkpoint_path) -> None:
    """A bad file costs one rescan, not a crashed run (scanner_core:426-427)."""
    checkpoint_path.write_text(content, encoding="utf-8")
    assert scanner.load_checkpoint() == set()


def test_saving_over_an_unwritable_target_only_warns(checkpoint_path, monkeypatch) -> None:
    """Same bargain on the write side (scanner_core:407-408).

    Checkpointing is an optimisation; a read-only output directory must degrade
    to "no resume" rather than abort a scan that is already running.
    """
    monkeypatch.setattr(
        scanner,
        "_CHECKPOINT_PATH",
        checkpoint_path / "nested" / "missing-dir" / "cp.json",
    )
    scanner.save_checkpoint({"600000.SH"})


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


def test_clearing_removes_the_file(checkpoint_path) -> None:
    _write_checkpoint(checkpoint_path)
    scanner.clear_checkpoint()
    assert not checkpoint_path.exists()
    assert scanner.load_checkpoint() == set()


def test_clearing_a_missing_file_is_a_no_op(checkpoint_path) -> None:
    scanner.clear_checkpoint()
