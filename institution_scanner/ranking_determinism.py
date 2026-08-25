"""Deterministic tie-breaking for production and shadow rankings.

Scanner workers complete out of order by design. Exact score ties must therefore
never use DataFrame arrival position as a semantic tie-breaker. This module keeps
all score/state priorities unchanged and uses normalized ticker code only for the
last ordering key.
"""
from __future__ import annotations

from typing import Any, Final

import numpy as np
import pandas as pd

import ranking_architecture_v83 as _legacy

RANKING_ARCHITECTURE_VERSION = _legacy.RANKING_ARCHITECTURE_VERSION
RANKING_DETERMINISM_VERSION: Final = (
    "2026-08-25-v107-ticker-stable-exact-tie-ranking-v1"
)
_STATE_PRIORITY = {
    "READY": 0,
    "CAUTIOUS": 1,
    "OBSERVE": 2,
    "BLOCKED": 3,
}


def _text(frame: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    source = frame.get(column, pd.Series(default, index=frame.index, dtype=object))
    if not isinstance(source, pd.Series):
        source = pd.Series(source, index=frame.index)
    return source.fillna(default).astype(str).str.strip()


def _stable_ordinal_rank(
    score: pd.Series,
    group: pd.Series,
    ticker: pd.Series,
    *,
    bucket: pd.Series | None = None,
) -> pd.Series:
    """Return 1..N ordinal rank with ticker as the exact-tie key."""
    position = np.arange(len(score), dtype=np.int64)
    normalized_ticker = ticker.fillna("").astype(str).str.strip().str.upper()
    missing = normalized_ticker.eq("")
    if missing.any():
        normalized_ticker = normalized_ticker.copy()
        normalized_ticker.loc[missing] = [
            f"~MISSING-{value:012d}" for value in position[missing.to_numpy()]
        ]
    numeric = pd.to_numeric(score, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    working = pd.DataFrame(
        {
            "_position": position,
            "_group": group.fillna("ALL").astype(str).to_numpy(dtype=object),
            "_score": numeric.fillna(-np.inf).to_numpy(dtype=float),
            "_ticker": normalized_ticker.to_numpy(dtype=object),
        }
    )
    columns = ["_group"]
    ascending = [True]
    if bucket is not None:
        numeric_bucket = pd.to_numeric(bucket, errors="coerce").fillna(999).astype(int)
        working["_bucket"] = numeric_bucket.to_numpy(dtype=int)
        columns.append("_bucket")
        ascending.append(True)
    columns.extend(["_score", "_ticker", "_position"])
    ascending.extend([False, True, True])
    ordered = working.sort_values(
        columns,
        ascending=ascending,
        kind="mergesort",
    )
    ordered["_rank"] = ordered.groupby("_group", sort=False).cumcount() + 1
    output = np.empty(len(working), dtype=np.int64)
    output[ordered["_position"].to_numpy(dtype=np.int64)] = ordered[
        "_rank"
    ].to_numpy(dtype=np.int64)
    return pd.Series(output, index=score.index, dtype="Int64")


def stamp_layered_ranking(frame: pd.DataFrame) -> pd.DataFrame:
    """Run the canonical v83 layer, then make exact TradeRank ties reproducible."""
    result = _legacy.stamp_layered_ranking(frame)
    if result is None or result.empty:
        return result
    required = {"AlphaScore", "ResearchAssetClass", "ExecutionState", "Ticker"}
    if not required.issubset(result.columns):
        return result
    state = _text(result, "ExecutionState", "OBSERVE").str.upper()
    bucket = state.map(_STATE_PRIORITY).fillna(4).astype(int)
    result["TradeRank"] = _stable_ordinal_rank(
        pd.to_numeric(result["AlphaScore"], errors="coerce"),
        _text(result, "ResearchAssetClass", "ALL").replace("", "ALL"),
        _text(result, "Ticker"),
        bucket=bucket,
    )
    result["RankingDeterminismVersion"] = RANKING_DETERMINISM_VERSION
    return result


def install_reliability(module: Any) -> None:
    """Make shadow Champion/Challenger ordinal ranks permutation-invariant."""
    if getattr(module, "_RANKING_DETERMINISM_V107_INSTALLED", False):
        return
    original = getattr(module, "annotate_shadow_model", None)
    if not callable(original):
        return

    def stable_shadow_ranks(frame: pd.DataFrame) -> pd.DataFrame:
        result = original(frame)
        if result is None or result.empty or "Ticker" not in result.columns:
            return result
        asset = _text(result, "ResearchAssetClass", "ALL").replace("", "ALL")
        ticker = _text(result, "Ticker")
        champion = pd.to_numeric(
            result.get("ChampionAxisScoreDiagnostic"), errors="coerce"
        )
        challenger = pd.to_numeric(
            result.get("ChallengerAxisScoreDiagnostic"), errors="coerce"
        )
        champion_rank = _stable_ordinal_rank(champion, asset, ticker)
        challenger_rank = _stable_ordinal_rank(challenger, asset, ticker)
        result["ChampionAxisRankWithinAsset"] = champion_rank
        result["ChallengerAxisRankWithinAsset"] = challenger_rank
        result["ChallengerAxisRankDelta"] = (
            champion_rank.astype("Float64") - challenger_rank.astype("Float64")
        ).round(0).astype("Int64")
        result["RankingDeterminismVersion"] = RANKING_DETERMINISM_VERSION
        return result

    module.annotate_shadow_model = stable_shadow_ranks
    module.RANKING_DETERMINISM_VERSION = RANKING_DETERMINISM_VERSION
    module._RANKING_DETERMINISM_V107_INSTALLED = True
