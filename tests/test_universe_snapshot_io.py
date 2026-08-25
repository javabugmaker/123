from __future__ import annotations

from pathlib import Path

import pandas as pd

import universe_snapshot_v82 as snapshot


def test_snapshot_csv_reads_only_required_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "AllResults.csv"
    payload = {
        "Ticker": ["600000.SH", "510300.SH"],
        "DataAsOf": ["2026-08-25", "2026-08-25"],
        "UniverseEligible": [True, False],
        "UniverseExclusionReason": ["", "excluded"],
    }
    for index in range(400):
        payload[f"Junk{index}"] = [index, index]
    pd.DataFrame(payload).to_csv(source, index=False, encoding="utf-8-sig")

    calls: list[object] = []
    original = snapshot.pd.read_csv

    def observed_read_csv(*args, **kwargs):
        calls.append(kwargs.get("usecols", "HEADER" if kwargs.get("nrows") == 0 else None))
        return original(*args, **kwargs)

    monkeypatch.setattr(snapshot.pd, "read_csv", observed_read_csv)

    target = snapshot.record_universe_snapshot_file(source, snapshot_dir=tmp_path / "pit")

    assert target is not None
    assert calls[0] == "HEADER"
    assert set(calls[1]) == {
        "Ticker",
        "DataAsOf",
        "UniverseEligible",
        "UniverseExclusionReason",
    }
    result = pd.read_csv(target, encoding="utf-8-sig")
    assert list(result.columns) == [
        "AsOf",
        "Ticker",
        "Eligible",
        "ExclusionReason",
        "SnapshotVersion",
    ]
    assert len(result) == 2
