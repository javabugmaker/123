from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

from config import (
    BACKTEST_FULL_WEIGHT_SAMPLES,
    BACKTEST_LOW_CONFIDENCE_MAX_SAMPLES,
    BACKTEST_MIN_SAMPLES_FOR_RANKING,
    BACKTEST_NEUTRAL_SCORE,
    BACKTEST_NORMAL_WEIGHT,
    BREAKOUT_CONFIRM_MIN_VOLUME_RATIO,
    BREAKOUT_CONFIRM_MIN_VOLUME_SCORE,
    CHASE_RISK_DISTANCE_HIGH,
    CHASE_RISK_DISTANCE_START,
    CHASE_RISK_HIGH_THRESHOLD,
    CHASE_RISK_MAX_PENALTY,
    CHASE_RISK_RSI_HARD,
    CHASE_RISK_RSI_START,
    DATA_FRESHNESS_DELAYED_FACTOR,
    DATA_FRESHNESS_DELAYED_TRADING_DAYS,
    DATA_FRESHNESS_STALE_FACTOR,
    DATA_FRESHNESS_STALE_TRADING_DAYS,
    ENTRY_SIGNAL_MULTIPLIERS,
    ENTRY_SIGNAL_PRIORITIES,
    HARD_RISK_AVOID_PENALTY,
    HARD_RISK_DATA_PENALTY,
    HARD_RISK_STAGE_PENALTY,
    HARD_RISK_VALUE_TRAP_PENALTY,
    INSTITUTIONAL_TIER_A_PERCENTILE,
    INSTITUTIONAL_TIER_A_SCORE,
    INSTITUTIONAL_TIER_B_PERCENTILE,
    INSTITUTIONAL_TIER_B_SCORE,
    INSTITUTIONAL_TIER_C_PERCENTILE,
    INSTITUTIONAL_TIER_C_SCORE,
    INSTITUTIONAL_TIER_MIN_DATA_CONFIDENCE,
    INSTITUTIONAL_TIER_TRAP_LABEL,
    INSTITUTIONAL_TIER_WAIT_LABEL,
    OUTPUT_DIR,
    QUALITY_MULTIPLIER_FAIL,
    QUALITY_MULTIPLIER_PASS,
    QUALITY_MULTIPLIER_UNKNOWN,
    QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE,
    TRADE_READY_MIN_INSTITUTIONAL_SCORE,
    VALUE_TRAP_HARD_RISK_THRESHOLD,
    VALUE_TRAP_RISK_THRESHOLD,
)

HISTORY_FILE = OUTPUT_DIR / "SignalHistory.csv"
TRACKING_FILE = OUTPUT_DIR / "SignalTracking.csv"
HISTORY_COLUMNS = [
    "TradeDate",
    "Ticker",
    "Name",
    "Close",
    "Score",
    "OpportunityScore",
    "InstitutionalScore",
    "InstitutionalTier",
    "BreakoutQualityFactor",
    "SignalRecencyFactor",
    "SectorConfirmationFactor",
    "FailureSignalFactor",
    "ScoreConfidence",
    "SignalActive",
    "SignalStatus",
    "SignalDays",
    "SignalStartDate",
    "Stage",
    "TrendScore",
    "AccumulationScore",
    "IndustryRelativeStrength",
    "SignalCount",
    "Return20D",
    "MaxDrawdown20D",
    "Return60D",
    "MaxDrawdown60D",
]


def _number(series: pd.Series, default: float = 0.0) -> pd.Series:
    return (
        pd.to_numeric(series, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .fillna(default)
    )


def _bool(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y", "是"}


def _bool_series(frame: pd.DataFrame, column: str, default: bool = False) -> pd.Series:
    values = frame.get(column, pd.Series(default, index=frame.index))
    return (
        values.map(_bool)
        if isinstance(values, pd.Series)
        else pd.Series(default, index=frame.index)
    )


def _text_series(frame: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    values = frame.get(column, pd.Series(default, index=frame.index))
    if not isinstance(values, pd.Series):
        values = pd.Series(values, index=frame.index)
    return values.fillna(default).astype(str).str.strip()


def _append_reason(current: pd.Series, condition: pd.Series, reason: str) -> pd.Series:
    existing = current.fillna("").astype(str)
    return existing.where(
        ~condition,
        np.where(existing.eq(""), reason, existing + "；" + reason),
    )


def _holding_status(frame: pd.DataFrame) -> pd.Series:
    existing = _text_series(frame, "InstitutionHoldingStatus", "").str.upper()
    periods = _number(
        frame.get("InstitutionHoldingPeriods", pd.Series(np.nan, index=frame.index)),
        np.nan,
    )
    trend = _text_series(frame, "InstitutionHoldingTrend", "").str.lower()
    inferred = pd.Series("UNKNOWN", index=frame.index)
    enough_history = periods.ge(2)
    inferred.loc[
        enough_history
        & trend.isin(
            {
                "increasing",
                "increase",
                "up",
                "上涨",
                "增加",
                "连续增加",
                "true",
                "1",
                "是",
            }
        )
    ] = "PASS"
    inferred.loc[
        enough_history
        & trend.isin(
            {
                "not_increasing",
                "decreasing",
                "decrease",
                "down",
                "减少",
                "连续减少",
                "false",
                "0",
                "否",
            }
        )
    ] = "FAIL"
    return existing.where(existing.isin({"PASS", "FAIL", "UNKNOWN"}), inferred)


def _data_freshness(
    frame: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    trading_age = _number(
        frame.get("DataTradingAgeDays", pd.Series(np.nan, index=frame.index)),
        np.nan,
    )
    calendar_age = _number(
        frame.get("DataAgeDays", pd.Series(np.nan, index=frame.index)), np.nan
    )
    effective_age = trading_age.where(trading_age.ge(0.0), calendar_age)
    known = effective_age.notna() & effective_age.ge(0.0)
    delayed = known & effective_age.gt(DATA_FRESHNESS_DELAYED_TRADING_DAYS)
    stale = known & effective_age.gt(DATA_FRESHNESS_STALE_TRADING_DAYS)

    status = pd.Series("未知", index=frame.index)
    status.loc[known & ~delayed] = "新鲜"
    status.loc[delayed & ~stale] = "延迟"
    status.loc[stale] = "过期"
    factor = pd.Series(1.0, index=frame.index)
    factor.loc[delayed & ~stale] = DATA_FRESHNESS_DELAYED_FACTOR
    factor.loc[stale] = DATA_FRESHNESS_STALE_FACTOR
    reason = pd.Series("未提供可用的行情日期", index=frame.index)
    reason.loc[known & ~delayed] = "行情日期正常"
    reason.loc[delayed & ~stale] = "行情数据延迟，建议刷新后确认"
    reason.loc[stale] = "行情数据已过期，禁止作为即时交易信号"
    return status, factor.round(4), reason


def _backtest_confidence(
    samples: pd.Series,
    effective_samples: pd.Series,
    return_std: pd.Series,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    samples = samples.clip(lower=0.0)
    effective_samples = effective_samples.clip(lower=0.0).where(
        effective_samples.gt(0.0), samples
    ).clip(upper=samples.where(samples.gt(0.0), 0.0))
    reliability = pd.Series(0.0, index=samples.index)
    low_start = float(BACKTEST_MIN_SAMPLES_FOR_RANKING)
    low_end = float(
        max(
            BACKTEST_LOW_CONFIDENCE_MAX_SAMPLES,
            BACKTEST_MIN_SAMPLES_FOR_RANKING + 1,
        )
    )
    full = float(
        max(BACKTEST_FULL_WEIGHT_SAMPLES, BACKTEST_LOW_CONFIDENCE_MAX_SAMPLES + 1)
    )
    low_mask = samples.ge(low_start) & samples.lt(low_end)
    medium_mask = samples.ge(low_end) & samples.lt(full)
    reliability.loc[low_mask] = 0.25 * (
        (samples.loc[low_mask] - low_start + 1.0) / (low_end - low_start + 1.0)
    )
    reliability.loc[medium_mask] = 0.25 + 0.75 * (
        (samples.loc[medium_mask] - low_end) / (full - low_end)
    )
    reliability.loc[samples.ge(full)] = 1.0
    independence = np.sqrt(
        (effective_samples / samples.where(samples.gt(0.0), 1.0)).clip(0.0, 1.0)
    )
    dispersion = (1.0 - return_std.abs().fillna(0.0) / 80.0).clip(0.55, 1.0)
    reliability = (reliability * independence * dispersion).clip(0.0, 1.0)
    tier = pd.Series("样本不足", index=samples.index)
    tier.loc[low_mask] = "低可信度"
    tier.loc[medium_mask] = "中可信度"
    tier.loc[samples.ge(full)] = "高可信度"
    return (
        reliability.round(4),
        (reliability * BACKTEST_NORMAL_WEIGHT).round(4),
        tier,
    )


def validate_signal_consistency(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply signal safety rules once before ranking/export."""
    result = frame.copy()
    signal = _text_series(result, "EntrySignal", "AVOID").str.upper()
    adjustments = _text_series(result, "SignalAdjustmentReason", "")
    trap = _number(
        result.get("ValueTrapRisk", pd.Series(0.0, index=result.index))
    )
    lifecycle = _text_series(result, "LifecycleStage", "未知")
    rsi = _number(
        result.get("RSI14", pd.Series(np.nan, index=result.index)), np.nan
    )
    chase_risk = _number(
        result.get("ChaseRiskScore", pd.Series(np.nan, index=result.index)),
        np.nan,
    )
    is_etf = _bool_series(result, "IsETF") | _text_series(
        result, "AssetType", ""
    ).str.lower().eq("etf")
    supplied_completeness = _number(
        result.get("QualityDataCompleteness", pd.Series(np.nan, index=result.index)),
        np.nan,
    )
    quality_failed = pd.Series(False, index=result.index)
    if "QualityGate" in result:
        quality_failed = ~_bool_series(result, "QualityGate", True) & ~is_etf
    quality_sparse = (
        supplied_completeness.notna()
        & supplied_completeness.lt(QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE)
        & ~is_etf
    )
    quality_action_block = quality_failed | quality_sparse
    freshness_status, _freshness_factor, _freshness_reason = _data_freshness(result)
    stale_data = freshness_status.eq("过期")

    volume_ratio = _number(
        result.get("BreakoutVolumeRatio", pd.Series(np.nan, index=result.index)),
        np.nan,
    )
    volume_score = _number(
        result.get("VolumeScore", pd.Series(np.nan, index=result.index)), np.nan
    )
    observed_volume_confirmation = volume_ratio.ge(
        BREAKOUT_CONFIRM_MIN_VOLUME_RATIO
    ) | volume_score.ge(BREAKOUT_CONFIRM_MIN_VOLUME_SCORE)
    volume_metrics_available = volume_ratio.notna() | volume_score.notna()
    if "BreakoutVolumeConfirmed" in result:
        volume_confirmed = _bool_series(result, "BreakoutVolumeConfirmed") & (
            ~volume_metrics_available | observed_volume_confirmation
        )
    else:
        volume_confirmed = observed_volume_confirmation

    cmf_positive = _bool_series(result, "CMF_Pos") | _number(
        result.get("CMF", pd.Series(np.nan, index=result.index)), np.nan
    ).gt(0.0)
    ad_positive = _bool_series(result, "AD_SlopePos") | _number(
        result.get("AD_Slope", pd.Series(np.nan, index=result.index)), np.nan
    ).gt(0.0)
    observed_flow_confirmation = (
        cmf_positive | ad_positive | _bool_series(result, "OBV_Div")
    )
    flow_metrics_available = any(
        column in result
        for column in ("CMF_Pos", "CMF", "AD_SlopePos", "AD_Slope", "OBV_Div")
    )
    if "BreakoutFlowConfirmed" in result:
        flow_confirmed = _bool_series(result, "BreakoutFlowConfirmed") & (
            (not flow_metrics_available) | observed_flow_confirmation
        )
    else:
        flow_confirmed = observed_flow_confirmation

    weak_breakout = signal.eq("BREAKOUT_CONFIRM") & ~(
        volume_confirmed & flow_confirmed
    )
    signal.loc[weak_breakout] = "PRICE_BREAKOUT"
    adjustments = _append_reason(
        adjustments, weak_breakout, "突破缺少量能或资金确认，降为等待量能"
    )
    trap_block = trap.ge(VALUE_TRAP_HARD_RISK_THRESHOLD) & signal.isin(
        {"BUY_NOW", "BREAKOUT_CONFIRM", "WAIT_PULLBACK"}
    )
    signal.loc[trap_block] = "AVOID"
    adjustments = _append_reason(
        adjustments, trap_block, "价值陷阱风险高，禁止积极买点"
    )
    stage_block = lifecycle.isin({"加速风险", "派发"}) & signal.isin(
        {"BUY_NOW", "BREAKOUT_CONFIRM", "WAIT_PULLBACK"}
    )
    signal.loc[stage_block & lifecycle.eq("加速风险")] = "HOLD_WAIT"
    signal.loc[stage_block & lifecycle.eq("派发")] = "AVOID"
    adjustments = _append_reason(adjustments, stage_block, "生命周期风险阶段，买点降级")
    rsi_block = rsi.ge(CHASE_RISK_RSI_HARD) & signal.isin(
        {"BUY_NOW", "BREAKOUT_CONFIRM"}
    )
    signal.loc[rsi_block] = "HOLD_WAIT"
    adjustments = _append_reason(adjustments, rsi_block, "RSI高位过热，停止追涨")
    chase_block = chase_risk.ge(CHASE_RISK_HIGH_THRESHOLD) & signal.isin(
        {"BUY_NOW", "BREAKOUT_CONFIRM"}
    )
    signal.loc[chase_block] = "HOLD_WAIT"
    adjustments = _append_reason(adjustments, chase_block, "追高风险过高，停止追涨")
    quality_buy_block = quality_action_block & signal.isin(
        {"BUY_NOW", "BREAKOUT_CONFIRM"}
    )
    signal.loc[quality_buy_block & signal.eq("BUY_NOW")] = "WAIT_PULLBACK"
    signal.loc[quality_buy_block & signal.eq("BREAKOUT_CONFIRM")] = "PRICE_BREAKOUT"
    adjustments = _append_reason(
        adjustments, quality_buy_block, "质量门槛未通过或数据不足，降为观察"
    )
    stale_signal_block = stale_data & signal.ne("AVOID")
    signal.loc[stale_signal_block] = "HOLD_WAIT"
    adjustments = _append_reason(
        adjustments, stale_signal_block, "行情数据已过期，停止使用即时买点"
    )

    operation_advice = _text_series(result, "OperationAdvice", "")
    operation_advice.loc[
        signal.isin({"PRICE_BREAKOUT", "WAIT_VOLUME_CONFIRM"})
    ] = "价格已突破，等待成交量确认，暂不追高。"
    operation_advice.loc[signal.eq("HOLD_WAIT")] = "暂缓操作，等待风险或趋势条件改善。"
    operation_advice.loc[signal.eq("AVOID")] = "暂不参与，等待风险解除。"
    operation_advice.loc[stale_data] = "行情数据已过期，请刷新后再判断。"
    result["EntrySignal"] = signal
    result["BreakoutVolumeConfirmed"] = volume_confirmed
    result["BreakoutFlowConfirmed"] = flow_confirmed
    result["PriceBreakout"] = result.get(
        "PriceBreakout",
        signal.isin({"PRICE_BREAKOUT", "BREAKOUT_CONFIRM"}),
    ).fillna(False)
    result["SignalAdjustmentReason"] = adjustments
    result["OperationAdvice"] = operation_advice
    return result


def finalize_signal_ranking(frame: pd.DataFrame) -> pd.DataFrame:
    """Single authority for quality gates, risk gates, tiers and final ranking."""
    if frame.empty:
        return frame
    result = frame.copy()
    if "Score" not in result:
        result["Score"] = 0.0
    if "FinalScore" not in result:
        result["FinalScore"] = _number(result["Score"])
    if "InstitutionalScore" not in result:
        result["InstitutionalScore"] = _number(result["FinalScore"])

    status = _holding_status(result)
    result["InstitutionHoldingStatus"] = status
    roe_available = pd.to_numeric(
        result.get("ROE", pd.Series(np.nan, index=result.index)), errors="coerce"
    ).notna()
    margin_available = pd.to_numeric(
        result.get(
            "IndustryGrossMarginPercentile", pd.Series(np.nan, index=result.index)
        ),
        errors="coerce",
    ).notna()
    profit_available = (
        pd.to_numeric(
            result.get("NetProfitY1", pd.Series(np.nan, index=result.index)),
            errors="coerce",
        ).notna()
        & pd.to_numeric(
            result.get("NetProfitY2", pd.Series(np.nan, index=result.index)),
            errors="coerce",
        ).notna()
        & pd.to_numeric(
            result.get("NetProfitY3", pd.Series(np.nan, index=result.index)),
            errors="coerce",
        ).notna()
    )
    completeness = (
        roe_available.astype(float)
        + margin_available.astype(float)
        + profit_available.astype(float)
        + status.isin({"PASS", "FAIL"}).astype(float)
    ) / 4.0
    supplied_completeness = _number(
        result.get("QualityDataCompleteness", pd.Series(np.nan, index=result.index)),
        np.nan,
    )
    result["QualityDataCompleteness"] = (
        supplied_completeness.where(supplied_completeness.notna(), completeness)
        .clip(0.0, 1.0)
        .round(4)
    )
    supplied_quality_fail = (
        _bool_series(result, "QualityDataAvailable")
        & ~_bool_series(result, "QualityGate", True)
        if "QualityGate" in result
        else pd.Series(False, index=result.index)
    )
    known_fail = (
        (roe_available & ~_bool_series(result, "QualityROE", True))
        | (margin_available & ~_bool_series(result, "QualityGrossMargin", True))
        | (profit_available & ~_bool_series(result, "QualityNetProfit", True))
        | status.eq("FAIL")
        | supplied_quality_fail
    )
    any_unknown = status.eq("UNKNOWN") | ~(
        roe_available & margin_available & profit_available
    )
    result["QualityGate"] = ~known_fail
    result["QualityMultiplier"] = np.select(
        [known_fail, any_unknown],
        [QUALITY_MULTIPLIER_FAIL, QUALITY_MULTIPLIER_UNKNOWN],
        default=QUALITY_MULTIPLIER_PASS,
    )
    quality_reason = _text_series(result, "QualityGateReason", "")
    quality_reason = quality_reason.where(~known_fail, "存在已确认质量未通过项")
    quality_reason = quality_reason.where(
        ~(status.eq("UNKNOWN") & ~known_fail),
        "机构覆盖家数历史不足，按中性处理",
    )
    quality_reason = quality_reason.where(
        ~(quality_reason.eq("") & ~known_fail & ~any_unknown),
        "全部可用质量项通过",
    )
    result["QualityGateReason"] = quality_reason

    freshness_status, freshness_factor, freshness_reason = _data_freshness(result)
    result["DataFreshnessStatus"] = freshness_status
    result["DataFreshnessFactor"] = freshness_factor
    result["DataFreshnessReason"] = freshness_reason
    stale_advice = freshness_status.eq("过期")
    if "OperationAdvice" in result:
        result.loc[stale_advice, "OperationAdvice"] = "行情数据已过期，请刷新后再判断。"
    if "RiskWarning" in result:
        result.loc[stale_advice, "RiskWarning"] = "行情数据过期"
    result["MarketRegimeFast"] = _text_series(
        result,
        "MarketRegimeFast",
        _text_series(result, "MarketRegime", "未知").iloc[0]
        if len(result)
        else "未知",
    )
    result["MarketRegimeSlow"] = _text_series(
        result,
        "MarketRegimeSlow",
        _text_series(result, "MarketRegime", "未知").iloc[0]
        if len(result)
        else "未知",
    )
    if "MarketRegimeConfidence" not in result:
        result["MarketRegimeConfidence"] = 0.0
    if "MarketRegimeReason" not in result:
        result["MarketRegimeReason"] = "沿用可用市场环境数据"

    samples = _number(
        result.get("BacktestSamples", pd.Series(0.0, index=result.index))
    )
    effective_samples = _number(
        result.get("BacktestEffectiveSamples", pd.Series(np.nan, index=result.index)),
        np.nan,
    )
    return_std = _number(
        result.get("BacktestReturnStd20D", pd.Series(np.nan, index=result.index)),
        np.nan,
    )
    reliability, effective_weight, confidence_tier = _backtest_confidence(
        samples, effective_samples, return_std
    )
    result["BacktestReliability"] = reliability
    result["BacktestEffectiveWeight"] = effective_weight
    result["BacktestConfidenceTier"] = confidence_tier
    backtest_score = _number(
        result.get(
            "BacktestScore", pd.Series(BACKTEST_NEUTRAL_SCORE, index=result.index)
        ),
        BACKTEST_NEUTRAL_SCORE,
    )
    result["BacktestAdjustedScore"] = (
        BACKTEST_NEUTRAL_SCORE
        + (backtest_score - BACKTEST_NEUTRAL_SCORE) * reliability
    ).round(4)
    result.loc[
        samples.lt(BACKTEST_MIN_SAMPLES_FOR_RANKING), "FailureSignalFactor"
    ] = 1.0

    rsi = _number(
        result.get("RSI14", pd.Series(np.nan, index=result.index)), np.nan
    )
    distance = _number(
        result.get("DistToLow52W", pd.Series(np.nan, index=result.index)), np.nan
    )
    dist_ma20 = _number(
        result.get("DistToMA20", pd.Series(np.nan, index=result.index)), np.nan
    )
    return20 = _number(
        result.get("RecentReturn20D", pd.Series(np.nan, index=result.index)), np.nan
    )
    atr_expansion = _number(
        result.get("ATRExpansion", pd.Series(np.nan, index=result.index)), np.nan
    )
    lifecycle = _text_series(result, "LifecycleStage", "未知")
    chase = pd.Series(0.0, index=result.index)
    chase += (
        rsi.sub(CHASE_RISK_RSI_START).clip(lower=0.0) * 1.5
    ).clip(upper=18.0).fillna(0.0)
    chase += np.where(rsi.ge(CHASE_RISK_RSI_HARD), 14.0, 0.0)
    chase += (
        distance.sub(CHASE_RISK_DISTANCE_START).clip(lower=0.0) * 0.45
    ).clip(upper=15.0).fillna(0.0)
    chase += np.where(distance.ge(CHASE_RISK_DISTANCE_HIGH), 12.0, 0.0)
    chase += (
        dist_ma20.sub(6.0).clip(lower=0.0) * 1.2
    ).clip(upper=12.0).fillna(0.0)
    chase += (
        return20.sub(12.0).clip(lower=0.0) * 0.65
    ).clip(upper=10.0).fillna(0.0)
    chase += (
        atr_expansion.sub(1.25).clip(lower=0.0) * 20.0
    ).clip(upper=8.0).fillna(0.0)
    chase += np.where(lifecycle.eq("加速风险"), 25.0, 0.0)
    chase += np.where(lifecycle.isin({"派发", "DISTRIBUTION"}), 35.0, 0.0)
    trend_confirmed = lifecycle.isin({"趋势确认", "初始启动"})
    chase = (
        chase
        - np.where(
            trend_confirmed & ~lifecycle.eq("加速风险"), 6.0, 0.0
        )
    ).clip(0.0, 100.0)
    result["ChaseRiskScore"] = chase.round(2)
    result["ChaseRiskLevel"] = np.select(
        [chase.ge(CHASE_RISK_HIGH_THRESHOLD), chase.ge(30.0)],
        ["高", "中"],
        default="低",
    )
    chase_reason = pd.Series("趋势结构正常", index=result.index)
    chase_reason.loc[rsi.ge(CHASE_RISK_RSI_HARD)] = "RSI高位过热"
    chase_reason.loc[distance.ge(CHASE_RISK_DISTANCE_HIGH)] = "远离52周低点且高位运行"
    chase_reason.loc[lifecycle.eq("加速风险")] = "高位加速阶段"
    chase_reason.loc[lifecycle.isin({"派发", "DISTRIBUTION"})] = "派发风险"
    result["ChaseRiskReason"] = chase_reason

    result = validate_signal_consistency(result)
    signal = _text_series(result, "EntrySignal", "AVOID").str.upper()
    trap = _number(
        result.get("ValueTrapRisk", pd.Series(0.0, index=result.index))
    )
    score_coverage = _number(
        result.get("ScoreCoverage", pd.Series(1.0, index=result.index)), 1.0
    )
    hard_penalty = pd.Series(1.0, index=result.index)
    hard_reason = pd.Series("", index=result.index)
    avoid = signal.eq("AVOID")
    stage_risk = lifecycle.isin({"加速风险", "派发", "DISTRIBUTION"})
    trap_observe = trap.ge(VALUE_TRAP_RISK_THRESHOLD)
    trap_risk = trap.ge(VALUE_TRAP_HARD_RISK_THRESHOLD)
    data_risk = score_coverage.lt(0.45)
    stale_data = freshness_status.eq("过期")
    is_etf = _bool_series(result, "IsETF") | _text_series(
        result, "AssetType", ""
    ).str.lower().eq("etf")
    quality_action_block = ~result["QualityGate"] | (
        result["QualityDataCompleteness"].lt(QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE)
        & ~is_etf
    )
    hard_penalty.loc[avoid] = np.minimum(
        hard_penalty.loc[avoid], HARD_RISK_AVOID_PENALTY
    )
    hard_penalty.loc[stage_risk] = np.minimum(
        hard_penalty.loc[stage_risk], HARD_RISK_STAGE_PENALTY
    )
    hard_penalty.loc[trap_risk] = np.minimum(
        hard_penalty.loc[trap_risk], HARD_RISK_VALUE_TRAP_PENALTY
    )
    hard_penalty.loc[data_risk] = np.minimum(
        hard_penalty.loc[data_risk], HARD_RISK_DATA_PENALTY
    )
    hard_penalty.loc[stale_data] = np.minimum(
        hard_penalty.loc[stale_data], DATA_FRESHNESS_STALE_FACTOR
    )
    hard_reason = _append_reason(hard_reason, avoid, "回避信号")
    hard_reason = _append_reason(hard_reason, stage_risk, "生命周期风险")
    hard_reason = _append_reason(hard_reason, trap_risk, "价值陷阱风险高")
    hard_reason = _append_reason(hard_reason, data_risk, "技术数据覆盖不足")
    hard_reason = _append_reason(hard_reason, stale_data, "行情数据已过期")
    result["HardRiskFlag"] = avoid | stage_risk | trap_risk | data_risk | stale_data
    result["HardRiskPenalty"] = hard_penalty.round(4)
    result["HardRiskReason"] = hard_reason

    ranking_penalty_reason = hard_reason.copy()
    ranking_penalty_reason = _append_reason(
        ranking_penalty_reason, quality_action_block, "质量门槛未通过或数据不足"
    )
    ranking_penalty_reason = _append_reason(
        ranking_penalty_reason, trap_observe & ~trap_risk, "价值陷阱风险，转观察"
    )
    ranking_penalty_reason = _append_reason(
        ranking_penalty_reason, freshness_status.eq("延迟"), "行情数据延迟"
    )
    base_score = _number(
        result.get(
            "InstitutionalScore",
            result.get(
                "FinalScore", result.get("Score", pd.Series(0.0, index=result.index))
            ),
        ),
        0.0,
    )
    minimum_score_risk = base_score.lt(TRADE_READY_MIN_INSTITUTIONAL_SCORE)
    ranking_penalty_reason = _append_reason(
        ranking_penalty_reason,
        signal.isin({"BUY_NOW", "BREAKOUT_CONFIRM"}) & minimum_score_risk,
        "综合评分未达交易门槛",
    )
    result["RankingPenaltyReason"] = ranking_penalty_reason

    trade_ready = (
        signal.isin({"BUY_NOW", "BREAKOUT_CONFIRM"})
        & ~stage_risk
        & ~trap_observe
        & ~quality_action_block
        & ~data_risk
        & ~stale_data
        & ~minimum_score_risk
    )
    result["RankingEligibility"] = np.select(
        [
            avoid
            | trap_risk
            | lifecycle.isin({"派发", "DISTRIBUTION"})
            | stale_data,
            trade_ready,
        ],
        ["风险过滤", "推荐"],
        default="观察",
    )
    readiness_reason = pd.Series("等待趋势、量能或风险条件改善", index=result.index)
    active_signal = signal.isin({"BUY_NOW", "BREAKOUT_CONFIRM"})
    hard_filter = (
        avoid
        | trap_risk
        | lifecycle.isin({"派发", "DISTRIBUTION"})
        | stale_data
    )
    readiness_reason.loc[hard_filter] = "硬风险过滤，不纳入交易就绪组"
    readiness_reason.loc[active_signal & data_risk & ~hard_filter] = (
        "技术数据覆盖不足，转为观察"
    )
    readiness_reason.loc[
        active_signal & minimum_score_risk & ~data_risk & ~hard_filter
    ] = "综合评分未达交易门槛，转为观察"
    readiness_reason.loc[
        active_signal
        & ~minimum_score_risk
        & ~data_risk
        & ~hard_filter
        & ~quality_action_block
        & ~trap_observe
    ] = "买点、质量、数据与综合评分均满足执行条件"
    result["TradeReadinessReason"] = readiness_reason
    result["OpportunityStage"] = lifecycle
    result.loc[trap_observe, "OpportunityStage"] = "底部观察"
    result.loc[quality_action_block & ~trap_observe, "OpportunityStage"] = "观察"

    entry_factor = signal.map(ENTRY_SIGNAL_MULTIPLIERS).fillna(
        ENTRY_SIGNAL_MULTIPLIERS["HOLD_WAIT"]
    )
    result["EntrySignalPriority"] = signal.map(ENTRY_SIGNAL_PRIORITIES).fillna(0.0)
    chase_factor = (
        1.0 - (chase / 100.0) * (1.0 - CHASE_RISK_MAX_PENALTY)
    ).clip(CHASE_RISK_MAX_PENALTY, 1.0)
    data_confidence = (
        (
            0.90
            + 0.06 * result["QualityDataCompleteness"]
            + 0.04 * score_coverage.clip(0.0, 1.0)
        ).clip(0.85, 1.0)
        * freshness_factor
    ).clip(DATA_FRESHNESS_STALE_FACTOR, 1.0)
    recency_factor = _number(
        result.get("SignalRecencyFactor", pd.Series(1.0, index=result.index)), 1.0
    ).clip(0.7, 1.0)
    recency_multiplier = 0.8 + 0.2 * recency_factor
    result["DataConfidenceFactor"] = data_confidence.round(4)
    result["ChaseRiskFactor"] = chase_factor.round(4)
    result["RankingScore"] = (
        base_score
        * entry_factor
        * hard_penalty
        * chase_factor
        * data_confidence
        * recency_multiplier
    ).round(4)

    score = _number(result["InstitutionalScore"], 0.0)
    result["InstitutionalRank"] = score.rank(method="min", ascending=False).astype(int)
    result["InstitutionalPercentile"] = (
        score.rank(method="average", pct=True) * 100.0
    ).round(2)
    percentile = result["InstitutionalPercentile"]
    signal_recency_days = _number(
        result.get("SignalRecencyDays", pd.Series(np.nan, index=result.index)), np.nan
    )
    recent_signal = signal_recency_days.between(0.0, 20.0)
    no_top_risk = (
        ~result["HardRiskFlag"]
        & signal.ne("AVOID")
        & result["QualityGate"]
        & ~quality_action_block
        & recent_signal
    )
    tier = pd.Series(INSTITUTIONAL_TIER_WAIT_LABEL, index=result.index)
    tier.loc[
        percentile.ge(INSTITUTIONAL_TIER_C_PERCENTILE)
        & score.ge(INSTITUTIONAL_TIER_C_SCORE)
    ] = "C级价值观察"
    tier.loc[
        percentile.ge(INSTITUTIONAL_TIER_B_PERCENTILE)
        & score.ge(INSTITUTIONAL_TIER_B_SCORE)
    ] = "B级观察"
    tier.loc[
        percentile.ge(INSTITUTIONAL_TIER_A_PERCENTILE)
        & score.ge(INSTITUTIONAL_TIER_A_SCORE)
        & no_top_risk
        & result["QualityDataCompleteness"].ge(
            INSTITUTIONAL_TIER_MIN_DATA_CONFIDENCE
        )
    ] = "A级机构启动"
    confirmed_quality_fail = ~result["QualityGate"] & ~is_etf
    tier.loc[confirmed_quality_fail & tier.eq("B级观察")] = "C级价值观察"
    tier.loc[trap.ge(VALUE_TRAP_RISK_THRESHOLD)] = INSTITUTIONAL_TIER_TRAP_LABEL
    tier.loc[signal.eq("AVOID") & tier.eq("A级机构启动")] = "B级观察"
    result["InstitutionalTier"] = tier
    result["InstitutionalTierReason"] = "分位和绝对分均未达到门槛"
    result.loc[tier.eq("A级机构启动"), "InstitutionalTierReason"] = (
        "市场前10%且满足绝对质量、时效与信号门槛"
    )
    result.loc[tier.eq("B级观察"), "InstitutionalTierReason"] = (
        "市场前25%且满足绝对分门槛"
    )
    result.loc[tier.eq("C级价值观察"), "InstitutionalTierReason"] = (
        "市场前50%且满足绝对分门槛"
    )
    result.loc[tier.eq(INSTITUTIONAL_TIER_TRAP_LABEL), "InstitutionalTierReason"] = (
        "价值陷阱风险限制等级"
    )

    rank_reason = pd.Series("等待趋势与量能确认", index=result.index)
    rank_reason.loc[signal.eq("BUY_NOW")] = "回踩结构与趋势满足买点"
    rank_reason.loc[signal.eq("BREAKOUT_CONFIRM")] = "量价与资金确认突破"
    rank_reason.loc[
        signal.isin({"PRICE_BREAKOUT", "WAIT_VOLUME_CONFIRM"})
    ] = "价格突破，等待量能确认"
    rank_reason.loc[signal.eq("WAIT_PULLBACK")] = "趋势确认，等待回调"
    rank_reason.loc[avoid] = "风险过滤：回避信号"
    rank_reason.loc[chase.ge(CHASE_RISK_HIGH_THRESHOLD)] = "高位过热，风险降级"
    rank_reason.loc[quality_action_block] = "质量门槛未通过或数据不足，转为观察"
    rank_reason.loc[trap_observe & ~trap_risk] = "价值陷阱风险，转为观察"
    rank_reason.loc[stale_data] = "行情数据已过期，风险过滤"
    rank_reason.loc[active_signal & data_risk & ~hard_filter] = (
        "技术数据覆盖不足，转为观察"
    )
    rank_reason.loc[
        active_signal & minimum_score_risk & ~data_risk & ~hard_filter
    ] = "买点成立但综合评分不足，转为观察"
    rank_reason.loc[samples.lt(BACKTEST_MIN_SAMPLES_FOR_RANKING) & ~avoid] += (
        "；回测样本不足，不参与校准"
    )
    result["RankingReason"] = rank_reason
    eligibility_order = result["RankingEligibility"].map(
        {"推荐": 2, "观察": 1, "风险过滤": 0}
    ).fillna(0)
    result = result.assign(_EligibilityOrder=eligibility_order).sort_values(
        [
            "_EligibilityOrder",
            "RankingScore",
            "InstitutionalScore",
            "FinalScore",
            "Score",
        ],
        ascending=[False, False, False, False, False],
        kind="mergesort",
    ).reset_index(drop=True)
    result["OverallRank"] = np.arange(1, len(result) + 1)
    return result.drop(columns="_EligibilityOrder")


def _atomic_write(df: pd.DataFrame, path: Path) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        df.to_csv(temporary_path, index=False, encoding="utf-8-sig")
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _load_history() -> pd.DataFrame:
    if not HISTORY_FILE.exists():
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    try:
        history = pd.read_csv(
            HISTORY_FILE, encoding="utf-8-sig", dtype={"Ticker": str}
        )
    except (OSError, UnicodeError, pd.errors.ParserError):
        return pd.DataFrame(columns=HISTORY_COLUMNS)
    text_columns = {
        "TradeDate",
        "Ticker",
        "Name",
        "InstitutionalTier",
        "SignalStatus",
        "SignalStartDate",
        "Stage",
    }
    outcome_columns = {
        "Return20D",
        "MaxDrawdown20D",
        "Return60D",
        "MaxDrawdown60D",
    }
    for column in HISTORY_COLUMNS:
        if column not in history:
            history[column] = (
                ""
                if column in text_columns
                else np.nan
                if column in outcome_columns
                else 0
            )
    return history


def _period_scores(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    trend = _number(frame.get("TrendScore", pd.Series(index=frame.index))) / 20 * 100
    volume = _number(frame.get("VolumeScore", pd.Series(index=frame.index))) / 25 * 100
    accumulation = (
        _number(frame.get("AccumulationScore", pd.Series(index=frame.index))) / 25 * 100
    )
    structure = (
        _number(frame.get("StructureScore", pd.Series(index=frame.index))) / 15 * 100
    )
    compression = (
        _number(frame.get("CompressionScore", pd.Series(index=frame.index))) / 15 * 100
    )
    industry = (
        (
            _number(frame.get("IndustryRelativeStrength", pd.Series(index=frame.index)))
            + 10
        ).clip(0, 20)
        / 20
        * 100
    )
    short = (
        volume * 0.45 + accumulation * 0.25 + trend * 0.20 + compression * 0.10
    ).round(2)
    middle = (
        trend * 0.35 + accumulation * 0.35 + structure * 0.20 + volume * 0.10
    ).round(2)
    long = (
        trend * 0.40 + structure * 0.30 + industry * 0.20 + accumulation * 0.10
    ).round(2)
    return short, middle, long


def _opportunity_score(
    short: pd.Series, middle: pd.Series, long: pd.Series
) -> pd.Series:
    return (short * 0.30 + middle * 0.40 + long * 0.30).clip(0, 100).round(2)


def _is_active(frame: pd.DataFrame) -> pd.Series:
    score = _number(frame.get("Score", pd.Series(index=frame.index)))
    signals = _number(frame.get("SignalCount", pd.Series(index=frame.index)))
    passed = frame.get("PassedFilters", pd.Series(False, index=frame.index)).map(_bool)
    return passed | ((score >= 35) & (signals >= 3))


def _stage(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    stage = (
        frame.get("Stage", pd.Series("观察", index=frame.index))
        .fillna("观察")
        .astype(str)
    )
    rsi = _number(frame.get("RSI14", pd.Series(index=frame.index)), 50)
    distance = _number(frame.get("DistToLow52W", pd.Series(index=frame.index)))
    score = _number(frame.get("Score", pd.Series(index=frame.index)))
    accumulation = _number(
        frame.get("AccumulationScore", pd.Series(index=frame.index))
    )
    result = pd.Series("底部观察", index=frame.index)
    result.loc[
        stage.eq("正在吸筹") | ((accumulation >= 15) & (score >= 40))
    ] = "机构吸筹"
    result.loc[stage.eq("已经启动")] = "初始启动"
    result.loc[stage.eq("趋势确认")] = "趋势确认"
    result.loc[(rsi >= 68) & (distance >= 30)] = "主升浪"
    result.loc[(rsi >= 78) | (distance >= 60)] = "加速风险"
    result.loc[(rsi <= 40) & (distance >= 45)] = "派发"
    suggestion = result.map(
        {
            "底部观察": "等待信号改善",
            "机构吸筹": "等待突破确认",
            "初始启动": "关注回踩承接",
            "趋势确认": "顺势跟踪",
            "主升浪": "持有并上移止损",
            "加速风险": "控制追高风险",
            "派发": "规避或减仓",
        }
    )
    risk = pd.Series("结构仍需确认", index=frame.index)
    risk.loc[result.eq("加速风险")] = "短期乖离偏高"
    risk.loc[result.eq("派发")] = "趋势与资金转弱"
    risk.loc[distance.between(0, 8)] = "接近52周低位，关注破位风险"
    return result, suggestion, risk


def _status(
    active: bool, previous: pd.Series | None, opportunity: float, days: int
) -> str:
    if not active:
        return (
            "FAILED"
            if previous is not None and _bool(previous["SignalActive"])
            else ""
        )
    if previous is None or not _bool(previous["SignalActive"]):
        return "NEW"
    previous_score = float(previous["OpportunityScore"])
    if days >= 5 and opportunity >= previous_score - 1:
        return "CONFIRMED"
    if opportunity >= previous_score + 2:
        return "STRENGTHEN"
    if opportunity <= previous_score - 2:
        return "WEAKEN"
    return "WATCH"


def enrich_signal_lifecycle(frame: pd.DataFrame) -> pd.DataFrame:
    """Track lifecycle/history only; ranking and tiers are finalized once below."""
    if frame.empty:
        return frame
    result = frame.copy()
    result["Ticker"] = result["Ticker"].astype(str).str.strip().str.upper()
    short, middle, long = _period_scores(result)
    result["ShortTermScore"] = short
    result["MediumTermScore"] = middle
    result["LongTermScore"] = long
    result["OpportunityScore"] = _opportunity_score(short, middle, long)
    result["LifecycleStage"], result["ActionSuggestion"], result["RiskNote"] = _stage(
        result
    )
    entry_signal = (
        result.get("EntrySignal", pd.Series("AVOID", index=result.index))
        .fillna("AVOID")
        .astype(str)
        .str.strip()
        .str.upper()
    )
    acceleration = result["LifecycleStage"].eq("加速风险")
    distribution = result["LifecycleStage"].eq("派发")
    result.loc[
        acceleration
        & entry_signal.isin(["BUY_NOW", "BREAKOUT_CONFIRM", "WAIT_PULLBACK"]),
        "EntrySignal",
    ] = "HOLD_WAIT"
    result.loc[distribution & entry_signal.ne("AVOID"), "EntrySignal"] = "AVOID"

    active = _is_active(result)
    history = _load_history()
    history["Ticker"] = history["Ticker"].astype(str).str.strip().str.upper()
    history["TradeDate"] = history["TradeDate"].astype(str)
    trade_dates = (
        result.get("DataAsOf", pd.Series("", index=result.index))
        .fillna("")
        .astype(str)
    )
    prior_history = history.copy()
    prior_history["_TradeDate"] = pd.to_datetime(
        prior_history["TradeDate"], errors="coerce"
    )
    dated_history = prior_history.dropna(subset=["_TradeDate"])
    history_by_ticker: dict[str, pd.DataFrame] = {
        str(ticker): group
        for ticker, group in dated_history.groupby("Ticker", sort=False)
    }
    previous_lookup = dated_history.drop_duplicates(
        ["_TradeDate", "Ticker"], keep="last"
    ).set_index(["_TradeDate", "Ticker"], drop=False)
    historical_dates = pd.DatetimeIndex(
        dated_history["_TradeDate"].dropna().unique()
    ).sort_values()
    parsed_trade_dates = pd.to_datetime(trade_dates, errors="coerce")
    previous_date_by_trade_date: dict[pd.Timestamp, pd.Timestamp] = {}
    for trade_date in pd.DatetimeIndex(parsed_trade_dates.dropna().unique()):
        position = historical_dates.searchsorted(trade_date, side="left")
        if position:
            previous_date_by_trade_date[pd.Timestamp(trade_date)] = pd.Timestamp(
                historical_dates[position - 1]
            )

    signal_days: list[int] = []
    starts: list[str] = []
    statuses: list[str] = []
    strengths: list[str] = []
    ticker_values = result["Ticker"].to_numpy(dtype=str)
    trade_date_values = parsed_trade_dates.to_numpy()
    active_values = active.to_numpy(dtype=bool)
    opportunity_values = result["OpportunityScore"].to_numpy(dtype=float)
    for position, ticker in enumerate(ticker_values):
        trade_date_text = str(trade_dates.iloc[position])
        previous: pd.Series | None = None
        ticker_history = history_by_ticker.get(ticker)
        trade_date = trade_date_values[position]
        if not pd.isna(trade_date):
            previous_date = previous_date_by_trade_date.get(pd.Timestamp(trade_date))
            if previous_date is not None:
                key = (previous_date, ticker)
                if key in previous_lookup.index:
                    previous = previous_lookup.loc[key]
        is_active = bool(active_values[position])
        prior_active = previous is not None and _bool(previous["SignalActive"])
        if is_active and prior_active and previous is not None:
            days = int(previous["SignalDays"]) + 1
            start = str(previous["SignalStartDate"])
        else:
            days = int(is_active)
            start = trade_date_text if is_active else ""
        statuses.append(
            _status(is_active, previous, float(opportunity_values[position]), days)
        )
        values = (
            ticker_history["OpportunityScore"].tail(29).tolist()
            if ticker_history is not None
            else []
        )
        strengths.append(
            "|".join(
                f"{value:.0f}"
                for value in [*values, float(opportunity_values[position])]
            )
        )
        signal_days.append(days)
        starts.append(start)

    result["SignalDays"] = signal_days
    result["SignalStartDate"] = starts
    signal_start = pd.to_datetime(result["SignalStartDate"], errors="coerce")
    data_asof = pd.to_datetime(trade_dates, errors="coerce")
    recency_days = (data_asof - signal_start).dt.days
    valid_recency = recency_days.notna() & recency_days.ge(0)
    result["SignalRecencyDays"] = recency_days.where(valid_recency)
    result["SignalRecencyFactor"] = np.where(
        valid_recency,
        np.maximum(0.7, 1.0 - recency_days / 100.0),
        1.0,
    )
    result["BreakoutQualityFactor"] = _number(
        result.get("BreakoutQualityFactor", pd.Series(1.0, index=result.index)), 1.0
    ).clip(0.0, 1.0)
    result["SignalStatus"] = statuses
    result["SignalStrengthHistory"] = strengths
    result["SignalTrend"] = (
        result["SignalStatus"]
        .map(
            {
                "STRENGTHEN": "持续增强",
                "WEAKEN": "快速下降",
                "CONFIRMED": "趋势确认",
                "WATCH": "横盘观察",
                "NEW": "新出现",
                "FAILED": "信号失效",
            }
        )
        .fillna("无信号")
    )
    result["ScoreConfidencePct"] = (
        _number(
            result.get("ScoreConfidence", pd.Series(index=result.index)), 0.0
        )
        * 100
    ).round(0)

    # Keep row identity stable for resume/history consumers while allowing the
    # single finalizer to calculate all mutable ranking/tier fields once.
    result["_LifecycleInputOrder"] = np.arange(len(result))
    result = finalize_signal_ranking(result)
    result = (
        result.sort_values("_LifecycleInputOrder", kind="mergesort")
        .drop(columns="_LifecycleInputOrder")
        .reset_index(drop=True)
    )

    snapshot = pd.DataFrame(
        {
            "TradeDate": _text_series(result, "DataAsOf", ""),
            "Return20D": _number(
                result.get("Return20D", pd.Series(index=result.index)), np.nan
            ),
            "MaxDrawdown20D": _number(
                result.get("MaxDrawdown20D", pd.Series(index=result.index)), np.nan
            ),
            "Return60D": _number(
                result.get("Return60D", pd.Series(index=result.index)), np.nan
            ),
            "MaxDrawdown60D": _number(
                result.get("MaxDrawdown60D", pd.Series(index=result.index)), np.nan
            ),
            "Ticker": result["Ticker"],
            "Name": result.get("Name", pd.Series("", index=result.index)),
            "Close": _number(
                result.get("Close", pd.Series(index=result.index)), np.nan
            ),
            "Score": _number(result["Score"]),
            "OpportunityScore": _number(result["OpportunityScore"]),
            "InstitutionalScore": _number(result["InstitutionalScore"], np.nan),
            "InstitutionalTier": result["InstitutionalTier"],
            "BreakoutQualityFactor": _number(
                result["BreakoutQualityFactor"], np.nan
            ),
            "SignalRecencyFactor": _number(result["SignalRecencyFactor"], np.nan),
            "SectorConfirmationFactor": _number(
                result.get(
                    "SectorConfirmationFactor", pd.Series(index=result.index)
                ),
                np.nan,
            ),
            "FailureSignalFactor": _number(
                result.get("FailureSignalFactor", pd.Series(index=result.index)),
                np.nan,
            ),
            "ScoreConfidence": _number(
                result.get("ScoreConfidence", pd.Series(index=result.index))
            ),
            "SignalActive": active.map(bool),
            "SignalStatus": result["SignalStatus"],
            "SignalDays": result["SignalDays"],
            "SignalStartDate": result["SignalStartDate"],
            "Stage": result["LifecycleStage"],
            "TrendScore": _number(
                result.get("TrendScore", pd.Series(index=result.index))
            ),
            "AccumulationScore": _number(
                result.get("AccumulationScore", pd.Series(index=result.index))
            ),
            "IndustryRelativeStrength": _number(
                result.get(
                    "IndustryRelativeStrength", pd.Series(index=result.index)
                )
            ),
            "SignalCount": _number(
                result.get("SignalCount", pd.Series(index=result.index))
            ),
        }
    )
    if not history.empty:
        outcome_columns = [
            "Return20D",
            "MaxDrawdown20D",
            "Return60D",
            "MaxDrawdown60D",
        ]
        prior_outcomes = history[
            ["TradeDate", "Ticker", *outcome_columns]
        ].drop_duplicates(["TradeDate", "Ticker"], keep="last")
        snapshot = snapshot.drop(columns=outcome_columns).merge(
            prior_outcomes,
            on=["TradeDate", "Ticker"],
            how="left",
            validate="one_to_one",
        )
    history = (
        snapshot
        if history.empty
        else pd.concat([history, snapshot], ignore_index=True)
    )
    history = history.drop_duplicates(
        ["TradeDate", "Ticker"], keep="last"
    ).sort_values(["TradeDate", "Ticker"])
    _atomic_write(history[HISTORY_COLUMNS], HISTORY_FILE)
    active = _is_active(result)
    tracking = result.loc[active].sort_values(
        ["SignalDays", "OpportunityScore"], ascending=False
    )
    _atomic_write(tracking, TRACKING_FILE)
    return result
