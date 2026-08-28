from performance_curve import PERFORMANCE_CURVE_VERSION


def test_performance_curve_version_is_explicit() -> None:
    assert PERFORMANCE_CURVE_VERSION.startswith("2026-08-28-")
