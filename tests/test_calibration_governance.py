from __future__ import annotations

from calibration_governance_v102 import calibration_governance_state


class Summary:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def to_dict(self) -> dict[str, object]:
        return self.payload


def test_unstable_calibration_is_fail_closed() -> None:
    active, reason, diagnostics = calibration_governance_state(
        Summary(
            {
                "calibration_stability": {"status": "UNSTABLE"},
                "monotonicity_high_low_20d": -0.01,
                "monotonicity_high_low_60d": -0.02,
                "rank_ic_20d": 0.05,
                "rank_ic_60d": -0.01,
            }
        )
    )
    assert active is False
    assert "diagnostic-only" in reason
    assert diagnostics["checks"]["walk_forward_stable"] is False


def test_stable_positive_calibration_can_activate() -> None:
    active, _, diagnostics = calibration_governance_state(
        Summary(
            {
                "calibration_stability": {"status": "STABLE"},
                "monotonicity_high_low_20d": 0.01,
                "monotonicity_high_low_60d": 0.02,
                "rank_ic_20d": 0.04,
                "rank_ic_60d": 0.03,
            }
        )
    )
    assert active is True
    assert all(diagnostics["checks"].values())
