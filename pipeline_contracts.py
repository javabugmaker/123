"""Explicit pipeline-stage contracts for canonical scanner publication.

The wide ScanResult object is kept for compatibility, but production execution
uses this module to make stage transitions explicit.  In particular a failed
or materially incomplete enrichment stage cannot silently fall back to base
scores and then be exported as a healthy canonical ranking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np

from config import (
    PIPELINE_ENRICHMENT_MAX_INCOMPLETE_ROWS,
    PIPELINE_ENRICHMENT_MIN_COMPLETE_RATIO,
)


class PipelineStage(StrEnum):
    RAW_SCAN = "RAW_SCAN"
    SCORED = "SCORED"
    ENRICHED = "ENRICHED"
    PUBLISHED = "PUBLISHED"


@dataclass(frozen=True)
class EnrichmentHealth:
    total_rows: int
    successful_rows: int
    complete_rows: int
    incomplete_rows: int
    complete_ratio: float
    status: str
    incomplete_tickers: tuple[str, ...] = ()

    @property
    def publishable(self) -> bool:
        return self.status in {"HEALTHY", "DEGRADED"}


def _finite(value: Any) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return False
    return bool(np.isfinite(numeric))


def _has_error(result: Any) -> bool:
    return bool(str(getattr(result, "error", "") or "").strip())


def _is_enrichment_complete(result: Any) -> bool:
    """Check fields that are created by the enrichment stage itself."""
    return bool(
        _finite(getattr(result, "technical_institutional_score", np.nan))
        and _finite(getattr(result, "institutional_score", np.nan))
        and str(getattr(result, "data_source", "") or "").strip()
        and str(getattr(result, "data_asof", "") or "").strip()
    )


def assess_enrichment(results: list[Any]) -> EnrichmentHealth:
    total = len(results)
    successful = [result for result in results if not _has_error(result)]
    complete = [result for result in successful if _is_enrichment_complete(result)]
    incomplete = [result for result in successful if not _is_enrichment_complete(result)]
    success_count = len(successful)
    complete_count = len(complete)
    incomplete_count = len(incomplete)
    ratio = complete_count / success_count if success_count else 0.0

    if not successful:
        status = "FAILED"
    elif incomplete_count == 0:
        status = "HEALTHY"
    elif (
        ratio >= float(PIPELINE_ENRICHMENT_MIN_COMPLETE_RATIO)
        and incomplete_count <= int(PIPELINE_ENRICHMENT_MAX_INCOMPLETE_ROWS)
    ):
        status = "DEGRADED"
    else:
        status = "FAILED"

    tickers = tuple(
        str(getattr(result, "ticker", "") or "").strip()
        for result in incomplete[:25]
        if str(getattr(result, "ticker", "") or "").strip()
    )
    return EnrichmentHealth(
        total_rows=total,
        successful_rows=success_count,
        complete_rows=complete_count,
        incomplete_rows=incomplete_count,
        complete_ratio=round(float(ratio), 6),
        status=status,
        incomplete_tickers=tickers,
    )


def enforce_enrichment_contract(
    results: list[Any],
    *,
    logger: logging.Logger | None = None,
) -> EnrichmentHealth:
    """Fail closed on material enrichment loss; quarantine isolated misses."""
    log = logger or logging.getLogger("institution_scanner")
    health = assess_enrichment(results)
    if health.status == "FAILED":
        examples = ", ".join(health.incomplete_tickers[:10]) or "none"
        raise ValueError(
            "ENRICHMENT_CONTRACT_FAILED: "
            f"complete={health.complete_rows}/{health.successful_rows} "
            f"({health.complete_ratio:.1%}), incomplete={health.incomplete_rows}, "
            f"examples={examples}"
        )

    if health.status == "DEGRADED":
        incomplete_set = set(health.incomplete_tickers)
        for result in results:
            ticker = str(getattr(result, "ticker", "") or "").strip()
            if ticker in incomplete_set and not _has_error(result):
                setattr(
                    result,
                    "error",
                    "ENRICHMENT_INCOMPLETE: excluded from canonical ranking/export",
                )
        log.warning(
            "Enrichment degraded: quarantined %d/%d incomplete rows (%.1f%% complete).",
            health.incomplete_rows,
            health.successful_rows,
            health.complete_ratio * 100.0,
        )
    return health
