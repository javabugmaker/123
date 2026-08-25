from __future__ import annotations

from pathlib import Path

import pandas as pd

from institution_scanner.page_health_fallback import (
    apply_model_health_fallback_html,
)


def test_model_health_missing_coverage_falls_back_to_allresults(tmp_path: Path) -> None:
    frame = pd.DataFrame(
        {
            "Ticker": ["A.ST", "B.ST", "C.ST"],
            "ChallengerAxisScoreDiagnostic": [61.0, 59.0, 55.0],
            "HierarchicalEvidenceStatus": [
                "DIAGNOSTIC_ONLY",
                "INSUFFICIENT",
                "INSUFFICIENT",
            ],
        }
    )
    frame.to_csv(
        tmp_path / "AllResults.csv",
        index=False,
        encoding="utf-8-sig",
    )
    html = (
        '<section id="model-health-v105">'
        '<article><span>CHALLENGER COVERAGE</span><strong>—</strong></article>'
        '<article><span>HIERARCHICAL EVIDENCE</span><strong>—</strong></article>'
        "</section></head>"
    )

    result = apply_model_health_fallback_html(html, tmp_path)

    assert "CHALLENGER COVERAGE</span><strong>3</strong>" in result
    assert "HIERARCHICAL EVIDENCE</span><strong>1 / 3</strong>" in result
    assert "page-health-fallback-version" in result


def test_existing_model_health_values_are_not_overwritten(tmp_path: Path) -> None:
    pd.DataFrame(
        {
            "ChallengerAxisScoreDiagnostic": [61.0],
            "HierarchicalEvidenceStatus": ["INSUFFICIENT"],
        }
    ).to_csv(tmp_path / "AllResults.csv", index=False, encoding="utf-8-sig")
    html = (
        '<section id="model-health-v105">'
        '<span>CHALLENGER COVERAGE</span><strong>6824</strong>'
        '<span>HIERARCHICAL EVIDENCE</span><strong>20</strong>'
        "</section></head>"
    )

    result = apply_model_health_fallback_html(html, tmp_path)

    assert "<strong>6824</strong>" in result
    assert "<strong>20</strong>" in result
