from __future__ import annotations

import unittest
from unittest.mock import patch

import pandas as pd

import analytics
from evidence import enrich_evidence_fields


def _frame(day: str, close: float = 10.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [close],
            "High": [close],
            "Low": [close],
            "Close": [close],
            "Volume": [1_000_000.0],
            "Amount": [10_000_000.0],
        },
        index=pd.to_datetime([day]),
    )


class BenchmarkFreshnessTests(unittest.TestCase):
    def test_existing_cache_does_not_bypass_provider_refresh(self) -> None:
        fresh = _frame("2026-08-19", 10.2)
        with (
            patch.object(analytics, "download_ticker", return_value=fresh) as download,
            patch.object(analytics, "_load_cache") as load_cache,
        ):
            frames = analytics._load_benchmark_frames("tickflow")

        self.assertEqual(set(frames), set(analytics.BENCHMARKS))
        self.assertEqual(download.call_count, len(analytics.BENCHMARKS))
        load_cache.assert_not_called()
        for frame in frames.values():
            self.assertEqual(frame.index.max().date().isoformat(), "2026-08-19")

    def test_cache_is_only_resilient_fallback_when_refresh_fails(self) -> None:
        stale = _frame("2026-08-11", 9.8)
        with (
            patch.object(
                analytics,
                "download_ticker",
                side_effect=RuntimeError("provider unavailable"),
            ) as download,
            patch.object(analytics, "_load_cache", return_value=stale) as load_cache,
        ):
            frames = analytics._load_benchmark_frames("tickflow")

        self.assertEqual(set(frames), set(analytics.BENCHMARKS))
        self.assertEqual(download.call_count, len(analytics.BENCHMARKS))
        self.assertEqual(load_cache.call_count, len(analytics.BENCHMARKS))

    def test_ticker_sample_shortfall_is_not_described_as_all_calibration_disabled(self) -> None:
        frame = pd.DataFrame(
            {
                "Ticker": ["000001.SZ"],
                "EntrySignal": ["WAIT_PULLBACK"],
                "BacktestMode": ["EXACT"],
                "BacktestEngine": ["sequential"],
            }
        )
        summary = analytics.BacktestSummary(
            ticker_count=1,
            requested_tickers=["000001.SZ"],
            mode="exact",
            engine="sequential",
        )
        result = analytics._apply_backtest_provenance(
            frame,
            summary,
            pd.Series([1.0], index=frame.index),
        )
        reason = str(result.loc[0, "BacktestSkipReason"])
        self.assertIn("本票历史样本不足", reason)
        self.assertIn("同类全局校准", reason)
        self.assertNotEqual(reason, "历史样本不足，不参与排名")

    def test_evidence_text_distinguishes_evidence_label_from_peer_model_weight(self) -> None:
        frame = pd.DataFrame(
            {
                "BacktestSamples": [1],
                "BacktestEffectiveSamples": [1.0],
                "BacktestMode": ["EXACT"],
                "BacktestConfidenceTier": ["样本不足"],
                "GlobalCalibrationSamples": [100],
                "GlobalCalibrationEffectiveSamples": [80.0],
                "GlobalCalibrationConfidence": [0.6],
                "GlobalCalibrationLevel": ["asset_signal"],
            }
        )
        result = enrich_evidence_fields(frame)
        reason = str(result.loc[0, "EvidenceReason"])
        self.assertIn("证据等级字段本身不参与排序", reason)
        self.assertIn("同类全局校准", reason)
        self.assertIn("综合分", reason)


if __name__ == "__main__":
    unittest.main()
