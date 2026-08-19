from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import fundamental_data
import fundamental_refresh_v61 as guard
import scan_service


class FundamentalRefreshIntegrityTests(unittest.TestCase):
    def test_scan_service_uses_guarded_fundamental_refresh(self) -> None:
        self.assertIs(fundamental_data.refresh_fundamental_data, guard.refresh_fundamental_data)
        self.assertIs(scan_service.refresh_fundamental_data, guard.refresh_fundamental_data)

    def test_zero_new_rows_do_not_advance_fundamental_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meta = root / "fundamental_data_meta.json"
            cache = root / "fundamental_data.csv"
            old_meta = b'{"quarter":"2026-Q2","updated":"2026-06-30"}'
            new_meta = b'{"quarter":"2026-Q3","updated":"2026-08-19"}'
            meta.write_bytes(old_meta)
            cache.write_text("Ticker,ROE\n000001.SZ,10\n", encoding="utf-8")

            def fake_refresh(*args, **kwargs):
                fundamental_data._fetch_fundamental_row("000001.SZ", None, {})
                meta.write_bytes(new_meta)
                return cache

            with patch.object(fundamental_data, "_META_PATH", meta), patch.object(
                guard, "_LEGACY_REFRESH", fake_refresh
            ), patch.object(guard, "_LEGACY_FETCH_ROW", return_value=None):
                result = guard.refresh_fundamental_data(["000001.SZ"], force=True)

            self.assertEqual(result, cache)
            self.assertEqual(meta.read_bytes(), old_meta)

    def test_real_new_row_keeps_new_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meta = root / "fundamental_data_meta.json"
            cache = root / "fundamental_data.csv"
            old_meta = b'{"quarter":"2026-Q2","updated":"2026-06-30"}'
            new_meta = b'{"quarter":"2026-Q3","updated":"2026-08-19"}'
            meta.write_bytes(old_meta)
            cache.write_text("Ticker,ROE\n000001.SZ,10\n", encoding="utf-8")

            def fake_refresh(*args, **kwargs):
                fundamental_data._fetch_fundamental_row("000001.SZ", None, {})
                meta.write_bytes(new_meta)
                return cache

            fresh_row = {
                "Ticker": "000001.SZ",
                "ROE": 12.0,
            }
            with patch.object(fundamental_data, "_META_PATH", meta), patch.object(
                guard, "_LEGACY_REFRESH", fake_refresh
            ), patch.object(guard, "_LEGACY_FETCH_ROW", return_value=fresh_row):
                result = guard.refresh_fundamental_data(["000001.SZ"], force=True)

            self.assertEqual(result, cache)
            self.assertEqual(meta.read_bytes(), new_meta)

    def test_partial_refresh_below_eighty_percent_keeps_old_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meta = root / "fundamental_data_meta.json"
            cache = root / "fundamental_data.csv"
            old_meta = b'{"quarter":"2026-Q2","updated":"2026-06-30"}'
            new_meta = b'{"quarter":"2026-Q3","updated":"2026-08-19"}'
            meta.write_bytes(old_meta)
            cache.write_text("Ticker,ROE\n000001.SZ,10\n", encoding="utf-8")
            tickers = [f"{index:06d}.SZ" for index in range(1, 11)]
            returned = iter(
                [
                    {"Ticker": tickers[index], "ROE": 12.0}
                    if index < 7
                    else None
                    for index in range(10)
                ]
            )

            def fake_refresh(*args, **kwargs):
                for ticker in tickers:
                    fundamental_data._fetch_fundamental_row(ticker, None, {})
                meta.write_bytes(new_meta)
                return cache

            with patch.object(fundamental_data, "_META_PATH", meta), patch.object(
                guard, "_LEGACY_REFRESH", fake_refresh
            ), patch.object(guard, "_LEGACY_FETCH_ROW", side_effect=lambda *a, **k: next(returned)):
                result = guard.refresh_fundamental_data(tickers, force=True)

            self.assertEqual(result, cache)
            self.assertEqual(meta.read_bytes(), old_meta)

    def test_eighty_percent_refresh_allows_new_metadata(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            meta = root / "fundamental_data_meta.json"
            cache = root / "fundamental_data.csv"
            old_meta = b'{"quarter":"2026-Q2","updated":"2026-06-30"}'
            new_meta = b'{"quarter":"2026-Q3","updated":"2026-08-19"}'
            meta.write_bytes(old_meta)
            cache.write_text("Ticker,ROE\n000001.SZ,10\n", encoding="utf-8")
            tickers = [f"{index:06d}.SZ" for index in range(1, 11)]
            returned = iter(
                [
                    {"Ticker": tickers[index], "ROE": 12.0}
                    if index < 8
                    else None
                    for index in range(10)
                ]
            )

            def fake_refresh(*args, **kwargs):
                for ticker in tickers:
                    fundamental_data._fetch_fundamental_row(ticker, None, {})
                meta.write_bytes(new_meta)
                return cache

            with patch.object(fundamental_data, "_META_PATH", meta), patch.object(
                guard, "_LEGACY_REFRESH", fake_refresh
            ), patch.object(guard, "_LEGACY_FETCH_ROW", side_effect=lambda *a, **k: next(returned)):
                result = guard.refresh_fundamental_data(tickers, force=True)

            self.assertEqual(result, cache)
            self.assertEqual(meta.read_bytes(), new_meta)


if __name__ == "__main__":
    unittest.main()
