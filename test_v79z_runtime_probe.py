from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import analytics  # noqa: F401
import analytics_core as core
import score_acceleration_v79 as raw
import score_core
import score_scale_migration_v95 as scale
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


class RuntimeProbeBeforeV80(unittest.TestCase):
    def test_probe_runtime_bindings(self) -> None:
        enriched = compute_all_indicators(_raw_frame())
        profile = core._resolve_backtest_profile("fast", 6000)
        historical = core._backtest_scoring_window(
            enriched,
            360,
            score_window=profile.score_window,
            include_volume_profile=False,
        )
        result = core.score_ticker(historical, is_etf=False)
        details = {
            "core_score_ticker": f"{getattr(core.score_ticker, '__module__', '')}.{getattr(core.score_ticker, '__qualname__', repr(core.score_ticker))}",
            "score_core_score_ticker": f"{getattr(score_core.score_ticker, '__module__', '')}.{getattr(score_core.score_ticker, '__qualname__', repr(score_core.score_ticker))}",
            "same_score_ticker": core.score_ticker is score_core.score_ticker,
            "trend_binding": f"{getattr(score_core.score_trend, '__module__', '')}.{getattr(score_core.score_trend, '__qualname__', repr(score_core.score_trend))}",
            "trend_is_raw_v79": score_core.score_trend is raw.score_trend,
            "volume_is_scale": score_core.score_volume is scale.score_volume,
            "accum_is_scale": score_core.score_accumulation is scale.score_accumulation,
            "structure_is_scale": score_core.score_structure is scale.score_structure,
            "profile_window": int(profile.score_window),
            "base": float(result.base_score),
            "trend": float(result.trend),
            "volume": float(result.volume),
            "accumulation": float(result.accumulation),
            "volatility": float(result.volatility),
            "structure": float(result.structure),
        }
        self.fail(repr(details))


if __name__ == "__main__":
    unittest.main()
