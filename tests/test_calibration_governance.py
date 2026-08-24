from __future__ import annotations

from calibration_governance_v102 import calibration_governance_state


class Summary:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def to_dict(self) -> dict[str, object]:
        return self.payload


def _valid_payload() -> dict[str, object]:
    return {
        "calibration_stability": {"status": "STABLE"},
        "monotonicity_high_low_20d": 0.01,
        "monotonicity_high_low_60d": 0.02,
        "rank_ic_20d": 0.04,
        "rank_ic_60d": 0.03,
        "point_in_time_universe": {
            "available": True,
            "survivorship_complete": True,
        },
        "universe_verified_samples": 120,
        "universe_unverified_samples": 0,
        "ranking_calibration_status": (
            "ENABLED_VERIFIED_POINT_IN_TIME"
        ),
        "peer_leave_one_out_verified": True,
    }


def test_unstable_calibration_is_fail_closed() -> None:
    payload = _valid_payload()
    payload["calibration_stability"] = {
        "status": "UNSTABLE"
    }
    payload["monotonicity_high_low_20d"] = -0.01

    active, reason, diagnostics = calibration_governance_state(
        Summary(payload)
    )
    assert active is False
    assert "diagnostic-only" in reason
    assert (
        diagnostics["checks"]["walk_forward_stable"]
        is False
    )


def test_stable_positive_calibration_can_activate_only_with_integrity() -> None:
    active, _, diagnostics = calibration_governance_state(
        Summary(_valid_payload())
    )
    assert active is True
    assert all(diagnostics["checks"].values())


def test_unverified_point_in_time_samples_block_peer_calibration() -> None:
    payload = _valid_payload()
    payload["universe_unverified_samples"] = 1

    active, reason, diagnostics = calibration_governance_state(
        Summary(payload)
    )
    assert active is False
    assert (
        diagnostics["checks"]["no_unverified_model_samples"]
        is False
    )
    assert "no_unverified_model_samples" in reason


def test_missing_leave_one_out_certification_blocks_peer_calibration() -> None:
    payload = _valid_payload()
    payload["peer_leave_one_out_verified"] = False

    active, reason, diagnostics = calibration_governance_state(
        Summary(payload)
    )
    assert active is False
    assert (
        diagnostics["checks"]["peer_leave_one_out_verified"]
        is False
    )
    assert "peer_leave_one_out_verified" in reason


def test_partial_survivorship_control_blocks_peer_calibration() -> None:
    payload = _valid_payload()
    payload["point_in_time_universe"] = {
        "available": True,
        "survivorship_complete": False,
    }

    active, reason, diagnostics = calibration_governance_state(
        Summary(payload)
    )
    assert active is False
    assert (
        diagnostics["checks"]["survivorship_control_complete"]
        is False
    )
    assert "survivorship_control_complete" in reason
