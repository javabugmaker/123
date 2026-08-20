"""Full-universe perturbation audit for InstitutionScanner ranking semantics.

This command is intentionally diagnostic. It reads the canonical
``output/AllResults.csv`` and measures how much each already-exported ranking
factor reshapes the cross-section. It never writes model parameters and it
never re-ranks a Top50 subset as if that subset were the universe.

Usage:
    python model_audit.py
    python model_audit.py --input output/AllResults.csv --output output/audit

Outputs:
    ranking_audit.json
    ranking_scenarios.csv
    ranking_top_movers.csv
    threshold_exposure.csv

Thresholds that require re-running raw feature/entry logic are reported as
*exposure diagnostics* rather than being counterfactually fabricated from an
already-scored CSV.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config import (
    BREAKOUT_CONFIRM_MIN_VOLUME_RATIO,
    CHASE_RISK_HIGH_THRESHOLD,
    CHASE_RISK_RSI_HARD,
    CHASE_RISK_RSI_START,
    ENTRY_SIGNAL_MULTIPLIERS,
    INSTITUTIONAL_TIER_A_SCORE,
    INSTITUTIONAL_TIER_B_SCORE,
    INSTITUTIONAL_TIER_C_SCORE,
    OUTPUT_DIR,
    QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE,
    TRADE_READY_MAX_STOP_DISTANCE_PCT,
    TRADE_READY_MIN_REWARD_RISK,
    VALUE_TRAP_HARD_RISK_THRESHOLD,
    VALUE_TRAP_RISK_THRESHOLD,
)
from ranking_provenance_v82 import stamp_ranking_decision_provenance

AUDIT_VERSION = "2026-08-21-v82-decision-tier-separation-v2"
_QUALITY_READINESS_FACTOR = 0.82
_LIFECYCLE_FAILED_READINESS_FACTOR = 0.70
_FILTER_FAILED_READINESS_FACTOR = 0.90


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    score: pd.Series


def _number(
    frame: pd.DataFrame,
    column: str,
    default: float = 1.0,
) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=float)
    return (
        pd.to_numeric(frame[column], errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .fillna(default)
        .astype(float)
    )


def _bool(frame: pd.DataFrame, column: str, default: bool = False) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=bool)
    values = frame[column]
    if pd.api.types.is_bool_dtype(values):
        return values.fillna(default).astype(bool)
    return (
        values.fillna(default)
        .astype(str)
        .str.strip()
        .str.lower()
        .isin({"true", "1", "yes", "y", "是"})
    )


def _text(frame: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype=object)
    return frame[column].fillna(default).astype(str).str.strip()


def _asset_group(frame: pd.DataFrame) -> pd.Series:
    is_etf = _bool(frame, "IsETF") | _text(frame, "AssetType").str.lower().eq("etf")
    return pd.Series(np.where(is_etf, "ETF", "STOCK"), index=frame.index)


def _entry_factor(frame: pd.DataFrame) -> pd.Series:
    signal = _text(frame, "EntrySignal", "AVOID").str.upper()
    mapping = {
        str(key).upper(): float(value)
        for key, value in ENTRY_SIGNAL_MULTIPLIERS.items()
    }
    return signal.map(mapping).fillna(float(mapping.get("AVOID", 0.50))).clip(
        1e-9, 1.0
    )


def _recency_multiplier(frame: pd.DataFrame) -> pd.Series:
    factor = _number(frame, "SignalRecencyFactor", 1.0).clip(0.7, 1.0)
    return (0.8 + 0.2 * factor).clip(1e-9, 1.0)


def _safe_divide(score: pd.Series, factor: pd.Series) -> pd.Series:
    factor = factor.astype(float).clip(lower=1e-9)
    return (score.astype(float) / factor).replace([np.inf, -np.inf], np.nan)


def _quality_action_block(frame: pd.DataFrame) -> pd.Series:
    is_etf = _asset_group(frame).eq("ETF")
    applicable = (
        _bool(frame, "QualityApplicable", True)
        if "QualityApplicable" in frame.columns
        else ~is_etf
    ) & ~is_etf
    completeness = _number(frame, "QualityDataCompleteness", 0.0).clip(0.0, 1.0)
    hard_complete = _bool(frame, "QualityHardDataComplete", True)
    gate = _bool(frame, "QualityGate", True)
    return applicable & (
        completeness.lt(float(QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE))
        | ~hard_complete
        | ~gate
    )


def _filter_failure(frame: pd.DataFrame) -> pd.Series:
    passed = _bool(frame, "PassedFilters", True)
    override = _bool(frame, "FilterOverrideApplied", False)
    return ~passed & ~override


def _lifecycle_failure(frame: pd.DataFrame) -> pd.Series:
    return _text(frame, "SignalStatus").str.upper().eq("FAILED")


def build_scenarios(frame: pd.DataFrame) -> tuple[list[Scenario], pd.DataFrame]:
    ranking = _number(frame, "RankingScore", np.nan)
    base = _number(frame, "CrossAssetScore", np.nan)
    entry = _entry_factor(frame)
    hard = _number(frame, "HardRiskPenalty", 1.0).clip(1e-9, 1.0)
    chase = _number(frame, "ChaseRiskFactor", 1.0).clip(1e-9, 1.0)
    data = _number(frame, "DataConfidenceFactor", 1.0).clip(1e-9, 1.0)
    recency = _recency_multiplier(frame)
    readiness = _number(frame, "ReadinessPenaltyFactor", 1.0).clip(1e-9, 1.0)

    provenance = stamp_ranking_decision_provenance(frame)
    decision = _number(provenance, "RankingDecisionFactor", 1.0).clip(1e-9, 1.0)
    decision_distance = _number(
        provenance, "RankingDecisionInferenceAbsError", np.nan
    )
    tier_reconciliation = _number(
        provenance, "RankingTierReconciliationFactor", 1.0
    ).clip(1e-9, 1.0)

    quality_mask = _quality_action_block(frame)
    filter_mask = _filter_failure(frame)
    lifecycle_mask = _lifecycle_failure(frame)

    quality_readiness = readiness.copy()
    quality_readiness.loc[quality_mask] = (
        quality_readiness.loc[quality_mask] / _QUALITY_READINESS_FACTOR
    ).clip(upper=1.0)

    filter_lifecycle_readiness = readiness.copy()
    filter_lifecycle_readiness.loc[filter_mask] = (
        filter_lifecycle_readiness.loc[filter_mask] / _FILTER_FAILED_READINESS_FACTOR
    ).clip(upper=1.0)
    filter_lifecycle_readiness.loc[lifecycle_mask] = (
        filter_lifecycle_readiness.loc[lifecycle_mask]
        / _LIFECYCLE_FAILED_READINESS_FACTOR
    ).clip(upper=1.0)

    scenarios = [
        Scenario("baseline", "Current exported RankingScore", ranking),
        Scenario(
            "no_readiness",
            "Set ReadinessPenaltyFactor to 1",
            _safe_divide(ranking, readiness),
        ),
        Scenario(
            "no_decision",
            "Remove ranking-time Decision factor; retain tier reconciliation",
            _safe_divide(ranking, decision),
        ),
        Scenario(
            "no_readiness_or_decision",
            "Remove Readiness and ranking-time Decision; retain tier reconciliation",
            _safe_divide(_safe_divide(ranking, readiness), decision),
        ),
        Scenario(
            "no_entry",
            "Set EntrySignal multiplier to 1",
            _safe_divide(ranking, entry),
        ),
        Scenario(
            "no_hard_risk",
            "Set HardRiskPenalty to 1",
            _safe_divide(ranking, hard),
        ),
        Scenario(
            "no_chase",
            "Set ChaseRiskFactor to 1",
            _safe_divide(ranking, chase),
        ),
        Scenario(
            "no_data_confidence",
            "Set DataConfidenceFactor to 1",
            _safe_divide(ranking, data),
        ),
        Scenario(
            "no_recency",
            "Remove lifecycle recency multiplier",
            _safe_divide(ranking, recency),
        ),
        Scenario(
            "neutralize_quality_decision_overlap",
            "Undo the quality readiness leg while retaining DecisionState",
            _safe_divide(ranking, readiness) * quality_readiness,
        ),
        Scenario(
            "neutralize_filter_lifecycle_decision_overlap",
            "Undo filter/lifecycle readiness legs while retaining DecisionState",
            _safe_divide(ranking, readiness) * filter_lifecycle_readiness,
        ),
    ]

    diagnostics = pd.DataFrame(
        {
            "Ticker": _text(frame, "Ticker"),
            "AssetGroup": _asset_group(frame),
            "RankingScore": ranking,
            "CrossAssetScore": base,
            "EntryFactor": entry,
            "HardRiskFactor": hard,
            "ChaseRiskFactor": chase,
            "DataConfidenceFactor": data,
            "RecencyMultiplier": recency,
            "ReadinessPenaltyFactor": readiness,
            "InferredDecisionFactor": decision,
            "DecisionInferenceAbsError": decision_distance,
            "RankingDecisionStateAtScore": _text(
                provenance, "RankingDecisionStateAtScore", "UNKNOWN"
            ),
            "RankingTierReconciliationFactor": tier_reconciliation,
            "RankingTierReconciliationState": _text(
                provenance, "RankingTierReconciliationState", "NONE"
            ),
            "ExportedDecisionState": _text(frame, "DecisionState"),
            "QualityActionBlock": quality_mask,
            "FilterFailure": filter_mask,
            "LifecycleFailure": lifecycle_mask,
        }
    )
    reconstructed = (
        base
        * entry
        * hard
        * chase
        * data
        * recency
        * readiness
        * decision
        * tier_reconciliation
    )
    diagnostics["ReconstructedRankingScore"] = reconstructed
    diagnostics["ReconstructionAbsError"] = (ranking - reconstructed).abs()
    diagnostics["ModelAuditIntegrityVersion"] = AUDIT_VERSION
    return scenarios, diagnostics


def _spearman(left: pd.Series, right: pd.Series) -> float:
    pair = (
        pd.concat([left, right], axis=1)
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )
    if (
        len(pair) < 2
        or pair.iloc[:, 0].nunique() < 2
        or pair.iloc[:, 1].nunique() < 2
    ):
        return float("nan")
    value = pair.iloc[:, 0].rank(method="average").corr(
        pair.iloc[:, 1].rank(method="average")
    )
    return float(value) if pd.notna(value) else float("nan")


def _top_index(score: pd.Series, n: int) -> pd.Index:
    clean = score.replace([np.inf, -np.inf], np.nan).dropna()
    return clean.nlargest(min(n, len(clean))).index


def _scenario_metrics(
    baseline: pd.Series,
    scenario: pd.Series,
    mask: pd.Series,
) -> dict[str, Any]:
    base = baseline.loc[mask]
    alt = scenario.loc[mask]
    metrics: dict[str, Any] = {
        "rows": int(mask.sum()),
        "spearman": _spearman(base, alt),
        "mean_score_shift": float(
            (alt - base).replace([np.inf, -np.inf], np.nan).mean()
        ),
    }
    shift = (alt - base).replace([np.inf, -np.inf], np.nan).dropna()
    for q, label in (
        (0.10, "p10_score_shift"),
        (0.50, "p50_score_shift"),
        (0.90, "p90_score_shift"),
    ):
        metrics[label] = float(shift.quantile(q)) if not shift.empty else float("nan")

    for n in (20, 50, 100):
        base_top = set(_top_index(base, n))
        alt_top = set(_top_index(alt, n))
        denominator = max(1, min(n, len(base.dropna())))
        metrics[f"top{n}_overlap"] = len(base_top & alt_top) / denominator
        if n == 50:
            metrics["top50_out"] = len(base_top - alt_top)
            metrics["top50_in"] = len(alt_top - base_top)

    union = _top_index(base, 200).union(_top_index(alt, 200))
    metrics["top200_union_spearman"] = _spearman(
        base.reindex(union), alt.reindex(union)
    )

    base_rank = base.rank(method="min", ascending=False)
    alt_rank = alt.rank(method="min", ascending=False)
    top100 = _top_index(base, 100)
    rank_shift = (alt_rank.reindex(top100) - base_rank.reindex(top100)).abs().dropna()
    metrics["baseline_top100_median_abs_rank_shift"] = (
        float(rank_shift.median()) if not rank_shift.empty else float("nan")
    )
    metrics["baseline_top100_p90_abs_rank_shift"] = (
        float(rank_shift.quantile(0.90)) if not rank_shift.empty else float("nan")
    )
    return metrics


def scenario_report(
    frame: pd.DataFrame,
    scenarios: list[Scenario],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    baseline = next(item.score for item in scenarios if item.name == "baseline")
    asset = _asset_group(frame)
    rows: list[dict[str, Any]] = []
    movers: list[pd.DataFrame] = []
    scope_masks = {
        "ALL": pd.Series(True, index=frame.index),
        "STOCK": asset.eq("STOCK"),
        "ETF": asset.eq("ETF"),
    }
    ticker = _text(frame, "Ticker")

    for scenario in scenarios:
        if scenario.name == "baseline":
            continue
        for scope, mask in scope_masks.items():
            metrics = _scenario_metrics(baseline, scenario.score, mask)
            rows.append(
                {
                    "Scenario": scenario.name,
                    "Scope": scope,
                    "Description": scenario.description,
                    **metrics,
                }
            )

        base_rank = baseline.rank(method="min", ascending=False)
        alt_rank = scenario.score.rank(method="min", ascending=False)
        movement = pd.DataFrame(
            {
                "Scenario": scenario.name,
                "Ticker": ticker,
                "AssetGroup": asset,
                "BaselineScore": baseline,
                "ScenarioScore": scenario.score,
                "BaselineRank": base_rank,
                "ScenarioRank": alt_rank,
                "AbsRankShift": (alt_rank - base_rank).abs(),
            }
        ).dropna(subset=["BaselineScore", "ScenarioScore"])
        movers.append(movement.nlargest(min(100, len(movement)), "AbsRankShift"))

    return (
        pd.DataFrame(rows),
        pd.concat(movers, ignore_index=True) if movers else pd.DataFrame(),
    )


def _near_count(values: pd.Series, threshold: float, window: float) -> int:
    numeric = pd.to_numeric(values, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    return int((numeric - float(threshold)).abs().le(float(window)).sum())


def _threshold_row(
    name: str,
    values: pd.Series,
    threshold: float,
    window: float,
    grid: Iterable[float],
    semantics: str,
) -> dict[str, Any]:
    numeric = (
        pd.to_numeric(values, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )
    near_count = _near_count(values, threshold, window)
    return {
        "Parameter": name,
        "Threshold": float(threshold),
        "Window": float(window),
        "NearCount": near_count,
        "ValidCount": len(numeric),
        "NearRatio": float(near_count / max(1, len(numeric))),
        "Q25": float(numeric.quantile(0.25)) if not numeric.empty else np.nan,
        "Median": float(numeric.quantile(0.50)) if not numeric.empty else np.nan,
        "Q75": float(numeric.quantile(0.75)) if not numeric.empty else np.nan,
        "PerturbationGrid": json.dumps([float(value) for value in grid]),
        "Semantics": semantics,
    }


def threshold_report(frame: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    close = _number(frame, "Close", np.nan)
    resistance = _number(frame, "BreakoutBuyPrice", np.nan)
    clearance = pd.Series(np.nan, index=frame.index, dtype=float)
    valid = close.gt(0.0) & resistance.gt(0.0)
    clearance.loc[valid] = (close.loc[valid] / resistance.loc[valid] - 1.0) * 100.0
    rows.append(
        _threshold_row(
            "ResistanceClearancePct",
            clearance,
            0.0,
            0.50,
            (-0.50, -0.25, -0.10, 0.0, 0.10, 0.25, 0.50),
            "additive rerun required; exported CSV cannot safely recreate TriggerScore",
        )
    )
    rows.extend(
        [
            _threshold_row(
                "BreakoutVolumeRatio",
                _number(frame, "BreakoutVolumeRatio", np.nan),
                float(BREAKOUT_CONFIRM_MIN_VOLUME_RATIO),
                0.10,
                (1.10, 1.15, 1.20, 1.25, 1.30),
                "entry/confirmation rerun required",
            ),
            _threshold_row(
                "EntryScoreHigh",
                _number(frame, "EntryScore", np.nan),
                70.0,
                5.0,
                (65.0, 67.5, 70.0, 72.5, 75.0),
                "entry-state cascade; rerun required",
            ),
            _threshold_row(
                "EntryScoreLow",
                _number(frame, "EntryScore", np.nan),
                50.0,
                5.0,
                (45.0, 47.5, 50.0, 52.5, 55.0),
                "entry-state cascade; rerun required",
            ),
            _threshold_row(
                "RSI14ChaseStart",
                _number(frame, "RSI14", np.nan),
                float(CHASE_RISK_RSI_START),
                3.0,
                (67.0, 68.5, 70.0, 71.5, 73.0),
                "chase factor threshold exposure",
            ),
            _threshold_row(
                "RSI14Hard",
                _number(frame, "RSI14", np.nan),
                float(CHASE_RISK_RSI_HARD),
                3.0,
                (75.0, 76.5, 78.0, 79.5, 81.0),
                "entry HOLD_WAIT cascade",
            ),
            _threshold_row(
                "StopDistancePct",
                _number(frame, "StopDistancePct", np.nan),
                float(TRADE_READY_MAX_STOP_DISTANCE_PCT),
                2.0,
                (10.0, 11.0, 12.0, 13.0, 14.0),
                "execution readiness boundary",
            ),
            _threshold_row(
                "RewardRiskRatio",
                _number(frame, "RewardRiskRatio", np.nan),
                float(TRADE_READY_MIN_REWARD_RISK),
                0.25,
                (0.8, 0.9, 1.0, 1.1, 1.2),
                "execution readiness boundary",
            ),
            _threshold_row(
                "ValueTrapObserve",
                _number(frame, "ValueTrapRisk", np.nan),
                float(VALUE_TRAP_RISK_THRESHOLD),
                5.0,
                (55.0, 57.5, 60.0, 62.5, 65.0),
                "readiness + decision cascade",
            ),
            _threshold_row(
                "ValueTrapHard",
                _number(frame, "ValueTrapRisk", np.nan),
                float(VALUE_TRAP_HARD_RISK_THRESHOLD),
                5.0,
                (65.0, 67.5, 70.0, 72.5, 75.0),
                "hard-risk + decision cascade",
            ),
            _threshold_row(
                "ChaseRiskScore",
                _number(frame, "ChaseRiskScore", np.nan),
                float(CHASE_RISK_HIGH_THRESHOLD),
                5.0,
                (55.0, 57.5, 60.0, 62.5, 65.0),
                "chase factor + READY eligibility cascade",
            ),
            _threshold_row(
                "QualityDataCompleteness",
                _number(frame, "QualityDataCompleteness", np.nan),
                float(QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE),
                0.10,
                (0.40, 0.45, 0.50, 0.55, 0.60),
                "quality readiness/decision boundary",
            ),
        ]
    )
    for name, threshold in (
        ("InstitutionalTierC", INSTITUTIONAL_TIER_C_SCORE),
        ("InstitutionalTierB", INSTITUTIONAL_TIER_B_SCORE),
        ("InstitutionalTierA", INSTITUTIONAL_TIER_A_SCORE),
    ):
        rows.append(
            _threshold_row(
                name,
                _number(frame, "InstitutionalScore", np.nan),
                float(threshold),
                2.5,
                (
                    threshold - 2.5,
                    threshold - 1.25,
                    threshold,
                    threshold + 1.25,
                    threshold + 2.5,
                ),
                "tier/readiness exposure",
            )
        )
    return pd.DataFrame(rows)


def _validate_full_universe(frame: pd.DataFrame) -> list[str]:
    warnings: list[str] = []
    if frame.empty:
        raise ValueError("input result set is empty")
    if "RankingUniverseSize" in frame.columns:
        sizes = (
            pd.to_numeric(frame["RankingUniverseSize"], errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .dropna()
            .astype(int)
            .unique()
            .tolist()
        )
        if len(sizes) == 1 and sizes[0] > 0 and sizes[0] != len(frame):
            raise ValueError(
                "ranking scope violation: "
                f"file has {len(frame)} rows but RankingUniverseSize={sizes[0]}"
            )
        if len(sizes) > 1:
            raise ValueError(f"mixed RankingUniverseSize values: {sorted(sizes)}")
    else:
        warnings.append(
            "RankingUniverseSize missing; full-universe scope cannot be independently verified"
        )
    if "RankingScope" in frame.columns:
        scope_text = _text(frame, "RankingScope")
        scopes = sorted(set(scope_text.loc[scope_text.ne("")]))
        if scopes and scopes != ["FULL_UNIVERSE"]:
            raise ValueError(f"unsupported ranking scopes: {scopes}")
    return warnings


def run_audit(input_path: Path, output_dir: Path) -> dict[str, Any]:
    frame = pd.read_csv(input_path, encoding="utf-8-sig", low_memory=False)
    warnings = _validate_full_universe(frame)
    scenarios, diagnostics = build_scenarios(frame)
    metrics, movers = scenario_report(frame, scenarios)
    thresholds = threshold_report(frame)

    output_dir.mkdir(parents=True, exist_ok=True)
    diagnostics.to_csv(
        output_dir / "ranking_scenarios.csv",
        index=False,
        encoding="utf-8-sig",
    )
    movers.to_csv(
        output_dir / "ranking_top_movers.csv",
        index=False,
        encoding="utf-8-sig",
    )
    thresholds.to_csv(
        output_dir / "threshold_exposure.csv",
        index=False,
        encoding="utf-8-sig",
    )

    reconstruction = (
        diagnostics["ReconstructionAbsError"]
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )
    decision_error = (
        diagnostics["DecisionInferenceAbsError"]
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )
    run_id_text = _text(frame, "RunId")
    run_ids = sorted(set(run_id_text.loc[run_id_text.ne("")]))[:10]
    payload: dict[str, Any] = {
        "audit_version": AUDIT_VERSION,
        "input": str(input_path),
        "rows": len(frame),
        "stocks": int(_asset_group(frame).eq("STOCK").sum()),
        "etfs": int(_asset_group(frame).eq("ETF").sum()),
        "run_ids": run_ids,
        "warnings": warnings,
        "reconstruction": {
            "median_abs_error": (
                float(reconstruction.median()) if not reconstruction.empty else None
            ),
            "p95_abs_error": (
                float(reconstruction.quantile(0.95))
                if not reconstruction.empty
                else None
            ),
            "max_abs_error": (
                float(reconstruction.max()) if not reconstruction.empty else None
            ),
            "decision_factor_median_snap_error": (
                float(decision_error.median()) if not decision_error.empty else None
            ),
            "decision_factor_p95_snap_error": (
                float(decision_error.quantile(0.95))
                if not decision_error.empty
                else None
            ),
        },
        "scenario_metrics": metrics.replace({np.nan: None}).to_dict(
            orient="records"
        ),
        "threshold_exposure": thresholds.replace({np.nan: None}).to_dict(
            orient="records"
        ),
        "notes": [
            (
                "Ranking-time DecisionFactor and later research-tier reconciliation "
                "are reconstructed as separate multiplicative legs."
            ),
            (
                "Final DecisionState may be execution-demoted after ranking and is "
                "therefore not used as a substitute for ranking-time provenance."
            ),
            (
                "Resistance/entry threshold grids are exposure diagnostics only; "
                "exact perturbations require re-running raw features/cache."
            ),
            "No model parameter is modified by this audit command.",
        ],
    }
    (output_dir / "ranking_audit.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Full-universe ranking perturbation audit"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=OUTPUT_DIR / "AllResults.csv",
        help="Canonical full-universe AllResults.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR / "audit",
        help="Audit output directory",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    payload = run_audit(args.input, args.output)
    print(
        "Ranking audit complete: "
        f"rows={payload['rows']}, stocks={payload['stocks']}, "
        f"etfs={payload['etfs']}; "
        f"report={args.output / 'ranking_audit.json'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
