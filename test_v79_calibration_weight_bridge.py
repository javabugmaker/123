from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import analytics
import analytics_core
import calibration_weight_cache_v79 as bridge


class CalibrationWeightBridgeTests(unittest.TestCase):
    def tearDown(self) -> None:
        bridge.install()

    def test_success_invalidates_weight_cache_after_backtest(self) -> None:
        summary = object()
        legacy = Mock(return_value=summary)
        invalidate = Mock()
        with patch.object(bridge, "_LEGACY_RUN_HISTORICAL_BACKTEST", legacy), patch.object(
            bridge._score,
            "invalidate_model_weight_cache",
            invalidate,
        ):
            result = bridge.run_historical_backtest(["000001.SZ"])
        self.assertIs(result, summary)
        legacy.assert_called_once_with(["000001.SZ"])
        invalidate.assert_called_once_with()

    def test_failure_still_invalidates_weight_cache(self) -> None:
        legacy = Mock(side_effect=RuntimeError("boom"))
        invalidate = Mock()
        with patch.object(bridge, "_LEGACY_RUN_HISTORICAL_BACKTEST", legacy), patch.object(
            bridge._score,
            "invalidate_model_weight_cache",
            invalidate,
        ):
            with self.assertRaisesRegex(RuntimeError, "boom"):
                bridge.run_historical_backtest(["000001.SZ"])
        invalidate.assert_called_once_with()

    def test_public_analytics_runtime_uses_bridge(self) -> None:
        bridge.install()
        self.assertIs(analytics_core.run_historical_backtest, bridge.run_historical_backtest)
        self.assertIs(analytics.run_historical_backtest, bridge.run_historical_backtest)


if __name__ == "__main__":
    unittest.main()
