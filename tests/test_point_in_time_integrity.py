from __future__ import annotations

from pathlib import Path

import pandas as pd

import historical_universe as universe


def _snapshot(
    root: Path,
    as_of: str,
    ticker: str = "600000.SH",
    eligible: bool = True,
) -> None:
    pd.DataFrame(
        {
            "AsOf": [as_of],
            "Ticker": [ticker],
            "Eligible": [eligible],
            "ExclusionReason": [""],
        }
    ).to_csv(
        root / f"{as_of}.csv",
        index=False,
        encoding="utf-8-sig",
    )
    universe._load_snapshot_index.cache_clear()


def test_point_in_time_snapshot_is_never_read_from_future(
    tmp_path: Path,
) -> None:
    _snapshot(tmp_path, "2026-08-24")

    eligible, reason = universe.point_in_time_eligibility(
        "600000.SH",
        "2026-08-23",
        tmp_path,
    )

    assert eligible is None
    assert reason == "snapshot_starts_after_signal"


def test_point_in_time_snapshot_carry_forward_is_bounded(
    tmp_path: Path,
) -> None:
    _snapshot(tmp_path, "2026-08-01")

    within, _ = universe.point_in_time_eligibility(
        "600000.SH",
        "2026-08-10",
        tmp_path,
    )
    stale, reason = universe.point_in_time_eligibility(
        "600000.SH",
        "2026-08-24",
        tmp_path,
    )

    assert within is True
    assert stale is None
    assert reason == "snapshot_too_old:23d"


def test_universe_status_never_claims_complete_survivorship_control(
    tmp_path: Path,
) -> None:
    _snapshot(tmp_path, "2026-08-20")
    _snapshot(tmp_path, "2026-08-21")
    _snapshot(tmp_path, "2026-08-25")

    status = universe.historical_universe_status(tmp_path)

    assert status["available"] is True
    assert status["survivorship_complete"] is False
    assert (
        status["survivorship_control"]
        == "PARTIAL_PROSPECTIVE_SNAPSHOTS"
    )
    assert (
        status["max_snapshot_age_days"]
        == universe.PIT_UNIVERSE_MAX_SNAPSHOT_AGE_DAYS
    )
    assert status["snapshot_date_count"] == 3
    assert status["max_snapshot_gap_days"] == 4
    assert status["median_snapshot_gap_days"] == 2.5
