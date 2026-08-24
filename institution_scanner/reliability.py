"""Canonical reliability foundation for production and shadow research models.

This module is deliberately post-ranking.  It annotates the published universe
with model-contract, challenger and hierarchical-evidence diagnostics without
changing production scores, eligibility, CandidateViewRank or TradeReady.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from .contracts import (
    CHALLENGER_CONTRACT,
    CONTRACT_VERSION,
    PRODUCTION_CONTRACT,
)

RELIABILITY_FOUNDATION_VERSION: Final = (
    "2026-08-24-v103-reliability-foundation-v1"
)
HIERARCHICAL_EVIDENCE_VERSION: Final = (
    "2026-08-24-v103-hierarchical-evidence-diagnostic-v1"
)

_INSTALLED = False
_ORIGINAL_APPLY: Any = None

_HIERARCHY_SPECS: tuple[tuple[str, tuple[str, ...], float], ...] = (
    ("INDUSTRY_SIGNAL", ("IndustryTopic", "EntrySignal"), 20.0),
    ("ASSET_SIGNAL", ("ResearchAssetClass", "EntrySignal"), 30.0),
    ("INDUSTRY", ("IndustryTopic",), 20.0),
    ("ASSET", ("ResearchAssetClass",), 50.0),
)


def _numeric(
    frame: pd.DataFrame,
    column: str,
    default: float = np.nan,
) -> pd.Series:
    source = frame.get(column, pd.Series(default, index=frame.index, dtype=float))
    if not isinstance(source, pd.Series):
        source = pd.Series(source, index=frame.index)
    return pd.to_numeric(source, errors="coerce").replace([np.inf, -np.inf], np.nan)


def _first_numeric(
    frame: pd.DataFrame,
    names: tuple[str, ...],
    default: float = np.nan,
) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return _numeric(frame, name, default)
    return pd.Series(default, index=frame.index, dtype=float)


def _text(frame: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    source = frame.get(column, pd.Series(default, index=frame.index, dtype=object))
    if not isinstance(source, pd.Series):
        source = pd.Series(source, index=frame.index)
    return source.fillna(default).astype(str).str.strip()


def _weighted_axis_score(
    setup: pd.Series,
    trigger: pd.Series,
    execution: pd.Series,
    *,
    setup_weight: float,
    trigger_weight: float,
    execution_weight: float,
) -> pd.Series:
    valid = setup.notna() & trigger.notna() & execution.notna()
    score = pd.Series(np.nan, index=setup.index, dtype=float)
    score.loc[valid] = (
        setup.loc[valid] * setup_weight
        + trigger.loc[valid] * trigger_weight
        + execution.loc[valid] * execution_weight
    )
    return score.clip(0.0, 100.0)


def annotate_shadow_model(frame: pd.DataFrame) -> pd.DataFrame:
    """Add champion/challenger diagnostics without touching production columns."""
    result = frame.copy()
    if result.empty:
        return result

    setup = _first_numeric(result, ("SetupScore", "BaseScore"))
    trigger = _first_numeric(result, ("TriggerScore",))
    execution = _first_numeric(result, ("ExecutionScore",))

    champion = _weighted_axis_score(
        setup,
        trigger,
        execution,
        setup_weight=PRODUCTION_CONTRACT.weights.setup,
        trigger_weight=PRODUCTION_CONTRACT.weights.trigger,
        execution_weight=PRODUCTION_CONTRACT.weights.execution,
    )
    challenger = _weighted_axis_score(
        setup,
        trigger,
        execution,
        setup_weight=CHALLENGER_CONTRACT.weights.setup,
        trigger_weight=CHALLENGER_CONTRACT.weights.trigger,
        execution_weight=CHALLENGER_CONTRACT.weights.execution,
    )

    asset = _text(result, "ResearchAssetClass", "ALL").replace("", "ALL")
    rank_frame = pd.DataFrame(
        {
            "asset": asset,
            "champion": champion,
            "challenger": challenger,
        },
        index=result.index,
    )

    champion_rank = rank_frame.groupby("asset", dropna=False)["champion"].rank(
        method="first",
        ascending=False,
        na_option="bottom",
    )
    challenger_rank = rank_frame.groupby("asset", dropna=False)["challenger"].rank(
        method="first",
        ascending=False,
        na_option="bottom",
    )

    result["ProductionModelRole"] = PRODUCTION_CONTRACT.role
    result["ProductionModelContractVersion"] = PRODUCTION_CONTRACT.version
    result["ProductionModelWeightSignatureLocked"] = (
        PRODUCTION_CONTRACT.weights.signature()
    )
    result["ChallengerModelRole"] = CHALLENGER_CONTRACT.role
    result["ChallengerModelVersion"] = CHALLENGER_CONTRACT.version
    result["ChallengerModelWeightSignature"] = (
        CHALLENGER_CONTRACT.weights.signature()
    )
    result["ChampionAxisScoreDiagnostic"] = champion.round(4)
    result["ChallengerAxisScoreDiagnostic"] = challenger.round(4)
    result["ChampionAxisRankWithinAsset"] = champion_rank.round(0).astype("Int64")
    result["ChallengerAxisRankWithinAsset"] = challenger_rank.round(0).astype("Int64")
    result["ChallengerAxisRankDelta"] = (
        champion_rank - challenger_rank
    ).round(0).astype("Int64")
    result["ChallengerProductionApplied"] = False
    result["ReliabilityFoundationVersion"] = RELIABILITY_FOUNDATION_VERSION
    result["ModelContractVersion"] = CONTRACT_VERSION
    return result


def _group_weighted_evidence(
    frame: pd.DataFrame,
    keys: tuple[str, ...],
    score: pd.Series,
    effective_n: pd.Series,
) -> pd.DataFrame:
    working = pd.DataFrame(index=frame.index)
    for key in keys:
        working[key] = _text(frame, key)
    working["_score"] = score
    working["_n"] = effective_n.clip(lower=0.0)
    working["_weighted"] = working["_score"] * working["_n"]

    valid = working["_score"].notna() & working["_n"].gt(0.0)
    for key in keys:
        valid &= working[key].ne("")
    working = working.loc[valid]
    if working.empty:
        return pd.DataFrame(columns=[*keys, "EvidenceScore", "EffectiveN", "Tickers"])

    grouped = (
        working.groupby(list(keys), dropna=False)
        .agg(
            WeightedSum=("_weighted", "sum"),
            EffectiveN=("_n", "sum"),
            Tickers=("_score", "size"),
        )
        .reset_index()
    )
    grouped["EvidenceScore"] = (
        grouped["WeightedSum"] / grouped["EffectiveN"].replace(0.0, np.nan)
    )
    return grouped[[*keys, "EvidenceScore", "EffectiveN", "Tickers"]]


def annotate_hierarchical_evidence(frame: pd.DataFrame) -> pd.DataFrame:
    """Pool sparse ticker evidence into diagnostic-only hierarchical summaries.

    The result is never fed back into CompositeScore or ranking.  It exists to
    accumulate research evidence at sensible peer levels while v102 production
    governance remains fail-closed.
    """
    result = frame.copy()
    if result.empty:
        return result

    score = _first_numeric(
        result,
        (
            "BacktestEvidenceScoreRaw",
            "BacktestScore",
        ),
        np.nan,
    )
    effective_n = _first_numeric(
        result,
        (
            "BacktestEffectiveSamples",
            "BacktestEffectiveSampleSize",
            "BacktestEffectiveN",
            "BacktestSamples",
        ),
        0.0,
    ).fillna(0.0)

    chosen_score = pd.Series(np.nan, index=result.index, dtype=float)
    chosen_n = pd.Series(0.0, index=result.index, dtype=float)
    chosen_level = pd.Series("INSUFFICIENT", index=result.index, dtype=object)
    chosen_tickers = pd.Series(0, index=result.index, dtype="Int64")

    for level, keys, min_effective_n in _HIERARCHY_SPECS:
        missing_keys = [key for key in keys if key not in result.columns]
        if missing_keys:
            continue

        stats = _group_weighted_evidence(result, keys, score, effective_n)
        if stats.empty:
            continue

        left = pd.DataFrame(index=result.index)
        left["_row_id"] = result.index
        for key in keys:
            left[key] = _text(result, key)
        merged = left.merge(stats, on=list(keys), how="left").set_index("_row_id")
        eligible = (
            chosen_level.eq("INSUFFICIENT")
            & merged["EffectiveN"].ge(min_effective_n).fillna(False)
            & merged["EvidenceScore"].notna()
        )
        chosen_score.loc[eligible] = merged.loc[eligible, "EvidenceScore"]
        chosen_n.loc[eligible] = merged.loc[eligible, "EffectiveN"]
        chosen_tickers.loc[eligible] = (
            pd.to_numeric(merged.loc[eligible, "Tickers"], errors="coerce")
            .fillna(0)
            .round(0)
            .astype("Int64")
        )
        chosen_level.loc[eligible] = level

    sufficient = chosen_level.ne("INSUFFICIENT")
    result["HierarchicalEvidenceScore"] = chosen_score.round(4)
    result["HierarchicalEvidenceEffectiveN"] = chosen_n.round(4)
    result["HierarchicalEvidencePeerTickers"] = chosen_tickers
    result["HierarchicalEvidenceLevel"] = chosen_level
    result["HierarchicalEvidenceStatus"] = np.where(
        sufficient,
        "DIAGNOSTIC_ONLY",
        "INSUFFICIENT",
    )
    result["HierarchicalEvidenceProductionApplied"] = False
    result["HierarchicalEvidenceVersion"] = HIERARCHICAL_EVIDENCE_VERSION
    return result


def annotate_reliability(frame: pd.DataFrame) -> pd.DataFrame:
    result = annotate_shadow_model(frame)
    result = annotate_hierarchical_evidence(result)
    return result


def reliability_summary(frame: pd.DataFrame) -> dict[str, object]:
    challenger_delta = _numeric(frame, "ChallengerAxisRankDelta")
    hierarchy_status = _text(frame, "HierarchicalEvidenceStatus")
    return {
        "version": RELIABILITY_FOUNDATION_VERSION,
        "production_contract": PRODUCTION_CONTRACT.to_dict(),
        "challenger_contract": CHALLENGER_CONTRACT.to_dict(),
        "rows": len(frame),
        "challenger_production_applied": False,
        "challenger_rows_scored": int(
            _numeric(frame, "ChallengerAxisScoreDiagnostic").notna().sum()
        ),
        "challenger_median_abs_rank_delta": (
            float(challenger_delta.abs().median())
            if challenger_delta.notna().any()
            else None
        ),
        "hierarchical_evidence": {
            "version": HIERARCHICAL_EVIDENCE_VERSION,
            "production_applied": False,
            "diagnostic_rows": int(hierarchy_status.eq("DIAGNOSTIC_ONLY").sum()),
            "insufficient_rows": int(hierarchy_status.eq("INSUFFICIENT").sum()),
        },
    }


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def install(core: Any) -> None:
    """Install as the final post-ranking diagnostic layer."""
    global _INSTALLED, _ORIGINAL_APPLY
    if _INSTALLED or getattr(core, "_RELIABILITY_FOUNDATION_V103_INSTALLED", False):
        return

    original = getattr(core, "_legacy_apply_backtest_ranking", None)
    if not callable(original):
        return
    _ORIGINAL_APPLY = original

    def reliability_annotated_apply_backtest_ranking(
        summary: Any,
        top_n: int = 50,
    ) -> None:
        _ORIGINAL_APPLY(summary, top_n=top_n)

        path = Path(core.OUTPUT_DIR) / "AllResults.csv"
        if not path.exists():
            return

        frame = pd.read_csv(path, encoding="utf-8-sig", low_memory=False)
        annotated = annotate_reliability(frame)

        from report import (
            _atomic_write_csv,
            _atomic_write_parquet,
            refresh_candidate_exports,
        )

        _atomic_write_csv(annotated, path)
        refresh_candidate_exports(annotated, output_dir=core.OUTPUT_DIR)
        _atomic_write_parquet(
            annotated,
            Path(core.OUTPUT_DIR) / "AllResults.parquet",
        )
        _atomic_json(
            Path(core.OUTPUT_DIR) / "ReliabilitySummary.json",
            reliability_summary(annotated),
        )

    core._legacy_apply_backtest_ranking = reliability_annotated_apply_backtest_ranking
    core.RELIABILITY_FOUNDATION_VERSION = RELIABILITY_FOUNDATION_VERSION
    core.HIERARCHICAL_EVIDENCE_VERSION = HIERARCHICAL_EVIDENCE_VERSION
    core.MODEL_CONTRACT_VERSION = CONTRACT_VERSION
    core._RELIABILITY_FOUNDATION_V103_INSTALLED = True
    _INSTALLED = True
