"""Current lifecycle/ranking policy facade.

``signal_lifecycle_core`` provides the stable lifecycle engine.  The facade
keeps cross-asset normalization bounded and reconciles lifecycle, tier and
decision state after Fundamental Gate 2.0.  v39 additionally requires the
core pass to preserve v38 fundamental-gate authority end to end.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import config as _config
import signal_lifecycle_core as _core
from signal_lifecycle_core import *  # noqa: F403

_legacy_finalize_signal_ranking = _core.finalize_signal_ranking


def _recompute_cross_asset_score(result: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    is_etf = _core._bool_series(result, "IsETF") | _core._text_series(
        result, "AssetType", ""
    ).str.lower().eq("etf")
    raw = _core._number(
        result.get(
            "InstitutionalScore",
            result.get("FinalScore", result.get("Score", pd.Series(0.0, index=result.index))),
        ),
        0.0,
    ).clip(0.0, 100.0)
    asset_group = pd.Series(np.where(is_etf, "ETF", "STOCK"), index=result.index)
    valid = raw.gt(0.0) & np.isfinite(raw)
    group_size = valid.groupby(asset_group).transform("sum")
    asset_percentile = raw.where(valid).groupby(asset_group).rank(
        method="average", pct=True
    ) * 100.0
    use_relative = valid & group_size.ge(5) & asset_percentile.notna()
    max_adjustment = float(
        getattr(_config, "CROSS_ASSET_PERCENTILE_MAX_ADJUSTMENT", 5.0)
    )
    adjustment = (
        (asset_percentile - 50.0) / 50.0 * max_adjustment
    ).clip(-max_adjustment, max_adjustment)
    adjustment = adjustment.where(use_relative, 0.0).fillna(0.0)
    corrected = (raw + adjustment).clip(0.0, 100.0)

    result["AssetPercentile"] = asset_percentile.round(2)
    result["CrossAssetAdjustment"] = adjustment.round(4)
    result["CrossAssetScore"] = corrected.round(4)
    return corrected, is_etf


def _rescale_ranking_for_cross_asset(
    result: pd.DataFrame, corrected: pd.Series, legacy_cross: pd.Series
) -> None:
    ranking = _core._number(
        result.get("RankingScore", pd.Series(0.0, index=result.index)), 0.0
    )
    ratio = pd.Series(1.0, index=result.index)
    usable = (
        legacy_cross.gt(0.0)
        & corrected.notna()
        & np.isfinite(corrected)
        & np.isfinite(legacy_cross)
    )
    ratio.loc[usable] = corrected.loc[usable] / legacy_cross.loc[usable]
    result["RankingScore"] = (ranking * ratio).clip(lower=0.0).round(4)


def _lifecycle_masks(result: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    status = _core._text_series(result, "SignalStatus", "").str.upper()
    trend = _core._text_series(result, "SignalTrend", "").str.upper()
    terminal = status.isin({"FAILED", "EXPIRED", "INACTIVE"})
    weakening = status.eq("WEAKEN") & (
        trend.str.contains("快速", regex=False)
        | trend.str.contains("FAST", regex=False)
        | trend.str.contains("RAPID", regex=False)
    )
    return terminal, weakening


def _apply_lifecycle_integrity(
    result: pd.DataFrame, terminal: pd.Series, weakening: pd.Series
) -> None:
    old_hard = _core._number(
        result.get("HardRiskPenalty", pd.Series(1.0, index=result.index)), 1.0
    ).clip(lower=1e-9)
    new_hard = old_hard.copy()
    new_hard.loc[terminal] = np.minimum(
        new_hard.loc[terminal], float(_config.HARD_RISK_STAGE_PENALTY)
    )
    hard_ratio = (new_hard / old_hard).replace([np.inf, -np.inf], 1.0).fillna(1.0)
    result["RankingScore"] = (
        _core._number(result["RankingScore"], 0.0) * hard_ratio
    ).round(4)
    result["HardRiskPenalty"] = new_hard.round(4)
    result["HardRiskFlag"] = _core._bool_series(result, "HardRiskFlag") | terminal

    hard_reason = _core._text_series(result, "HardRiskReason", "")
    result["HardRiskReason"] = _core._append_reason(
        hard_reason, terminal, "信号生命周期已结束"
    )
    ranking_reason = _core._text_series(result, "RankingPenaltyReason", "")
    ranking_reason = _core._append_reason(
        ranking_reason, terminal, "信号生命周期已结束"
    )
    ranking_reason = _core._append_reason(
        ranking_reason, weakening, "信号快速衰减，禁止进入推荐"
    )
    result["RankingPenaltyReason"] = ranking_reason

    old_readiness = _core._number(
        result.get("ReadinessPenaltyFactor", pd.Series(1.0, index=result.index)),
        1.0,
    ).clip(lower=1e-9)
    new_readiness = old_readiness.copy()
    weaken_factor = float(getattr(_config, "LIFECYCLE_WEAKEN_RANKING_FACTOR", 0.82))
    new_readiness.loc[weakening] = (
        new_readiness.loc[weakening] * weaken_factor
    ).clip(lower=0.0, upper=1.0)
    readiness_ratio = (
        new_readiness / old_readiness
    ).replace([np.inf, -np.inf], 1.0).fillna(1.0)
    result["RankingScore"] = (
        _core._number(result["RankingScore"], 0.0) * readiness_ratio
    ).round(4)
    result["ReadinessPenaltyFactor"] = new_readiness.round(4)



def _strip_reason_tokens(series: pd.Series, tokens: tuple[str, ...]) -> pd.Series:
    cleaned = series.fillna("").astype(str)
    for token in tokens:
        cleaned = cleaned.str.replace(token, "", regex=False)
    cleaned = cleaned.str.replace("；；", "；", regex=False)
    return cleaned.str.strip("；， ")


def _sync_final_explanations(
    result: pd.DataFrame,
    strong_ready: pd.Series,
    cautious_ready: pd.Series,
    filter_override: pd.Series,
) -> None:
    """Synchronize reason/penalty text with the final post-normalization decision."""
    actionable = strong_ready | cautious_ready
    readiness_reason = _core._text_series(
        result, "TradeReadinessReason", "等待趋势、量能或风险条件改善"
    )
    ranking_reason = _core._text_series(result, "RankingReason", "")
    ranking_reason.loc[actionable] = readiness_reason.loc[actionable]

    backtest_eligible = _core._bool_series(result, "BacktestEligibleForRanking")
    confidence = _core._text_series(result, "BacktestConfidenceTier", "")
    backtest_status = _core._text_series(result, "BacktestStatus", "").str.upper()
    insufficient_evidence = actionable & ~backtest_eligible & (
        confidence.str.contains("样本不足", regex=False)
        | backtest_status.isin({"SAMPLES", "NO_SIGNAL_SAMPLES"})
    )
    ranking_reason.loc[insufficient_evidence] = (
        ranking_reason.loc[insufficient_evidence].str.rstrip("；")
        + "；回测样本不足，不参与校准"
    )

    strict_override = actionable & filter_override
    override_text = "量价资金确认突破，严格覆盖基础筛选缺口"
    missing_override_text = strict_override & ~ranking_reason.str.contains(
        override_text, regex=False
    )
    ranking_reason.loc[missing_override_text] = (
        ranking_reason.loc[missing_override_text].str.rstrip("；")
        + "；"
        + override_text
    )
    result["RankingReason"] = ranking_reason

    penalty = _core._text_series(result, "RankingPenaltyReason", "")
    if strong_ready.any():
        cleaned = _strip_reason_tokens(
            penalty.loc[strong_ready],
            (
                "B级仅列谨慎候选",
                "B级量价资金突破确认，谨慎候选",
            ),
        )
        penalty.loc[strong_ready] = cleaned
    result["RankingPenaltyReason"] = penalty


def _sync_final_action_text(
    result: pd.DataFrame,
    *,
    signal: pd.Series,
    strong_ready: pd.Series,
    cautious_ready: pd.Series,
    quality_action_block: pd.Series,
    terminal: pd.Series,
    weakening: pd.Series,
) -> None:
    """Make compact action/risk copy obey the final decision state.

    ``ActionSuggestion`` starts as a lifecycle-stage description.  It must be
    reconciled after quality, evidence tier and lifecycle checks; otherwise an
    OBSERVE row can still say ``顺势跟踪`` even though execution is explicitly
    disallowed elsewhere in the same record.
    """
    decision = _core._text_series(result, "DecisionState", "OBSERVE").str.upper()
    observe = decision.eq("OBSERVE")
    blocked = decision.eq("BLOCKED")

    suggestion = _core._text_series(result, "ActionSuggestion", "等待条件改善")
    suggestion.loc[observe] = "继续观察，等待条件改善"
    suggestion.loc[observe & signal.eq("WAIT_PULLBACK")] = "等待回调与资格确认"
    suggestion.loc[
        observe & signal.isin({"BUY_NOW", "BREAKOUT_CONFIRM"})
    ] = "仅观察，暂不执行"
    suggestion.loc[quality_action_block & observe] = "仅研究观察，等待基本面改善"
    suggestion.loc[weakening & ~terminal] = "等待信号重新增强"
    suggestion.loc[cautious_ready] = "谨慎观察，等待进一步确认"
    suggestion.loc[strong_ready & signal.eq("BUY_NOW")] = "执行条件满足，按计划分批"
    suggestion.loc[strong_ready & signal.eq("BREAKOUT_CONFIRM")] = (
        "突破确认，等待计划内执行"
    )
    suggestion.loc[blocked] = "风险过滤，暂不参与"
    result["ActionSuggestion"] = suggestion

    risk_note = _core._text_series(result, "RiskNote", "结构仍需确认")
    readiness_reason = _core._text_series(
        result, "TradeReadinessReason", "等待趋势、量能或风险条件改善"
    )
    hard_reason = _core._text_series(result, "HardRiskReason", "")
    risk_note.loc[quality_action_block & observe] = readiness_reason.loc[
        quality_action_block & observe
    ]
    risk_note.loc[weakening & ~terminal] = "信号快速衰减"
    risk_note.loc[cautious_ready] = "B级候选，仍需进一步确认"
    risk_note.loc[blocked] = hard_reason.loc[blocked].where(
        hard_reason.loc[blocked].str.strip().ne(""), readiness_reason.loc[blocked]
    )
    result["RiskNote"] = risk_note

def _recompute_tiers_and_decisions(
    result: pd.DataFrame,
    corrected: pd.Series,
    is_etf: pd.Series,
    terminal: pd.Series,
    weakening: pd.Series,
) -> None:
    result["InstitutionalRank"] = corrected.rank(
        method="min", ascending=False
    ).astype(int)
    result["InstitutionalPercentile"] = (
        corrected.rank(method="average", pct=True) * 100.0
    ).round(2)
    percentile = result["InstitutionalPercentile"]

    signal = _core._text_series(result, "EntrySignal", "AVOID").str.upper()
    lifecycle = _core._text_series(result, "LifecycleStage", "未知")
    trap = _core._number(
        result.get("ValueTrapRisk", pd.Series(0.0, index=result.index)), 0.0
    )
    score_coverage = _core._number(
        result.get("ScoreCoverage", pd.Series(1.0, index=result.index)), 1.0
    )
    quality_gate = _core._bool_series(result, "QualityGate", True)
    quality_applicable = (
        _core._bool_series(result, "QualityApplicable", True)
        if "QualityApplicable" in result
        else ~is_etf
    ) & ~is_etf
    quality_completeness = _core._number(
        result.get(
            "QualityDataCompleteness", pd.Series(0.0, index=result.index)
        ),
        0.0,
    ).clip(0.0, 1.0)
    hard_data_complete = _core._bool_series(
        result, "QualityHardDataComplete", True
    )
    quality_action_block = quality_applicable & (
        quality_completeness.lt(_config.QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE)
        | ~hard_data_complete
        | ~quality_gate
    )
    recency_days = _core._number(
        result.get("SignalRecencyDays", pd.Series(np.nan, index=result.index)),
        np.nan,
    )
    recent_signal = recency_days.between(0.0, 20.0)
    hard_risk = _core._bool_series(result, "HardRiskFlag")
    no_top_risk = (
        ~hard_risk
        & signal.ne("AVOID")
        & quality_gate
        & ~quality_action_block
        & recent_signal
    )

    tier = pd.Series(_config.INSTITUTIONAL_TIER_WAIT_LABEL, index=result.index)
    tier.loc[
        percentile.ge(_config.INSTITUTIONAL_TIER_C_PERCENTILE)
        & corrected.ge(_config.INSTITUTIONAL_TIER_C_SCORE)
    ] = "C级价值观察"
    tier.loc[
        percentile.ge(_config.INSTITUTIONAL_TIER_B_PERCENTILE)
        & corrected.ge(_config.INSTITUTIONAL_TIER_B_SCORE)
    ] = "B级观察"
    tier.loc[
        percentile.ge(_config.INSTITUTIONAL_TIER_A_PERCENTILE)
        & corrected.ge(_config.INSTITUTIONAL_TIER_A_SCORE)
        & no_top_risk
        & (
            ~quality_applicable
            | quality_completeness.ge(
                _config.INSTITUTIONAL_TIER_MIN_DATA_CONFIDENCE
            )
        )
    ] = "A级机构启动"
    confirmed_quality_fail = ~quality_gate & ~is_etf
    tier.loc[confirmed_quality_fail & tier.eq("B级观察")] = "C级价值观察"
    tier.loc[trap.ge(_config.VALUE_TRAP_RISK_THRESHOLD)] = (
        _config.INSTITUTIONAL_TIER_TRAP_LABEL
    )
    tier.loc[signal.eq("AVOID") & tier.eq("A级机构启动")] = "B级观察"

    result["InstitutionalTier"] = tier
    result["ResearchTier"] = tier.map(
        {
            "A级机构启动": "A",
            "B级观察": "B",
            "C级价值观察": "C",
            _config.INSTITUTIONAL_TIER_WAIT_LABEL: "WAIT",
            _config.INSTITUTIONAL_TIER_TRAP_LABEL: "TRAP",
        }
    ).fillna("WAIT")
    tier_reason = pd.Series("分位和绝对分均未达到研究等级门槛", index=result.index)
    tier_reason.loc[tier.eq("A级机构启动")] = (
        "市场前10%且满足研究质量、时效与信号门槛"
    )
    tier_reason.loc[tier.eq("B级观察")] = "市场前25%且满足绝对分门槛"
    tier_reason.loc[tier.eq("C级价值观察")] = "市场前50%且满足绝对分门槛"
    tier_reason.loc[tier.eq(_config.INSTITUTIONAL_TIER_TRAP_LABEL)] = (
        "价值陷阱风险限制等级"
    )
    result["InstitutionalTierReason"] = tier_reason

    passed_filters = _core._bool_series(result, "PassedFilters", True)
    universe_eligible = _core._bool_series(result, "UniverseEligible", True)
    breakout_confirmation_ok = _core._breakout_confirmation_ok(result, signal)
    filter_override = (
        ~passed_filters
        & universe_eligible
        & signal.eq("BREAKOUT_CONFIRM")
        & _core._bool_series(result, "BreakoutVolumeConfirmed")
        & _core._bool_series(result, "BreakoutFlowConfirmed")
        & _core._breakout_confirmation_ok(result, signal)
        & ~terminal
        & ~weakening
    )
    stage_risk = lifecycle.isin({"加速风险", "派发", "DISTRIBUTION"})
    trap_observe = trap.ge(_config.VALUE_TRAP_RISK_THRESHOLD)
    trap_risk = trap.ge(_config.VALUE_TRAP_HARD_RISK_THRESHOLD)
    data_risk = score_coverage.lt(0.45)
    stale_data = _core._text_series(
        result, "DataFreshnessStatus", "未知"
    ).eq("过期")
    minimum_score_risk = _core._number(
        result.get(
            "InstitutionalScore", pd.Series(0.0, index=result.index)
        ),
        0.0,
    ).lt(_config.TRADE_READY_MIN_INSTITUTIONAL_SCORE)
    chase = _core._number(
        result.get("ChaseRiskScore", pd.Series(0.0, index=result.index)), 0.0
    )
    execution_risk_block = _core._execution_risk_block(result, signal)

    trade_ready = (
        signal.isin({"BUY_NOW", "BREAKOUT_CONFIRM"})
        & breakout_confirmation_ok
        & (passed_filters | filter_override)
        & ~terminal
        & ~weakening
        & ~stage_risk
        & ~trap_observe
        & ~quality_action_block
        & ~data_risk
        & ~stale_data
        & ~minimum_score_risk
        & ~execution_risk_block
        & chase.lt(_config.CHASE_RISK_HIGH_THRESHOLD)
    )
    hard_block = (
        signal.eq("AVOID")
        | trap_risk
        | lifecycle.isin({"派发", "DISTRIBUTION"})
        | stale_data
        | terminal
    )
    decision = pd.Series("OBSERVE", index=result.index)
    decision.loc[hard_block] = "BLOCKED"
    decision.loc[trade_ready] = "READY"

    strong_ready = decision.eq("READY") & tier.eq("A级机构启动")
    cautious_ready = (
        decision.eq("READY")
        & tier.eq("B级观察")
        & signal.eq("BREAKOUT_CONFIRM")
        & breakout_confirmation_ok
    )
    tier_demoted = decision.eq("READY") & ~(strong_ready | cautious_ready)
    decision.loc[cautious_ready] = "CAUTIOUS"
    decision.loc[tier_demoted] = "OBSERVE"

    result["DecisionState"] = decision
    result["RankingEligibility"] = decision.map(
        {
            "READY": "推荐",
            "CAUTIOUS": "谨慎候选",
            "OBSERVE": "观察",
            "BLOCKED": "风险过滤",
        }
    ).fillna("观察")
    result["TradeReadiness"] = result["RankingEligibility"]

    reason = _core._text_series(
        result, "TradeReadinessReason", "等待趋势、量能或风险条件改善"
    )
    reason.loc[strong_ready] = "买点、质量、数据与综合评分均满足执行条件"
    reason.loc[cautious_ready] = "B级观察但量价资金突破确认，列为谨慎候选"
    reason.loc[tier_demoted] = "研究等级未达到A级执行门槛，转为观察"
    reason.loc[execution_risk_block & ~hard_block] = (
        "止损距离或预期盈亏比未达执行门槛，转为观察"
    )
    reason.loc[
        signal.eq("BREAKOUT_CONFIRM")
        & ~breakout_confirmation_ok
        & ~hard_block
    ] = "突破事件量能或资金确认不足，转为观察"
    reason.loc[weakening & ~terminal] = (
        "信号处于WEAKEN且快速下降，等待重新增强后再进入推荐"
    )
    reason.loc[terminal] = "信号生命周期已结束，禁止作为当前交易信号"
    result["TradeReadinessReason"] = reason
    result["DecisionReason"] = reason
    _sync_final_explanations(result, strong_ready, cautious_ready, filter_override)

    advice = _core._text_series(result, "OperationAdvice", "")
    advice.loc[strong_ready & signal.eq("BUY_NOW")] = (
        "价格处于买入区间且执行门槛满足，可按计划分批执行。"
    )
    advice.loc[strong_ready & signal.eq("BREAKOUT_CONFIRM")] = (
        "量价资金突破确认且执行门槛满足，等待计划内执行条件。"
    )
    advice.loc[cautious_ready] = (
        "B级突破确认，仅列谨慎候选；控制仓位并等待进一步确认。"
    )
    advice.loc[tier_demoted] = (
        "技术买点存在，但研究等级不足以列为强推荐，继续观察。"
    )
    advice.loc[weakening & ~terminal] = (
        "信号正在快速衰减，等待强度重新增强后再评估。"
    )
    advice.loc[terminal] = "信号生命周期已结束，当前不参与。"
    result["OperationAdvice"] = advice
    _sync_final_action_text(
        result,
        signal=signal,
        strong_ready=strong_ready,
        cautious_ready=cautious_ready,
        quality_action_block=quality_action_block,
        terminal=terminal,
        weakening=weakening,
    )


def finalize_signal_ranking(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply the stable v34 pass, then enforce v35 score/decision integrity."""
    result = _legacy_finalize_signal_ranking(frame)
    if result is None or result.empty:
        return result

    legacy_cross = _core._number(
        result.get(
            "CrossAssetScore",
            result.get(
                "InstitutionalScore", pd.Series(0.0, index=result.index)
            ),
        ),
        0.0,
    )
    corrected, is_etf = _recompute_cross_asset_score(result)
    _rescale_ranking_for_cross_asset(result, corrected, legacy_cross)

    terminal, weakening = _lifecycle_masks(result)
    _apply_lifecycle_integrity(result, terminal, weakening)
    _recompute_tiers_and_decisions(
        result, corrected, is_etf, terminal, weakening
    )

    result["RankingScore"] = _core._number(
        result["RankingScore"], 0.0
    ).clip(lower=0.0).round(4)
    return result


_core.finalize_signal_ranking = finalize_signal_ranking
_core._sync_final_explanations = _sync_final_explanations
_core._sync_final_action_text = _sync_final_action_text
sys.modules[__name__] = _core
