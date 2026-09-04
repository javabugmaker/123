"""Canonical execution-capacity model for daily research candidates.

Liquidity is separated into market capacity and portfolio capacity. Market
capacity describes whether the security itself is liquid enough for directional
research; portfolio capacity answers whether the configured order size fits
inside the allowed turnover participation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class LiquidityCapacityPolicy:
    market_turnover_floor_cny: float = 2_500_000.0
    max_participation_rate: float = 0.01
    assumed_order_notional_cny: float = 50_000.0

    def normalized(self) -> "LiquidityCapacityPolicy":
        return LiquidityCapacityPolicy(
            market_turnover_floor_cny=max(0.0, float(self.market_turnover_floor_cny)),
            max_participation_rate=max(0.0, float(self.max_participation_rate)),
            assumed_order_notional_cny=max(0.0, float(self.assumed_order_notional_cny)),
        )


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except ValueError:
        return float(default)
    return value if np.isfinite(value) else float(default)


def policy_from_config(config: Any) -> LiquidityCapacityPolicy:
    """Resolve capacity policy without coupling the pure calculator to config.py."""
    market_floor = float(
        getattr(
            config,
            "TRADE_LIQUIDITY_MARKET_FLOOR_CNY",
            getattr(config, "VOLUME_MIN_MEDIAN_TURNOVER_60D", 2_500_000.0),
        )
    )
    max_participation = float(
        getattr(config, "TRADE_READY_MAX_ASSUMED_PARTICIPATION_RATE", 0.01)
    )
    default_notional = float(
        getattr(config, "BACKTEST_ASSUMED_TRADE_NOTIONAL", 50_000.0)
    )
    configured_notional = float(
        getattr(config, "LIVE_EXECUTION_ASSUMED_NOTIONAL_CNY", default_notional)
    )
    live_notional = _env_float(
        "INSTITUTION_SCANNER_ORDER_NOTIONAL_CNY",
        configured_notional,
    )
    return LiquidityCapacityPolicy(
        market_turnover_floor_cny=market_floor,
        max_participation_rate=max_participation,
        assumed_order_notional_cny=live_notional,
    )


def stamp_liquidity_capacity(
    result: pd.DataFrame,
    policy: LiquidityCapacityPolicy,
) -> pd.Series:
    """Stamp capacity diagnostics and return portfolio execution eligibility."""
    normalized = policy.normalized()
    market_floor = normalized.market_turnover_floor_cny
    max_participation = normalized.max_participation_rate
    notional = normalized.assumed_order_notional_cny

    result["TradeLiquidityMarketThresholdCNY"] = round(market_floor, 2)
    result["TradeLiquidityAssumedNotionalCNY"] = round(notional, 2)
    result["TradeLiquidityMaxParticipationPct"] = round(
        max_participation * 100.0, 4
    )

    if "MedianTurnover60" not in result.columns:
        result["TradeLiquidityApplicable"] = False
        result["MarketExecutionEligible"] = True
        result["PortfolioExecutionEligible"] = True
        result["TradeLiquidityPassed"] = True
        result["TradeLiquidityStatus"] = "LEGACY_UNKNOWN"
        result["TradeLiquidityThresholdCNY"] = round(market_floor, 2)
        result["TradeLiquidityParticipationPct"] = np.nan
        result["TradeLiquidityMaxOrderCNY"] = np.nan
        result["TradeLiquidityHeadroomCNY"] = np.nan
        result["TradeLiquidityReason"] = (
            "历史结果缺少60日中位成交额，沿用旧执行语义"
        )
        return pd.Series(True, index=result.index, dtype=bool)

    turnover = pd.to_numeric(result["MedianTurnover60"], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    valid = turnover.gt(0.0)
    max_order = turnover.mul(max_participation)
    participation = pd.Series(np.nan, index=result.index, dtype=float)
    participation.loc[valid] = notional / turnover.loc[valid]

    market_pass = valid & turnover.ge(market_floor)
    if max_participation > 0.0:
        portfolio_pass = market_pass & participation.le(
            max_participation + 1e-12
        )
    else:
        portfolio_pass = pd.Series(False, index=result.index, dtype=bool)
    if notional <= 0.0:
        portfolio_pass = market_pass.copy()

    effective_threshold = pd.Series(market_floor, index=result.index, dtype=float)
    if max_participation > 0.0:
        effective_threshold[:] = max(market_floor, notional / max_participation)

    result["TradeLiquidityApplicable"] = True
    result["MarketExecutionEligible"] = market_pass
    result["PortfolioExecutionEligible"] = portfolio_pass
    result["TradeLiquidityPassed"] = portfolio_pass
    result["TradeLiquidityStatus"] = np.select(
        [~valid, ~market_pass, portfolio_pass],
        ["UNKNOWN", "MARKET_FAIL", "PASS"],
        default="PORTFOLIO_FAIL",
    )
    result["TradeLiquidityThresholdCNY"] = effective_threshold.round(2)
    result["TradeLiquidityParticipationPct"] = (participation * 100.0).round(4)
    result["TradeLiquidityMaxOrderCNY"] = max_order.round(2)
    result["TradeLiquidityHeadroomCNY"] = max_order.sub(notional).round(2)

    reason = pd.Series("", index=result.index, dtype=object)
    reason.loc[~valid] = "缺少有效60日中位成交额，执行容量未知"
    reason.loc[valid & ~market_pass] = (
        "市场成交容量不足：60日中位成交额低于最低研究执行门槛"
    )
    portfolio_fail = market_pass & ~portfolio_pass
    reason.loc[portfolio_fail] = [
        (
            f"市场容量合格，但当前假设订单{notional / 10_000:.2f}万元超过"
            f"{max_participation:.1%}参与率容量；该标的当前最大建议订单约"
            f"{value / 10_000:.2f}万元"
        )
        for value in max_order.loc[portfolio_fail]
    ]
    reason.loc[portfolio_pass] = [
        (
            f"市场容量与账户容量均通过；按{max_participation:.1%}参与率，"
            f"当前最大建议订单约{value / 10_000:.2f}万元"
        )
        for value in max_order.loc[portfolio_pass]
    ]
    result["TradeLiquidityReason"] = reason
    return portfolio_pass.astype(bool)
