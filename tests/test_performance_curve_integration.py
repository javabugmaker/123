from __future__ import annotations

import json

from institution_scanner.performance_curve_integration import inject_into_html


def test_injects_before_calibration(tmp_path) -> None:
    page = tmp_path / "index.html"
    page.write_text('<html><body><section id="score-bucket-calibration-v93">cal</section></body></html>', encoding="utf-8")
    curve = tmp_path / "PerformanceCurve.json"
    curve.write_text(
        json.dumps(
            {
                "rows": [
                    {
                        "Date": "2026-08-26",
                        "ResearchCohortNAV": 1.0,
                        "ResearchCohortDrawdown": 0.0,
                        "RollingRankIC60": 0.1,
                        "ICMedian": 0.1,
                        "ICRiskFlag": False,
                        "BetaCanaryNAV": 1.0,
                        "BetaCanarySpread20": 0.1,
                        "BetaRiskFlag": False,
                    },
                    {
                        "Date": "2026-08-27",
                        "ResearchCohortNAV": 1.01,
                        "ResearchCohortDrawdown": 0.0,
                        "RollingRankIC60": 0.11,
                        "ICMedian": 0.1,
                        "ICRiskFlag": False,
                        "BetaCanaryNAV": 1.01,
                        "BetaCanarySpread20": 0.2,
                        "BetaRiskFlag": False,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    assert inject_into_html(page, curve)
    text = page.read_text(encoding="utf-8")
    assert text.index("performance-curves-v2") < text.index("score-bucket-calibration-v93")
    assert 'href="performance.html"' in text


def test_archive_links_to_root_performance_page(tmp_path) -> None:
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    page = report_dir / "2026-08-27.html"
    page.write_text("<html><body></main></body></html>", encoding="utf-8")
    curve = tmp_path / "PerformanceCurve.json"
    curve.write_text(
        json.dumps(
            {
                "rows": [
                    {"Date": "2026-08-26", "ResearchCohortNAV": 1.0},
                    {"Date": "2026-08-27", "ResearchCohortNAV": 1.01},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert inject_into_html(page, curve)
    assert 'href="../performance.html"' in page.read_text(encoding="utf-8")
