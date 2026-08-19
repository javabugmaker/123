from __future__ import annotations

import os
import unittest
from datetime import date
from unittest.mock import Mock, patch

import pandas as pd

import downloader


class _FakeTickFlow:
    free_calls = 0
    api_keys: list[str | None] = []

    def __init__(self, api_key: str | None = None) -> None:
        type(self).api_keys.append(api_key)
        self.api_key = api_key

    @classmethod
    def free(cls):
        cls.free_calls += 1
        return cls(api_key=None)

    def close(self) -> None:
        return None


class V55TickFlowEodCloseTests(unittest.TestCase):
    def setUp(self) -> None:
        _FakeTickFlow.free_calls = 0
        _FakeTickFlow.api_keys = []
        downloader._TICKFLOW_CLIENT = None
        downloader._TICKFLOW_CLIENT_MODE = None

    def tearDown(self) -> None:
        downloader._TICKFLOW_CLIENT = None
        downloader._TICKFLOW_CLIENT_MODE = None

    def test_api_key_switches_client_from_free_to_authenticated(self) -> None:
        secret = "unit-test-secret"
        with patch.dict(os.environ, {"TICKFLOW_API_KEY": secret}, clear=False), patch.object(
            downloader, "TickFlow", _FakeTickFlow
        ):
            client = downloader._tickflow()

        self.assertEqual(client.api_key, secret)
        self.assertEqual(_FakeTickFlow.api_keys, [secret])
        self.assertEqual(_FakeTickFlow.free_calls, 0)
        self.assertEqual(downloader._TICKFLOW_CLIENT_MODE, "authenticated")

    def test_without_api_key_keeps_zero_configuration_free_client(self) -> None:
        environment = dict(os.environ)
        environment.pop("TICKFLOW_API_KEY", None)
        with patch.dict(os.environ, environment, clear=True), patch.object(
            downloader, "TickFlow", _FakeTickFlow
        ):
            client = downloader._tickflow()

        self.assertIsNone(client.api_key)
        self.assertEqual(_FakeTickFlow.free_calls, 1)
        self.assertEqual(downloader._TICKFLOW_CLIENT_MODE, "free")

    def test_post_close_quote_becomes_completed_daily_bar(self) -> None:
        timestamp_ms = int(
            pd.Timestamp("2026-08-19 15:00:05", tz="Asia/Shanghai").timestamp()
            * 1000
        )
        quote_frame = pd.DataFrame(
            {
                "symbol": ["600000.SH"],
                "timestamp": [timestamp_ms],
                "open": [10.00],
                "high": [10.20],
                "low": [9.90],
                "last_price": [10.10],
                "prev_close": [9.90],
                # Quote volume may be board lots.  Amount lets the existing v51
                # normalizer infer and convert it to individual shares.
                "volume": [1000.0],
                "amount": [1_010_000.0],
            }
        )
        client = Mock()
        client.quotes.get.return_value = quote_frame

        with (
            patch.dict(os.environ, {"TICKFLOW_API_KEY": "unit-test-secret"}, clear=False),
            patch.object(downloader, "_tickflow", return_value=client),
            patch.object(downloader, "_is_a_share_market_closed", return_value=True),
            patch.object(
                downloader,
                "_latest_completed_trading_day",
                return_value=date(2026, 8, 19),
            ),
        ):
            bars = downloader._fetch_eod_quote_bars(["600000.SH"])

        self.assertIn("600000.SH", bars)
        bar = bars["600000.SH"]
        self.assertEqual(str(bar.index[-1].date()), "2026-08-19")
        self.assertAlmostEqual(float(bar["Close"].iloc[-1]), 10.10)
        self.assertAlmostEqual(float(bar["Volume"].iloc[-1]), 100_000.0)
        self.assertTrue(bar.attrs["eod_quote_fallback"])
        self.assertEqual(bar.attrs["eod_quote_source"], "tickflow_quotes")

    def test_quote_fallback_repairs_one_day_stale_history(self) -> None:
        timestamp_ms = int(
            pd.Timestamp("2026-08-19 15:00:05", tz="Asia/Shanghai").timestamp()
            * 1000
        )
        client = Mock()
        client.quotes.get.return_value = pd.DataFrame(
            {
                "symbol": ["600000.SH"],
                "timestamp": [timestamp_ms],
                "open": [10.00],
                "high": [10.20],
                "low": [9.80],
                "last_price": [10.10],
                "prev_close": [9.90],
                "volume": [1000.0],
                "amount": [1_010_000.0],
            }
        )
        history = pd.DataFrame(
            {
                "Open": [9.80],
                "High": [10.00],
                "Low": [9.70],
                "Close": [9.90],
                "Volume": [90_000.0],
                "Amount": [891_000.0],
            },
            index=pd.to_datetime(["2026-08-18"]),
        )

        def completed(frame, now=None):
            del now
            return bool(pd.Timestamp(frame.index.max()).date() >= date(2026, 8, 19))

        with (
            patch.dict(os.environ, {"TICKFLOW_API_KEY": "unit-test-secret"}, clear=False),
            patch.object(downloader, "_tickflow", return_value=client),
            patch.object(downloader, "_is_a_share_market_closed", return_value=True),
            patch.object(
                downloader,
                "_latest_completed_trading_day",
                return_value=date(2026, 8, 19),
            ),
            patch.object(
                downloader, "_cache_has_completed_daily_bar", side_effect=completed
            ),
        ):
            augmented, changed = downloader._augment_with_eod_quotes(
                {"600000.SH": history}
            )

        self.assertEqual(changed, {"600000.SH"})
        self.assertEqual(str(augmented["600000.SH"].index[-1].date()), "2026-08-19")
        self.assertAlmostEqual(float(augmented["600000.SH"]["Close"].iloc[-1]), 10.10)

    def test_corporate_action_mismatch_fails_safe(self) -> None:
        history = pd.DataFrame(
            {"Close": [10.0]}, index=pd.to_datetime(["2026-08-18"])
        )
        quote = pd.DataFrame(
            {"Close": [8.0]}, index=pd.to_datetime(["2026-08-19"])
        )
        quote.attrs["eod_quote_prev_close"] = 8.0
        self.assertFalse(downloader._quote_history_is_compatible(history, quote))


if __name__ == "__main__":
    unittest.main()
