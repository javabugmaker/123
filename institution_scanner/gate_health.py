"""Run-level gate distribution diagnostics.

This module never changes ranking or execution eligibility. It detects abrupt
changes in gate distributions so provider/schema/financial-season semantics
cannot silently collapse the actionable universe.
"""

from __future__ import annotations

from typing import Any


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed and abs(parsed) != float("inf") else default


def _previous_quality(previous_summary: dict[str, object]) -> dict[str, object]:
    for key in ("quality_gate", "quality", "gate_health"):
        value = previous_summary.get(key)
        if isinstance(value, dict):
            if key == "gate_health" and isinstance(value.get("quality_gate"), dict):
                return value["quality_gate"]
            return value
    universe = previous_summary.get("universe")
    return universe if isinstance(universe, dict) else {}


def build_gate_health(
    scan_profile: dict[str, object],
    previous_summary: dict[str, object],
) -> dict[str, object]:
    current_rate = _number(scan_profile.get("quality_gate_pass_rate", 0.0))
    current_complete = _number(
        scan_profile.get("quality_hard_data_complete_rate", 0.0)
    )
    applicable = int(scan_profile.get("quality_applicable_stocks", 0) or 0)
    passed = int(scan_profile.get("quality_gate_passed_stocks", 0) or 0)

    previous = _previous_quality(previous_summary)
    previous_rate = _number(
        previous.get(
            "pass_rate",
            previous.get("quality_gate_pass_rate", 0.0),
        )
    )
    delta = current_rate - previous_rate
    ratio = current_rate / previous_rate if previous_rate > 1e-12 else None

    flags: list[str] = []
    if applicable >= 100 and current_rate < 0.01:
        flags.append("QUALITY_GATE_PASS_RATE_NEAR_ZERO")
    if applicable >= 100 and current_complete >= 0.90 and current_rate < 0.02:
        flags.append("HIGH_COMPLETENESS_LOW_PASS_RATE")
    if (
        applicable >= 100
        and previous_rate >= 0.05
        and current_rate <= previous_rate * 0.25
    ):
        flags.append("QUALITY_GATE_DISTRIBUTION_COLLAPSE")

    status = (
        "CRITICAL"
        if "QUALITY_GATE_DISTRIBUTION_COLLAPSE" in flags
        else "WARNING"
        if flags
        else "NORMAL"
    )
    return {
        "status": status,
        "quality_gate": {
            "applicable_stocks": applicable,
            "passed_stocks": passed,
            "pass_rate": round(current_rate, 4),
            "previous_pass_rate": round(previous_rate, 4),
            "pass_rate_delta": round(delta, 4),
            "pass_rate_ratio_to_previous": (
                round(ratio, 4) if ratio is not None else None
            ),
            "hard_data_complete_rate": round(current_complete, 4),
        },
        "flags": flags,
        "diagnostic_only": True,
    }
