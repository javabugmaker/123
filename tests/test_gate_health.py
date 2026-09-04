from __future__ import annotations

from institution_scanner.gate_health import build_gate_health


def test_quality_gate_collapse_is_flagged_when_data_is_complete() -> None:
    current = {
        "quality_applicable_stocks": 500,
        "quality_gate_passed_stocks": 0,
        "quality_gate_pass_rate": 0.0,
        "quality_hard_data_complete_rate": 0.98,
    }
    previous = {"quality_gate": {"pass_rate": 0.20}}

    health = build_gate_health(current, previous)

    assert health["status"] == "CRITICAL"
    assert "QUALITY_GATE_PASS_RATE_NEAR_ZERO" in health["flags"]
    assert "HIGH_COMPLETENESS_LOW_PASS_RATE" in health["flags"]
    assert "QUALITY_GATE_DISTRIBUTION_COLLAPSE" in health["flags"]
    assert health["diagnostic_only"] is True


def test_normal_quality_gate_distribution_stays_diagnostic_only() -> None:
    current = {
        "quality_applicable_stocks": 500,
        "quality_gate_passed_stocks": 90,
        "quality_gate_pass_rate": 0.18,
        "quality_hard_data_complete_rate": 0.95,
    }
    previous = {"quality_gate": {"pass_rate": 0.20}}

    health = build_gate_health(current, previous)

    assert health["status"] == "NORMAL"
    assert health["flags"] == []
    assert health["quality_gate"]["pass_rate_delta"] == -0.02
