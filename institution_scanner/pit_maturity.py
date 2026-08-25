"""Diagnostic maturity state for prospective point-in-time evidence.

Maturity never changes production weights. It answers a narrower question: is
the archived universe/evidence broad and old enough to be *considered* for
shadow validation? Complete survivorship control remains a separate hard
requirement for any future production promotion.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Final

PIT_MATURITY_VERSION: Final = "2026-08-25-v108.7-pit-readiness-state-v1"


def _integer(value: object) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def build_pit_readiness(summary: dict[str, object]) -> dict[str, object]:
    universe = _mapping(summary.get("point_in_time_universe"))
    available = bool(universe.get("available", False))
    snapshot_days = _integer(universe.get("snapshot_date_count"))
    max_gap = _integer(universe.get("max_snapshot_gap_days"))
    verified = _integer(summary.get("heldout_verified_test_samples"))
    raw = _integer(summary.get("heldout_raw_test_samples"))
    start = _date(universe.get("start_date"))
    end = _date(universe.get("end_date"))
    span_days = max(0, (end - start).days) if start and end else 0
    survivorship_complete = bool(universe.get("survivorship_complete", False))

    reasons: list[str] = []
    if not available or snapshot_days == 0:
        status = "NO_ARCHIVE"
        reasons.append("point_in_time_archive_unavailable")
    elif verified < 2 or snapshot_days < 20 or span_days < 30:
        status = "WARMUP"
        if verified < 2:
            reasons.append("insufficient_verified_heldout_samples")
        if snapshot_days < 20:
            reasons.append("insufficient_snapshot_days")
        if span_days < 30:
            reasons.append("insufficient_archive_span")
    elif verified < 50 or snapshot_days < 40 or span_days < 60 or max_gap > 14:
        status = "OBSERVING"
        if verified < 50:
            reasons.append("verified_sample_depth_below_shadow_threshold")
        if snapshot_days < 40:
            reasons.append("snapshot_depth_below_shadow_threshold")
        if span_days < 60:
            reasons.append("archive_span_below_shadow_threshold")
        if max_gap > 14:
            reasons.append("snapshot_gap_too_large")
    elif not survivorship_complete:
        status = "SHADOW_ELIGIBLE"
        reasons.append("survivorship_control_still_partial")
    elif verified < 200 or span_days < 252:
        status = "SHADOW_ELIGIBLE"
        reasons.append("promotion_depth_not_yet_met")
    else:
        status = "PROMOTION_CANDIDATE"
        reasons.append("diagnostic_thresholds_met_manual_review_required")

    return {
        "version": PIT_MATURITY_VERSION,
        "status": status,
        "production_activation_allowed": False,
        "manual_promotion_required": True,
        "snapshot_date_count": snapshot_days,
        "archive_span_calendar_days": span_days,
        "max_snapshot_gap_days": max_gap,
        "heldout_raw_test_samples": raw,
        "heldout_verified_test_samples": verified,
        "survivorship_complete": survivorship_complete,
        "reasons": reasons,
    }


def install(core: Any) -> None:
    marker = "_PIT_MATURITY_V1087_INSTALLED"
    if getattr(core, marker, False):
        return
    summary_type = getattr(core, "BacktestSummary", None)
    if summary_type is None or not hasattr(summary_type, "to_dict"):
        return
    original = summary_type.to_dict

    def to_dict(self: Any) -> dict[str, object]:
        payload = original(self)
        if isinstance(payload, dict):
            payload["pit_readiness"] = build_pit_readiness(payload)
        return payload

    summary_type.to_dict = to_dict
    core.PIT_MATURITY_VERSION = PIT_MATURITY_VERSION
    setattr(core, marker, True)
