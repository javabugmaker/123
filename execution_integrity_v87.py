"""Vectorized breakout-confirmation and executable-economics diagnostics."""

from __future__ import annotations

import numpy as np
import pandas as pd

import config as _config
from config import (
    BACKTEST_ASSUMED_TRADE_NOTIONAL,
    BACKTEST_LIQUIDITY_IMPACT_AT_ONE_PERCENT,
    BACKTEST_MAX_LIQUIDITY_SLIPPAGE,
    TRADE_READY_BASE_SLIPPAGE_RATE,
    TRADE_READY_MIN_BREAKOUT_PRICE_CONFIRMATION_SCORE,
    TRADE_READY_MIN_TARGET_COST_MULTIPLE,
    TRADE_READY_STOCK_STAMP_DUTY_RATE,
)
from execution_costs import BrokerFeeSchedule
from institution_scanner.execution_capacity import (
    policy_from_config,
    stamp_liquidity_capacity,
)

_ACTIONABLE_SIGNAL_TYPES = frozenset(
    {"BUY_NOW", "BREAKOUT_CONFIRM", "WAIT_PULLBACK"}
)


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )


def _signal(frame: pd.DataFrame) -> pd.Series:
    return (
        frame.get("EntrySignal", pd.Series("AVOID", index=frame.index))
        .fillna("AVOID")
        .astype(str)
        .str.strip()
        .str.upper()
    )


def legacy_breakout_price_component(clearance_pct: np.ndarray) -> np.ndarray:
    """Vectorized exact copy of the production trigger price component."""
    clearance = np.asarray(clearance_pct, dtype=np.float64)
    result = np.zeros(clearance.shape, dtype=np.float64)
    positive = np.isfinite(clearance) & (clearance > 0.0)
    near = np.isfinite(clearance) & ~positive & (clearance >= -1.5)
    result[positive] = 35.0 + np.clip(clearance[positive] / 3.0, 0.0, 1.0) * 15.0
    result[near] = np.clip((clearance[near] + 1.5) / 1.5, 0.0, 1.0) * 12.0
    return result


def smooth_breakout_price_component(
    clearance_pct: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Smooth the -0.5%..+0.5% legacy cliff and return confirmation strength."""
    clearance = np.asarray(clearance_pct, dtype=np.float64)
    legacy = legacy_breakout_price_component(clearance)
    smooth = legacy.copy()
    valid = np.isfinite(clearance)
    transition = valid & (clearance >= -0.5) & (clearance <= 0.5)
    if np.any(transition):
        x = np.clip((clearance[transition] + 0.5) / 1.0, 0.0, 1.0)
        step = x * x * (3.0 - 2.0 * x)
        smooth[transition] = 8.0 + (37.5 - 8.0) * step
    confirmation = np.zeros(clearance.shape, dtype=np.float64)
    confirmation[valid & (clearance >= 0.5)] = 100.0
    middle = valid & (clearance > -0.5) & (clearance < 0.5)
    if np.any(middle):
        x = np.clip((clearance[middle] + 0.5) / 1.0, 0.0, 1.0)
        confirmation[middle] = (x * x * (3.0 - 2.0 * x)) * 100.0
    return smooth, confirmation


def stamp_breakout_price_diagnostics(result: pd.DataFrame) -> pd.Series:
    """Stamp price confirmation and return its pass mask."""
    signal = _signal(result)
    schema_available = {"Close", "BreakoutBuyPrice"}.issubset(result.columns)
    applicable = pd.Series(
        signal.eq("BREAKOUT_CONFIRM") & schema_available,
        index=result.index,
        dtype=bool,
    )
    close = _numeric(result, "Close")
    resistance = _numeric(result, "BreakoutBuyPrice")
    valid = close.gt(0.0) & resistance.gt(0.0)
    clearance = pd.Series(np.nan, index=result.index, dtype=float)
    clearance.loc[valid] = (
        close.loc[valid].div(resistance.loc[valid]).sub(1.0).mul(100.0)
    )
    legacy = legacy_breakout_price_component(clearance.to_numpy(dtype=np.float64))
    smooth, confirmation = smooth_breakout_price_component(
        clearance.to_numpy(dtype=np.float64)
    )
    minimum = float(TRADE_READY_MIN_BREAKOUT_PRICE_CONFIRMATION_SCORE)
    passed = ~applicable | (
        valid & pd.Series(confirmation, index=result.index).ge(minimum)
    )

    result["BreakoutClearancePct"] = clearance.round(4)
    result["LegacyBreakoutPriceComponent"] = np.round(legacy, 4)
    result["SmoothBreakoutPriceComponent"] = np.round(smooth, 4)
    result["BreakoutPriceConfirmationScore"] = np.round(confirmation, 2)
    result["BreakoutPriceGateApplicable"] = applicable
    result["BreakoutPriceGatePassed"] = passed
    result["BreakoutPriceMinimumConfirmationScore"] = round(minimum, 2)
    result["BreakoutPriceGateStatus"] = np.select(
        [~applicable, passed],
        ["NOT_APPLICABLE", "PASS"],
        default="FAIL",
    )
    reason = pd.Series("非突破确认信号，不适用价格确认门槛", index=result.index)
    reason.loc[applicable & ~valid] = "突破确认缺少有效收盘价或突破价"
    reason.loc[applicable & valid & passed] = "突破价格确认强度满足执行门槛"
    reason.loc[applicable & valid & ~passed] = (
        "突破幅度仍处于零附近过渡区，价格确认强度不足"
    )
    if not schema_available:
        reason[:] = "历史结果缺少突破价格字段，沿用旧执行语义"
    result["BreakoutPriceGateReason"] = reason
    return passed.astype(bool)


def stamp_trade_liquidity_capacity_diagnostics(result: pd.DataFrame) -> pd.Series:
    """Expose market capacity separately from configured portfolio capacity."""
    return stamp_liquidity_capacity(result, policy_from_config(_config))


def stamp_trade_economics_diagnostics(
    result: pd.DataFrame,
    is_etf: pd.Series,
) -> pd.Series:
    """Estimate round-trip friction and require the target to cover it."""
    required = {"Close", "ProjectedTarget", "MedianTurnover60"}
    schema_available = required.issubset(result.columns)
    candidate_signal = _signal(result).isin(_ACTIONABLE_SIGNAL_TYPES)
    applicable = pd.Series(
        candidate_signal & schema_available,
        index=result.index,
        dtype=bool,
    )
    close = _numeric(result, "Close")
    target = _numeric(result, "ProjectedTarget")
    turnover = _numeric(result, "MedianTurnover60")
    valid = close.gt(0.0) & target.ge(close) & turnover.gt(0.0)

    schedule = BrokerFeeSchedule()
    notional = max(1.0, float(BACKTEST_ASSUMED_TRADE_NOTIONAL))
    stock_commission = max(
        float(schedule.stock_commission_rate),
        float(schedule.stock_min_commission) / notional,
    )
    etf_commission = max(
        float(schedule.etf_commission_rate),
        float(schedule.etf_min_commission) / notional,
    )
    etf_mask = is_etf.fillna(False).astype(bool).to_numpy(dtype=bool)
    commission = np.where(etf_mask, etf_commission, stock_commission)
    participation = np.divide(
        notional,
        turnover.to_numpy(dtype=np.float64),
        out=np.full(len(result), np.nan, dtype=np.float64),
        where=turnover.to_numpy(dtype=np.float64) > 0.0,
    )
    impact = float(BACKTEST_LIQUIDITY_IMPACT_AT_ONE_PERCENT) * np.sqrt(
        np.clip(participation, 0.0, 1.0) / 0.01
    )
    impact = np.clip(impact, 0.0, float(BACKTEST_MAX_LIQUIDITY_SLIPPAGE))
    per_side_slippage = float(TRADE_READY_BASE_SLIPPAGE_RATE) + impact
    stamp_duty = np.where(
        etf_mask, 0.0, float(TRADE_READY_STOCK_STAMP_DUTY_RATE)
    )
    cost_pct = (commission * 2.0 + per_side_slippage * 2.0 + stamp_duty) * 100.0
    cost_pct[~turnover.gt(0.0).to_numpy(dtype=bool)] = np.nan
    gross_target_pct = target.div(close).sub(1.0).mul(100.0)
    cost_series = pd.Series(cost_pct, index=result.index, dtype=float)
    multiple = gross_target_pct.div(cost_series.where(cost_series.gt(0.0)))
    minimum = float(TRADE_READY_MIN_TARGET_COST_MULTIPLE)
    passed = ~applicable | (
        valid
        & cost_series.gt(0.0)
        & gross_target_pct.gt(0.0)
        & multiple.ge(minimum)
    )

    result["TradeEconomicsApplicable"] = applicable
    result["TradeEconomicsPassed"] = passed
    result["TradeGrossTargetPct"] = gross_target_pct.round(6)
    result["TradeEstimatedRoundTripCostPct"] = cost_series.round(6)
    result["TradeNetTargetPct"] = gross_target_pct.sub(cost_series).round(6)
    result["TradeTargetCostMultiple"] = multiple.round(4)
    result["TradeMinTargetCostMultiple"] = round(minimum, 4)
    result["TradeEconomicsStatus"] = np.select(
        [~applicable, passed],
        ["NOT_APPLICABLE", "PASS"],
        default="FAIL",
    )
    reason = pd.Series("非候选信号，不适用目标成本门槛", index=result.index)
    reason.loc[applicable & ~valid] = "缺少有效价格、目标价或60日中位成交额"
    reason.loc[applicable & valid & passed] = "预期目标足以覆盖估算往返交易成本"
    reason.loc[applicable & valid & ~passed] = (
        "预期目标不足以覆盖最低往返成本倍数"
    )
    if not schema_available:
        reason[:] = "历史结果缺少执行经济性字段，沿用旧执行语义"
    result["TradeEconomicsReason"] = reason

    # Add capacity diagnostics at the same canonical execution boundary. The
    # legacy v54 gate may still consume compatibility fields afterwards, but
    # these additional columns retain the market-vs-portfolio distinction.
    stamp_trade_liquidity_capacity_diagnostics(result)
    return passed.astype(bool)
