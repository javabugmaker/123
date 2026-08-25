"""Fallback public MODEL HEALTH coverage from canonical AllResults fields.

ReliabilitySummary.json is useful metadata, but the public page must not show
unknown coverage when the row-level reliability annotations are present in the
canonical result set. This module changes presentation only.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pandas as pd

PAGE_HEALTH_FALLBACK_VERSION: Final = (
    "2026-08-25-v106.5-model-health-row-fallback-v1"
)


def _coverage(output_dir: Path) -> tuple[int, int, int]:
    path = Path(output_dir) / "AllResults.csv"
    if not path.is_file():
        return 0, 0, 0
    wanted = {
        "ChallengerAxisScoreDiagnostic",
        "HierarchicalEvidenceStatus",
    }
    try:
        frame = pd.read_csv(
            path,
            encoding="utf-8-sig",
            low_memory=False,
            usecols=lambda name: name in wanted,
        )
    except (OSError, UnicodeError, ValueError, pd.errors.ParserError):
        return 0, 0, 0
    total = len(frame)
    challenger = 0
    if "ChallengerAxisScoreDiagnostic" in frame.columns:
        challenger = int(
            pd.to_numeric(
                frame["ChallengerAxisScoreDiagnostic"],
                errors="coerce",
            ).notna().sum()
        )
    hierarchical = 0
    if "HierarchicalEvidenceStatus" in frame.columns:
        hierarchical = int(
            frame["HierarchicalEvidenceStatus"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.upper()
            .eq("DIAGNOSTIC_ONLY")
            .sum()
        )
    return total, challenger, hierarchical


def apply_model_health_fallback_html(text: str, output_dir: Path) -> str:
    """Replace only missing MODEL HEALTH coverage values with row-derived facts."""
    if not text or 'id="model-health-v105"' not in text:
        return text
    total, challenger, hierarchical = _coverage(Path(output_dir))
    if total <= 0:
        return text

    challenger_value = f"{challenger:,}"
    hierarchy_value = f"{hierarchical:,} / {total:,}"
    text = re.sub(
        r'(<span>CHALLENGER COVERAGE</span><strong>)(?:—|-)(</strong>)',
        rf"\g<1>{challenger_value}\g<2>",
        text,
        count=1,
    )
    text = re.sub(
        r'(<span>HIERARCHICAL EVIDENCE</span><strong>)(?:—|-)(</strong>)',
        rf"\g<1>{hierarchy_value}\g<2>",
        text,
        count=1,
    )
    meta = (
        '<meta name="page-health-fallback-version" '
        f'content="{PAGE_HEALTH_FALLBACK_VERSION}">'
    )
    if "page-health-fallback-version" not in text and "</head>" in text:
        text = text.replace("</head>", meta + "</head>", 1)
    return text
