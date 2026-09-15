"""Per-signal derived attributes for the lifecycle engine.

Extracted from ``signal_lifecycle_core`` (T4).  The seventeen functions here are
the dependency closure that ``tests/recon_extraction_targets.py --seed`` reports
as self-consistent: none of them is rebound by an overlay, and none of them
depends on anything left behind.

Three things are deliberately NOT here:

* ``_is_active``, ``finalize_signal_ranking`` and ``strict_filter_override_mask``
  -- all three resolve to ``signal_lifecycle`` at runtime.  Moving them would
  leave the overlay rebinding ``signal_lifecycle_core`` while production read
  this module.
* ``_load_history`` -- it reads ``HISTORY_FILE`` and ``HISTORY_COLUMNS``, which
  are defined in ``signal_lifecycle_core`` and also used by
  ``enrich_signal_lifecycle``.  Importing them from there would make this module
  depend on the module it was extracted from.
* ``enrich_signal_lifecycle`` -- it calls the rebound ``_is_active`` and
  ``finalize_signal_ranking``, so extracting it would detach production from the
  patch.

The config constants below are imported by value.  That is safe *today*:
comparing ``config`` before and after the production assembly leaves all eleven
unchanged, while the ``INSTITUTIONAL_TIER_*`` scores that T3' had to late-bind do
move (35.0 -> 36.0825).  ``CONSTANT_NAMES`` in
``tests/golden_signal_lifecycle.py`` re-checks that every run, so a future
migration is caught as a mismatch rather than a silent off-by-one.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

from config import (
    BACKTEST_FULL_WEIGHT_SAMPLES,
    BACKTEST_LOW_CONFIDENCE_MAX_SAMPLES,
    BACKTEST_MIN_SAMPLES_FOR_RANKING,
    BACKTEST_NORMAL_WEIGHT,
    BREAKOUT_CONFIRM_MIN_VOLUME_RATIO,
    DATA_FRESHNESS_DELAYED_FACTOR,
    DATA_FRESHNESS_DELAYED_TRADING_DAYS,
    DATA_FRESHNESS_STALE_FACTOR,
    DATA_FRESHNESS_STALE_TRADING_DAYS,
    TRADE_READY_MAX_STOP_DISTANCE_PCT,
    TRADE_READY_MIN_REWARD_RISK,
)


def _bool(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y", "是"}


def _number(series: pd.Series, default: float = 0.0) -> pd.Series:
    return (
        pd.to_numeric(series, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .fillna(default)
    )


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
    # Reason construction happens in both the stable engine and the current
    # policy facade.  Make appends idempotent so a blocker can be reconciled
    # more than once without producing duplicated audit text.
    has_reason = ("；" + existing + "；").str.contains(
        "；" + str(reason) + "；", regex=False
    )
    should_append = condition.fillna(False).astype(bool) & ~has_reason
    return existing.where(
        ~should_append,
        np.where(existing.eq(""), reason, existing + "；" + reason),
    )


def _execution_risk_block(
    frame: pd.DataFrame, signal: pd.Series
) -> pd.Series:
    """Block current active signals whose exported risk geometry is invalid.

    A field that is absent from a legacy schema remains neutral.  Every field
    present in the schema must be finite and valid, so current scanner rows
    (which export both fields) cannot become trade-ready through a NaN value.
    """
    stop_present = "StopDistancePct" in frame.columns
    reward_present = "RewardRiskRatio" in frame.columns
    if not (stop_present or reward_present):
        return pd.Series(False, index=frame.index)

    stop_distance = _number(
        frame.get("StopDistancePct", pd.Series(np.nan, index=frame.index)),
        np.nan,
    )
    reward_risk = _number(
        frame.get("RewardRiskRatio", pd.Series(np.nan, index=frame.index)),
        np.nan,
    )
    stop_ok = pd.Series(True, index=frame.index) if not stop_present else (
        stop_distance.notna()
        & stop_distance.gt(0.0)
        & stop_distance.le(TRADE_READY_MAX_STOP_DISTANCE_PCT)
    )
    reward_ok = pd.Series(True, index=frame.index) if not reward_present else (
        reward_risk.notna()
        & reward_risk.ge(TRADE_READY_MIN_REWARD_RISK)
    )
    return signal.isin({"BUY_NOW", "BREAKOUT_CONFIRM"}) & ~(
        stop_ok & reward_ok
    )


def _breakout_confirmation_ok(
    frame: pd.DataFrame, signal: pd.Series
) -> pd.Series:
    """Require boolean confirmations and the event-volume ratio when present."""
    breakout = signal.eq("BREAKOUT_CONFIRM")
    confirmed = (
        _bool_series(frame, "BreakoutVolumeConfirmed", False)
        & _bool_series(frame, "BreakoutFlowConfirmed", False)
    )
    if "BreakoutVolumeRatio" in frame.columns:
        ratio = _number(frame["BreakoutVolumeRatio"], np.nan)
        confirmed &= ratio.notna() & ratio.ge(BREAKOUT_CONFIRM_MIN_VOLUME_RATIO)
    return ~breakout | confirmed


def _lifecycle_risk_masks(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Return terminal and rapidly weakening lifecycle masks.

    Strict base-filter overrides, final eligibility and integrity validation
    must consume one lifecycle definition.  Keeping this logic here prevents a
    confirmed breakout from being treated as an override in the core pass and
    then losing that override only after the final policy pass.
    """
    status = _text_series(frame, "SignalStatus", "").str.upper()
    trend = _text_series(frame, "SignalTrend", "").str.upper()
    terminal = status.isin({"FAILED", "EXPIRED", "INACTIVE"})
    weakening = status.eq("WEAKEN") & (
        trend.str.contains("快速", regex=False)
        | trend.str.contains("FAST", regex=False)
        | trend.str.contains("RAPID", regex=False)
    )
    return terminal, weakening


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
    """Validate technical confirmation without rewriting the raw EntrySignal."""
    result = frame.copy()
    signal = _text_series(result, "EntrySignal", "AVOID").str.upper()
    result["RawEntrySignal"] = _text_series(result, "RawEntrySignal", "")
    result.loc[result["RawEntrySignal"].eq(""), "RawEntrySignal"] = signal
    adjustments = _text_series(result, "SignalAdjustmentReason", "")

    volume_ratio = _number(
        result.get("BreakoutVolumeRatio", pd.Series(np.nan, index=result.index)), np.nan
    )
    observed_volume_confirmation = volume_ratio.notna() & volume_ratio.ge(
        BREAKOUT_CONFIRM_MIN_VOLUME_RATIO
    )
    if "BreakoutVolumeConfirmed" in result:
        supplied_volume_confirmation = _bool_series(
            result, "BreakoutVolumeConfirmed"
        )
        # Legacy exports predate BreakoutVolumeRatio.  Preserve their explicit
        # confirmation flag for schema compatibility; whenever the ratio field
        # exists, current event evidence is authoritative and fail-closed.
        volume_confirmed = (
            supplied_volume_confirmation & observed_volume_confirmation
            if "BreakoutVolumeRatio" in result
            else supplied_volume_confirmation
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
    adjustments = _append_reason(
        adjustments, weak_breakout, "突破状态缺少量能或资金确认，决策层转观察"
    )
    result["EntrySignal"] = signal
    result["BreakoutVolumeConfirmed"] = volume_confirmed
    result["BreakoutFlowConfirmed"] = flow_confirmed
    result["PriceBreakout"] = result.get(
        "PriceBreakout",
        signal.isin({"PRICE_BREAKOUT", "BREAKOUT_CONFIRM"}),
    ).fillna(False)
    result["SignalAdjustmentReason"] = adjustments
    return result


def _atomic_write(df: pd.DataFrame, path: Path) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        df.to_csv(temporary_path, index=False, encoding="utf-8-sig")
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


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
    risk = pd.Series("结构仍需确认", index=result.index)
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
