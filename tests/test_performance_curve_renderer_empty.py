from institution_scanner.performance_curve_web import performance_curve_html


def test_renderer_empty_when_missing(tmp_path) -> None:
    assert performance_curve_html(tmp_path / "missing.json") == ""
