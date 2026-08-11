from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import config
import downloader
import performance_cache
from config import MIN_VOLUME
from filters import filter_min_volume


class V36TickFlowVolumeUnitTests(unittest.TestCase):
    @staticmethod
    def _raw_frame(*, volume: float, lot_based: bool = True, rows: int = 60) -> pd.DataFrame:
        index = pd.date_range("2026-01-01", periods=rows, freq="B")
        close = np.full(rows, 10.0)
        multiplier = 100.0 if lot_based else 1.0
        return pd.DataFrame(
            {
                "trade_date": index,
                "open": close,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "volume": np.full(rows, volume),
                "amount": close * volume * multiplier,
            }
        )

    def test_tickflow_lot_volume_is_converted_to_individual_shares(self):
        normalized = downloader._normalize_tickflow_frame(
            self._raw_frame(volume=2_500.0, lot_based=True)
        )
        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertTrue(np.allclose(normalized["Volume"].to_numpy(), 250_000.0))
        turnover_ratio = normalized["Amount"] / (
            normalized["Close"] * normalized["Volume"]
        )
        self.assertAlmostEqual(float(turnover_ratio.median()), 1.0, places=8)

    def test_already_share_based_payload_is_not_scaled_twice(self):
        normalized = downloader._normalize_tickflow_frame(
            self._raw_frame(volume=250_000.0, lot_based=False)
        )
        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertTrue(np.allclose(normalized["Volume"].to_numpy(), 250_000.0))

    def test_missing_amount_uses_cn_board_lot_fallback(self):
        raw = self._raw_frame(volume=2_500.0, lot_based=True).drop(columns=["amount"])
        normalized = downloader._normalize_tickflow_frame(raw)
        self.assertIsNotNone(normalized)
        assert normalized is not None
        self.assertTrue(np.allclose(normalized["Volume"].to_numpy(), 250_000.0))

    def test_min_volume_filter_uses_canonical_share_units(self):
        self.assertEqual(MIN_VOLUME, 200_000)
        liquid = downloader._normalize_tickflow_frame(
            self._raw_frame(volume=2_500.0, lot_based=True)
        )
        illiquid = downloader._normalize_tickflow_frame(
            self._raw_frame(volume=1_500.0, lot_based=True)
        )
        assert liquid is not None and illiquid is not None
        self.assertTrue(filter_min_volume(liquid).passed)
        self.assertFalse(filter_min_volume(illiquid).passed)

    def test_market_and_compute_caches_are_isolated_from_pre_v36_units(self):
        self.assertIn("v4-tickflow-forward-volume-shares", str(downloader._PRICE_CACHE_DIR))
        self.assertIn("volume-shares-v1", str(performance_cache.INDICATOR_CACHE_DIR))
        self.assertIn("volume-shares-v1", str(performance_cache.BACKTEST_CACHE_DIR))
        self.assertEqual(downloader.TICKFLOW_CANONICAL_VOLUME_UNIT, "shares")
        self.assertIn("v36", config.PIPELINE_VERSION)
        self.assertIn("v35", config.SCORING_VERSION)
        self.assertIn("volume-shares", config.MARKET_DATA_VERSION)


if __name__ == "__main__":
    unittest.main()
