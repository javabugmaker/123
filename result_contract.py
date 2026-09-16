from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

RankingScope = Literal["FULL_UNIVERSE"]
FULL_UNIVERSE_SCOPE: RankingScope = "FULL_UNIVERSE"


@dataclass(frozen=True)
class ResultField:
    name: str
    domain: str
    dtype: str
    label: str
    nullable: bool = False
    default: object = ""


RESULT_SCHEMA: tuple[ResultField, ...] = (
    ResultField("RunId", "provenance", "string", "数据运行ID"),
    ResultField("ModelVersion", "provenance", "string", "评分版本"),
    ResultField("PipelineVersion", "provenance", "string", "流水线版本"),
    ResultField("OutputContractVersion", "provenance", "string", "输出契约版本"),
    ResultField("DecisionIntegrityVersion", "provenance", "string", "决策校验版本"),
    ResultField("FundamentalGateVersion", "provenance", "string", "基本面门槛版本"),
    ResultField("DecisionPolicySignature", "provenance", "string", "决策策略签名"),
    ResultField("RankingScope", "ranking", "string", "排名作用域"),
    ResultField("RankingUniverseSize", "ranking", "int", "排名股票池规模", default=0),
    ResultField("RankingRunId", "ranking", "string", "排名运行ID"),
    ResultField("PriceAdjustmentMode", "market_data", "string", "价格复权方式"),
    ResultField("AdjustmentBaseDate", "market_data", "string", "复权基准日期"),
    ResultField("ATRAsOf", "market_data", "string", "ATR截止日期"),
    ResultField(
        "CorporateActionRebaseDetected",
        "market_data",
        "bool",
        "复权基准重建",
        default=False,
    ),
)

RESULT_FIELD_MAP = {field.name: field for field in RESULT_SCHEMA}
RESULT_FIELD_LABELS = {field.name: field.label for field in RESULT_SCHEMA}

# Required production evidence is intentionally much smaller than the full
# research surface. These columns prove what model/policy generated a row and
# that shadow/diagnostic layers did not leak into production. Missing one is a
# contract failure rather than an optional diagnostic omission.
#
# CandidateViewRank is deliberately NOT an AllResults contract field. It is a
# presentation/view rank attached only after a candidate subset is selected;
# requiring it on the full-universe surface conflates ranking provenance with
# candidate-view presentation semantics.
REQUIRED_PRODUCTION_COLUMNS: frozenset[str] = frozenset(
    {
        "Ticker",
        "RunId",
        "ModelVersion",
        "PipelineVersion",
        "OutputContractVersion",
        "DecisionIntegrityVersion",
        "DecisionPolicySignature",
        "ModelWeightSignature",
        "ProductionModelRole",
        "ProductionModelWeightSignatureLocked",
        "ChallengerModelRole",
        "ChallengerProductionApplied",
        "HierarchicalEvidenceProductionApplied",
        "ReliabilityFoundationVersion",
        "ModelContractVersion",
        "GlobalCalibrationGovernanceStatus",
        "BacktestPeerEvidenceWeight",
        "BacktestLocalEvidenceWeight",
        "BacktestEligibleForRanking",
        "RankingScope",
        "RankingUniverseSize",
        "RankingRunId",
        "RankingScore",
        "ExecutionState",
        "EntrySignal",
        "DataFreshnessStatus",
    }
)

REQUIRED_MIXED_VIEW_COLUMNS: frozenset[str] = frozenset(
    {
        "Ticker",
        "RunId",
        "CandidateViewRank",
        "RankingScore",
        "ExecutionState",
        "EntrySignal",
    }
)

REQUIRED_TRADE_READY_COLUMNS: frozenset[str] = frozenset(
    {
        "Ticker",
        "RunId",
        "CandidateViewRank",
        "ExecutionState",
        "EntrySignal",
        "QualityLayerStatus",
        "DataFreshnessStatus",
        "Close",
        "StopLoss",
        "ProjectedTarget",
    }
)

_POLICY_PREFIXES = (
    "AD_",
    "ADX_",
    "ATR_",
    "BACKTEST_",
    "BB_",
    "BEAR_",
    "BREAKOUT_",
    "CHASE_RISK_",
    "CCI_",
    "CMF_",
    "CONSOLIDATION_",
    "CROSS_ASSET_",
    "DATA_FRESHNESS_",
    "DONCHIAN_",
    "EMA_",
    "ENTRY_",
    "ETF_",
    "FRESHNESS_",
    "GLOBAL_CALIBRATION_",
    "HARD_RISK_",
    "HV_",
    "INSTITUTIONAL_",
    "LIFECYCLE_",
    "MACD_",
    "MA_",
    "MFI_",
    "MODEL_",
    "OBV_",
    "QUALITY_",
    "REGRESSION_",
    "ROC_",
    "RSI_",
    "SECTOR_CONFIRMATION_",
    "STOCK_INDUSTRY_",
    "THEME_CLUSTER_",
    "TRADE_READY_",
    "VALUE_TRAP_",
    "VOLUME_",
    "VWAP_",
)
_POLICY_NAMES = {
    "ENABLE_VOLUME_PROFILE",
    "EXCLUDED_SECURITY_KEYWORDS",
    "HISTORY_YEARS",
    "MAX_PRICE",
    "MIN_MARKET_CAP",
    "MIN_PRICE",
    "MIN_VOLUME",
    "SCORING_WEIGHTS",
    "TICKFLOW_ADJUST",
}

_POLICY_EXCLUDED_NAMES = {
    "BACKTEST_CACHE_ENABLED",
    "BACKTEST_CHUNK_SIZE",
    "BACKTEST_FAST_CHUNK_SIZE",
    "BACKTEST_INCREMENTAL_TAIL_BARS",
    "BACKTEST_MAX_PROCESSES",
    "BACKTEST_PROCESS_MIN_TICKERS",
    "BACKTEST_PROGRESS_INTERVAL",
    "BACKTEST_PROVENANCE_VERSION",
}


def _normalize_policy_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _normalize_policy_value(asdict(value))
    if isinstance(value, dict):
        return {
            str(key): _normalize_policy_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_policy_value(item) for item in value]
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    raise TypeError(f"unsupported policy value: {type(value).__name__}")


def decision_policy_payload(exclude_version_strings: bool = False) -> dict[str, Any]:
    """Policy parameters that produced a result.

    ``exclude_version_strings`` drops the ``*_VERSION`` names (nine of the 162
    names at last count).  Those are *labels*, not parameters: each names the
    patch that last moved it (``…-v80-tradeability-sample-array-v1``), so
    rewording one -- even cosmetically -- moves the signature without touching a
    single threshold.  See ``decision_policy_parameter_digest``.
    """
    import config

    payload: dict[str, Any] = {}
    for name in sorted(dir(config)):
        if name in _POLICY_EXCLUDED_NAMES:
            continue
        if name not in _POLICY_NAMES and not name.startswith(_POLICY_PREFIXES):
            continue
        if exclude_version_strings and name.endswith("_VERSION"):
            continue
        value = getattr(config, name)
        try:
            payload[name] = _normalize_policy_value(value)
        except TypeError:
            continue
    return payload


def _policy_digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def decision_policy_signature() -> str:
    """Fail-safe fingerprint: changes on *any* policy change, including labels.

    Deliberately inclusive.  A narrower digest would let a policy change that
    forgets to touch a parameter slip through with an unchanged signature.
    """
    return _policy_digest(decision_policy_payload())


def decision_policy_parameter_digest() -> str:
    """Discriminating fingerprint: changes only when a *parameter* changes.

    ``DecisionPolicySignature`` answers "did anything about the policy change?"
    and always says yes -- including for a pure label edit.  This one answers
    "did a number change?", which is what you want when a signature moves and
    you need to know whether the strategy actually changed or someone just
    reworded a version string.
    """
    return _policy_digest(decision_policy_payload(exclude_version_strings=True))


def candidate_generation_stage(backtest_stage: pd.Series) -> pd.Series:
    normalized = backtest_stage.fillna("").astype(str).str.upper().str.strip()
    return normalized.map(
        {
            "EXACT": "EXACT_REFINED",
            "EXACT_REFINEMENT": "EXACT_REFINED",
            "FAST_SCREEN": "FAST_SCREEN",
            "NOT_EVALUATED": "NOT_EVALUATED",
        }
    ).fillna("UNKNOWN")


def _unique_text(frame: pd.DataFrame, column: str) -> list[str]:
    if column not in frame:
        return []
    return sorted(
        {
            str(value).strip()
            for value in frame[column].dropna().tolist()
            if str(value).strip()
        }
    )


def validate_ranking_input(frame: pd.DataFrame) -> None:
    if frame is None or frame.empty:
        return
    scopes = _unique_text(frame, "RankingScope")
    if scopes and scopes != [FULL_UNIVERSE_SCOPE]:
        raise ValueError(f"unsupported ranking scope: {scopes}")

    if "RankingUniverseSize" in frame:
        expected = (
            pd.to_numeric(frame["RankingUniverseSize"], errors="coerce")
            .replace([np.inf, -np.inf], np.nan)
            .dropna()
        )
        expected = expected.loc[expected.gt(0)].astype(int).unique().tolist()
        if len(expected) > 1:
            raise ValueError(
                f"mixed RankingUniverseSize values: {sorted(expected)}"
            )
        if expected and expected[0] != len(frame):
            raise ValueError(
                "ranking scope violation: this frame contains "
                f"{len(frame)} rows but was ranked against {expected[0]}; "
                "candidate subsets must not be re-ranked"
            )

    ranking_run_ids = _unique_text(frame, "RankingRunId")
    run_ids = _unique_text(frame, "RunId")
    if len(run_ids) > 1:
        raise ValueError(f"mixed RunId values: {run_ids}")
    if len(ranking_run_ids) > 1:
        raise ValueError(f"mixed RankingRunId values: {ranking_run_ids}")
    if ranking_run_ids and len(run_ids) == 1 and ranking_run_ids != run_ids:
        raise ValueError(
            f"ranking RunId {ranking_run_ids[0]} does not match data RunId {run_ids[0]}"
        )


def stamp_ranking_contract(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return frame
    run_ids = _unique_text(frame, "RunId")
    if len(run_ids) > 1:
        raise ValueError(f"mixed RunId values: {run_ids}")
    frame["RankingScope"] = FULL_UNIVERSE_SCOPE
    frame["RankingUniverseSize"] = len(frame)
    frame["RankingRunId"] = run_ids[0] if len(run_ids) == 1 else "UNSCOPED"
    frame["DecisionPolicySignature"] = decision_policy_signature()
    return frame


def apply_schema_defaults(record: dict[str, Any]) -> dict[str, Any]:
    result = dict(record)
    for field in RESULT_SCHEMA:
        result.setdefault(field.name, field.default)
    return result