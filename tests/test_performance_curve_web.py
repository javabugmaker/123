from __future__ import annotations

import json

from institution_scanner.performance_curve_web import performance_curve_html


def test_renderer_includes_three_panels(tmp_path) -> None:
    rows = []
    for index in range(30):
        rows.append(
            {
                "Date": f"2026-07-{(index % 28) + 1:02d}",
                "ResearchCohortNAV": 1.0 + index * 0.01,
                "ResearchCohortDrawdown": -1.0,
                "BenchmarkNAV": 1.0 + index * 0.004,
                "BenchmarkDrawdown": -0.5,
                "ResearchExcessNAV": 1.0 + index * 0.006,
                "RollingRankIC60": 0.1 if index > 5 else -0.05,
                "ICMedian": 0.08,
                "ICRiskFlag": index <= 5,
                "BetaCanaryNAV": 1.0 + index * 0.004,
                "BetaCanarySpread20": 0.2,
                "BetaRiskFlag": index % 7 == 0,
                "MaturedDates20": index + 1,
                "MaturedSamples20Cumulative": (index + 1) * 8,
                "BenchmarkCohortReturn20": 0.4,
            }
        )
    path = tmp_path / "PerformanceCurve.json"
    path.write_text(json.dumps({"version": "test", "rows": rows}), encoding="utf-8")
    rendered = performance_curve_html(path)
    assert "FORWARD PERFORMANCE" in rendered
    assert "沪深300同窗基准" in rendered
    assert "OPEN FULL AUDIT" in rendered
    assert 'href="performance.html"' in rendered
    assert 'id="performance-curves-v2"' in rendered
    assert "<svg" in rendered
