from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

import checkpoint_inputs_v59 as inputs
import scanner
import scanner_resume_v59 as resume


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [10.0],
            "High": [10.5],
            "Low": [9.5],
            "Close": [10.2],
            "Volume": [1_000_000.0],
            "Amount": [10_200_000.0],
        },
        index=pd.to_datetime(["2026-08-19"]),
    )


class CheckpointInputFingerprintTests(unittest.TestCase):
    def tearDown(self) -> None:
        resume._reset_session()

    def test_contract_contains_fundamental_and_universe_fingerprints(self) -> None:
        with TemporaryDirectory() as temp_dir, patch.object(
            inputs._config, "CACHE_DIR", Path(temp_dir)
        ):
            payload = resume._contract_payload()

        self.assertIn("fundamental_data_signature", payload)
        self.assertIn("universe_metadata_signature", payload)
        self.assertEqual(payload["fundamental_data_signature"], "missing")
        self.assertEqual(payload["universe_metadata_signature"], "missing")

    def test_fundamental_cache_change_invalidates_checkpoint(self) -> None:
        frame = _frame()
        result = scanner.ScanResult(ticker="000001.SZ", close=10.2)

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            checkpoint = root / "_checkpoint.json"
            fundamental = root / "fundamental_data.csv"
            fundamental.write_text("Ticker,ROE\n000001.SZ,10\n", encoding="utf-8")

            with patch.object(scanner, "_CHECKPOINT_PATH", checkpoint), patch.object(
                scanner, "_checkpoint_trade_date", return_value="2026-08-19"
            ), patch.object(inputs._config, "CACHE_DIR", root), patch.object(
                inputs._config, "FUNDAMENTAL_DATA_PATH", ""
            ):
                scanner.save_checkpoint(
                    {"000001.SZ"},
                    "tickflow",
                    results=[result],
                    market_frames={"000001.SZ": frame},
                )
                self.assertEqual(set(scanner.load_checkpoint("tickflow")), {"000001.SZ"})

                fundamental.write_text(
                    "Ticker,ROE\n000001.SZ,15\n",
                    encoding="utf-8",
                )
                os.utime(fundamental, None)
                loaded = scanner.load_checkpoint("tickflow")

        self.assertEqual(loaded, set())

    def test_universe_metadata_change_invalidates_checkpoint(self) -> None:
        frame = _frame()
        result = scanner.ScanResult(ticker="000001.SZ", close=10.2)

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            checkpoint = root / "_checkpoint.json"
            universe = root / "_tickflow_universe.json"
            universe.write_text('{"stocks":["000001.SZ"]}', encoding="utf-8")

            with patch.object(scanner, "_CHECKPOINT_PATH", checkpoint), patch.object(
                scanner, "_checkpoint_trade_date", return_value="2026-08-19"
            ), patch.object(inputs._config, "CACHE_DIR", root), patch.object(
                inputs._config, "FUNDAMENTAL_DATA_PATH", ""
            ):
                scanner.save_checkpoint(
                    {"000001.SZ"},
                    "tickflow",
                    results=[result],
                    market_frames={"000001.SZ": frame},
                )
                self.assertEqual(set(scanner.load_checkpoint("tickflow")), {"000001.SZ"})

                universe.write_text(
                    '{"stocks":["000001.SZ"],"metadata":{"000001.SZ":{"industry":"银行"}}}',
                    encoding="utf-8",
                )
                os.utime(universe, None)
                loaded = scanner.load_checkpoint("tickflow")

        self.assertEqual(loaded, set())

    def test_configured_external_fundamental_path_is_fingerprinted(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            external = root / "external_fundamentals.csv"
            external.write_text("Ticker,ROE\n000001.SZ,12\n", encoding="utf-8")
            cache_dir = root / "cache"
            cache_dir.mkdir()

            with patch.object(inputs._config, "CACHE_DIR", cache_dir), patch.object(
                inputs._config, "FUNDAMENTAL_DATA_PATH", str(external)
            ):
                payload = inputs.input_fingerprints()

        self.assertIn(str(external.resolve()), payload["fundamental_data_signature"])
        self.assertNotEqual(payload["fundamental_data_signature"], "missing")


if __name__ == "__main__":
    unittest.main()
