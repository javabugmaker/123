"""Auditable A-share/ETF brokerage and execution-cost calculations."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from config import (
    BACKTEST_ASSUMED_TRADE_NOTIONAL,
    BACKTEST_ETF_COMMISSION_RATE,
    BACKTEST_ETF_MIN_COMMISSION,
    BACKTEST_LIQUIDITY_IMPACT_AT_ONE_PERCENT,
    BACKTEST_MAX_LIQUIDITY_SLIPPAGE,
    BACKTEST_STOCK_COMMISSION_RATE,
    BACKTEST_STOCK_MIN_COMMISSION,
)


@dataclass(frozen=True)
class BrokerFeeSchedule:
    stock_commission_rate: float = BACKTEST_STOCK_COMMISSION_RATE
    etf_commission_rate: float = BACKTEST_ETF_COMMISSION_RATE
    stock_min_commission: float = BACKTEST_STOCK_MIN_COMMISSION
    etf_min_commission: float = BACKTEST_ETF_MIN_COMMISSION
    assumed_trade_notional: float = BACKTEST_ASSUMED_TRADE_NOTIONAL

    def to_dict(self) -> dict[str, float]:
        return {key: float(value) for key, value in asdict(self).items()}

    def commission_rate(self, *, is_etf: bool) -> float:
        return float(
            self.etf_commission_rate if is_etf else self.stock_commission_rate
        )

    def minimum_commission(self, *, is_etf: bool) -> float:
        return float(
            self.etf_min_commission if is_etf else self.stock_min_commission
        )


def effective_commission_rate(
    *,
    is_etf: bool,
    schedule: BrokerFeeSchedule,
    notional: float | None = None,
) -> float:
    trade_notional = float(notional or schedule.assumed_trade_notional)
    if not np.isfinite(trade_notional) or trade_notional <= 0:
        trade_notional = float(schedule.assumed_trade_notional)
    proportional = schedule.commission_rate(is_etf=is_etf)
    minimum_rate = schedule.minimum_commission(is_etf=is_etf) / trade_notional
    return float(max(proportional, minimum_rate))


def liquidity_slippage_rate(
    *,
    base_slippage: float,
    price: float,
    volume_shares: float,
    notional: float,
) -> float:
    """Return base slippage plus a bounded square-root participation impact."""
    if not all(
        np.isfinite(value) and value > 0
        for value in (price, volume_shares, notional)
    ):
        return float(max(0.0, base_slippage))
    traded_value = float(price * volume_shares)
    participation = float(np.clip(notional / traded_value, 0.0, 1.0))
    impact = float(BACKTEST_LIQUIDITY_IMPACT_AT_ONE_PERCENT) * np.sqrt(
        participation / 0.01
    )
    return float(
        max(0.0, base_slippage)
        + np.clip(impact, 0.0, float(BACKTEST_MAX_LIQUIDITY_SLIPPAGE))
    )


def round_trip_cost_percent(
    *,
    is_etf: bool,
    entry_price: float,
    entry_volume: float,
    exit_price: float,
    exit_volume: float,
    base_slippage: float,
    stamp_duty: float,
    schedule: BrokerFeeSchedule,
) -> float:
    notional = float(schedule.assumed_trade_notional)
    commission = effective_commission_rate(
        is_etf=is_etf, schedule=schedule, notional=notional
    )
    entry_slippage = liquidity_slippage_rate(
        base_slippage=base_slippage,
        price=entry_price,
        volume_shares=entry_volume,
        notional=notional,
    )
    exit_slippage = liquidity_slippage_rate(
        base_slippage=base_slippage,
        price=exit_price,
        volume_shares=exit_volume,
        notional=notional,
    )
    total_rate = (
        commission * 2.0
        + entry_slippage
        + exit_slippage
        + (0.0 if is_etf else max(0.0, float(stamp_duty)))
    )
    return float(total_rate * 100.0)
