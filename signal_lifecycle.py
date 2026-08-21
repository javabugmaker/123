"""v58 lifecycle policy facade.

v58 keeps the v52 setup-backed breakout override and v54 execution-only
liquidity gate, and adds a second execution-only freshness gate. Research rows
may remain visible when provider data is delayed, but READY/CAUTIOUS decisions
must be based on the latest completed trading session.

The v51 facade replaces its module entry with ``signal_lifecycle_core`` after
initialization. Therefore this facade wraps the exported v51
``finalize_signal_ranking`` instead of depending on private module replacement
order. Research RankingScore is never changed by the v58 execution gates.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import config as _config
import signal_lifecycle_v51 as _core
from execution_integrity_v87 import (
    stamp_breakout_price_diagnostics,
    stamp_trade_economics_diagnostics,
)
from research_policy_v87 import vectorized_etf_research_policy
from signal_lifecycle_v51 import *  # noqa: F403

_LEGACY_FINALIZE_SIGNAL_RANKING = _core.finalize_signal_ranking

_SETUP_COLUMNS = (
    "VolAccum",
    "volume_accumulation",
    "OBV_Div",
    "obv_divergence",
    "Consolidation",
    "consolidation",
    "VolContract",
    "volatility_contraction",
)


def _has_v52_setup_schema(frame: pd.DataFrame) -> bool:
    return "SignalCount" in frame.columns and any(
        column in frame.columns for column in _SETUP_COLUMNS
    )


def _setup_support_mask(frame: pd.DataFrame) -> pd.Series:
    # Historical result files and older unit fixtures legitimately do not have
    # the v52 setup columns. Preserve their old interpretation instead of
    # manufacturing a failure from missing future-schema fields.
    if not _has_v52_setup_schema(frame):
        return pd.Series(True, index=frame.index, dtype=bool)

    accumulation = (
        _core._bool_series(frame, "VolAccum", False)
        | _core._bool_series(frame, "volume_accumulation", False)
        | _core._bool_series(frame, "OBV_Div", False)
        | _core._bool_series(frame, "obv_divergence", False)
    )
    structure = (
        _core._bool_series(frame, "Consolidation", False)
        | _core._bool_series(frame, "consolidation", False)
        | _core._bool_series(frame, "VolContract", False)
        | _core._bool_series(frame, "volatility_contraction", False)
    )
    signal_count = _core._number(
        frame.get("SignalCount", pd.Series(0.0, index=frame.index)),
        0.0,
    )
    minimum = int(getattr(_config, "FILTER_OVERRIDE_MIN_SIGNAL_COUNT", 3))
    return (accumulation | structure) & signal_count.ge(minimum)


def strict_filter_override_mask(
    frame: pd.DataFrame,
    signal: pd.Series | None = None,
    passed_filters: pd.Series | None = None,
    universe_eligible: pd.Series | None = None,
) -> pd.Series:
    """Allow only setup-backed, fully confirmed current-schema overrides."""
    normalized_signal = (
        signal.fillna("AVOID").astype(str).str.strip().str.upper()
        if signal is not None
        else _core._text_series(frame, "EntrySignal", "AVOID").str.upper()
    )
    passed = (
        passed_filters.map(_core._bool)
        if passed_filters is not None
        else _core._bool_series(frame, "PassedFilters", True)
    )
    eligible = (
        universe_eligible.map(_core._bool)
        if universe_eligible is not None
        else _core._bool_series(frame, "UniverseEligible", True)
    )
    terminal, weakening = _core._lifecycle_risk_masks(frame)
    return (
        ~passed
        & eligible
        & normalized_signal.eq("BREAKOUT_CONFIRM")
        & _core._bool_series(frame, "BreakoutVolumeConfirmed", False)
        & _core._bool_series(frame, "BreakoutFlowConfirmed", False)
        & _core._breakout_confirmation_ok(frame, normalized_signal)
        & _setup_support_mask(frame)
        & ~terminal
        & ~weakening
    )


def _is_active(frame: pd.DataFrame) -> pd.Series:
    """Lifecycle activity must use the same canonical override as ranking."""
    score = _core._number(
        frame.get("Score", pd.Series(0.0, index=frame.index)),
        0.0,
    )
    signals = _core._number(
        frame.get("SignalCount", pd.Series(0.0, index=frame.index)),
        0.0,
    )
    passed = _core._bool_series(frame, "PassedFilters", False)
    eligible = _core._bool_series(frame, "UniverseEligible", True)
    entry_signal = _core._text_series(frame, "EntrySignal", "AVOID").str.upper()
    override = strict_filter_override_mask(
        frame,
        signal=entry_signal,
        passed_filters=passed,
        universe_eligible=eligible,
    )
    return passed | ((score >= 35.0) & (signals >= 3.0)) | override


def _trading_board_series(frame: pd.DataFrame, is_etf: pd.Series) -> pd.Series:
    ticker = _core._text_series(frame, "Ticker", "").str.upper()
    code = ticker.str.split(".").str[0]
    suffix = ticker.str.rsplit(".", n=1).str[-1]
    stock = ~is_etf
    board = pd.Series("其他", index=frame.index, dtype=object)
    board.loc[is_etf] = "ETF"
    board.loc[stock & suffix.eq("BJ")] = "北交所"
    board.loc[
        stock & suffix.eq("SH") & code.str.startswith(("688", "689"))
    ] = "科创板"
    board.loc[
        stock & suffix.eq("SH") & ~code.str.startswith(("688", "689"))
    ] = "沪市主板"
    board.loc[
        stock & suffix.eq("SZ") & code.str.startswith(("300", "301"))
    ] = "创业板"
    board.loc[
        stock & suffix.eq("SZ") & ~code.str.startswith(("300", "301"))
    ] = "深市主板"
    return board


def _add_board_diagnostics(
    result: pd.DataFrame,
    corrected: pd.Series,
    is_etf: pd.Series,
) -> None:
    """Expose board context without changing any score or eligibility state."""
    board = _trading_board_series(result, is_etf)
    numeric = pd.to_numeric(corrected, errors="coerce")
    valid = numeric.notna() & np.isfinite(numeric)
    counts = board.value_counts(dropna=False)
    board_rank = numeric.where(valid).groupby(board).rank(
        method="min", ascending=False
    )
    board_percentile = numeric.where(valid).groupby(board).rank(
        method="average", pct=True
    ) * 100.0

    result["TradingBoard"] = board
    result["BoardUniverseCount"] = board.map(counts).astype(int)
    result["BoardRank"] = board_rank.astype("Int64")
    result["BoardPercentile"] = board_percentile.round(2)
    result["BoardDiagnosticOnly"] = True


def _board_diagnostic_inputs(result: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    is_etf = _core._bool_series(result, "IsETF") | _core._text_series(
        result, "AssetType", ""
    ).str.lower().eq("etf")
    corrected = _core._number(
        result.get(
            "CrossAssetScore",
            result.get(
                "InstitutionalScore",
                result.get(
                    "FinalScore",
                    result.get("Score", pd.Series(0.0, index=result.index)),
                ),
            ),
        ),
        0.0,
    ).clip(0.0, 100.0)
    return corrected, is_etf


def _trade_liquidity_threshold() -> float:
    absolute_floor = max(
        0.0,
        float(
            getattr(
                _config,
                "TRADE_READY_MIN_MEDIAN_TURNOVER_60D",
                5_000_000.0,
            )
        ),
    )
    max_participation = float(
        getattr(_config, "TRADE_READY_MAX_ASSUMED_PARTICIPATION_RATE", 0.01)
    )
    assumed_notional = max(
        0.0,
        float(getattr(_config, "BACKTEST_ASSUMED_TRADE_NOTIONAL", 50_000.0)),
    )
    participation_floor = (
        assumed_notional / max_participation
        if max_participation > 0.0
        else np.inf
    )
    return float(max(absolute_floor, participation_floor))


def _trade_liquidity_diagnostics(result: pd.DataFrame) -> pd.Series:
    """Return the execution-liquidity pass mask and stamp its provenance."""
    threshold = _trade_liquidity_threshold()
    assumed_notional = max(
        0.0,
        float(getattr(_config, "BACKTEST_ASSUMED_TRADE_NOTIONAL", 50_000.0)),
    )
    max_participation = max(
        0.0,
        float(
            getattr(
                _config,
                "TRADE_READY_MAX_ASSUMED_PARTICIPATION_RATE",
                0.01,
            )
        ),
    )

    if "MedianTurnover60" not in result.columns:
        result["TradeLiquidityApplicable"] = False
        result["TradeLiquidityPassed"] = True
        result["TradeLiquidityStatus"] = "LEGACY_UNKNOWN"
        result["TradeLiquidityThresholdCNY"] = round(threshold, 2)
        result["TradeLiquidityAssumedNotionalCNY"] = round(assumed_notional, 2)
        result["TradeLiquidityParticipationPct"] = np.nan
        result["TradeLiquidityMaxParticipationPct"] = round(
            max_participation * 100.0, 4
        )
        result["TradeLiquidityReason"] = (
            "历史结果缺少60日中位成交额，沿用旧执行语义"
        )
        return pd.Series(True, index=result.index, dtype=bool)

    turnover = pd.to_numeric(result["MedianTurnover60"], errors="coerce")
    valid = turnover.notna() & np.isfinite(turnover) & turnover.gt(0.0)
    participation = pd.Series(np.nan, index=result.index, dtype=float)
    participation.loc[valid] = assumed_notional / turnover.loc[valid]
    passed = (
        valid
        & turnover.ge(threshold)
        & participation.le(max_participation + 1e-12)
    )

    result["TradeLiquidityApplicable"] = True
    result["TradeLiquidityPassed"] = passed
    result["TradeLiquidityStatus"] = np.where(passed, "PASS", "FAIL")
    result["TradeLiquidityThresholdCNY"] = round(threshold, 2)
    result["TradeLiquidityAssumedNotionalCNY"] = round(assumed_notional, 2)
    result["TradeLiquidityParticipationPct"] = (participation * 100.0).round(4)
    result["TradeLiquidityMaxParticipationPct"] = round(
        max_participation * 100.0, 4
    )

    reason = pd.Series("", index=result.index, dtype=object)
    reason.loc[passed] = (
        f"60日中位成交额满足执行门槛（至少{threshold / 10_000:.0f}万元，"
        f"假设订单参与率不高于{max_participation:.1%}）"
    )
    reason.loc[~valid] = "缺少有效60日中位成交额，执行流动性门槛未通过"
    thin = valid & ~passed
    reason.loc[thin] = (
        f"60日中位成交额低于执行门槛{threshold / 10_000:.0f}万元，"
        f"假设{assumed_notional / 10_000:.0f}万元订单参与率超过"
        f"{max_participation:.1%}"
    )
    result["TradeLiquidityReason"] = reason
    return passed


def _append_dynamic_reason(
    base: pd.Series,
    mask: pd.Series,
    extra: pd.Series,
) -> pd.Series:
    output = base.fillna("").astype(str).copy()
    active = mask.fillna(False).astype(bool)
    if not active.any():
        return output
    current = output.loc[active].str.strip().str.rstrip("；")
    addition = (
        extra.loc[active]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lstrip("；")
    )
    output.loc[active] = np.where(
        current.ne(""),
        current + "；" + addition,
        addition,
    )
    return output


def _apply_directional_research_gate(
    result: pd.DataFrame,
    is_etf: pd.Series,
) -> pd.Series:
    """Block cash-equivalent ETFs from directional research and execution."""
    eligible_values, reason_values = vectorized_etf_research_policy(
        result,
        is_etf.to_numpy(dtype=bool),
    )
    eligible = pd.Series(eligible_values, index=result.index, dtype=bool)
    reason = pd.Series(reason_values, index=result.index, dtype=object)
    applicable = is_etf.fillna(False).astype(bool)
    result["DirectionalResearchGateApplicable"] = applicable
    result["DirectionalResearchEligible"] = eligible
    result["DirectionalResearchReason"] = reason
    result["DirectionalResearchStatus"] = np.select(
        [~applicable, eligible],
        ["NOT_APPLICABLE", "PASS"],
        default="FAIL",
    )

    decision = _core._text_series(result, "DecisionState", "OBSERVE").str.upper()
    demote = applicable & ~eligible & decision.ne("BLOCKED")
    result["DirectionalResearchGateApplied"] = demote
    if not demote.any():
        return demote

    result.loc[demote, "DecisionState"] = "BLOCKED"
    result.loc[demote, "RankingEligibility"] = "风险过滤"
    result.loc[demote, "TradeReadiness"] = "风险过滤"
    readiness = _core._text_series(result, "TradeReadinessReason", "")
    readiness = _append_dynamic_reason(readiness, demote, reason)
    result["TradeReadinessReason"] = readiness
    result["DecisionReason"] = readiness
    ranking_reason = _core._text_series(result, "RankingReason", "")
    result["RankingReason"] = _append_dynamic_reason(
        ranking_reason,
        demote,
        pd.Series(
            "非方向性现金等价产品，不进入股票/方向性ETF研究候选",
            index=result.index,
        ),
    )
    action = _core._text_series(result, "ActionSuggestion", "等待条件改善")
    action.loc[demote] = "排除：非方向性现金等价产品"
    result["ActionSuggestion"] = action
    risk_note = _core._text_series(result, "RiskNote", "结构仍需确认")
    risk_note.loc[demote] = "产品属性不适用于方向性信号研究"
    result["RiskNote"] = risk_note
    advice = _core._text_series(result, "OperationAdvice", "")
    advice.loc[demote] = "该产品不属于本项目的方向性股票/ETF研究范围。"
    result["OperationAdvice"] = advice
    return demote


def _apply_breakout_price_gate(result: pd.DataFrame) -> pd.Series:
    """Demote weak zero-neighbourhood breakouts without changing alpha."""
    passed = stamp_breakout_price_diagnostics(result)
    applicable = _core._bool_series(result, "BreakoutPriceGateApplicable", False)
    decision = _core._text_series(result, "DecisionState", "OBSERVE").str.upper()
    demote = decision.isin({"READY", "CAUTIOUS"}) & applicable & ~passed
    result["BreakoutPriceGateApplied"] = demote
    if not demote.any():
        return demote

    result.loc[demote, "DecisionState"] = "OBSERVE"
    result.loc[demote, "RankingEligibility"] = "观察"
    result.loc[demote, "TradeReadiness"] = "观察"
    gate_reason = _core._text_series(
        result, "BreakoutPriceGateReason", "突破价格确认不足"
    )
    readiness = _core._text_series(result, "TradeReadinessReason", "")
    readiness = _append_dynamic_reason(readiness, demote, gate_reason)
    result["TradeReadinessReason"] = readiness
    result["DecisionReason"] = readiness
    ranking_reason = _core._text_series(result, "RankingReason", "")
    result["RankingReason"] = _append_dynamic_reason(
        ranking_reason,
        demote,
        pd.Series(
            "研究排序保留，但突破价格确认不足，不进入推荐",
            index=result.index,
        ),
    )
    action = _core._text_series(result, "ActionSuggestion", "等待条件改善")
    action.loc[demote] = "仅观察，等待价格有效脱离突破位"
    result["ActionSuggestion"] = action
    risk_note = _core._text_series(result, "RiskNote", "结构仍需确认")
    risk_note.loc[demote] = "突破价格确认不足"
    result["RiskNote"] = risk_note
    advice = _core._text_series(result, "OperationAdvice", "")
    advice.loc[demote] = "突破仍处于零附近过渡区，等待更明确的价格确认。"
    result["OperationAdvice"] = advice
    return demote


def _apply_trade_economics_gate(
    result: pd.DataFrame,
    is_etf: pd.Series,
) -> pd.Series:
    """Demote candidates whose projected target cannot cover friction."""
    passed = stamp_trade_economics_diagnostics(result, is_etf)
    applicable = _core._bool_series(result, "TradeEconomicsApplicable", False)
    decision = _core._text_series(result, "DecisionState", "OBSERVE").str.upper()
    demote = decision.isin({"READY", "CAUTIOUS"}) & applicable & ~passed
    result["TradeEconomicsGateApplied"] = demote
    if not demote.any():
        return demote

    result.loc[demote, "DecisionState"] = "OBSERVE"
    result.loc[demote, "RankingEligibility"] = "观察"
    result.loc[demote, "TradeReadiness"] = "观察"
    economics_reason = _core._text_series(
        result, "TradeEconomicsReason", "预期目标无法覆盖交易成本"
    )
    readiness = _core._text_series(result, "TradeReadinessReason", "")
    readiness = _append_dynamic_reason(readiness, demote, economics_reason)
    result["TradeReadinessReason"] = readiness
    result["DecisionReason"] = readiness
    ranking_reason = _core._text_series(result, "RankingReason", "")
    result["RankingReason"] = _append_dynamic_reason(
        ranking_reason,
        demote,
        pd.Series(
            "研究排序保留，但目标空间不足以覆盖往返成本，不进入推荐",
            index=result.index,
        ),
    )
    action = _core._text_series(result, "ActionSuggestion", "等待条件改善")
    action.loc[demote] = "仅观察，等待目标空间扩大或成本下降"
    result["ActionSuggestion"] = action
    risk_note = _core._text_series(result, "RiskNote", "结构仍需确认")
    risk_note.loc[demote] = "预期目标无法充分覆盖交易成本"
    result["RiskNote"] = risk_note
    advice = _core._text_series(result, "OperationAdvice", "")
    advice.loc[demote] = "按统一成本模型估算后净目标不足，当前不满足执行经济性。"
    result["OperationAdvice"] = advice
    return demote


def _apply_trade_liquidity_gate(result: pd.DataFrame) -> pd.Series:
    """Demote only execution readiness; keep research ranking untouched."""
    passed = _trade_liquidity_diagnostics(result)
    applicable = _core._bool_series(result, "TradeLiquidityApplicable", False)
    decision = _core._text_series(result, "DecisionState", "OBSERVE").str.upper()
    actionable = decision.isin({"READY", "CAUTIOUS"})
    demote = actionable & applicable & ~passed
    result["TradeLiquidityGateApplied"] = demote
    if not demote.any():
        return demote

    result.loc[demote, "DecisionState"] = "OBSERVE"
    result.loc[demote, "RankingEligibility"] = "观察"
    result.loc[demote, "TradeReadiness"] = "观察"

    liquidity_reason = _core._text_series(
        result,
        "TradeLiquidityReason",
        "执行流动性不足",
    )
    readiness = _core._text_series(
        result,
        "TradeReadinessReason",
        "等待趋势、量能或风险条件改善",
    )
    readiness = _append_dynamic_reason(readiness, demote, liquidity_reason)
    result["TradeReadinessReason"] = readiness
    result["DecisionReason"] = readiness

    ranking_reason = _core._text_series(result, "RankingReason", "")
    research_reason = pd.Series(
        "研究排序保留，但执行流动性不足，不进入推荐",
        index=result.index,
        dtype=object,
    )
    result["RankingReason"] = _append_dynamic_reason(
        ranking_reason,
        demote,
        research_reason,
    )

    action = _core._text_series(result, "ActionSuggestion", "等待条件改善")
    action.loc[demote] = "仅观察，等待流动性改善"
    result["ActionSuggestion"] = action

    risk_note = _core._text_series(result, "RiskNote", "结构仍需确认")
    risk_note.loc[demote] = "执行流动性不足"
    result["RiskNote"] = risk_note

    advice = _core._text_series(result, "OperationAdvice", "")
    advice.loc[demote] = (
        "研究排序保留，但成交额不足以满足计划订单参与率；等待流动性改善后再执行。"
    )
    result["OperationAdvice"] = advice
    return demote


def _trade_freshness_diagnostics(result: pd.DataFrame) -> pd.Series:
    """Stamp current-session execution freshness without changing research score."""
    max_age = max(
        0,
        int(getattr(_config, "TRADE_READY_MAX_DATA_AGE_TRADING_DAYS", 0)),
    )
    if "DataTradingAgeDays" not in result.columns:
        result["TradeFreshnessApplicable"] = False
        result["TradeFreshnessPassed"] = True
        result["TradeFreshnessStatus"] = "LEGACY_UNKNOWN"
        result["TradeFreshnessTradingDays"] = np.nan
        result["TradeFreshnessMaxTradingDays"] = max_age
        result["TradeFreshnessReason"] = (
            "历史结果缺少行情交易日年龄，沿用旧执行语义"
        )
        return pd.Series(True, index=result.index, dtype=bool)

    age = pd.to_numeric(result["DataTradingAgeDays"], errors="coerce")
    valid = age.notna() & np.isfinite(age) & age.ge(0.0)
    passed = valid & age.le(float(max_age))

    result["TradeFreshnessApplicable"] = True
    result["TradeFreshnessPassed"] = passed
    result["TradeFreshnessTradingDays"] = age
    result["TradeFreshnessMaxTradingDays"] = max_age
    result["TradeFreshnessStatus"] = np.select(
        [~valid, passed],
        ["UNKNOWN", "CURRENT"],
        default="LAGGED",
    )

    reason = pd.Series("", index=result.index, dtype=object)
    reason.loc[~valid] = "缺少有效行情交易日年龄，执行时效门槛未通过"
    reason.loc[passed] = "行情已覆盖最新完成交易日，可用于即时执行判断"
    lagged = valid & ~passed
    reason.loc[lagged] = [
        f"行情落后 {int(value)} 个交易日，超过即时执行允许的 {max_age} 个交易日"
        for value in age.loc[lagged]
    ]
    result["TradeFreshnessReason"] = reason
    return passed


def _apply_trade_freshness_gate(result: pd.DataFrame) -> pd.Series:
    """Demote stale actionable rows while preserving their research ranking."""
    passed = _trade_freshness_diagnostics(result)
    applicable = _core._bool_series(result, "TradeFreshnessApplicable", False)
    decision = _core._text_series(result, "DecisionState", "OBSERVE").str.upper()
    actionable = decision.isin({"READY", "CAUTIOUS"})
    demote = actionable & applicable & ~passed
    result["TradeFreshnessGateApplied"] = demote
    if not demote.any():
        return demote

    result.loc[demote, "DecisionState"] = "OBSERVE"
    result.loc[demote, "RankingEligibility"] = "观察"
    result.loc[demote, "TradeReadiness"] = "观察"

    freshness_reason = _core._text_series(
        result,
        "TradeFreshnessReason",
        "行情时效不足",
    )
    readiness = _core._text_series(
        result,
        "TradeReadinessReason",
        "等待趋势、量能或风险条件改善",
    )
    readiness = _append_dynamic_reason(readiness, demote, freshness_reason)
    result["TradeReadinessReason"] = readiness
    result["DecisionReason"] = readiness

    ranking_reason = _core._text_series(result, "RankingReason", "")
    research_reason = pd.Series(
        "研究排序保留，但行情不是最新完成交易日，不进入推荐",
        index=result.index,
        dtype=object,
    )
    result["RankingReason"] = _append_dynamic_reason(
        ranking_reason,
        demote,
        research_reason,
    )

    action = _core._text_series(result, "ActionSuggestion", "等待条件改善")
    action.loc[demote] = "仅观察，刷新至最新交易日后再判断"
    result["ActionSuggestion"] = action

    risk_note = _core._text_series(result, "RiskNote", "结构仍需确认")
    risk_note.loc[demote] = "行情时效不足"
    result["RiskNote"] = risk_note

    advice = _core._text_series(result, "OperationAdvice", "")
    advice.loc[demote] = (
        "研究排序保留，但行情未覆盖最新完成交易日；刷新行情后再执行。"
    )
    result["OperationAdvice"] = advice
    return demote


def finalize_signal_ranking(frame: pd.DataFrame) -> pd.DataFrame:
    """Run stable ranking, then apply vectorized v87 integrity gates."""
    result = _LEGACY_FINALIZE_SIGNAL_RANKING(frame)
    if result is None or result.empty:
        return result

    corrected, is_etf = _board_diagnostic_inputs(result)
    _add_board_diagnostics(result, corrected, is_etf)
    _apply_directional_research_gate(result, is_etf)
    _apply_breakout_price_gate(result)
    _apply_trade_economics_gate(result, is_etf)
    _apply_trade_liquidity_gate(result)
    _apply_trade_freshness_gate(result)

    # v54/v58/v87 gates never alter the research RankingScore.
    result["RankingScore"] = _core._number(
        result.get("RankingScore", pd.Series(0.0, index=result.index)),
        0.0,
    ).clip(lower=0.0).round(4)

    stamp = getattr(_core, "stamp_ranking_contract", None)
    return stamp(result) if callable(stamp) else result


_core.strict_filter_override_mask = strict_filter_override_mask
_core._is_active = _is_active
_core._trading_board_series = _trading_board_series
_core._add_board_diagnostics = _add_board_diagnostics
_core._board_diagnostic_inputs = _board_diagnostic_inputs
_core._apply_directional_research_gate = _apply_directional_research_gate
_core._apply_breakout_price_gate = _apply_breakout_price_gate
_core._apply_trade_economics_gate = _apply_trade_economics_gate
_core._trade_liquidity_threshold = _trade_liquidity_threshold
_core._trade_liquidity_diagnostics = _trade_liquidity_diagnostics
_core._apply_trade_liquidity_gate = _apply_trade_liquidity_gate
_core._trade_freshness_diagnostics = _trade_freshness_diagnostics
_core._apply_trade_freshness_gate = _apply_trade_freshness_gate
_core.finalize_signal_ranking = finalize_signal_ranking
sys.modules[__name__] = _core
