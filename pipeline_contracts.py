"""Explicit immutable pipeline-stage contracts for canonical publication.

The legacy wide ``ScanResult`` remains available to avoid a risky flag-day
migration.  New stage views freeze only the fields owned by each processing
boundary, preventing downstream code from quietly reinterpreting partially
populated objects as fully enriched/published results.
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
    DECISION = "DECISION"
    PUBLISHED = "PUBLISHED"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return np.nan
    return numeric if np.isfinite(numeric) else np.nan


def _finite(value: Any) -> bool:
    return bool(np.isfinite(_number(value)))


@dataclass(frozen=True)
class RawScanView:
    ticker: str
    error: str

    @classmethod
    def from_result(cls, result: Any) -> RawScanView:
        return cls(ticker=_text(getattr(result, "ticker", "")), error=_text(getattr(result, "error", "")))

    @property
    def successful(self) -> bool:
        return not self.error


@dataclass(frozen=True)
class ScoredResultView:
    ticker: str
    final_score: float
    entry_signal: str
    score_confidence: float

    @classmethod
    def from_result(cls, result: Any) -> ScoredResultView:
        return cls(
            ticker=_text(getattr(result, "ticker", "")),
            final_score=_number(getattr(result, "final_score", np.nan)),
            entry_signal=_text(getattr(result, "entry_signal", "AVOID")).upper(),
            score_confidence=_number(getattr(result, "score_confidence", np.nan)),
        )


@dataclass(frozen=True)
class EnrichedResultView:
    ticker: str
    technical_institutional_score: float
    institutional_score: float
    data_source: str
    data_asof: str
    error: str

    @classmethod
    def from_result(cls, result: Any) -> EnrichedResultView:
        return cls(
            ticker=_text(getattr(result, "ticker", "")),
            technical_institutional_score=_number(
                getattr(result, "technical_institutional_score", np.nan)
            ),
            institutional_score=_number(getattr(result, "institutional_score", np.nan)),
            data_source=_text(getattr(result, "data_source", "")),
            data_asof=_text(getattr(result, "data_asof", "")),
            error=_text(getattr(result, "error", "")),
        )

    @property
    def complete(self) -> bool:
        return bool(
            not self.error
            and np.isfinite(self.technical_institutional_score)
            and np.isfinite(self.institutional_score)
            and self.data_source
            and self.data_asof
        )


@dataclass(frozen=True)
class DecisionResultView:
    ticker: str
    ranking_score: float
    ranking_eligibility: str
    hard_risk: bool

    @classmethod
    def from_result(cls, result: Any) -> DecisionResultView:
        return cls(
            ticker=_text(getattr(result, "ticker", "")),
            ranking_score=_number(getattr(result, "ranking_score", np.nan)),
            ranking_eligibility=_text(getattr(result, "ranking_eligibility", "观察")),
            hard_risk=bool(getattr(result, "hard_risk_flag", False)),
        )


@dataclass(frozen=True)
class PublishedResultView:
    ticker: str
    run_id: str
    ranking_scope: str
    overall_rank: int

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> PublishedResultView:
        try:
            rank = int(float(record.get("OverallRank", 0) or 0))
        except (TypeError, ValueError):
            rank = 0
        return cls(
            ticker=_text(record.get("Ticker", "")),
            run_id=_text(record.get("RunId", "")),
            ranking_scope=_text(record.get("RankingScope", "")),
            overall_rank=rank,
        )


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


def _has_error(result: Any) -> bool:
    return bool(RawScanView.from_result(result).error)


def _is_enrichment_complete(result: Any) -> bool:
    """Check fields created by the enrichment stage through its frozen view."""
    return EnrichedResultView.from_result(result).complete


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
        RawScanView.from_result(result).ticker
        for result in incomplete[:25]
        if RawScanView.from_result(result).ticker
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
            view = RawScanView.from_result(result)
            if view.ticker in incomplete_set and not view.error:
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
