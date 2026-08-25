"""Repair point-in-time held-out split counts without relaxing PIT governance.

The core backtest always records raw split sizes in ``rolling_oos`` and
``rolling_oos_stats``.  The v106 PIT overlay also tries to capture raw/verified
counts while the verified frame is built, but acceleration/wrapper composition
can make that transient hook unavailable.  In that case a real raw test
partition must not be reported as 0/0.

This module restores only count provenance.  Verified samples are never
inferred from raw samples, so a missing PIT verification count remains zero and
calibration stays fail-closed.
"""
from __future__ import annotations

from typing import Any, Final

from . import point_in_time_backtest as _pit

PIT_COUNT_REPAIR_VERSION: Final = (
    "2026-08-25-v106.5-pit-raw-split-count-fallback-v1"
)
_MODEL_SPLITS = ("train", "validation", "test")
_INSTALLED = False
_ORIGINAL_APPLY: Any = None


def _integer(value: object) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _summary_value(summary: Any, name: str, default: object = None) -> object:
    if isinstance(summary, dict):
        return summary.get(name, default)
    return getattr(summary, name, default)


def _raw_split_from_summary(summary: Any, split: str) -> int:
    rolling = _mapping(_summary_value(summary, "rolling_oos", {}))
    raw = _integer(rolling.get(split))
    if raw > 0:
        return raw
    stats = _mapping(_summary_value(summary, "rolling_oos_stats", {}))
    bucket = _mapping(stats.get(split, {}))
    return _integer(bucket.get("samples"))


def _verified_split_from_summary(summary: Any, split: str) -> int:
    stats = _mapping(_summary_value(summary, "rolling_oos_stats", {}))
    bucket = _mapping(stats.get(split, {}))
    return _integer(bucket.get("point_in_time_verified_samples"))


def normalize_runtime_counts(
    summary: Any,
    counts: dict[str, dict[str, int]] | None,
) -> dict[str, dict[str, int]]:
    """Fill missing raw counts from durable core split provenance.

    Raw counts may be recovered from ``rolling_oos`` because those are direct
    partition sizes. Verified counts are recovered only from an explicit PIT
    field; they are never guessed from the raw population.
    """
    source = counts if isinstance(counts, dict) else {}
    normalized: dict[str, dict[str, int]] = {}
    for split in _MODEL_SPLITS:
        bucket = _mapping(source.get(split, {}))
        raw = _integer(bucket.get("raw"))
        verified = _integer(bucket.get("verified"))
        if raw <= 0:
            raw = _raw_split_from_summary(summary, split)
        if verified <= 0:
            verified = _verified_split_from_summary(summary, split)
        raw = max(raw, verified)
        normalized[split] = {
            "raw": raw,
            "verified": verified,
            "unverified": max(0, raw - verified),
        }
    return normalized


def repair_summary_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a page-safe BacktestSummary copy with truthful PIT test counts."""
    if not isinstance(payload, dict) or not payload:
        return payload
    repaired = dict(payload)
    explicit = {
        "test": {
            "raw": _integer(repaired.get("heldout_raw_test_samples")),
            "verified": _integer(repaired.get("heldout_verified_test_samples")),
            "unverified": _integer(repaired.get("heldout_unverified_test_samples")),
        }
    }
    counts = normalize_runtime_counts(repaired, explicit)
    test = counts["test"]
    raw = test["raw"]
    verified = test["verified"]
    unverified = test["unverified"]
    repaired["heldout_raw_test_samples"] = raw
    repaired["heldout_verified_test_samples"] = verified
    repaired["heldout_unverified_test_samples"] = unverified

    metric_available = verified >= 2
    if metric_available and unverified == 0:
        status = "VERIFIED_ONLY"
    elif metric_available:
        status = "VERIFIED_SUBSET"
    elif raw >= 2 and verified == 0:
        status = "PIT_WARMUP"
    else:
        status = "INSUFFICIENT_VERIFIED_TEST"
    repaired["heldout_point_in_time_status"] = status
    repaired["heldout_metric_available"] = metric_available
    repaired["heldout_calibration_enabled"] = metric_available
    if not metric_available:
        repaired["heldout_metric_warning"] = (
            "PIT held-out calibration disabled: "
            f"{verified}/{raw} test samples are point-in-time verified; "
            "production scoring continues with unverified backtest evidence excluded"
        )
    return repaired


def install() -> None:
    """Patch v106 PIT summary scoping after its runtime wrapper is installed."""
    global _INSTALLED, _ORIGINAL_APPLY
    if _INSTALLED or getattr(_pit, "_PIT_COUNT_REPAIR_V1065_INSTALLED", False):
        return
    original = getattr(_pit, "apply_summary_pit_scope", None)
    if not callable(original):
        return
    _ORIGINAL_APPLY = original

    def repaired_apply_summary_pit_scope(
        summary: Any,
        counts: dict[str, dict[str, int]],
    ) -> Any:
        normalized = normalize_runtime_counts(summary, counts)
        return _ORIGINAL_APPLY(summary, normalized)

    _pit.apply_summary_pit_scope = repaired_apply_summary_pit_scope
    _pit.PIT_COUNT_REPAIR_VERSION = PIT_COUNT_REPAIR_VERSION
    _pit._PIT_COUNT_REPAIR_V1065_INSTALLED = True
    _INSTALLED = True
