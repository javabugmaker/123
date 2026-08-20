"""v80-compatible model-weight lookup bridge.

v79 briefly added a one-second TTL in front of score_core's calibration loader.
That saved filesystem stat calls but also hid an immediately replaced accepted
ScoreCalibration.json until the TTL expired. v80's whole-ticker FAST matrix
calls the weight loader far less often, so correctness wins here: delegate to
the stable state-aware loader, which already caches parsed weights and reloads
as soon as the calibration file mtime/size changes.
"""

from __future__ import annotations

import threading

import score_core as _score

_LEGACY_MODEL_COMPONENT_WEIGHTS = _score._model_component_weights
_LEGACY_INVALIDATE_MODEL_WEIGHT_CACHE = _score.invalidate_model_weight_cache
_LOCK = threading.RLock()
_INSTALLED = False


def model_component_weights() -> tuple[float, float, float]:
    with _LOCK:
        weights = tuple(float(value) for value in _LEGACY_MODEL_COMPONENT_WEIGHTS())
    return weights[0], weights[1], weights[2]


def invalidate_model_weight_cache() -> None:
    with _LOCK:
        _LEGACY_INVALIDATE_MODEL_WEIGHT_CACHE()


def install() -> None:
    global _INSTALLED
    _score._model_component_weights = model_component_weights
    _score.invalidate_model_weight_cache = invalidate_model_weight_cache
    _INSTALLED = True


install()
