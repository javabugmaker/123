from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import analytics_acceleration_v77 as accelerated
import analytics_core


def _result(ticker: str) -> SimpleNamespace:
    return SimpleNamespace(
        ticker=ticker,
        is_etf=False,
        name=ticker,
        industry="行业A",
        sector="行业A",
        model_classification="",
        etf_tracking_key="",
        theme_cluster="",
        industry_relative_strength=float("nan"),
        industry_momentum_60d=float("nan"),
        sector_confirmation_factor=1.0,
        failure_adjusted_score=50.0,
        final_score=50.0,
        score=SimpleNamespace(total=50.0),
        breakout_quality_factor=1.0,
        entry_signal="WAIT_PULLBACK",
        technical_institutional_score=float("nan"),
        institutional_score=float("nan"),
        quality_score=50.0,
        quality_data_available=True,
    )


class AnalyticsAccelerationTests(unittest.TestCase):
    def test_enrichment_reuses_first_pass_return_and_classification(self) -> None:
        first = _result("000001.SZ")
        second = _result("000002.SZ")
        returns = {"000001.SZ": 10.0, "000002.SZ": 20.0}
        tiny_frame = pd.DataFrame({"Close": [1.0]})

        def fake_enrich(result, *args, **kwargs):
            del args, kwargs
            return result, tiny_frame, returns[result.ticker]

        with patch.object(
            analytics_core, "_load_benchmark_frames", return_value={}
        ), patch.object(
            analytics_core, "_benchmark_regime", return_value=("震荡", "reason")
        ), patch.object(
            analytics_core,
            "_benchmark_regime_components",
            return_value=("震荡", "震荡", "震荡", 0.5, "reason"),
        ), patch.object(
            analytics_core, "_enrich_one_result", side_effect=fake_enrich
        ), patch.object(
            analytics_core, "model_classification", return_value="行业A"
        ) as classify, patch.object(
            analytics_core, "theme_cluster", return_value="主题A"
        ), patch.object(
            analytics_core,
            "_sector_confirmation_factor",
            side_effect=lambda peer, relative: 1.0,
        ), patch.object(
            analytics_core,
            "_quality_adjusted_score",
            side_effect=lambda score, *args: score,
        ), patch.object(
            analytics_core,
            "_safe_return",
            side_effect=AssertionError("second-pass return recomputation is forbidden"),
        ):
            accelerated.enrich_results([first, second], "tickflow", frames={})

        self.assertEqual(classify.call_count, 2)
        self.assertEqual(first.model_classification, "行业A")
        self.assertEqual(second.model_classification, "行业A")
        self.assertEqual(first.industry_momentum_60d, 20.0)
        self.assertEqual(first.industry_relative_strength, -10.0)
        self.assertEqual(second.industry_momentum_60d, 10.0)
        self.assertEqual(second.industry_relative_strength, 10.0)
        self.assertEqual(first.technical_institutional_score, 50.0)
        self.assertEqual(second.technical_institutional_score, 50.0)

    def test_public_core_is_patched(self) -> None:
        accelerated.install()
        self.assertIs(analytics_core.enrich_results, accelerated.enrich_results)


if __name__ == "__main__":
    unittest.main()
