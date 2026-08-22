from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

import historical_universe
from universe_snapshot_v82 import record_universe_snapshot


class UniverseSnapshotV82Tests(unittest.TestCase):
    def test_mixed_scan_snapshot_contains_stocks_and_etfs(self) -> None:
        frame = pd.DataFrame(
            {
                "Ticker": ["000001.SZ", "600000.SH", "510300.SH"],
                "DataAsOf": ["2026-08-20"] * 3,
                "UniverseEligible": [True, False, True],
                "UniverseExclusionReason": ["", "excluded", ""],
                "IsETF": [False, False, True],
                "AssetType": ["stock", "stock", "etf"],
            }
        )
        with TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            path = record_universe_snapshot(frame, snapshot_dir=directory)
            self.assertIsNotNone(path)
            assert path is not None

            snapshot = pd.read_csv(path, encoding="utf-8-sig")
            self.assertEqual(
                set(snapshot["Ticker"]),
                {"000001.SZ", "600000.SH", "510300.SH"},
            )

            etf_state, etf_reason = historical_universe.point_in_time_eligibility(
                "510300.SH", "2026-08-20", snapshot_dir=directory
            )
            self.assertTrue(etf_state)
            self.assertEqual(etf_reason, "eligible")


if __name__ == "__main__":
    unittest.main()
