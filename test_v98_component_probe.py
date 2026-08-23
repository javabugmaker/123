from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import analytics  # noqa: F401
import analytics_core as core
import backtest_vectorization_v98 as v98
from indicators import compute_all_indicators


def _raw_frame(rows: int = 900) -> pd.DataFrame:
    rng = np.random.default_rng(802026)
    returns = rng.normal(0.00025, 0.013, rows)
    close = 18.0 * np.cumprod(1.0 + returns)
    open_price = close * (1.0 + rng.normal(0.0, 0.0035, rows))
    high = np.maximum(open_price, close) * (1.0 + rng.uniform(0.002, 0.018, rows))
    low = np.minimum(open_price, close) * (1.0 - rng.uniform(0.002, 0.018, rows))
    volume = rng.integers(900_000, 15_000_000, rows).astype(float)
    return pd.DataFrame(
        {
            "Open": open_price,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
            "Amount": volume * close,
        },
        index=pd.bdate_range("2018-01-02", periods=rows),
    )


class V98ComponentProbe(unittest.TestCase):
    def test_probe_scalar_vs_dense_setup(self) -> None:
        enriched = compute_all_indicators(_raw_frame())
        index = 360
        profile = core._resolve_backtest_profile("fast", 6000)
        historical = core._backtest_scoring_window(
            enriched,
            index,
            score_window=profile.score_window,
            include_volume_profile=False,
        )
        scalar = core.score_ticker(historical, is_etf=False)
        original = v98._ORIGINAL_FAST_SCORE_MATRIX(enriched, is_etf=False)
        assert original is not None
        old_trend = float(v98._dense_trend_score(enriched, peak_window=252)[index])
        new_trend = float(v98._dense_trend_score(enriched, peak_window=504)[index])
        details = {
            "profile_window": int(profile.score_window),
            "historical_len": len(historical),
            "scalar_base": float(scalar.base_score),
            "scalar_total": float(scalar.total),
            "scalar_trend": float(scalar.trend),
            "scalar_volume": float(scalar.volume),
            "scalar_accumulation": float(scalar.accumulation),
            "scalar_volatility": float(scalar.volatility),
            "scalar_structure": float(scalar.structure),
            "scalar_coverage": float(scalar.indicator_coverage),
            "original_fast_base": float(original.base_score[index]),
            "dense_trend_252": old_trend,
            "dense_trend_504": new_trend,
            "trend_delta": new_trend - old_trend,
        }
        self.fail(repr(details))


if __name__ == "__main__":
    unittest.main()
