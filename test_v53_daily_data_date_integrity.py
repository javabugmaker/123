from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import config
import daily_pipeline


def _write_rows(path: Path, dates: list[str], *, errors: int = 0) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["Ticker", "AssetType", "DataAsOf", "Error"],
        )
        writer.writeheader()
        for index, value in enumerate(dates):
            writer.writerow(
                {
                    "Ticker": f"{index:06d}.SZ",
                    "AssetType": "stock",
                    "DataAsOf": value,
                    "Error": "",
                }
            )
        for index in range(errors):
            writer.writerow(
                {
                    "Ticker": f"E{index:05d}.SZ",
                    "AssetType": "stock",
                    "DataAsOf": "",
                    "Error": "provider error",
                }
            )


class V53DailyDataDateIntegrityTests(unittest.TestCase):
    def test_versions_mark_provider_settlement_contract(self) -> None:
        self.assertIn("v53", config.PIPELINE_VERSION)
        self.assertIn("v53", config.OUTPUT_CONTRACT_VERSION)
        self.assertEqual(config.DAILY_MAX_PROVIDER_LAG_TRADING_DAYS, 1)

    def test_coherent_one_trading_day_provider_lag_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "AllResults.csv"
            _write_rows(path, ["2026-08-17"] * 100, errors=1)
            profile = daily_pipeline._csv_profile(path, "2026-08-18")

        self.assertEqual(profile["market_data_date_status"], "PROVIDER_LAG")
        self.assertTrue(profile["market_data_date_accepted"])
        self.assertEqual(profile["effective_trading_date"], "2026-08-17")
        self.assertEqual(profile["market_data_lag_trading_days"], 1)
        self.assertEqual(profile["calendar_expected_fresh_ratio"], 0.0)
        self.assertGreater(profile["fresh_ratio"], 0.98)
        self.assertEqual(profile["data_asof_counts"], {"2026-08-17": 100})

    def test_partial_settlement_remains_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "AllResults.csv"
            _write_rows(path, ["2026-08-17"] * 60 + ["2026-08-18"] * 40)
            profile = daily_pipeline._csv_profile(path, "2026-08-18")

        self.assertEqual(profile["market_data_date_status"], "MIXED_DATA_DATES")
        self.assertFalse(profile["market_data_date_accepted"])
        errors = daily_pipeline._quality_gate_errors(
            profile, {}, quality_gates=True
        )
        self.assertTrue(any("行情交易日一致性失败" in error for error in errors))
        self.assertTrue(any("覆盖率" in error for error in errors))

    def test_two_trading_day_staleness_remains_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "AllResults.csv"
            _write_rows(path, ["2026-08-14"] * 100)
            profile = daily_pipeline._csv_profile(path, "2026-08-18")

        self.assertEqual(profile["market_data_date_status"], "STALE_DATA")
        self.assertFalse(profile["market_data_date_accepted"])
        self.assertEqual(profile["market_data_lag_trading_days"], 2)

    def test_current_day_full_settlement_remains_on_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "AllResults.csv"
            _write_rows(path, ["2026-08-18"] * 100)
            profile = daily_pipeline._csv_profile(path, "2026-08-18")

        self.assertEqual(profile["market_data_date_status"], "ON_TIME")
        self.assertTrue(profile["market_data_date_accepted"])
        self.assertEqual(profile["effective_trading_date"], "2026-08-18")
        self.assertEqual(profile["fresh_ratio"], 1.0)


if __name__ == "__main__":
    unittest.main()
