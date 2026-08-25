from __future__ import annotations

from institution_scanner.pit_maturity import build_pit_readiness


def _summary(
    *,
    snapshot_days: int,
    start: str,
    end: str,
    verified: int,
    raw: int,
    max_gap: int = 1,
    survivorship_complete: bool = False,
) -> dict[str, object]:
    return {
        "point_in_time_universe": {
            "available": snapshot_days > 0,
            "snapshot_date_count": snapshot_days,
            "start_date": start,
            "end_date": end,
            "max_snapshot_gap_days": max_gap,
            "survivorship_complete": survivorship_complete,
        },
        "heldout_verified_test_samples": verified,
        "heldout_raw_test_samples": raw,
    }


def test_pit_readiness_stays_warmup_without_mature_verified_samples() -> None:
    result = build_pit_readiness(
        _summary(
            snapshot_days=30,
            start="2026-07-01",
            end="2026-08-25",
            verified=0,
            raw=1000,
        )
    )
    assert result["status"] == "WARMUP"
    assert result["production_activation_allowed"] is False


def test_partial_survivorship_can_only_be_shadow_eligible() -> None:
    result = build_pit_readiness(
        _summary(
            snapshot_days=80,
            start="2026-04-01",
            end="2026-08-25",
            verified=120,
            raw=200,
            survivorship_complete=False,
        )
    )
    assert result["status"] == "SHADOW_ELIGIBLE"
    assert "survivorship_control_still_partial" in result["reasons"]
    assert result["production_activation_allowed"] is False


def test_promotion_candidate_is_diagnostic_and_manual_only() -> None:
    result = build_pit_readiness(
        _summary(
            snapshot_days=260,
            start="2025-08-01",
            end="2026-08-25",
            verified=300,
            raw=320,
            survivorship_complete=True,
        )
    )
    assert result["status"] == "PROMOTION_CANDIDATE"
    assert result["manual_promotion_required"] is True
    assert result["production_activation_allowed"] is False
