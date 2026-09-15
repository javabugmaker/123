"""Candidate-selection helpers extracted from ``report_core``.

``report_core`` is 104,909 bytes against a 105,000 byte budget -- 91 bytes of
headroom, about two lines.  These 11 functions are the dependency closure of
``_apply_research_policy`` / ``_ensure_diversity_columns`` /
``_diversify_ranked_candidates`` / ``_institutional_tier`` that
``tests/recon_extraction_targets.py`` reports as self-consistent: it depends on
nothing an overlay rebinds, and nothing outside itself, so it can move as a
unit without creating an import cycle.

They were extracted under the golden equivalence gate in
``tests/test_report_core_golden.py``: the fixture froze their behaviour *before*
the move and still matches *after* it.

Three rules shaped what could come across:

* **Nothing here may be rebound by an overlay.**  ``report.py`` rebinds
  ``_results_to_dataframe``, ``export_all`` and ``DECISION_RESULT_COLUMNS`` on
  ``report_core`` at import time, and ``report_determinism`` rebinds
  ``_rankable_results``.  None of those are in this cluster -- ``_rankable_results``
  is captured as a canary precisely so the gate keeps proving that.
* **This module must not import ``report_core``.**  ``report_core`` imports back
  from here; the reverse edge would be a cycle.
* **The tier thresholds are read through the ``config`` module object, not
  imported by value.**  ``score.py`` calls
  ``score_threshold_migration_v95.install(config)`` at import time, rewriting
  ``INSTITUTIONAL_TIER_A/B/C_SCORE`` from 35.0/30.0/25.0 to
  36.0825/30.9278/25.7732.  ``report_core`` sees the migrated values only
  because its ``from analytics import ...`` (line 25) pulls in ``score`` before
  its ``from config import ...`` (line 32) runs -- an accident of import order
  that this module would not inherit.  Binding them by value here would freeze
  whichever snapshot happened to be current when this module was first imported,
  changing every tier decision by up to 1.08 score points while every other
  captured value stayed identical.  The corpus carries three probes inside the
  migration gaps; see ``MIGRATION_PROBES`` in ``tests/golden_report_core.py``.

``ScanResult`` is imported under ``TYPE_CHECKING`` only.  It is a type
annotation, and importing ``scanner`` for real would pull ``scanner_core`` ->
``analytics`` -> ``report_core`` -> back into this module while it is still
being initialised.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

# Read at call time, never bound by value -- see the module docstring.  The
# remaining config constants are not touched by any overlay (verified: identical
# in ``config`` and ``config_core``, before and after assembly), so they are
# imported normally.
import config as _config
from classification import (
    ETF_CANONICAL_TRACKING_KEYS,
    etf_theme_key,
    etf_tracking_key,
    theme_cluster,
)
from config import (
    ETF_THEME_MAX_PER_TOP_LIST,
    ETF_TRACKING_MAX_PER_TOP_LIST,
    INSTITUTIONAL_TIER_TRAP_LABEL,
    INSTITUTIONAL_TIER_WAIT_LABEL,
    STOCK_INDUSTRY_MAX_PER_TOP_LIST,
    THEME_CLUSTER_SOFT_PENALTY,
    VALUE_TRAP_RISK_THRESHOLD,
)
from institution_scanner._common import _truthy
from research_policy_v87 import vectorized_etf_research_policy

if TYPE_CHECKING:
    from scanner import ScanResult


def _institutional_tier(result: ScanResult) -> str:
    score = (
        float(result.institutional_score)
        if np.isfinite(result.institutional_score)
        else float(result.final_score)
        if np.isfinite(result.final_score)
        else float(result.score.total)
    )
    volume_confirmed = bool(
        result.filter_details.get("volume_accumulation", False)
    ) or result.score.volume >= 15
    quality_failed = (
        not result.is_etf
        and result.quality_data_available
        and not result.quality_gate
    )
    if (
        score > _config.INSTITUTIONAL_TIER_A_SCORE
        and 0 <= result.signal_recency_days <= 20
        and volume_confirmed
    ):
        tier = "A级机构启动"
    elif _config.INSTITUTIONAL_TIER_B_SCORE <= score < _config.INSTITUTIONAL_TIER_A_SCORE:
        tier = "B级观察"
    elif score >= _config.INSTITUTIONAL_TIER_C_SCORE:
        tier = "C级价值观察"
    else:
        tier = INSTITUTIONAL_TIER_WAIT_LABEL
    if quality_failed:
        tier = {
            "A级机构启动": "B级观察",
            "B级观察": "C级价值观察",
            "C级价值观察": "C级价值观察",
        }.get(tier, INSTITUTIONAL_TIER_WAIT_LABEL)
    value_trap_risk = float(result.value_trap_risk)
    if (
        not result.is_etf
        and np.isfinite(value_trap_risk)
        and value_trap_risk >= VALUE_TRAP_RISK_THRESHOLD
    ):
        return INSTITUTIONAL_TIER_TRAP_LABEL
    return tier

_NULLISH_TEXT = frozenset({"", "nan", "none", "nat", "<na>"})
_TRUTHY_TEXT = frozenset({"true", "1", "yes", "y", "是"})


def _policy_column(
    frame: pd.DataFrame,
    column: str,
    default: object,
) -> pd.Series:
    """Return a position-indexed column so duplicate source indexes are safe."""
    if column in frame.columns:
        return pd.Series(frame[column].to_numpy(copy=False), copy=False)
    return pd.Series(np.full(len(frame), default, dtype=object), copy=False)


def _policy_text(values: pd.Series) -> pd.Series:
    """Vectorized counterpart of classification.safe_text/_clean_group_key."""
    text = values.astype("string").fillna("").str.strip()
    return text.mask(text.str.lower().isin(_NULLISH_TEXT), "")


def _policy_truthy(values: pd.Series) -> np.ndarray:
    return (
        values.astype("string")
        .fillna("")
        .str.strip()
        .str.lower()
        .isin(_TRUTHY_TEXT)
        .to_numpy(dtype=bool)
    )


def _vectorized_etf_research_policy(
    frame: pd.DataFrame,
    is_etf: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Compatibility entry point for the shared vectorized v87 policy."""
    return vectorized_etf_research_policy(frame, is_etf)


def _apply_research_policy(frame: pd.DataFrame) -> pd.DataFrame:
    """Mark non-directional ETF products before any TopN candidate ranking."""
    working = frame.copy()
    if working.empty:
        working["ResearchEligible"] = pd.Series(dtype=bool)
        working["ResearchExclusionReason"] = pd.Series(dtype="object")
        return working

    asset_type = _policy_text(_policy_column(working, "AssetType", ""))
    is_etf = _policy_truthy(_policy_column(working, "IsETF", False)) | asset_type.str.lower().eq(
        "etf"
    ).to_numpy(dtype=bool)
    etf_eligible, etf_reasons = _vectorized_etf_research_policy(working, is_etf)

    hard_value = _policy_column(working, "HardGatePassed", None)
    hard_text = hard_value.astype("string").fillna("").str.strip()
    hard_missing = hard_value.isna().to_numpy(dtype=bool) | hard_text.eq("").to_numpy(
        dtype=bool
    )
    universe_value = _policy_column(working, "UniverseEligible", True)
    effective_hard_value = hard_value.astype(object).copy()
    effective_hard_value.iloc[np.flatnonzero(hard_missing)] = universe_value.iloc[
        np.flatnonzero(hard_missing)
    ].to_numpy(copy=False)
    hard_ok = _policy_truthy(effective_hard_value)

    failed_names = _policy_text(_policy_column(working, "HardGateFailedNames", ""))
    failed_blank = failed_names.eq("").to_numpy(dtype=bool)
    legacy_combined_pass = (
        ~hard_ok
        & failed_blank
        & _policy_truthy(_policy_column(working, "PassedFilters", False))
    )
    # ``Series.to_numpy`` may return a read-only view when pandas
    # Copy-on-Write is enabled (and under pandas versions where CoW is the
    # default). Keep this as a vectorized, non-mutating OR so report export is
    # portable across pandas runtime modes.
    hard_ok = hard_ok | legacy_combined_pass

    hard_failed = ~hard_ok
    hard_reason = pd.Series(
        np.where(
            failed_blank,
            "硬准入条件未通过",
            "硬准入失败：" + failed_names.astype(str),
        ),
        dtype="string",
    )
    reason = pd.Series(etf_reasons, dtype="string")
    has_etf_reason = reason.ne("").to_numpy(dtype=bool)
    reason = reason.mask(hard_failed & ~has_etf_reason, hard_reason)
    reason = reason.mask(
        hard_failed & has_etf_reason,
        reason + "；" + hard_reason,
    )

    working["ResearchEligible"] = etf_eligible & hard_ok
    working["ResearchExclusionReason"] = reason.fillna("").to_numpy(dtype=object)
    return working


def _clean_group_key(value: object) -> str:
    """Normalize nullable categorical keys before diversity accounting."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "nat", "<na>"}:
        return ""
    return text


def _etf_theme_key(row: pd.Series) -> str:
    if not (_truthy(row.get("IsETF", False)) or str(row.get("AssetType", "")).strip().lower() == "etf"):
        return ""
    return etf_theme_key(
        name=row.get("Name", ""),
        industry=row.get("Industry", ""),
        sector=row.get("Sector", ""),
        ticker=row.get("Ticker", ""),
    )


def _ensure_diversity_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Populate expensive ETF/theme provenance once and preserve existing values."""
    working = frame.copy()
    asset_type = working.get(
        "AssetType", pd.Series("", index=working.index)
    ).fillna("").astype(str).str.strip().str.lower()
    is_etf = working.get(
        "IsETF", pd.Series(False, index=working.index)
    ).map(_truthy) | asset_type.eq("etf")

    if "ETFTheme" not in working:
        working["ETFTheme"] = ""
    working["ETFTheme"] = working["ETFTheme"].astype("object")
    missing_theme = is_etf & working["ETFTheme"].fillna("").astype(str).str.strip().eq("")
    if missing_theme.any():
        working.loc[missing_theme, "ETFTheme"] = working.loc[missing_theme].apply(
            _etf_theme_key, axis=1
        )

    if "ETFTrackingKey" not in working:
        working["ETFTrackingKey"] = ""
    working["ETFTrackingKey"] = working["ETFTrackingKey"].astype("object")
    if is_etf.any():
        inferred_tracking = working.loc[is_etf].apply(
            lambda row: etf_tracking_key(
                name=row.get("Name", ""),
                industry=row.get("Industry", ""),
                sector=row.get("Sector", ""),
                ticker=row.get("Ticker", ""),
            ),
            axis=1,
        )
        existing_tracking = (
            working.loc[is_etf, "ETFTrackingKey"]
            .fillna("")
            .astype(str)
            .str.strip()
        )
        replace_tracking = existing_tracking.eq("") | inferred_tracking.isin(
            ETF_CANONICAL_TRACKING_KEYS
        )
        replace_index = replace_tracking.index[replace_tracking]
        working.loc[replace_index, "ETFTrackingKey"] = inferred_tracking.loc[
            replace_index
        ]

    if "ThemeCluster" not in working:
        working["ThemeCluster"] = ""
    working["ThemeCluster"] = working["ThemeCluster"].astype("object")
    missing_cluster = working["ThemeCluster"].fillna("").astype(str).str.strip().eq("")
    if missing_cluster.any():
        working.loc[missing_cluster, "ThemeCluster"] = working.loc[missing_cluster].apply(
            lambda row: theme_cluster(
                is_etf=_truthy(row.get("IsETF", False))
                or str(row.get("AssetType", "")).strip().lower() == "etf",
                name=row.get("Name", ""),
                industry=row.get("Industry", ""),
                sector=row.get("Sector", ""),
                classification=row.get("ModelClassification", ""),
                ticker=row.get("Ticker", ""),
            ),
            axis=1,
        )
    return working


def _diversify_ranked_candidates(
    frame: pd.DataFrame,
    limit: int,
    max_per_theme: int = ETF_THEME_MAX_PER_TOP_LIST,
    max_per_stock_industry: int = STOCK_INDUSTRY_MAX_PER_TOP_LIST,
    diversity_prepared: bool = False,
) -> pd.DataFrame:
    if frame.empty or limit <= 0:
        return frame.head(0).copy()
    # refresh_candidate_exports() prepares these columns once for the full wide
    # frame.  Reusing them avoids repeated 200+ column copies for each view.
    working = frame if diversity_prepared else _ensure_diversity_columns(frame)
    row_count = len(working)
    asset_type = working.get(
        "AssetType", pd.Series("", index=working.index)
    ).fillna("").astype(str).str.strip().str.lower()
    is_etf = (
        working.get("IsETF", pd.Series(False, index=working.index)).map(_truthy)
        | asset_type.eq("etf")
    ).to_numpy(dtype=bool)

    def normalized_column(name: str) -> pd.Series:
        return working.get(
            name, pd.Series("", index=working.index)
        ).map(_clean_group_key)

    theme = normalized_column("ETFTheme").where(is_etf, "")
    tracking = normalized_column("ETFTrackingKey").where(is_etf, "")
    classification = normalized_column("ModelClassification")
    industry = normalized_column("Industry")
    sector = normalized_column("Sector")
    classification = classification.where(classification.ne(""), industry)
    classification = classification.where(classification.ne(""), sector)
    classification = classification.where(~is_etf, "")
    cluster = normalized_column("ThemeCluster")

    def factorize_groups(values: pd.Series) -> tuple[np.ndarray, np.ndarray]:
        codes, uniques = pd.factorize(values.where(values.ne("")), sort=False)
        return codes.astype(np.intp, copy=False), np.zeros(len(uniques), dtype=np.int64)

    theme_codes, theme_counts = factorize_groups(theme)
    tracking_codes, tracking_counts = factorize_groups(tracking)
    classification_codes, classification_counts = factorize_groups(classification)
    cluster_codes, cluster_counts = factorize_groups(cluster)

    rank_score = pd.to_numeric(
        working.get("RankingScore", working.get("CrossAssetScore", pd.Series(0.0, index=working.index))),
        errors="coerce",
    ).fillna(0.0).to_numpy(dtype=float)
    risk_filtered = working.get(
        "RankingEligibility", pd.Series("观察", index=working.index)
    ).fillna("观察").astype(str).eq("风险过滤").to_numpy(dtype=bool)
    risk_bucket_penalty = risk_filtered.astype(float) * 1_000_000_000.0

    active = np.ones(row_count, dtype=bool)
    selected_positions: list[int] = []
    selected_penalties: list[float] = []
    tracking_limit = max(1, int(ETF_TRACKING_MAX_PER_TOP_LIST))
    theme_limit = max(1, int(max_per_theme))
    stock_industry_limit = max(1, int(max_per_stock_industry))
    cluster_step = float(THEME_CLUSTER_SOFT_PENALTY)

    for _ in range(min(int(limit), row_count)):
        eligible = active.copy()

        grouped = is_etf & (tracking_codes >= 0)
        eligible[grouped] &= (
            tracking_counts[tracking_codes[grouped]] < tracking_limit
        )
        grouped = is_etf & (theme_codes >= 0)
        eligible[grouped] &= theme_counts[theme_codes[grouped]] < theme_limit
        grouped = (~is_etf) & (classification_codes >= 0)
        eligible[grouped] &= (
            classification_counts[classification_codes[grouped]]
            < stock_industry_limit
        )

        penalties = np.ones(row_count, dtype=float)
        grouped = cluster_codes >= 0
        penalties[grouped] = np.maximum(
            0.70,
            1.0 - cluster_step * cluster_counts[cluster_codes[grouped]],
        )
        values = rank_score * penalties - risk_bucket_penalty
        values[~eligible] = -np.inf
        best_position = int(np.argmax(values))
        if np.isneginf(values[best_position]):
            break

        active[best_position] = False
        selected_positions.append(best_position)
        selected_penalties.append(round(float(penalties[best_position]), 4))
        for codes, counts in (
            (theme_codes, theme_counts),
            (tracking_codes, tracking_counts),
            (classification_codes, classification_counts),
            (cluster_codes, cluster_counts),
        ):
            code = int(codes[best_position])
            if code >= 0:
                counts[code] += 1

    result = working.iloc[selected_positions].copy().reset_index(drop=True)
    result["ResearchDiversityPenalty"] = selected_penalties
    result["ResearchPoolRank"] = np.arange(1, len(result) + 1)
    return result
