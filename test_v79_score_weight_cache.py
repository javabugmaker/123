from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import score_core
import score_weight_cache_v79 as weight_cache


class ScoreWeightCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        weight_cache.install()

    def tearDown(self) -> None:
        weight_cache.install()

    def test_each_bridge_call_rechecks_stable_state_aware_loader(self) -> None:
        legacy = Mock(side_effect=[(0.60, 0.15, 0.25), (0.60, 0.25, 0.15)])
        with patch.object(weight_cache, "_LEGACY_MODEL_COMPONENT_WEIGHTS", legacy):
            first = weight_cache.model_component_weights()
            second = weight_cache.model_component_weights()
        self.assertEqual(first, (0.60, 0.15, 0.25))
        self.assertEqual(second, (0.60, 0.25, 0.15))
        self.assertEqual(legacy.call_count, 2)

    def test_explicit_invalidation_delegates_to_stable_cache_layer(self) -> None:
        legacy_invalidate = Mock()
        with patch.object(
            weight_cache,
            "_LEGACY_INVALIDATE_MODEL_WEIGHT_CACHE",
            legacy_invalidate,
        ):
            weight_cache.invalidate_model_weight_cache()
        legacy_invalidate.assert_called_once_with()

    def test_public_score_core_uses_state_aware_bridge(self) -> None:
        weight_cache.install()
        self.assertIs(score_core._model_component_weights, weight_cache.model_component_weights)
        self.assertIs(
            score_core.invalidate_model_weight_cache,
            weight_cache.invalidate_model_weight_cache,
        )


if __name__ == "__main__":
    unittest.main()
