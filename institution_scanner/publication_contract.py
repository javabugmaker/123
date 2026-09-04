"""Compact, stable contract for public candidate views.

The full ``AllResults`` frame is the audit artifact.  Public pages and small
downstream consumers should not depend on that 400+ column implementation
surface, nor repeat long legacy version strings on every candidate row.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

import pandas as pd

from .policy_manifest import decision_policy_hash
from .version_manifest import build_version_manifest

PUBLICATION_CONTRACT_VERSION: Final = (
    "2026-09-04-v114-compact-publication-contract-v1"
)
PUBLIC_CANDIDATE_COLUMNS: Final[tuple[str, ...]] = (
    "Ticker",
    "Name",
    "Sector",
    "Industry",
    "ETFTheme",
    "IsETF",
    "AssetType",
    "ModelClassification",
    "ThemeCluster",
    "Close",
    "AlphaScore",
    "FinalScore",
    "RankingScore",
    "OpportunityScore",
    "InstitutionalScore",
    "CandidateViewRank",
    "ResearchPoolRank",
    "OverallRank",
    "RankingEligibility",
    "ExecutionState",
    "EntrySignal",
    "SignalStatus",
    "SignalDays",
    "DataAsOf",
    "DataTradingAgeDays",
    "DataFreshnessStatus",
    "EntryZone",
    "BreakoutBuyPrice",
    "StopLoss",
    "ProjectedTarget",
    "RewardRiskRatio",
    "TradeReadinessReason",
    "RankingReason",
    "DecisionReason",
    "QualityLayerStatus",
    "QualityLayerReason",
    "BacktestEligibleForRanking",
    "BacktestEffectiveSamples",
    "BacktestStatus",
    "BacktestConfidenceTier",
    "GlobalCalibrationGovernanceStatus",
    "HierarchicalEvidenceStatus",
    "HierarchicalEvidenceScore",
    "HierarchicalEvidenceEffectiveN",
    "RunId",
    "RankingRunId",
    "ModelWeightSignature",
    "DecisionPolicySignature",
    "ResearchDiversityPenalty",
)
VIEW_FLAGS: Final[tuple[tuple[str, str], ...]] = (
    ("InMixed", "MIXED_RESEARCH"),
    ("InStocks", "STOCK_RESEARCH"),
    ("InETF", "ETF_RESEARCH"),
    ("InTradeReady", "TRADE_READY"),
    ("InOpportunity", "OPPORTUNITY"),
    ("InBreakout", "CONFIRMED_BREAKOUT"),
    ("InEntry", "ENTRY_SETUP"),
    ("InSustained", "SUSTAINED_SIGNAL"),
    ("InValueTrapRisk", "VALUE_TRAP_RISK"),
)


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return str(value).strip()


def _first_present(frame: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    for name in names:
        if name in frame.columns:
            return frame[name]
    return pd.Series("", index=frame.index, dtype="object")


def _ticker_set(frame: pd.DataFrame | None) -> set[str]:
    if frame is None or frame.empty or "Ticker" not in frame.columns:
        return set()
    return {
        _clean_text(value).upper()
        for value in frame["Ticker"]
        if _clean_text(value)
    }


def build_public_candidates(
    mixed: pd.DataFrame,
    *,
    views: Mapping[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    """Project a mixed candidate list onto the stable public schema."""
    working = mixed.copy().reset_index(drop=True)
    if "AlphaScore" not in working.columns:
        working["AlphaScore"] = _first_present(
            working, ("FinalScore", "CompositeScore", "Score")
        )
    if "ExecutionState" not in working.columns:
        working["ExecutionState"] = _first_present(
            working, ("DecisionState", "RankingEligibility")
        )
    if "ProjectedTarget" not in working.columns:
        working["ProjectedTarget"] = _first_present(
            working, ("ProfitTarget", "TargetPrice")
        )

    compact = working.reindex(columns=PUBLIC_CANDIDATE_COLUMNS).copy()
    compact["Ticker"] = compact["Ticker"].map(_clean_text).str.upper()
    compact = compact.loc[compact["Ticker"].ne("")].reset_index(drop=True)
    if compact["CandidateViewRank"].isna().all():
        compact["CandidateViewRank"] = range(1, len(compact) + 1)

    available = views or {}
    for column, view_name in VIEW_FLAGS:
        tickers = _ticker_set(available.get(view_name))
        compact[column] = compact["Ticker"].isin(tickers)
    if not compact.empty and not compact["InMixed"].any():
        compact["InMixed"] = True
    return compact.loc[:, [*PUBLIC_CANDIDATE_COLUMNS, *(item[0] for item in VIEW_FLAGS)]]


def _mode(frame: pd.DataFrame, columns: tuple[str, ...]) -> str:
    for column in columns:
        if column not in frame.columns:
            continue
        values = frame[column].map(_clean_text)
        values = values.loc[values.ne("")]
        if not values.empty:
            return str(values.value_counts().index[0])
    return ""


def _state_counts(frame: pd.DataFrame) -> dict[str, int]:
    if "ExecutionState" not in frame.columns:
        return {}
    counts = frame["ExecutionState"].map(_clean_text).str.upper().value_counts()
    return {str(key): int(value) for key, value in counts.items() if key}


def build_publication_manifest(
    candidates: pd.DataFrame,
    *,
    source_rows: pd.DataFrame | None = None,
) -> dict[str, object]:
    """Build run-level provenance once instead of repeating it per row."""
    source = source_rows if source_rows is not None else candidates
    manifest = build_version_manifest()
    state_counts = _state_counts(candidates)
    is_etf = candidates.get(
        "IsETF", pd.Series(False, index=candidates.index)
    ).astype(str).str.strip().str.lower().isin({"true", "1", "yes", "y", "是"})
    asset = candidates.get(
        "AssetType", pd.Series("", index=candidates.index)
    ).astype(str).str.strip().str.lower()
    is_etf |= asset.eq("etf")
    view_counts = {
        view_name: int(candidates[column].fillna(False).astype(bool).sum())
        for column, view_name in VIEW_FLAGS
        if column in candidates.columns
    }

    import config

    return {
        "schema": PUBLICATION_CONTRACT_VERSION,
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(
            timespec="seconds"
        ),
        "report_date": _mode(source, ("DataAsOf", "TradeDate")),
        "run": {
            "run_id": _mode(source, ("RunId",)),
            "ranking_run_id": _mode(source, ("RankingRunId",)),
        },
        "market": {
            "regime": _mode(
                source,
                ("MarketRegime", "MarketState", "MarketRegimeCode"),
            ),
            "freshness": _mode(source, ("DataFreshnessStatus",)),
        },
        "execution": {
            "assumed_order_notional_cny": float(
                getattr(config, "LIVE_EXECUTION_ASSUMED_NOTIONAL_CNY", 50_000.0)
            ),
            "state_counts": state_counts,
        },
        "candidates": {
            "rows": len(candidates),
            "stocks": int((~is_etf).sum()),
            "etfs": int(is_etf.sum()),
            "view_counts": view_counts,
        },
        "policy": {
            "decision_policy_hash": decision_policy_hash(config),
        },
        "versions": manifest,
        "artifacts": {
            "audit": "AllResults.parquet",
            "decision": "DecisionResults.csv",
            "public_candidates": "PublicCandidates.csv",
        },
    }


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def write_publication_contract(
    mixed: pd.DataFrame,
    *,
    destination: Path,
    views: Mapping[str, pd.DataFrame] | None = None,
    source_rows: pd.DataFrame | None = None,
) -> tuple[Path, Path]:
    """Materialize the compact candidate CSV and its run-level manifest."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    candidates = build_public_candidates(mixed, views=views)
    candidate_path = destination / "PublicCandidates.csv"
    temporary = candidate_path.with_name(f".{candidate_path.name}.tmp")
    candidates.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, candidate_path)

    manifest_path = destination / "PublicationManifest.json"
    _atomic_write_json(
        manifest_path,
        build_publication_manifest(candidates, source_rows=source_rows),
    )
    return candidate_path, manifest_path
