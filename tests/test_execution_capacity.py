from __future__ import annotations

import pandas as pd

from institution_scanner.execution_capacity import (
    LiquidityCapacityPolicy,
    stamp_liquidity_capacity,
)


def test_small_portfolio_order_can_fit_a_liquid_enough_market() -> None:
    frame = pd.DataFrame({"MedianTurnover60": [3_180_000.0]})
    passed = stamp_liquidity_capacity(
        frame,
        LiquidityCapacityPolicy(
            market_turnover_floor_cny=2_500_000.0,
            max_participation_rate=0.01,
            assumed_order_notional_cny=20_000.0,
        ),
    )

    assert passed.iloc[0]
    assert bool(frame.loc[0, "MarketExecutionEligible"])
    assert bool(frame.loc[0, "PortfolioExecutionEligible"])
    assert frame.loc[0, "TradeLiquidityMaxOrderCNY"] == 31_800.0
    assert frame.loc[0, "TradeLiquidityParticipationPct"] == 0.6289


def test_market_capacity_and_portfolio_capacity_are_separate() -> None:
    frame = pd.DataFrame({"MedianTurnover60": [3_180_000.0]})
    passed = stamp_liquidity_capacity(
        frame,
        LiquidityCapacityPolicy(
            market_turnover_floor_cny=2_500_000.0,
            max_participation_rate=0.01,
            assumed_order_notional_cny=50_000.0,
        ),
    )

    assert not passed.iloc[0]
    assert bool(frame.loc[0, "MarketExecutionEligible"])
    assert not bool(frame.loc[0, "PortfolioExecutionEligible"])
    assert frame.loc[0, "TradeLiquidityStatus"] == "PORTFOLIO_FAIL"
    assert frame.loc[0, "TradeLiquidityMaxOrderCNY"] == 31_800.0


def test_market_floor_blocks_genuinely_thin_security() -> None:
    frame = pd.DataFrame({"MedianTurnover60": [2_000_000.0]})
    passed = stamp_liquidity_capacity(
        frame,
        LiquidityCapacityPolicy(
            market_turnover_floor_cny=2_500_000.0,
            max_participation_rate=0.01,
            assumed_order_notional_cny=10_000.0,
        ),
    )

    assert not passed.iloc[0]
    assert not bool(frame.loc[0, "MarketExecutionEligible"])
    assert frame.loc[0, "TradeLiquidityStatus"] == "MARKET_FAIL"
