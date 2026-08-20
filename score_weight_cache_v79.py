"""v79 model-weight lookup acceleration.

score_core already caches accepted component weights, but it still stats
ScoreCalibration.json on every score call to validate the cache state.  A full
scan/backtest can therefore issue thousands of redundant filesystem metadata
queries.  Bound that refresh to a short worker-local TTL while preserving the
legacy parser, guard rails and explicit invalidation behavior.
"""

from __future__ import annotations

import threading
import time

import score_core as _score

_LEGACY_MODEL_COMPONENT_WEIGHTS = _score._model_component_weights
_LEGACY_INVALIDATE_MODEL_WEIGHT_CACHE = _score.invalidate_model_weight_cache
_TTL_SECONDS = 1.0
_LOCK = threading.RLock()
_CACHED_WEIGHTS: tuple[float, float, float] | None = None
_DEADLINE = 0.0
_INSTALLED = False


def model_component_weights() -> tuple[float, float, float]:
    global _CACHED_WEIGHTS, _DEADLINE
    now = time.monotonic()
    cached = _CACHED_WEIGHTS
    if cached is not None and now < _DEADLINE:
        return cached
    with _LOCK:
        now = time.monotonic()
        if _CACHED_WEIGHTS is not None and now < _DEADLINE:
            return _CACHED_WEIGHTS
        weights = tuple(float(value) for value in _LEGACY_MODEL_COMPONENT_WEIGHTS())
        _CACHED_WEIGHTS = (weights[0], weights[1], weights[2])
        _DEADLINE = now + _TTL_SECONDS
        return _CACHED_WEIGHTS


def invalidate_model_weight_cache() -> None:
    global _CACHED_WEIGHTS, _DEADLINE
    with _LOCK:
        _LEGACY_INVALIDATE_MODEL_WEIGHT_CACHE()
        _CACHED_WEIGHTS = None
        _DEADLINE = 0.0


def install() -> None:
    global _INSTALLED
    _score._model_component_weights = model_component_weights
    _score.invalidate_model_weight_cache = invalidate_model_weight_cache
    _INSTALLED = True


install()
