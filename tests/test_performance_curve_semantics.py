from performance_curve import PERFORMANCE_CURVE_VERSION


def test_curve_contract_declares_point_in_time_model_health() -> None:
    assert "pit-model-health" in PERFORMANCE_CURVE_VERSION
