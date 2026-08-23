from __future__ import annotations

import inspect
import logging
import os
import unittest
import warnings
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

import resonance_reporting_v90 as reporting
import resonance_runtime_v91 as runtime
import technical_resonance_v90 as resonance
import web_report as web


def _trend_frame(rows: int = 180) -> pd.DataFrame:
    index = pd.bdate_range("2025-01-02", periods=rows)
    x = np.arange(rows, dtype=float)
    close = 20.0 + 0.035 * x + 0.0008 * x * x + 0.18 * np.sin(x / 4.0)
    return pd.DataFrame(
        {
            "Open": close - 0.05,
            "High": close + 0.35,
            "Low": close - 0.35,
            "Close": close,
            "Volume": 1_000_000.0 + x * 5_000.0,
        },
        index=index,
    )


class V91VectorizedRuntimeTests(unittest.TestCase):
    def test_vectorized_resonance_emits_no_downcast_future_warning(self) -> None:
        frame = _trend_frame()
        samples = [
            {
                "ticker": "000001.SZ",
                "signal_date": value.strftime("%Y-%m-%d"),
                "entry_date": frame.index[position + 1].strftime("%Y-%m-%d"),
            }
            for position, value in enumerate(
                frame.index[-30:-1], start=len(frame) - 30
            )
        ]
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            computed = resonance.compute_five_factor_resonance(frame)
            attached = resonance.attach_resonance_to_samples(samples, frame)
        future = [item for item in caught if issubclass(item.category, FutureWarning)]
        self.assertEqual(future, [])
        self.assertEqual(len(attached), len(samples))
        self.assertGreater(computed["ResonanceCount"].notna().sum(), 0)
        self.assertTrue(all("resonance_count" in item for item in attached))

    def test_parent_recovery_fills_worker_missing_resonance(self) -> None:
        frame = _trend_frame()
        sample_dates = frame.index[-6:-1]
        samples = pd.DataFrame(
            {
                "ticker": ["000001.SZ"] * len(sample_dates),
                "signal_date": sample_dates.strftime("%Y-%m-%d"),
                "entry_signal": ["BUY_NOW"] * len(sample_dates),
            }
        )
        with mock.patch.object(runtime._core, "_load_cache", return_value=frame):
            recovered = runtime.ensure_parent_resonance(samples)
        self.assertEqual(len(recovered), len(samples))
        self.assertEqual(
            int(
                pd.to_numeric(
                    recovered["resonance_count"], errors="coerce"
                ).notna().sum()
            ),
            len(samples),
        )
        self.assertTrue(
            recovered["resonance_version"].astype(str).str.contains("v91").all()
        )

    def test_resonance_hot_paths_have_no_rowwise_dataframe_iteration(self) -> None:
        sources = "\n".join(
            [
                inspect.getsource(resonance.compute_five_factor_resonance),
                inspect.getsource(resonance.attach_resonance_to_sample_frame),
                inspect.getsource(resonance._aggregate_groups),
                inspect.getsource(runtime.ensure_parent_resonance),
            ]
        )
        self.assertNotIn(".iterrows(", sources)
        self.assertNotIn(".itertuples(", sources)
        self.assertNotIn(".apply(axis=1", sources)
        self.assertNotIn(".apply(lambda row", sources)

    def test_metric_materialization_prefers_exact_without_rowwise_parse(self) -> None:
        summary = {
            "by_ticker": [
                {
                    "ticker": "000001.SZ",
                    "entry_signal": "BUY_NOW",
                    "backtest_stage": "FAST_SCREEN",
                    "resonance_mean_count": "3.1",
                    "resonance_strong_bull_share": "0.30",
                    "resonance_rising_share": "0.40",
                },
                {
                    "ticker": "000001.SZ",
                    "entry_signal": "BUY_NOW",
                    "backtest_stage": "EXACT_REFINEMENT",
                    "resonance_mean_count": "4.2",
                    "resonance_strong_bull_share": "0.70",
                    "resonance_rising_share": "0.60",
                },
            ]
        }
        results = pd.DataFrame(
            [{"Ticker": "000001.SZ", "EntrySignal": "BUY_NOW"}]
        )
        metrics = reporting._metric_frame(summary, results)
        self.assertEqual(len(metrics), 1)
        self.assertEqual(float(metrics.iloc[0]["BacktestResonanceMeanCount"]), 4.2)

    def test_web_publish_env_is_local_and_false_disables_publish(self) -> None:
        built = web.WebReportResult(
            report_date="2026-08-23",
            index_path=Path("index.html"),
            archive_path=Path("archive.html"),
        )
        with (
            mock.patch.dict(os.environ, {web.WEB_PUBLISH_ENV: "0"}, clear=False),
            mock.patch.object(web, "build_web_report", return_value=built),
            mock.patch.object(web, "publish_site") as publish,
        ):
            result = web.build_and_publish_web_report(
                logger=logging.getLogger("test-v91"),
                reason="test",
            )
        self.assertIs(result, built)
        publish.assert_not_called()

    def test_web_generation_fault_is_fail_soft_for_daily_publication(self) -> None:
        with (
            mock.patch.object(web, "is_canonical_output_dir", return_value=True),
            mock.patch.object(
                web,
                "build_and_publish_web_report",
                side_effect=RuntimeError("page generation failed"),
            ),
        ):
            result = web.maybe_publish_canonical_report(
                Path("output"),
                logger=logging.getLogger("test-v91"),
                reason="daily-complete",
            )
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
