"""Canonical reliability foundation for production and shadow research models.

This module is deliberately post-ranking. It annotates the published universe
with model-contract, challenger and hierarchical-evidence diagnostics without
changing production scores, eligibility, CandidateViewRank or TradeReady.

v106 makes hierarchical peer evidence mathematically conservative:
- every ticker is excluded from its own peer aggregate (leave-one-out);
- raw per-ticker effective samples are never summed and presented as if they
  were independent cross-sectional trials;
- breadth is measured with a Kish effective-peer count and each independent
  peer is capped at a small amount of information.

The hierarchy remains DIAGNOSTIC_ONLY and is never fed back into production.
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
    "2026-08-24-v106-pit-hierarchical-integrity-v1"
)
HIERARCHICAL_EVIDENCE_VERSION: Final = (
    "2026-08-24-v106-loo-kish-breadth-cap-diagnostic-v1"
)
HIERARCHICAL_MAX_INFORMATION_PER_EFFECTIVE_PEER: Final = 3.0
HIERARCHICAL_MIN_PEER_TICKERS: Final = 2

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
    source = frame.get(
        column,
        pd.Series(default, index=frame.index, dtype=float),
    )
    if not isinstance(source, pd.Series):
        source = pd.Series(source, index=frame.index)
    return pd.to_numeric(source, errors="coerce").replace(
        [np.inf, -np.inf],
        np.nan,
    )


def _first_numeric(
    frame: pd.DataFrame,
    names: tuple[str, ...],
    default: float = np.nan,
) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return _numeric(frame, name, default)
    return pd.Series(default, index=frame.index, dtype=float)


def _text(
    frame: pd.DataFrame,
    column: str,
    default: str = "",
) -> pd.Series:
    source = frame.get(
        column,
        pd.Series(default, index=frame.index, dtype=object),
    )
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

    asset = _text(
        result,
        "ResearchAssetClass",
        "ALL",
    ).replace("", "ALL")
    rank_frame = pd.DataFrame(
        {
            "asset": asset,
            "champion": champion,
            "challenger": challenger,
        },
        index=result.index,
    )

    champion_rank = rank_frame.groupby(
        "asset",
        dropna=False,
    )["champion"].rank(
        method="first",
        ascending=False,
        na_option="bottom",
    )
    challenger_rank = rank_frame.groupby(
        "asset",
        dropna=False,
    )["challenger"].rank(
        method="first",
        ascending=False,
        na_option="bottom",
    )

    result["ProductionModelRole"] = PRODUCTION_CONTRACT.role
    result["ProductionModelContractVersion"] = (
        PRODUCTION_CONTRACT.version
    )
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
    result["ChampionAxisRankWithinAsset"] = (
        champion_rank.round(0).astype("Int64")
    )
    result["ChallengerAxisRankWithinAsset"] = (
        challenger_rank.round(0).astype("Int64")
    )
    result["ChallengerAxisRankDelta"] = (
        champion_rank - challenger_rank
    ).round(0).astype("Int64")
    result["ChallengerProductionApplied"] = False
    result["ReliabilityFoundationVersion"] = (
        RELIABILITY_FOUNDATION_VERSION
    )
    result["ModelContractVersion"] = CONTRACT_VERSION
    return result


def _group_leave_one_out_evidence(
    frame: pd.DataFrame,
    keys: tuple[str, ...],
    score: pd.Series,
    effective_n: pd.Series,
) -> pd.DataFrame:
    """Return row-aligned leave-one-out peer evidence for one hierarchy.

    The raw ticker-level ``effective_n`` measures temporal support inside a
    ticker. Summing it across correlated tickers would exaggerate independent
    information. We therefore:
    1. subtract the focal ticker exactly;
    2. compute Kish effective peer breadth from the remaining ticker weights;
    3. cap information at three effective samples per Kish peer.

    This is a conservative breadth proxy, not a claim that pairwise return
    correlations were estimated from ticker-level summaries.
    """
    working = pd.DataFrame(index=frame.index)
    for key in keys:
        working[key] = _text(frame, key)
    working["_score"] = score
    working["_n"] = effective_n.fillna(0.0).clip(lower=0.0)
    working["_weighted"] = working["_score"] * working["_n"]
    working["_n2"] = working["_n"].pow(2)
    working["_valid_contributor"] = (
        working["_score"].notna() & working["_n"].gt(0.0)
    )

    valid_keys = pd.Series(True, index=working.index, dtype=bool)
    for key in keys:
        valid_keys &= working[key].ne("")

    groupable = working.loc[valid_keys].copy()
    output = pd.DataFrame(
        {
            "EvidenceScore": np.nan,
            "NominalN": 0.0,
            "EffectiveN": 0.0,
            "KishPeers": 0.0,
            "PeerTickers": 0,
        },
        index=frame.index,
    )
    if groupable.empty:
        return output

    grouping_key: str | list[str]
    grouping_key = keys[0] if len(keys) == 1 else list(keys)
    grouped = groupable.groupby(
        grouping_key,
        dropna=False,
        sort=False,
    )
    totals = grouped.agg(
        _total_n=("_n", "sum"),
        _total_n2=("_n2", "sum"),
        _total_weighted=("_weighted", "sum"),
        _contributors=("_valid_contributor", "sum"),
    )

    left = groupable.copy()
    left["_row_id"] = groupable.index
    left = left.reset_index(drop=True)
    stats = totals.reset_index()
    merged = left.merge(
        stats,
        on=list(keys),
        how="left",
        validate="many_to_one",
    ).set_index("_row_id")

    own_valid = merged["_valid_contributor"].astype(bool)
    own_n = merged["_n"].where(own_valid, 0.0)
    own_weighted = merged["_weighted"].where(own_valid, 0.0)
    peer_nominal = (
        merged["_total_n"] - own_n
    ).clip(lower=0.0)
    peer_n2 = (
        merged["_total_n2"] - own_n.pow(2)
    ).clip(lower=0.0)
    peer_weighted = merged["_total_weighted"] - own_weighted
    peer_tickers = (
        merged["_contributors"].astype(float)
        - own_valid.astype(float)
    ).clip(lower=0.0)

    kish = pd.Series(
        0.0,
        index=merged.index,
        dtype=float,
    )
    kish_valid = peer_nominal.gt(0.0) & peer_n2.gt(0.0)
    kish.loc[kish_valid] = (
        peer_nominal.loc[kish_valid].pow(2)
        / peer_n2.loc[kish_valid]
    )
    kish = pd.concat(
        [kish, peer_tickers],
        axis=1,
    ).min(axis=1).clip(lower=0.0)

    corrected = pd.concat(
        [
            peer_nominal,
            kish * HIERARCHICAL_MAX_INFORMATION_PER_EFFECTIVE_PEER,
        ],
        axis=1,
    ).min(axis=1).clip(lower=0.0)

    peer_score = pd.Series(
        np.nan,
        index=merged.index,
        dtype=float,
    )
    score_valid = peer_nominal.gt(0.0)
    peer_score.loc[score_valid] = (
        peer_weighted.loc[score_valid]
        / peer_nominal.loc[score_valid]
    )

    output.loc[merged.index, "EvidenceScore"] = peer_score
    output.loc[merged.index, "NominalN"] = peer_nominal
    output.loc[merged.index, "EffectiveN"] = corrected
    output.loc[merged.index, "KishPeers"] = kish
    output.loc[merged.index, "PeerTickers"] = peer_tickers
    return output


def annotate_hierarchical_evidence(
    frame: pd.DataFrame,
) -> pd.DataFrame:
    """Add leave-one-out, breadth-capped hierarchical diagnostics only."""
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

    chosen_score = pd.Series(
        np.nan,
        index=result.index,
        dtype=float,
    )
    chosen_nominal_n = pd.Series(
        0.0,
        index=result.index,
        dtype=float,
    )
    chosen_n = pd.Series(
        0.0,
        index=result.index,
        dtype=float,
    )
    chosen_kish = pd.Series(
        0.0,
        index=result.index,
        dtype=float,
    )
    chosen_level = pd.Series(
        "INSUFFICIENT",
        index=result.index,
        dtype=object,
    )
    chosen_tickers = pd.Series(
        0,
        index=result.index,
        dtype="Int64",
    )

    for level, keys, min_effective_n in _HIERARCHY_SPECS:
        if any(key not in result.columns for key in keys):
            continue
        stats = _group_leave_one_out_evidence(
            result,
            keys,
            score,
            effective_n,
        )
        eligible = (
            chosen_level.eq("INSUFFICIENT")
            & stats["EffectiveN"].ge(min_effective_n)
            & stats["PeerTickers"].ge(HIERARCHICAL_MIN_PEER_TICKERS)
            & stats["EvidenceScore"].notna()
        )
        chosen_score.loc[eligible] = stats.loc[
            eligible,
            "EvidenceScore",
        ]
        chosen_nominal_n.loc[eligible] = stats.loc[
            eligible,
            "NominalN",
        ]
        chosen_n.loc[eligible] = stats.loc[
            eligible,
            "EffectiveN",
        ]
        chosen_kish.loc[eligible] = stats.loc[
            eligible,
            "KishPeers",
        ]
        chosen_tickers.loc[eligible] = (
            pd.to_numeric(
                stats.loc[eligible, "PeerTickers"],
                errors="coerce",
            )
            .fillna(0)
            .round(0)
            .astype("Int64")
        )
        chosen_level.loc[eligible] = level

    sufficient = chosen_level.ne("INSUFFICIENT")
    ratio = (
        chosen_nominal_n
        / chosen_n.replace(0.0, np.nan)
    ).replace([np.inf, -np.inf], np.nan)

    result["HierarchicalEvidenceScore"] = chosen_score.round(4)
    result["HierarchicalEvidenceNominalN"] = (
        chosen_nominal_n.round(4)
    )
    result["HierarchicalEvidenceEffectiveN"] = chosen_n.round(4)
    result["HierarchicalEvidenceKishPeers"] = chosen_kish.round(4)
    result["HierarchicalEvidencePeerTickers"] = chosen_tickers
    result["HierarchicalEvidenceInflationRatio"] = ratio.round(4)
    result["HierarchicalEvidenceLevel"] = chosen_level
    result["HierarchicalEvidenceStatus"] = np.where(
        sufficient,
        "DIAGNOSTIC_ONLY",
        "INSUFFICIENT",
    )
    result["HierarchicalEvidenceSelfExcluded"] = True
    result["HierarchicalEvidenceEffectiveNMethod"] = (
        "LOO_KISH_PEER_BREADTH_CAP_3"
    )
    result["HierarchicalEvidenceProductionApplied"] = False
    result["HierarchicalEvidenceVersion"] = (
        HIERARCHICAL_EVIDENCE_VERSION
    )
    return result


def annotate_reliability(frame: pd.DataFrame) -> pd.DataFrame:
    result = annotate_shadow_model(frame)
    result = annotate_hierarchical_evidence(result)
    return result


def _summary_payload(backtest_summary: Any) -> dict[str, Any]:
    if backtest_summary is None:
        return {}
    serializer = getattr(backtest_summary, "to_dict", None)
    if callable(serializer):
        try:
            value = serializer()
        except (ArithmeticError, KeyError, TypeError, ValueError):
            value = {}
        if isinstance(value, dict):
            return value
    if isinstance(backtest_summary, dict):
        return backtest_summary
    return {}


def reliability_summary(
    frame: pd.DataFrame,
    backtest_summary: Any = None,
) -> dict[str, object]:
    challenger_delta = _numeric(
        frame,
        "ChallengerAxisRankDelta",
    )
    hierarchy_status = _text(
        frame,
        "HierarchicalEvidenceStatus",
    )
    inflation = _numeric(
        frame,
        "HierarchicalEvidenceInflationRatio",
    )
    payload = _summary_payload(backtest_summary)
    pit_raw = payload.get("point_in_time_universe", {})
    pit = pit_raw if isinstance(pit_raw, dict) else {}
    verified = int(
        pd.to_numeric(
            pd.Series(
                [payload.get("universe_verified_samples", 0)]
            ),
            errors="coerce",
        ).fillna(0).iloc[0]
    )
    unverified = int(
        pd.to_numeric(
            pd.Series(
                [payload.get("universe_unverified_samples", 0)]
            ),
            errors="coerce",
        ).fillna(0).iloc[0]
    )
    ranking_status = str(
        payload.get("ranking_calibration_status", "") or ""
    )
    survivorship_complete = bool(
        pit.get("survivorship_complete", False)
    )
    peer_loo_verified = bool(
        payload.get(
            "peer_leave_one_out_verified",
            False,
        )
    )

    return {
        "version": RELIABILITY_FOUNDATION_VERSION,
        "production_contract": PRODUCTION_CONTRACT.to_dict(),
        "challenger_contract": CHALLENGER_CONTRACT.to_dict(),
        "rows": len(frame),
        "challenger_production_applied": False,
        "challenger_rows_scored": int(
            _numeric(
                frame,
                "ChallengerAxisScoreDiagnostic",
            ).notna().sum()
        ),
        "challenger_median_abs_rank_delta": (
            float(challenger_delta.abs().median())
            if challenger_delta.notna().any()
            else None
        ),
        "point_in_time": {
            "universe_available": bool(pit.get("available", False)),
            "universe_version": str(pit.get("version", "") or ""),
            "verified_model_samples": verified,
            "unverified_model_samples": unverified,
            "ranking_calibration_status": ranking_status,
            "survivorship_control": str(
                pit.get("survivorship_control", "") or ""
            ),
            "survivorship_complete": survivorship_complete,
            "peer_leave_one_out_verified": peer_loo_verified,
            "production_peer_ready": bool(
                bool(pit.get("available", False))
                and verified > 0
                and unverified == 0
                and ranking_status
                == "ENABLED_VERIFIED_POINT_IN_TIME"
                and survivorship_complete
                and peer_loo_verified
            ),
        },
        "hierarchical_evidence": {
            "version": HIERARCHICAL_EVIDENCE_VERSION,
            "production_applied": False,
            "self_excluded": True,
            "effective_n_method": "LOO_KISH_PEER_BREADTH_CAP_3",
            "max_information_per_effective_peer": (
                HIERARCHICAL_MAX_INFORMATION_PER_EFFECTIVE_PEER
            ),
            "diagnostic_rows": int(
                hierarchy_status.eq("DIAGNOSTIC_ONLY").sum()
            ),
            "insufficient_rows": int(
                hierarchy_status.eq("INSUFFICIENT").sum()
            ),
            "median_nominal_to_effective_ratio": (
                float(inflation.dropna().median())
                if inflation.notna().any()
                else None
            ),
            "max_nominal_to_effective_ratio": (
                float(inflation.dropna().max())
                if inflation.notna().any()
                else None
            ),
        },
    }


def _atomic_json(
    path: Path,
    payload: dict[str, object],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def install(core: Any) -> None:
    """Install as the final post-ranking diagnostic layer."""
    global _INSTALLED, _ORIGINAL_APPLY
    if _INSTALLED or getattr(
        core,
        "_RELIABILITY_FOUNDATION_V103_INSTALLED",
        False,
    ):
        return

    original = getattr(
        core,
        "_legacy_apply_backtest_ranking",
        None,
    )
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

        frame = pd.read_csv(
            path,
            encoding="utf-8-sig",
            low_memory=False,
        )
        annotated = annotate_reliability(frame)

        from report import (
            _atomic_write_csv,
            _atomic_write_parquet,
            refresh_candidate_exports,
        )

        _atomic_write_csv(annotated, path)
        refresh_candidate_exports(
            annotated,
            output_dir=core.OUTPUT_DIR,
        )
        _atomic_write_parquet(
            annotated,
            Path(core.OUTPUT_DIR) / "AllResults.parquet",
        )
        _atomic_json(
            Path(core.OUTPUT_DIR) / "ReliabilitySummary.json",
            reliability_summary(annotated, summary),
        )

    core._legacy_apply_backtest_ranking = (
        reliability_annotated_apply_backtest_ranking
    )
    core.RELIABILITY_FOUNDATION_VERSION = (
        RELIABILITY_FOUNDATION_VERSION
    )
    core.HIERARCHICAL_EVIDENCE_VERSION = (
        HIERARCHICAL_EVIDENCE_VERSION
    )
    core.MODEL_CONTRACT_VERSION = CONTRACT_VERSION
    core._RELIABILITY_FOUNDATION_V103_INSTALLED = True
    _INSTALLED = True
