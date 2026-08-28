from __future__ import annotations

import json

from institution_scanner.performance_curve_web import (
    performance_page_html,
    write_performance_page,
)


def _curve(tmp_path):
    path = tmp_path / "PerformanceCurve.json"
    rows = []
    for index in range(30):
        rows.append(
            {
                "Date": f"2026-07-{(index % 28) + 1:02d}",
                "ResearchCohortNAV": 1.0 + index * 0.01,
                "BenchmarkNAV": 1.0 + index * 0.003,
                "ResearchExcessNAV": 1.0 + index * 0.007,
                "ResearchCohortDrawdown": -float(index % 4),
                "BenchmarkDrawdown": -float(index % 3),
                "RollingRankIC60": 0.08,
                "ICMedian": 0.06,
                "ICRiskFlag": False,
                "BetaCanaryNAV": 1.0 + index * 0.002,
                "BetaRiskFlag": False,
                "MaturedDates20": index + 1,
                "MaturedSamples20Cumulative": (index + 1) * 10,
                "BenchmarkCohortReturn20": 0.3,
            }
        )
    path.write_text(
        json.dumps({"version": "test-forward-performance", "rows": rows}),
        encoding="utf-8",
    )
    return path


def test_standalone_page_contains_four_audit_panels(tmp_path) -> None:
    rendered = performance_page_html(_curve(tmp_path))
    assert "FORWARD PERFORMANCE" in rendered
    assert "01 / FORWARD COHORT NAV PROXY" in rendered
    assert "02 / DRAWDOWN" in rendered
    assert "03 / ROLLING RANK IC" in rendered
    assert "04 / RISK-APPETITE CANARY" in rendered
    assert "不发布 Sharpe、CAGR" in rendered
    assert rendered.count("<svg") == 4


def test_writer_materializes_performance_html(tmp_path) -> None:
    target = tmp_path / "site" / "performance.html"
    assert write_performance_page(target, _curve(tmp_path)) == target
    assert target.is_file()
    assert "前瞻绩效" in target.read_text(encoding="utf-8")
