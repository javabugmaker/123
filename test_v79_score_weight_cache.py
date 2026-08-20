from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import score_core
import score_weight_cache_v79 as weight_cache


class ScoreWeightCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        weight_cache._CACHED_WEIGHTS = None
        weight_cache._DEADLINE = 0.0
        weight_cache.install()

    def tearDown(self) -> None:
        weight_cache._CACHED_WEIGHTS = None
        weight_cache._DEADLINE = 0.0
        weight_cache.install()

    def test_repeated_calls_within_ttl_use_one_legacy_refresh(self) -> None:
        legacy = Mock(return_value=(0.60, 0.15, 0.25))
        with patch.object(weight_cache, "_LEGACY_MODEL_COMPONENT_WEIGHTS", legacy), patch.object(
            weight_cache.time,
            "monotonic",
            side_effect=[100.0, 100.0, 100.2, 100.3],
        ):
            first = weight_cache.model_component_weights()
            second = weight_cache.model_component_weights()
            third = weight_cache.model_component_weights()
        self.assertEqual(first, (0.60, 0.15, 0.25))
        self.assertEqual(second, first)
        self.assertEqual(third, first)
        self.assertEqual(legacy.call_count, 1)

    def test_ttl_expiry_rechecks_legacy_guarded_loader(self) -> None:
        legacy = Mock(side_effect=[(0.60, 0.15, 0.25), (0.60, 0.25, 0.15)])
        with patch.object(weight_cache, "_LEGACY_MODEL_COMPONENT_WEIGHTS", legacy), patch.object(
            weight_cache.time,
            "monotonic",
            side_effect=[10.0, 10.0, 12.0, 12.0],
        ):
            first = weight_cache.model_component_weights()
            second = weight_cache.model_component_weights()
        self.assertEqual(first, (0.60, 0.15, 0.25))
        self.assertEqual(second, (0.60, 0.25, 0.15))
        self.assertEqual(legacy.call_count, 2)

    def test_explicit_invalidation_clears_both_cache_layers(self) -> None:
        legacy_invalidate = Mock()
        weight_cache._CACHED_WEIGHTS = (0.60, 0.15, 0.25)
        weight_cache._DEADLINE = 999.0
        with patch.object(
            weight_cache,
            "_LEGACY_INVALIDATE_MODEL_WEIGHT_CACHE",
            legacy_invalidate,
        ):
            weight_cache.invalidate_model_weight_cache()
        self.assertIsNone(weight_cache._CACHED_WEIGHTS)
        self.assertEqual(weight_cache._DEADLINE, 0.0)
        legacy_invalidate.assert_called_once_with()

    def test_public_score_core_uses_v79_weight_cache(self) -> None:
        weight_cache.install()
        self.assertIs(score_core._model_component_weights, weight_cache.model_component_weights)
        self.assertIs(
            score_core.invalidate_model_weight_cache,
            weight_cache.invalidate_model_weight_cache,
        )


if __name__ == "__main__":
    unittest.main()
