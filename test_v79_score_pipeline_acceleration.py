from __future__ import annotations

import unittest
from unittest.mock import Mock

import numpy as np
import pandas as pd

import analytics
import score
import score_acceleration_v79 as accelerated
import score_core
import score_runtime_v97 as runtime
import score_scale_migration_v95 as scale
from indicators import compute_all_indicators


def _raw_frame(rows: int = 700) -> pd.DataFrame:
    rng = np.random.default_rng(20260820)
    returns = rng.normal(0.00035, 0.015, rows)
    close = 24.0 * np.cumprod(1.0 + returns)
    open_ = close * (1.0 + rng.normal(0.0, 0.004, rows))
    high = np.maximum(open_, close) * (1.0 + rng.uniform(0.002, 0.02, rows))
    low = np.minimum(open_, close) * (1.0 - rng.uniform(0.002, 0.02, rows))
    volume = rng.integers(700_000, 12_000_000, rows).astype(float)
    return pd.DataFrame(
        {
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
            "Amount": volume * close,
        },
        index=pd.bdate_range("2023-10-02", periods=rows),
    )


def _enriched_frame(*, with_holes: bool = False) -> pd.DataFrame:
    frame = compute_all_indicators(_raw_frame())
    if with_holes:
        # Exercise the stable dropna chronology without corrupting the endpoint.
        for column, positions in {
            "Close": (301, 412),
            "Volume": (322,),
            "OBV": (355,),
            "CMF": (377,),
            "AD": (388,),
            "MA200": (405,),
            "BB_Width": (433,),
        }.items():
            if column in frame.columns:
                frame.iloc[list(positions), frame.columns.get_loc(column)] = np.nan
        if "HV20" in frame.columns:
            frame.iloc[444, frame.columns.get_loc("HV20")] = np.inf
    return frame


def _legacy_trigger_event_score(df: pd.DataFrame) -> float:
    close = accelerated._LEGACY_SERIES(df, "Close")
    high = accelerated._LEGACY_SERIES(df, "High")
    volume = accelerated._LEGACY_SERIES(df, "Volume")
    valid = pd.concat(
        {"close": close, "high": high, "volume": volume}, axis=1
    ).dropna()
    if len(valid) < 21:
        return 0.0

    price = float(valid["close"].iloc[-1])
    resistance = float(valid["high"].iloc[-21:-1].max())
    volume_now = float(valid["volume"].iloc[-1])
    volume_baseline = float(valid["volume"].iloc[-21:-1].mean())
    points = 0.0

    if resistance > 0.0:
        clearance_pct = (price / resistance - 1.0) * 100.0
        if clearance_pct > 0.0:
            points += 35.0 + score_core._clamp(clearance_pct / 3.0) * 15.0
        elif clearance_pct >= -1.5:
            points += score_core._clamp((clearance_pct + 1.5) / 1.5) * 12.0

    if volume_baseline > 0.0:
        volume_ratio = volume_now / volume_baseline
        points += score_core._clamp((volume_ratio - 1.0) / 1.25) * 25.0

    cmf = accelerated._LEGACY_SERIES(df, "CMF").dropna()
    if len(cmf) >= 6:
        cmf_delta = float(cmf.iloc[-1] - cmf.iloc[-6])
        points += score_core._clamp(cmf_delta / 0.12) * 10.0

    ad_slope = accelerated._LEGACY_SERIES(df, "AD_Slope").dropna()
    if len(ad_slope) >= 6:
        current_ad = float(ad_slope.iloc[-1])
        prior_ad = float(ad_slope.iloc[-6:-1].median())
        if current_ad > 0.0 and prior_ad <= 0.0:
            points += 8.0
        elif current_ad > 0.0 and current_ad > prior_ad:
            points += 4.0

    obv = accelerated._LEGACY_SERIES(df, "OBV").dropna()
    if len(obv) >= 11:
        recent_change = float(obv.iloc[-1] - obv.iloc[-6])
        prior_change = float(obv.iloc[-6] - obv.iloc[-11])
        if recent_change > 0.0 and recent_change > max(prior_change, 0.0):
            points += 7.0

    return score_core._clamp(points, 0.0, 100.0)


class ScoreKernelEquivalenceTests(unittest.TestCase):
    def setUp(self) -> None:
        accelerated.clear_thread_score_cache()
        runtime.install()

    def tearDown(self) -> None:
        accelerated.clear_thread_score_cache()
        runtime.install()

    def _assert_components_equal(self, frame: pd.DataFrame) -> None:
        # These pairs intentionally test the raw v79 implementation kernel. The
        # public v95+ runtime wraps Volume/Accumulation with nominal scaling.
        pairs = (
            (accelerated._LEGACY_SCORE_TREND, accelerated.score_trend),
            (accelerated._LEGACY_SCORE_VOLUME, accelerated.score_volume),
            (accelerated._LEGACY_SCORE_ACCUMULATION, accelerated.score_accumulation),
            (accelerated._LEGACY_SCORE_STRUCTURE, accelerated.score_structure),
        )
        for legacy, current in pairs:
            accelerated.clear_thread_score_cache()
            expected = legacy(frame)
            accelerated.clear_thread_score_cache()
            actual = current(frame)
            self.assertAlmostEqual(actual, expected, places=10)

    def test_component_kernels_match_stable_formulas(self) -> None:
        self._assert_components_equal(_enriched_frame())

    def test_component_kernels_match_with_internal_nan_inf_holes(self) -> None:
        self._assert_components_equal(_enriched_frame(with_holes=True))

    def test_trigger_event_score_matches_pre_v79_formula(self) -> None:
        for frame in (_enriched_frame(), _enriched_frame(with_holes=True)):
            accelerated.clear_thread_score_cache()
            expected = _legacy_trigger_event_score(frame)
            accelerated.clear_thread_score_cache()
            actual = score.trigger_event_score(frame)
            self.assertAlmostEqual(actual, expected, places=10)

    def test_volatility_state_matches_stable_implementation(self) -> None:
        import volatility_state

        for frame in (_enriched_frame(), _enriched_frame(with_holes=True)):
            expected = accelerated._LEGACY_VOLATILITY_STATE(frame)
            accelerated.clear_thread_score_cache()
            actual = accelerated.evaluate_volatility_contraction(frame)
            for field in ("atr_ratio", "bb_ratio", "bb_percentile", "hv_ratio"):
                expected_value = float(getattr(expected, field))
                actual_value = float(getattr(actual, field))
                if np.isnan(expected_value):
                    self.assertTrue(np.isnan(actual_value), msg=field)
                else:
                    self.assertAlmostEqual(actual_value, expected_value, places=12, msg=field)
            for field in (
                "atr_contracting",
                "bb_contracting",
                "hv_contracting",
                "available_components",
            ):
                self.assertEqual(getattr(actual, field), getattr(expected, field), msg=field)

    def test_whole_score_breakdown_survives_raw_kernel_reinstallation(self) -> None:
        frame = _enriched_frame()
        runtime.install()
        accelerated.clear_thread_score_cache()
        expected = score.score_ticker(frame.copy(), is_etf=False)

        # Simulate an older facade re-installing its raw kernels, then restore
        # the single canonical composition exactly as production does.
        accelerated.install()
        runtime.install()
        accelerated.clear_thread_score_cache()
        actual = score.score_ticker(frame.copy(), is_etf=False)
        fields = (
            "total",
            "trend",
            "volume",
            "accumulation",
            "volatility",
            "structure",
            "indicator_coverage",
            "base_score",
            "breakout_score",
            "entry_score",
            "execution_score",
            "value_trap_risk",
            "trigger_score",
            "final_score",
            "entry_zone_low",
            "entry_zone_high",
            "breakout_buy_price",
            "stop_loss",
        )
        for field in fields:
            expected_value = float(getattr(expected, field))
            actual_value = float(getattr(actual, field))
            if np.isnan(expected_value):
                self.assertTrue(np.isnan(actual_value), msg=field)
            else:
                self.assertAlmostEqual(actual_value, expected_value, places=9, msg=field)
        self.assertEqual(actual.missing_indicators, expected.missing_indicators)


class ScoreCacheBehaviorTests(unittest.TestCase):
    def setUp(self) -> None:
        accelerated.clear_thread_score_cache()
        runtime.install()

    def tearDown(self) -> None:
        accelerated.clear_thread_score_cache()
        runtime.install()

    def test_numeric_series_is_reused_for_same_frame(self) -> None:
        frame = _enriched_frame()
        first = accelerated._series(frame, "Close")
        second = accelerated._series(frame, "Close")
        self.assertIs(first, second)
        copied = frame.copy()
        third = accelerated._series(copied, "Close")
        self.assertIsNot(first, third)

    def test_style_is_computed_once_per_frame_and_asset_type(self) -> None:
        frame = _enriched_frame()
        legacy = Mock(return_value="均衡")
        with unittest.mock.patch.object(accelerated, "_LEGACY_CLASSIFY_STYLE", legacy):
            self.assertEqual(accelerated.classify_style(frame, False), "均衡")
            self.assertEqual(accelerated.classify_style(frame, False), "均衡")
            self.assertEqual(accelerated.classify_style(frame, True), "均衡")
        self.assertEqual(legacy.call_count, 2)

    def test_entry_point_is_computed_once_for_identical_endpoint_inputs(self) -> None:
        frame = _enriched_frame()
        legacy = Mock(
            return_value={
                "score": 55.0,
                "signal": "WAIT_PULLBACK",
                "low": 10.0,
                "high": 11.0,
                "breakout": 12.0,
                "stop": 9.0,
            }
        )
        with unittest.mock.patch.object(accelerated, "_LEGACY_ENTRY_POINT", legacy):
            first = accelerated.entry_point(
                frame,
                breakout=66.0,
                volume_score=12.0,
                value_trap_risk_value=30.0,
                price_decimals=2,
            )
            second = accelerated.entry_point(
                frame,
                breakout=66.0,
                volume_score=99.0,
                value_trap_risk_value=30.0,
                price_decimals=2,
            )
        self.assertEqual(first, second)
        self.assertEqual(legacy.call_count, 1)

    def test_volatility_state_is_cached_for_filter_and_score_consumers(self) -> None:
        frame = _enriched_frame()
        first = accelerated.evaluate_volatility_contraction(frame)
        second = accelerated.evaluate_volatility_contraction(frame)
        self.assertIs(first, second)

    def test_v79_kernel_is_inner_layer_of_canonical_runtime(self) -> None:
        accelerated.install()
        runtime.install()
        self.assertIs(score_core._series, accelerated._series)
        self.assertIs(score_core.score_volume, scale.score_volume)
        self.assertIs(score_core.score_accumulation, scale.score_accumulation)
        self.assertIs(scale._ORIGINAL_SCORE_VOLUME, accelerated.score_volume)
        self.assertIs(
            scale._ORIGINAL_SCORE_ACCUMULATION,
            accelerated.score_accumulation,
        )
        self.assertIs(score_core.entry_point, accelerated.entry_point)
        self.assertEqual(
            analytics.PERFORMANCE_ENGINE_VERSION,
            "2026-08-20-v80-vectorized-backtest-workstation-v1",
        )


if __name__ == "__main__":
    unittest.main()
