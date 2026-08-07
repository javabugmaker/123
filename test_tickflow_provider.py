
from __future__ import annotations

from unittest import TestCase
from unittest.mock import Mock, patch

import pandas as pd

import downloader


class TickFlowProviderTests(TestCase):
    def test_legacy_market_source_names_normalize_to_tickflow(self):
        for source in ("tickflow", "auto", "akshare", "eastmoney", "sina", "tencent"):
            self.assertEqual(downloader.normalize_data_source(source), "tickflow")

    def test_tickflow_frame_normalizes_to_project_ohlcv(self):
        frame = pd.DataFrame({
            "trade_date": ["2026-08-06", "2026-08-07"],
            "open": [10.0, 10.2],
            "high": [10.5, 10.4],
            "low": [9.9, 10.0],
            "close": [10.2, 10.3],
            "volume": [1000, 1200],
            "amount": [10200, 12360],
        })
        result = downloader._normalize_tickflow_frame(frame)
        self.assertEqual(
            result.columns.tolist(),
            ["Open", "High", "Low", "Close", "Volume", "Amount"],
        )
        self.assertEqual(str(result.index[-1].date()), "2026-08-07")

    def test_batch_fetch_uses_forward_adjusted_daily_klines(self):
        client = Mock()
        client.klines.batch.return_value = {
            "600000.SH": pd.DataFrame({
                "trade_date": ["2026-08-07"],
                "open": [10.0],
                "high": [10.2],
                "low": [9.9],
                "close": [10.1],
                "volume": [1000],
                "amount": [10100],
            })
        }
        with patch.object(downloader, "_tickflow", return_value=client):
            result = downloader._batch_fetch(["600000.SH"])
        self.assertIn("600000.SH", result)
        kwargs = client.klines.batch.call_args.kwargs
        self.assertEqual(kwargs["period"], "1d")
        self.assertEqual(kwargs["adjust"], "forward")
        self.assertLessEqual(kwargs["max_workers"], 10)

    def test_download_batch_reuses_fresh_cache_and_batches_missing_symbols(self):
        fresh = pd.DataFrame(
            {
                "Open": [10.0],
                "High": [10.2],
                "Low": [9.9],
                "Close": [10.1],
                "Volume": [1000],
            },
            index=pd.to_datetime(["2026-08-07"]),
        )
        fetched = pd.DataFrame(
            {
                "Open": [20.0],
                "High": [20.2],
                "Low": [19.9],
                "Close": [20.1],
                "Volume": [2000],
            },
            index=pd.to_datetime(["2026-08-07"]),
        )
        tickers = [
            downloader.TickerInfo("600000.SH"),
            downloader.TickerInfo("000001.SZ"),
        ]
        with (
            patch.object(
                downloader,
                "_load_cache",
                side_effect=lambda t, source=None: fresh if t == "600000.SH" else None,
            ),
            patch.object(
                downloader, "_cache_has_completed_daily_bar", return_value=True
            ),
            patch.object(
                downloader,
                "_batch_fetch",
                return_value={"000001.SZ": fetched},
            ) as batch,
            patch.object(downloader, "_save_cache"),
        ):
            result = downloader.download_batch(tickers)

        self.assertEqual(set(result), {"600000.SH", "000001.SZ"})
        batch.assert_called_once_with(["000001.SZ"])
