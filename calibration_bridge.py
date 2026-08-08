from __future__ import annotations

from typing import Any

import numpy as np


def _number(value: Any, default: float = np.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if np.isfinite(result) else default


def _key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        str(row.get("ticker", "")),
        str(row.get("entry_signal", "UNKNOWN")).upper(),
    )


def _score(row: dict[str, Any]) -> float:
    adjusted = _number(row.get("backtest_adjusted_score"))
    if np.isfinite(adjusted):
        return adjusted
    return _number(row.get("backtest_score"))


def bridge_global_calibration(
    global_rows: list[dict[str, Any]] | None,
    fast_rows: list[dict[str, Any]] | None,
    exact_rows: list[dict[str, Any]] | None,
    *,
    min_samples: int = 10,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [dict(row) for row in (global_rows or [])]
    fast_map = {_key(row): row for row in (fast_rows or [])}
    differences: list[float] = []
    for exact in exact_rows or []:
        key = _key(exact)
        fast = fast_map.get(key)
        if fast is None:
            continue
        if int(_number(exact.get("samples"), 0.0)) < int(min_samples):
            continue
        if int(_number(fast.get("samples"), 0.0)) < int(min_samples):
            continue
        exact_score = _score(exact)
        fast_score = _score(fast)
        if np.isfinite(exact_score) and np.isfinite(fast_score):
            differences.append(float(exact_score - fast_score))

    metadata: dict[str, Any] = {
        "accepted": False,
        "pairs": len(differences),
        "median_score_delta": 0.0,
        "mad": 0.0,
        "confidence": 0.0,
        "applied_delta": 0.0,
    }
    if len(differences) < 5 or not rows:
        return rows, metadata

    values = np.asarray(differences, dtype=float)
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    pair_confidence = float(np.clip(len(values) / 30.0, 0.0, 1.0))
    stability = float(np.clip(1.0 - mad / 20.0, 0.25, 1.0))
    confidence = pair_confidence * stability
    applied_delta = float(np.clip(median, -8.0, 8.0) * confidence)

    adjusted: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        score = _number(item.get("calibration_score"), 50.0)
        row_confidence = float(np.clip(_number(item.get("confidence"), 0.0), 0.0, 1.0))
        item["calibration_score"] = round(
            float(np.clip(score + applied_delta * row_confidence, 0.0, 100.0)), 4
        )
        item["fast_exact_bridge_delta"] = round(applied_delta, 4)
        adjusted.append(item)

    metadata.update(
        {
            "accepted": True,
            "median_score_delta": round(median, 4),
            "mad": round(mad, 4),
            "confidence": round(confidence, 4),
            "applied_delta": round(applied_delta, 4),
        }
    )
    return adjusted, metadata
