"""Typed decision-policy manifest and stable policy hash.

Legacy concatenated version strings remain compatibility surfaces. This module
provides a machine-readable policy contract so runs can be compared without
parsing those strings.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Final

POLICY_MANIFEST_SCHEMA: Final = "2026-09-04-v113-policy-manifest-v1"


@dataclass(frozen=True)
class ModelPolicy:
    setup_weight: float
    trigger_weight: float
    execution_weight: float
    quality_weight: float
    backtest_min_samples: int


@dataclass(frozen=True)
class QualityPolicy:
    general_roe_threshold: float
    financial_roe_threshold: float
    cyclical_roe_threshold: float
    defensive_roe_threshold: float
    general_margin_max_percentile: float
    cyclical_margin_max_percentile: float
    roe_semantics: str = "LATEST_ANNOUNCED_ANNUAL_FOR_FULL_YEAR_GATE"
    interim_roe_semantics: str = "DIAGNOSTIC_ONLY"
    evidence_semantics: str = "PASS_FAIL_UNKNOWN_NOT_APPLICABLE"


@dataclass(frozen=True)
class ExecutionPolicy:
    market_turnover_floor_cny: float
    assumed_order_notional_cny: float
    max_participation_rate: float
    max_stop_distance_pct: float
    min_reward_risk: float
    max_data_age_trading_days: int
    min_target_cost_multiple: float


@dataclass(frozen=True)
class DecisionPolicyManifest:
    schema: str
    model: ModelPolicy
    quality: QualityPolicy
    execution: ExecutionPolicy

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def canonical_json(self) -> str:
        return json.dumps(
            self.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def policy_hash(self) -> str:
        digest = hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()
        return f"DP-{digest[:12]}"


def build_decision_policy_manifest(config: Any | None = None) -> DecisionPolicyManifest:
    if config is None:
        import config as runtime_config

        config = runtime_config
    return DecisionPolicyManifest(
        schema=POLICY_MANIFEST_SCHEMA,
        model=ModelPolicy(
            setup_weight=float(getattr(config, "MODEL_SETUP_WEIGHT", 0.60)),
            trigger_weight=float(getattr(config, "MODEL_TRIGGER_WEIGHT", 0.25)),
            execution_weight=float(getattr(config, "MODEL_EXECUTION_WEIGHT", 0.15)),
            quality_weight=float(getattr(config, "MODEL_QUALITY_WEIGHT", 0.0)),
            backtest_min_samples=int(
                getattr(config, "BACKTEST_MIN_SAMPLES_FOR_RANKING", 10)
            ),
        ),
        quality=QualityPolicy(
            general_roe_threshold=float(
                getattr(config, "QUALITY_GENERAL_ROE_THRESHOLD", 10.0)
            ),
            financial_roe_threshold=float(
                getattr(config, "QUALITY_FINANCIAL_ROE_THRESHOLD", 6.0)
            ),
            cyclical_roe_threshold=float(
                getattr(config, "QUALITY_CYCLICAL_ROE_THRESHOLD", 5.0)
            ),
            defensive_roe_threshold=float(
                getattr(config, "QUALITY_DEFENSIVE_ROE_THRESHOLD", 6.0)
            ),
            general_margin_max_percentile=float(
                getattr(config, "QUALITY_GENERAL_MARGIN_MAX_PERCENTILE", 0.30)
            ),
            cyclical_margin_max_percentile=float(
                getattr(config, "QUALITY_CYCLICAL_MARGIN_MAX_PERCENTILE", 0.50)
            ),
        ),
        execution=ExecutionPolicy(
            market_turnover_floor_cny=float(
                getattr(config, "TRADE_LIQUIDITY_MARKET_FLOOR_CNY", 2_500_000.0)
            ),
            assumed_order_notional_cny=float(
                getattr(config, "LIVE_EXECUTION_ASSUMED_NOTIONAL_CNY", 50_000.0)
            ),
            max_participation_rate=float(
                getattr(config, "TRADE_READY_MAX_ASSUMED_PARTICIPATION_RATE", 0.01)
            ),
            max_stop_distance_pct=float(
                getattr(config, "TRADE_READY_MAX_STOP_DISTANCE_PCT", 12.0)
            ),
            min_reward_risk=float(
                getattr(config, "TRADE_READY_MIN_REWARD_RISK", 1.0)
            ),
            max_data_age_trading_days=int(
                getattr(config, "TRADE_READY_MAX_DATA_AGE_TRADING_DAYS", 0)
            ),
            min_target_cost_multiple=float(
                getattr(config, "TRADE_READY_MIN_TARGET_COST_MULTIPLE", 1.5)
            ),
        ),
    )


def decision_policy_hash(config: Any | None = None) -> str:
    return build_decision_policy_manifest(config).policy_hash()
