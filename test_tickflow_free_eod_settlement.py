from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

import downloader


def _frame(day: str, close: float = 10.0) -> pd.DataFrame:
    index = pd.to_datetime([day])
    return pd.DataFrame(
        {
            "Open": [close - 0.1],
            "High": [close + 0.2],
            "Low": [close - 0.2],
            "Close": [close],
            "Volume": [1_000_000.0],
            "Amount": [10_000_000.0],
        },
        index=index,
    )


class TickFlowFreeEODSettlementTests(unittest.TestCase):
    def setUp(self) -> None:
        downloader.close_tickflow_client()

    def tearDown(self) -> None:
        downloader.close_tickflow_client()

    def test_downloader_client_is_free_only(self) -> None:
        sentinel = object()

        class FakeTickFlow:
            calls = 0

            @classmethod
            def free(cls):
                cls.calls += 1
                return sentinel

        with patch.object(downloader, "TickFlow", FakeTickFlow):
            downloader._TICKFLOW_CLIENT = None
            client = downloader._tickflow()

        self.assertIs(client, sentinel)
        self.assertEqual(FakeTickFlow.calls, 1)

    def test_post_close_retry_repairs_stale_free_daily_bar(self) -> None:
        stale = _frame("2026-08-18", 10.0)
        fresh = _frame("2026-08-19", 10.2)

        with (
            patch.object(downloader, "_is_a_share_market_closed", return_value=True),
            patch.object(
                downloader,
                "_latest_completed_trading_day",
                return_value=date(2026, 8, 19),
            ),
            patch.object(
                downloader,
                "_batch_fetch",
                return_value={"000001.SZ": fresh},
            ) as batch_fetch,
            patch.object(downloader, "_requires_full_rebase", return_value=False),
            patch.object(downloader, "_save_cache") as save_cache,
            patch.object(downloader, "_record_market_manifest"),
            patch.object(downloader, "_flush_market_manifest"),
            patch.object(downloader, "close_tickflow_client") as close_client,
        ):
            result = downloader._refresh_free_eod_frames(
                {"000001.SZ": stale}, attempts=1, pause_seconds=0.0
            )

        repaired = result["000001.SZ"]
        self.assertEqual(repaired.index.max().date(), date(2026, 8, 19))
        self.assertAlmostEqual(float(repaired["Close"].iloc[-1]), 10.2)
        self.assertTrue(repaired.attrs.get("free_eod_settlement_retry"))
        batch_fetch.assert_called_once()
        close_client.assert_called_once()
        save_cache.assert_called_once()

    def test_retry_waits_for_provider_date_instead_of_fabricating_bar(self) -> None:
        stale = _frame("2026-08-18", 10.0)
        still_stale = _frame("2026-08-18", 10.05)
        fresh = _frame("2026-08-19", 10.2)

        with (
            patch.object(downloader, "_is_a_share_market_closed", return_value=True),
            patch.object(
                downloader,
                "_latest_completed_trading_day",
                return_value=date(2026, 8, 19),
            ),
            patch.object(
                downloader,
                "_batch_fetch",
                side_effect=[
                    {"000001.SZ": still_stale},
                    {"000001.SZ": fresh},
                ],
            ) as batch_fetch,
            patch.object(downloader, "_requires_full_rebase", return_value=False),
            patch.object(downloader, "_save_cache"),
            patch.object(downloader, "_record_market_manifest"),
            patch.object(downloader, "_flush_market_manifest"),
            patch.object(downloader, "close_tickflow_client") as close_client,
        ):
            result = downloader._refresh_free_eod_frames(
                {"000001.SZ": stale}, attempts=2, pause_seconds=0.0
            )

        self.assertEqual(result["000001.SZ"].index.max().date(), date(2026, 8, 19))
        self.assertEqual(batch_fetch.call_count, 2)
        self.assertEqual(close_client.call_count, 2)

    def test_unsettled_free_bar_remains_old_and_is_not_synthesized(self) -> None:
        stale = _frame("2026-08-18", 10.0)

        with (
            patch.object(downloader, "_is_a_share_market_closed", return_value=True),
            patch.object(
                downloader,
                "_latest_completed_trading_day",
                return_value=date(2026, 8, 19),
            ),
            patch.object(
                downloader,
                "_batch_fetch",
                return_value={"000001.SZ": _frame("2026-08-18", 10.05)},
            ),
            patch.object(downloader, "_save_cache") as save_cache,
            patch.object(downloader, "_record_market_manifest"),
            patch.object(downloader, "_flush_market_manifest"),
            patch.object(downloader, "close_tickflow_client"),
        ):
            result = downloader._refresh_free_eod_frames(
                {"000001.SZ": stale}, attempts=2, pause_seconds=0.0
            )

        self.assertEqual(result["000001.SZ"].index.max().date(), date(2026, 8, 18))
        self.assertEqual(len(result["000001.SZ"]), 1)
        save_cache.assert_not_called()

    def test_mixed_73_percent_today_distribution_fails_fast(self) -> None:
        frames: dict[str, pd.DataFrame] = {}
        for index in range(73):
            frames[f"{index:06d}.SZ"] = _frame("2026-08-19")
        for index in range(73, 100):
            frames[f"{index:06d}.SZ"] = _frame("2026-08-18")

        with (
            patch.object(downloader, "_is_a_share_market_closed", return_value=True),
            patch.object(
                downloader,
                "_latest_completed_trading_day",
                return_value=date(2026, 8, 19),
            ),
        ):
            with self.assertRaises(downloader.DownloadError) as caught:
                downloader._assert_free_eod_coherence(frames)

        message = str(caught.exception)
        self.assertIn("73.0%", message)
        self.assertIn("混合结算", message)

    def test_coherent_previous_day_provider_lag_remains_allowed(self) -> None:
        frames: dict[str, pd.DataFrame] = {}
        for index in range(95):
            frames[f"{index:06d}.SZ"] = _frame("2026-08-18")
        for index in range(95, 100):
            frames[f"{index:06d}.SZ"] = _frame("2026-08-19")

        with (
            patch.object(downloader, "_is_a_share_market_closed", return_value=True),
            patch.object(
                downloader,
                "_latest_completed_trading_day",
                return_value=date(2026, 8, 19),
            ),
        ):
            downloader._assert_free_eod_coherence(frames)

    def test_cache_first_never_triggers_post_close_retry(self) -> None:
        stale = _frame("2026-08-18", 10.0)
        with (
            patch.object(
                downloader,
                "_FREE_EOD_LEGACY_DOWNLOAD_BATCH",
                return_value={"000001.SZ": stale},
            ),
            patch.object(downloader, "_refresh_free_eod_frames") as retry,
            patch.object(downloader, "_assert_free_eod_coherence") as coherence,
        ):
            result = downloader.download_batch([], cache_first=True)

        self.assertIn("000001.SZ", result)
        retry.assert_not_called()
        coherence.assert_not_called()


if __name__ == "__main__":
    unittest.main()
