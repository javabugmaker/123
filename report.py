"""
report.py — Output generation: CSV, Parquet, and terminal reports.

Produces:
  - Top50.csv:          the 50 highest-scoring tickers with key metrics.
  - AllResults.csv:     every scored ticker, sorted by score.
  - Top200.parquet:     the top 200 in Parquet format.
  - AllResults.parquet: every scored ticker in Parquet format.
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from analytics import refresh_research_outcomes, write_research_reports
from classification import (
    etf_research_eligibility,
    etf_theme_key,
    etf_tracking_key,
    theme_cluster,
)
from config import (
    BREAKOUT_CONFIRM_MIN_VOLUME_RATIO,
    ETF_THEME_MAX_PER_TOP_LIST,
    ETF_TRACKING_MAX_PER_TOP_LIST,
    INSTITUTIONAL_TIER_A_SCORE,
    INSTITUTIONAL_TIER_B_SCORE,
    INSTITUTIONAL_TIER_C_SCORE,
    INSTITUTIONAL_TIER_TRAP_LABEL,
    INSTITUTIONAL_TIER_WAIT_LABEL,
    OUTPUT_DIR,
    PIPELINE_VERSION,
    QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE,
    SCORING_VERSION,
    STOCK_INDUSTRY_MAX_PER_TOP_LIST,
    THEME_CLUSTER_SOFT_PENALTY,
    TOP_N_PARQUET,
    TOP_N_REPORT,
    TRADE_READY_MAX_STOP_DISTANCE_PCT,
    TRADE_READY_MIN_REWARD_RISK,
    VALUE_TRAP_RISK_THRESHOLD,
)
from evidence import enrich_evidence_fields
from performance_cache import BACKTEST_CACHE_VERSION, INDICATOR_CACHE_VERSION
from scanner import ScanReport, ScanResult
from score import model_weight_signature, tradable_price_decimals
from signal_lifecycle import (
    enrich_signal_lifecycle,
    finalize_signal_ranking,
    strict_filter_override_mask,
)

logger = logging.getLogger("institution_scanner.report")


# ======================================================================
# Data export helpers
# ======================================================================


def _quality_label(result: ScanResult) -> str:
    signal_count = int(result.filter_details.get("signal_count", 0))
    score = (
        float(result.final_score)
        if np.isfinite(result.final_score)
        else float(result.score.total)
    )
    if result.passed_filters and (
        (signal_count >= 5 and score >= 40) or (signal_count >= 4 and score >= 48)
    ):
        return "强候选"
    if result.passed_filters and signal_count >= 4:
        return "候选"
    if score >= 35 or signal_count >= 3:
        return "观察"
    return "普通"


def _institutional_tier(result: ScanResult) -> str:
    score = (
        float(result.institutional_score)
        if np.isfinite(result.institutional_score)
        else float(result.final_score)
        if np.isfinite(result.final_score)
        else float(result.score.total)
    )
    volume_confirmed = bool(
        result.filter_details.get("volume_accumulation", False)
    ) or result.score.volume >= 15
    quality_failed = (
        not result.is_etf
        and result.quality_data_available
        and not result.quality_gate
    )
    if (
        score > INSTITUTIONAL_TIER_A_SCORE
        and 0 <= result.signal_recency_days <= 20
        and volume_confirmed
    ):
        tier = "A级机构启动"
    elif INSTITUTIONAL_TIER_B_SCORE <= score < INSTITUTIONAL_TIER_A_SCORE:
        tier = "B级观察"
    elif score >= INSTITUTIONAL_TIER_C_SCORE:
        tier = "C级价值观察"
    else:
        tier = INSTITUTIONAL_TIER_WAIT_LABEL
    if quality_failed:
        tier = {
            "A级机构启动": "B级观察",
            "B级观察": "C级价值观察",
            "C级价值观察": "C级价值观察",
        }.get(tier, INSTITUTIONAL_TIER_WAIT_LABEL)
    value_trap_risk = float(result.value_trap_risk)
    if (
        not result.is_etf
        and np.isfinite(value_trap_risk)
        and value_trap_risk >= VALUE_TRAP_RISK_THRESHOLD
    ):
        return INSTITUTIONAL_TIER_TRAP_LABEL
    return tier


def _rankable_results(results: list[ScanResult]) -> list[ScanResult]:
    valid = [r for r in results if not r.error]

    def rank_score(result: ScanResult) -> float:
        for value in (
            result.ranking_score,
            result.institutional_score,
            result.final_score,
            result.score.total,
        ):
            if np.isfinite(value):
                return float(value)
        return 0.0

    return sorted(
        valid,
        key=lambda r: (
            r.ranking_eligibility != "风险过滤",
            rank_score(r),
            int(r.filter_details.get("signal_count", 0)),
        ),
        reverse=True,
    )


_HARD_GATE_FILTER_KEYS = ("min_price", "min_volume", "min_market_cap", "sufficient_history")
_DIAGNOSTIC_FILTER_KEYS = (
    "volume_accumulation",
    "obv_divergence",
    "cmf_positive",
    "ad_slope",
    "consolidation",
    "volatility_contraction",
)


def _hard_gate_evaluated(result: ScanResult) -> bool:
    return any(key in result.filter_details for key in _HARD_GATE_FILTER_KEYS)


def _hard_gate_passed(result: ScanResult) -> bool:
    if _hard_gate_evaluated(result):
        return bool(result.universe_eligible)
    return bool(result.passed_filters)


def _failed_filter_names(result: ScanResult, keys: tuple[str, ...]) -> list[str]:
    if (
        keys == _HARD_GATE_FILTER_KEYS
        and not _hard_gate_evaluated(result)
        and result.passed_filters
    ):
        return []
    names: list[str] = []
    for key in keys:
        if result.is_etf and key in {"min_price", "min_market_cap"}:
            continue
        if not bool(result.filter_details.get(key, False)):
            names.append(key)
    return names


def _results_to_dataframe(results: list[ScanResult]) -> pd.DataFrame:
    """Convert ScanResult list to a sorted, clean DataFrame."""
    scan_timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    run_id = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    rows = []
    for r in results:
        price_decimals = tradable_price_decimals(
            r.is_etf or r.asset_type.lower() == "etf"
        )
        rows.append(
            {
                "Ticker": r.ticker,
                "Name": r.name,
                "Sector": r.sector,
                "Industry": r.industry,
                "IsETF": r.is_etf,
                "AssetType": r.asset_type,
                "Style": r.style,
                "Quality": _quality_label(r),
                "InstitutionalTier": _institutional_tier(r),
                "InstitutionalPercentile": round(r.institutional_percentile, 2) if np.isfinite(r.institutional_percentile) else None,
                "InstitutionalRank": r.institutional_rank or None,
                "InstitutionalTierReason": r.institutional_tier_reason,
                "OverallRank": r.overall_rank or None,
                "RankingScore": round(r.ranking_score, 4) if np.isfinite(r.ranking_score) else None,
                "RankingEligibility": r.ranking_eligibility,
                "RankingReason": r.ranking_reason,
                "Close": r.close,
                "MarketCap": r.filter_details.get("market_cap"),
                "MarketCapDataAvailable": bool(
                    r.filter_details.get("market_cap_available", False)
                ),
                "MarketCapPassed": bool(
                    r.filter_details.get("min_market_cap", False)
                ),
                "Score": round(r.score.total, 2),
                "BaseScore": round(r.base_score, 2) if np.isfinite(r.base_score) else None,
                "TriggerScore": round(r.trigger_score, 2) if np.isfinite(r.trigger_score) else None,
                "ExecutionScore": round(r.score.execution_score, 2),
                "FinalScore": round(r.final_score, 2) if np.isfinite(r.final_score) else None,
                "ModelWeightSignature": model_weight_signature(),
                "BreakoutScore": round(r.breakout_score, 2) if np.isfinite(r.breakout_score) else None,
                "SmartMoneyStage": r.smart_money_stage,
                "EntryScore": round(r.entry_score, 2) if np.isfinite(r.entry_score) else None,
                "EntrySignal": r.entry_signal,
                "RawEntrySignal": r.raw_entry_signal or r.entry_signal,
                "DecisionState": r.decision_state,
                "DecisionReason": r.decision_reason,
                "EntryZone": r.entry_zone,
                "EntryZoneDistancePct": round(r.entry_zone_distance_pct, 4) if np.isfinite(r.entry_zone_distance_pct) else None,
                "EntryZoneDistanceATR": round(r.entry_zone_distance_atr, 4) if np.isfinite(r.entry_zone_distance_atr) else None,
                "PullbackQualityScore": round(r.pullback_quality_score, 2) if np.isfinite(r.pullback_quality_score) else None,
                "BreakoutBuyPrice": round(r.breakout_buy_price, price_decimals) if np.isfinite(r.breakout_buy_price) else None,
                "BreakoutVolumeRatio": round(r.breakout_volume_ratio, 4) if np.isfinite(r.breakout_volume_ratio) else None,
                "BreakoutVolumeConfirmed": r.breakout_volume_confirmed,
                "BreakoutFlowConfirmed": r.breakout_flow_confirmed,
                "PriceBreakout": r.price_breakout,
                "StopLoss": round(r.stop_loss, price_decimals) if np.isfinite(r.stop_loss) else None,
                "ProjectedTarget": round(r.projected_target, price_decimals) if np.isfinite(r.projected_target) else None,
                "StopDistancePct": round(r.stop_distance_pct, 4) if np.isfinite(r.stop_distance_pct) else None,
                "RewardRiskRatio": round(r.reward_risk_ratio, 4) if np.isfinite(r.reward_risk_ratio) else None,
                "ValueTrapRisk": round(r.value_trap_risk, 2) if np.isfinite(r.value_trap_risk) else None,
                "RiskWarning": r.risk_warning,
                "OperationAdvice": r.operation_advice,
                "BacktestScore": round(r.backtest_score, 2)
                if np.isfinite(r.backtest_score)
                else None,
                "BacktestReliability": round(r.backtest_reliability, 4)
                if np.isfinite(r.backtest_reliability)
                else None,
                "BacktestAdjustedScore": round(r.backtest_adjusted_score, 4)
                if np.isfinite(r.backtest_adjusted_score)
                else None,
                "BacktestEffectiveWeight": round(r.backtest_effective_weight, 4),
                "BacktestConfidenceTier": r.backtest_confidence_tier,
                "CompositeScore": round(r.composite_score, 2)
                if np.isfinite(r.composite_score)
                else None,
                "FailureSignalFactor": round(r.failure_signal_factor, 4),
                "FailureAdjustedScore": round(r.failure_adjusted_score, 2)
                if np.isfinite(r.failure_adjusted_score)
                else None,
                "SectorConfirmationFactor": round(r.sector_confirmation_factor, 4),
                "SignalRecencyDays": r.signal_recency_days
                if r.signal_recency_days >= 0
                else None,
                "SignalRecencyFactor": round(r.signal_recency_factor, 4),
                "BreakoutQualityFactor": round(r.breakout_quality_factor, 4),
                "InstitutionalScore": round(r.institutional_score, 2)
                if np.isfinite(r.institutional_score)
                else None,
                "PreBacktestInstitutionalScore": round(
                    r.pre_backtest_institutional_score, 4
                )
                if np.isfinite(r.pre_backtest_institutional_score)
                else None,
                "ROE": round(r.quality_roe, 4) if np.isfinite(r.quality_roe) else None,
                "GrossMargin": round(r.quality_gross_margin, 4) if np.isfinite(r.quality_gross_margin) else None,
                "InstitutionHoldingTrend": r.quality_institution_holding_trend,
                "InstitutionHoldingPeriods": round(r.quality_institution_holding_periods, 4) if np.isfinite(r.quality_institution_holding_periods) else None,
                "NetProfitY1": round(r.quality_net_profit_y1, 4) if np.isfinite(r.quality_net_profit_y1) else None,
                "NetProfitY2": round(r.quality_net_profit_y2, 4) if np.isfinite(r.quality_net_profit_y2) else None,
                "NetProfitY3": round(r.quality_net_profit_y3, 4) if np.isfinite(r.quality_net_profit_y3) else None,
                "IndustryGrossMarginPercentile": round(r.quality_industry_gross_margin_percentile, 4) if np.isfinite(r.quality_industry_gross_margin_percentile) else None,
                "QualityROE": r.quality_roe_factor,
                "QualityGrossMargin": r.quality_gross_margin_factor,
                "QualityInstitutionHolding": r.quality_institution_holding_factor,
                "QualityNetProfit": r.quality_net_profit_factor,
                "QualityScore": round(r.quality_score, 2) if np.isfinite(r.quality_score) else None,
                "QualityGate": r.quality_gate,
                "QualityReason": r.quality_reason,
                "QualityDataAvailable": r.quality_data_available,
                "QualityApplicable": r.quality_applicable,
                "InstitutionHoldingStatus": r.quality_institution_holding_status,
                "QualityDataCompleteness": round(r.quality_data_completeness, 4),
                "QualityHardDataComplete": r.quality_hard_data_complete,
                "QualityGateReason": r.quality_gate_reason,
                "QualityMultiplier": round(r.quality_multiplier, 4),
                "QualityProfile": r.quality_profile,
                "ProfitTrendStatus": r.quality_profit_trend_status,
                "CyclicalQualityOverride": r.cyclical_quality_override,
                "BacktestSamples": r.backtest_samples,
                "BacktestEffectiveSamples": round(r.backtest_effective_samples, 4),
                "BacktestWinRate20D": round(r.backtest_win_rate_20d, 4)
                if np.isfinite(r.backtest_win_rate_20d)
                else None,
                "BacktestWinRate60D": round(r.backtest_win_rate_60d, 4)
                if np.isfinite(r.backtest_win_rate_60d)
                else None,
                "BacktestAverageReturn20D": round(r.backtest_average_return_20d, 4)
                if np.isfinite(r.backtest_average_return_20d)
                else None,
                "BacktestAverageReturn60D": round(r.backtest_average_return_60d, 4)
                if np.isfinite(r.backtest_average_return_60d)
                else None,
                "BacktestMedianReturn20D": round(r.backtest_median_return_20d, 4)
                if np.isfinite(r.backtest_median_return_20d)
                else None,
                "BacktestMedianReturn60D": round(r.backtest_median_return_60d, 4)
                if np.isfinite(r.backtest_median_return_60d)
                else None,
                "BacktestMaxDrawdown20D": round(r.backtest_max_drawdown_20d, 4)
                if np.isfinite(r.backtest_max_drawdown_20d)
                else None,
                "BacktestMaxDrawdown60D": round(r.backtest_max_drawdown_60d, 4)
                if np.isfinite(r.backtest_max_drawdown_60d)
                else None,
                "BacktestProfitFactor": round(r.backtest_profit_factor, 4)
                if np.isfinite(r.backtest_profit_factor)
                else None,
                "BacktestSignalSpanDays": r.backtest_signal_span_days,
                "BacktestReturnStd20D": round(r.backtest_return_std_20d, 4)
                if np.isfinite(r.backtest_return_std_20d)
                else None,
                "BacktestObjectiveValue": round(r.backtest_objective_value, 4)
                if np.isfinite(r.backtest_objective_value)
                else None,
                "BacktestMode": r.backtest_mode,
                "BacktestCacheHit": r.backtest_cache_hit,
                "BacktestLastEvaluatedDate": r.backtest_last_evaluated_date,
                "BacktestDataCutoffDate": (
                    r.backtest_data_cutoff_date or r.backtest_last_evaluated_date
                ),
                "BacktestLastMatureSignalDate": r.backtest_last_mature_signal_date,
                "BacktestFreshnessTradingDays": (
                    round(r.backtest_freshness_trading_days, 4)
                    if np.isfinite(r.backtest_freshness_trading_days)
                    else None
                ),
                "BacktestFreshnessStatus": r.backtest_freshness_status,
                "BacktestFreshnessReason": r.backtest_freshness_reason,
                "BacktestEngine": r.backtest_engine,
                "BacktestStatus": r.backtest_status,
                "GlobalCalibrationScore": round(r.global_calibration_score, 4) if np.isfinite(r.global_calibration_score) else None,
                "GlobalCalibrationConfidence": round(r.global_calibration_confidence, 4),
                "GlobalCalibrationLevel": r.global_calibration_level,
                "UniverseType": r.universe_type,
                "SurvivorshipBiasWarning": r.survivorship_bias_warning,
                "TrendScore": round(r.score.trend, 2),
                "VolumeScore": round(r.score.volume, 2),
                "AccumulationScore": round(r.score.accumulation, 2),
                "CompressionScore": round(r.score.volatility, 2),
                "StructureScore": round(r.score.structure, 2),
                "ScoreMissingIndicators": r.score_missing_indicators,
                "ScoreCoverage": round(r.score_coverage, 4),
                "ScoreConfidence": round(r.score_confidence, 4),
                "ScoreContributionTrend": round(
                    r.score.contributions.get("trend", r.score.trend), 2
                ),
                "ScoreContributionVolume": round(
                    r.score.contributions.get("volume", r.score.volume), 2
                ),
                "ScoreContributionAccumulation": round(
                    r.score.contributions.get("accumulation", r.score.accumulation), 2
                ),
                "ScoreContributionCompression": round(
                    r.score.contributions.get("compression", r.score.volatility), 2
                ),
                "ScoreContributionStructure": round(
                    r.score.contributions.get("structure", r.score.structure), 2
                ),
                "OBV": r.obv if not np.isnan(r.obv) else None,
                "CMF": round(r.cmf, 4) if not np.isnan(r.cmf) else None,
                "AD": r.ad if not np.isnan(r.ad) else None,
                "ATR14": round(r.atr14, 4) if not np.isnan(r.atr14) else None,
                "ATR50": round(r.atr50, 4) if np.isfinite(r.atr50) else None,
                "ATRExpansionSource": r.atr_expansion_source,
                "RSI14": round(r.rsi14, 2) if not np.isnan(r.rsi14) else None,
                "DistToLow52W": round(r.dist_to_low_52w, 2)
                if not np.isnan(r.dist_to_low_52w)
                else None,
                "DistToMA20": round(r.dist_to_ma20, 4) if np.isfinite(r.dist_to_ma20) else None,
                "DistToMA50": round(r.dist_to_ma50, 4) if np.isfinite(r.dist_to_ma50) else None,
                "RecentReturn20D": round(r.recent_return_20d, 4) if np.isfinite(r.recent_return_20d) else None,
                "ATRExpansion": round(r.atr_expansion, 4) if np.isfinite(r.atr_expansion) else None,
                "WyckoffPhase": r.wyckoff_phase,
                "Stage": r.stage,
                "MarketRegime": r.market_regime,
                "MarketRegimeFast": r.market_regime_fast,
                "MarketRegimeSlow": r.market_regime_slow,
                "MarketRegimeConfidence": round(r.market_regime_confidence, 4),
                "MarketRegimeReason": r.market_regime_reason,
                "IndustryRelativeStrength": round(r.industry_relative_strength, 2)
                if not np.isnan(r.industry_relative_strength)
                else None,
                "IndustryMomentum60D": round(r.industry_momentum_60d, 2)
                if not np.isnan(r.industry_momentum_60d)
                else None,
                "DataSource": r.data_source,
                "DataAsOf": r.data_asof,
                "DataAgeDays": r.data_age_days,
                "DataTradingAgeDays": r.data_trading_age_days,
                "DataCoverage": round(r.data_coverage, 4),
                "VolAccumDays": r.volume_accum_days,
                "SignalCount": r.filter_details.get("signal_count", 0),
                "FilterCount": r.filter_details.get("filter_count", 0),
                # Compatibility field: historically this meant the combined
                # hard-gate + accumulation/structure recipe, not "every filter".
                "PassedFilters": r.passed_filters,
                "UniverseEligible": _hard_gate_passed(r),
                "HardGatePassed": _hard_gate_passed(r),
                "HardGateFailedCount": len(_failed_filter_names(r, _HARD_GATE_FILTER_KEYS)),
                "HardGateFailedNames": ",".join(_failed_filter_names(r, _HARD_GATE_FILTER_KEYS)),
                "SignalConfirmed": r.signal_confirmed,
                "DiagnosticFailedCount": len(_failed_filter_names(r, _DIAGNOSTIC_FILTER_KEYS)),
                "DiagnosticFailedNames": ",".join(_failed_filter_names(r, _DIAGNOSTIC_FILTER_KEYS)),
                "FailedFilterCount": r.failed_filter_count,
                "FailedFilterNames": r.failed_filter_names,
                "MinPricePassed": r.filter_details.get("min_price", False),
                "MinVolumePassed": r.filter_details.get("min_volume", False),
                "MinMarketCapPassed": r.filter_details.get("min_market_cap", False),
                "SufficientHistoryPassed": r.filter_details.get("sufficient_history", False),
                "OBV_Div": r.filter_details.get("obv_divergence", False),
                "CMF_Pos": r.filter_details.get("cmf_positive", False),
                "CMF_Improving": r.filter_details.get("cmf_improving", False),
                "AD_SlopePos": r.filter_details.get("ad_slope", False),
                "BearMarket": r.filter_details.get("bear_market", False),
                "Consolidation": r.filter_details.get("consolidation", False),
                "VolAccum": r.filter_details.get("volume_accumulation", False),
                "VolContract": r.filter_details.get("volatility_contraction", False),
                "ChaseRiskScore": round(r.chase_risk_score, 2),
                "ChaseRiskLevel": r.chase_risk_level,
                "ChaseRiskReason": r.chase_risk_reason,
                "HardRiskFlag": r.hard_risk_flag,
                "HardRiskPenalty": round(r.hard_risk_penalty, 4),
                "HardRiskReason": r.hard_risk_reason,
                "RankingPenaltyReason": r.ranking_penalty_reason,
                "TradeReadiness": r.trade_readiness or r.ranking_eligibility,
                "ResearchTier": r.research_tier,
                "TechnicalInstitutionalScore": round(r.technical_institutional_score, 4) if np.isfinite(r.technical_institutional_score) else None,
                "AssetPercentile": round(r.asset_percentile, 2) if np.isfinite(r.asset_percentile) else None,
                "CrossAssetScore": round(r.cross_asset_score, 4) if np.isfinite(r.cross_asset_score) else None,
                "ModelClassification": r.model_classification,
                "ETFTrackingKey": r.etf_tracking_key,
                "ThemeCluster": r.theme_cluster,
                "SignalAdjustmentReason": r.signal_adjustment_reason,
                "OpportunityStage": r.opportunity_stage,
                "Error": r.error if r.error else "",
                "ModelVersion": SCORING_VERSION,
                "PipelineVersion": PIPELINE_VERSION,
                "IndicatorCacheVersion": INDICATOR_CACHE_VERSION,
                "BacktestCacheVersion": BACKTEST_CACHE_VERSION,
                "RunId": run_id,
                "ScanTimestamp": scan_timestamp,
                "CandidateGenerationStage": "SCAN",
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    return finalize_signal_ranking(df)


# ======================================================================
# CSV Export
# ======================================================================


def _atomic_write_csv(df: pd.DataFrame, path: Path) -> None:
    temporary_path = path.with_name(f".{path.name}.tmp")
    try:
        df.to_csv(temporary_path, index=False, encoding="utf-8-sig")
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _atomic_write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=path.suffix, dir=path.parent, delete=False) as file:
        temporary_path = Path(file.name)
    try:
        pq.write_table(pa.Table.from_pandas(df), temporary_path)
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _apply_research_policy(frame: pd.DataFrame) -> pd.DataFrame:
    """Mark non-directional ETF products before any TopN candidate ranking."""
    working = frame.copy()
    if working.empty:
        working["ResearchEligible"] = pd.Series(dtype=bool)
        working["ResearchExclusionReason"] = pd.Series(dtype="object")
        return working
    eligibility: list[bool] = []
    reasons: list[str] = []
    for _, row in working.iterrows():
        asset = str(row.get("AssetType", "") or "").strip().lower()
        is_etf = _truthy(row.get("IsETF", False)) or asset == "etf"
        eligible, reason = etf_research_eligibility(
            is_etf=is_etf,
            name=row.get("Name", ""),
            industry=row.get("Industry", ""),
            sector=row.get("Sector", ""),
            classification=row.get("ModelClassification", row.get("ETFTheme", "")),
            ticker=row.get("Ticker", ""),
        )
        hard_value = row.get("HardGatePassed", None)
        try:
            hard_missing = hard_value is None or pd.isna(hard_value)
        except (TypeError, ValueError):
            hard_missing = hard_value is None
        if hard_missing or str(hard_value).strip() == "":
            hard_value = row.get("UniverseEligible", True)
        hard_ok = _truthy(hard_value)
        failed_names = _clean_group_key(row.get("HardGateFailedNames", ""))
        legacy_combined_pass = (
            not hard_ok
            and not failed_names
            and _truthy(row.get("PassedFilters", False))
        )
        if legacy_combined_pass:
            hard_ok = True
        if not hard_ok:
            hard_reason = (
                f"硬准入失败：{failed_names}" if failed_names else "硬准入条件未通过"
            )
            reason = f"{reason}；{hard_reason}" if reason else hard_reason
        eligibility.append(bool(eligible) and hard_ok)
        reasons.append(str(reason or ""))
    working["ResearchEligible"] = eligibility
    working["ResearchExclusionReason"] = reasons
    return working


def validate_decision_integrity(frame: pd.DataFrame) -> None:
    """Fail closed on contradictions between eligibility, gate and lifecycle."""
    if frame.empty:
        return

    violations: list[str] = []
    ticker = frame.get("Ticker", pd.Series("", index=frame.index)).fillna("").astype(str)

    if {"ResearchEligible", "HardGatePassed"}.issubset(frame.columns):
        bad = _bool_series_for_integrity(frame, "ResearchEligible") & ~_bool_series_for_integrity(
            frame, "HardGatePassed"
        )
        if bad.any():
            violations.append(
                "hard-gate rows marked research-eligible: " + ",".join(ticker.loc[bad].head(5))
            )

    if {"QualityReason", "QualityGate"}.issubset(frame.columns):
        reason = frame["QualityReason"].fillna("").astype(str)
        bad = reason.str.contains("行业自适应硬门槛通过", regex=False) & ~_bool_series_for_integrity(
            frame, "QualityGate"
        )
        if bad.any():
            violations.append(
                "Fundamental Gate 2.0 pass rewritten to fail: " + ",".join(ticker.loc[bad].head(5))
            )

    lifecycle_columns = {"RankingEligibility", "SignalStatus", "SignalDays"}
    if lifecycle_columns.issubset(frame.columns):
        actionable = frame["RankingEligibility"].fillna("").astype(str).isin({"推荐", "谨慎候选"})
        status = frame["SignalStatus"].fillna("").astype(str).str.upper().str.strip()
        days = pd.to_numeric(frame["SignalDays"], errors="coerce").fillna(0.0)
        bad = actionable & (status.eq("") | status.isin({"FAILED", "EXPIRED", "INACTIVE"}) | days.lt(1.0))
        if bad.any():
            violations.append(
                "actionable rows without active lifecycle: " + ",".join(ticker.loc[bad].head(5))
            )

    if "RankingEligibility" in frame.columns:
        eligibility = frame["RankingEligibility"].fillna("").astype(str)
        if "DecisionState" in frame.columns:
            expected_eligibility = (
                frame["DecisionState"]
                .fillna("")
                .astype(str)
                .str.upper()
                .map(
                    {
                        "READY": "推荐",
                        "CAUTIOUS": "谨慎候选",
                        "OBSERVE": "观察",
                        "BLOCKED": "风险过滤",
                    }
                )
            )
            comparable = expected_eligibility.notna()
            bad = comparable & eligibility.ne(expected_eligibility)
            if bad.any():
                violations.append(
                    "decision state disagrees with eligibility: "
                    + ",".join(ticker.loc[bad].head(5))
                )

        if "TradeReadiness" in frame.columns:
            readiness = frame["TradeReadiness"].fillna("").astype(str)
            bad = readiness.ne("") & readiness.ne(eligibility)
            if bad.any():
                violations.append(
                    "trade readiness disagrees with eligibility: "
                    + ",".join(ticker.loc[bad].head(5))
                )

        if {"TradeReadinessReason", "DecisionReason"}.issubset(frame.columns):
            readiness_reason = frame["TradeReadinessReason"].fillna("").astype(str)
            decision_reason = frame["DecisionReason"].fillna("").astype(str)
            bad = decision_reason.ne(readiness_reason)
            if bad.any():
                violations.append(
                    "decision reason disagrees with trade-readiness reason: "
                    + ",".join(ticker.loc[bad].head(5))
                )

        actionable = eligibility.isin({"推荐", "谨慎候选"})
        asset_type = frame.get(
            "AssetType", pd.Series("", index=frame.index)
        ).fillna("").astype(str).str.lower()
        is_etf = _bool_series_for_integrity(frame, "IsETF") | asset_type.eq("etf")
        quality_applicable = (
            _bool_series_for_integrity(frame, "QualityApplicable")
            if "QualityApplicable" in frame.columns
            else ~is_etf
        ) & ~is_etf
        if "QualityGate" in frame.columns:
            bad = (
                actionable
                & quality_applicable
                & ~_bool_series_for_integrity(frame, "QualityGate")
            )
            if bad.any():
                violations.append(
                    "actionable stock rows failed the quality gate: "
                    + ",".join(ticker.loc[bad].head(5))
                )
        if "QualityHardDataComplete" in frame.columns:
            bad = (
                actionable
                & quality_applicable
                & ~_bool_series_for_integrity(frame, "QualityHardDataComplete")
            )
            if bad.any():
                violations.append(
                    "actionable stock rows missing required fundamental data: "
                    + ",".join(ticker.loc[bad].head(5))
                )

        signal = frame.get(
            "EntrySignal", pd.Series("", index=frame.index)
        ).fillna("").astype(str).str.upper()

        if {
            "PassedFilters",
            "ReadinessPenaltyFactor",
            "RankingPenaltyReason",
        }.issubset(frame.columns):
            passed_filters = _bool_series_for_integrity(frame, "PassedFilters")
            universe_eligible = frame.get(
                "UniverseEligible", pd.Series(True, index=frame.index)
            ).map(_truthy)
            filter_override = strict_filter_override_mask(
                frame,
                signal=signal,
                passed_filters=passed_filters,
                universe_eligible=universe_eligible,
            )
            if "FilterOverrideApplied" in frame.columns:
                recorded_override = _bool_series_for_integrity(
                    frame, "FilterOverrideApplied"
                )
                bad = recorded_override.ne(filter_override)
                if bad.any():
                    violations.append(
                        "recorded base-filter override disagrees with canonical policy: "
                        + ",".join(ticker.loc[bad].head(5))
                    )
            if "FilterOverrideReason" in frame.columns:
                override_reason = (
                    frame["FilterOverrideReason"].fillna("").astype(str).str.strip()
                )
                bad = filter_override & ~override_reason.str.contains(
                    "严格覆盖基础筛选缺口", regex=False
                )
                if bad.any():
                    violations.append(
                        "active base-filter override lacks audit reason: "
                        + ",".join(ticker.loc[bad].head(5))
                    )
                bad = ~filter_override & override_reason.ne("")
                if bad.any():
                    violations.append(
                        "inactive base-filter override carries stale audit reason: "
                        + ",".join(ticker.loc[bad].head(5))
                    )
            unresolved_filter_failure = ~passed_filters & ~filter_override
            penalty_reason = frame["RankingPenaltyReason"].fillna("").astype(str)
            bad = unresolved_filter_failure & ~penalty_reason.str.contains(
                "基础筛选未全通过", regex=False
            )
            if bad.any():
                violations.append(
                    "readiness penalty lacks base-filter explanation: "
                    + ",".join(ticker.loc[bad].head(5))
                )

            readiness_reason = frame.get(
                "TradeReadinessReason", pd.Series("", index=frame.index)
            ).fillna("").astype(str)
            nonblocked = eligibility.ne("风险过滤")
            bad = (
                unresolved_filter_failure
                & nonblocked
                & ~readiness_reason.str.contains("基础筛选未全通过", regex=False)
            )
            if bad.any():
                violations.append(
                    "trade-readiness reason omits base-filter blocker: "
                    + ",".join(ticker.loc[bad].head(5))
                )

            quality_hard_complete = frame.get(
                "QualityHardDataComplete", pd.Series(True, index=frame.index)
            ).map(_truthy)
            quality_completeness = pd.to_numeric(
                frame.get(
                    "QualityDataCompleteness", pd.Series(1.0, index=frame.index)
                ),
                errors="coerce",
            ).fillna(0.0)
            quality_block = quality_applicable & (
                ~_bool_series_for_integrity(frame, "QualityGate")
                | ~quality_hard_complete
                | quality_completeness.lt(QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE)
            )
            bad = (
                quality_block
                & nonblocked
                & ~readiness_reason.str.contains("质量门槛", regex=False)
            )
            if bad.any():
                violations.append(
                    "trade-readiness reason omits quality blocker: "
                    + ",".join(ticker.loc[bad].head(5))
                )

            stop_present = "StopDistancePct" in frame.columns
            reward_present = "RewardRiskRatio" in frame.columns
            stop_distance = pd.to_numeric(
                frame.get("StopDistancePct", pd.Series(np.nan, index=frame.index)),
                errors="coerce",
            )
            reward_risk = pd.to_numeric(
                frame.get("RewardRiskRatio", pd.Series(np.nan, index=frame.index)),
                errors="coerce",
            )
            stop_bad = pd.Series(False, index=frame.index)
            reward_bad = pd.Series(False, index=frame.index)
            if stop_present:
                stop_bad = (
                    stop_distance.isna()
                    | stop_distance.le(0.0)
                    | stop_distance.gt(TRADE_READY_MAX_STOP_DISTANCE_PCT)
                )
            if reward_present:
                reward_bad = reward_risk.isna() | reward_risk.lt(
                    TRADE_READY_MIN_REWARD_RISK
                )
            active_signal = signal.isin({"BUY_NOW", "BREAKOUT_CONFIRM"})
            execution_block = active_signal & (stop_bad | reward_bad)
            bad = (
                execution_block
                & nonblocked
                & ~readiness_reason.str.contains(
                    "止损距离或预期盈亏比", regex=False
                )
            )
            if bad.any():
                violations.append(
                    "trade-readiness reason omits execution-risk blocker: "
                    + ",".join(ticker.loc[bad].head(5))
                )

        breakout_actionable = actionable & signal.eq("BREAKOUT_CONFIRM")
        confirmation_flags = {
            "BreakoutVolumeConfirmed",
            "BreakoutFlowConfirmed",
        }
        if (
            breakout_actionable.any()
            and "BreakoutVolumeRatio" in frame.columns
            and not confirmation_flags.issubset(frame.columns)
        ):
            violations.append(
                "actionable breakouts have incomplete confirmation schema"
            )
        breakout_confirmation_bad = pd.Series(False, index=frame.index)
        for confirmation_column in (
            "BreakoutVolumeConfirmed",
            "BreakoutFlowConfirmed",
        ):
            if confirmation_column in frame.columns:
                breakout_confirmation_bad |= (
                    breakout_actionable
                    & ~_bool_series_for_integrity(frame, confirmation_column)
                )
        if "BreakoutVolumeRatio" in frame.columns:
            volume_ratio = pd.to_numeric(
                frame["BreakoutVolumeRatio"], errors="coerce"
            )
            breakout_confirmation_bad |= (
                breakout_actionable
                & (
                    volume_ratio.isna()
                    | volume_ratio.lt(BREAKOUT_CONFIRM_MIN_VOLUME_RATIO)
                )
            )
        if breakout_confirmation_bad.any():
            violations.append(
                "actionable breakouts lack valid event-volume confirmation: "
                + ",".join(ticker.loc[breakout_confirmation_bad].head(5))
            )

        if "StopDistancePct" in frame.columns:
            stop_distance = pd.to_numeric(
                frame["StopDistancePct"], errors="coerce"
            )
            bad = actionable & (
                stop_distance.isna()
                | stop_distance.le(0.0)
                | stop_distance.gt(TRADE_READY_MAX_STOP_DISTANCE_PCT)
            )
            if bad.any():
                violations.append(
                    "actionable rows lack valid stop-distance bounds: "
                    + ",".join(ticker.loc[bad].head(5))
                )
        if "RewardRiskRatio" in frame.columns:
            reward_risk = pd.to_numeric(
                frame["RewardRiskRatio"], errors="coerce"
            )
            bad = actionable & (
                reward_risk.isna()
                | reward_risk.lt(TRADE_READY_MIN_REWARD_RISK)
            )
            if bad.any():
                violations.append(
                    "actionable rows lack valid reward-risk bounds: "
                    + ",".join(ticker.loc[bad].head(5))
                )

        recommended = frame["RankingEligibility"].fillna("").astype(str).eq("推荐")
        stale = pd.Series(False, index=frame.index)
        if "RankingReason" in frame.columns:
            ranking_reason = frame["RankingReason"].fillna("").astype(str)
            stale |= recommended & (
                ranking_reason.str.contains("谨慎候选", regex=False)
                | ranking_reason.str.contains("转为观察", regex=False)
                | ranking_reason.str.contains("禁止进入推荐", regex=False)
            )
        if "RankingPenaltyReason" in frame.columns:
            penalty_reason = frame["RankingPenaltyReason"].fillna("").astype(str)
            stale |= recommended & (
                penalty_reason.str.contains("B级仅列谨慎候选", regex=False)
                | penalty_reason.str.contains("禁止进入推荐", regex=False)
            )
        if stale.any():
            violations.append(
                "recommended rows carry stale cautious explanation: "
                + ",".join(ticker.loc[stale].head(5))
            )

    if violations:
        raise ValueError("Decision integrity violation: " + " | ".join(violations))


def _bool_series_for_integrity(frame: pd.DataFrame, column: str) -> pd.Series:
    return frame.get(column, pd.Series(False, index=frame.index)).map(_truthy)


def _rank_valid_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    """Return research-eligible valid results in canonical candidate order."""
    if frame.empty:
        return enrich_evidence_fields(_apply_research_policy(frame))
    prepared = enrich_evidence_fields(_apply_research_policy(frame))
    validate_decision_integrity(prepared)
    valid = prepared.loc[
        prepared.get("Error", pd.Series("", index=prepared.index))
        .fillna("")
        .astype(str)
        .str.strip()
        .eq("")
        & prepared["ResearchEligible"].fillna(False).astype(bool)
    ].copy()
    if valid.empty:
        return valid.reset_index(drop=True)

    def sort_metric(column: str) -> pd.Series:
        return (
            pd.to_numeric(
                valid.get(column, pd.Series(np.nan, index=valid.index)),
                errors="coerce",
            )
            .replace([np.inf, -np.inf], np.nan)
            .fillna(-np.inf)
        )

    risk_order = valid.get(
        "RankingEligibility", pd.Series("观察", index=valid.index)
    ).eq("风险过滤").astype(int)
    ranked = valid.assign(
        _RiskOrder=risk_order,
        _RankingScore=sort_metric("RankingScore"),
        _InstitutionalScore=sort_metric("InstitutionalScore"),
        _BacktestAdjustedScore=sort_metric("BacktestAdjustedScore"),
        _EntrySignalPriority=sort_metric("EntrySignalPriority"),
        _FinalScore=sort_metric("FinalScore"),
        _Score=sort_metric("Score"),
    ).sort_values(
        [
            "_RiskOrder",
            "_RankingScore",
            "_InstitutionalScore",
            "_BacktestAdjustedScore",
            "_EntrySignalPriority",
            "_FinalScore",
            "_Score",
        ],
        ascending=[True, False, False, False, False, False, False],
        kind="mergesort",
    ).drop(
        columns=[
            "_RiskOrder",
            "_RankingScore",
            "_InstitutionalScore",
            "_BacktestAdjustedScore",
            "_EntrySignalPriority",
            "_FinalScore",
            "_Score",
        ]
    ).reset_index(drop=True)
    ranked["OverallRank"] = np.arange(1, len(ranked) + 1)
    return ranked


_ETF_THEME_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("医药医疗", ("创新药", "医疗", "医药", "生物科技", "生物医药", "医疗器械", "医疗设备")),
    ("半导体芯片", ("半导体", "芯片", "集成电路")),
    ("人工智能", ("人工智能", "AI", "算力", "数据中心")),
    ("机器人", ("机器人", "人形机器人")),
    ("黄金", ("黄金", "金矿")),
    ("有色金属", ("有色", "铜", "铝", "稀土", "锂")),
    ("新能源", ("新能源", "光伏", "风电", "储能", "电池")),
    ("券商", ("证券", "券商")),
    ("军工", ("军工", "国防")),
    ("消费", ("消费", "白酒", "食品饮料")),
    ("传媒游戏", ("传媒", "游戏")),
    ("港股科技", ("恒生科技", "港股科技", "互联网")),
    ("红利", ("红利", "高股息")),
)


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y", "是"}


def _clean_group_key(value: object) -> str:
    """Normalize nullable categorical keys before diversity accounting."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "nat", "<na>"}:
        return ""
    return text


def _etf_theme_key(row: pd.Series) -> str:
    if not (_truthy(row.get("IsETF", False)) or str(row.get("AssetType", "")).strip().lower() == "etf"):
        return ""
    return etf_theme_key(
        name=row.get("Name", ""),
        industry=row.get("Industry", ""),
        sector=row.get("Sector", ""),
        ticker=row.get("Ticker", ""),
    )


def _ensure_diversity_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Populate expensive ETF/theme provenance once and preserve existing values."""
    working = frame.copy()
    asset_type = working.get(
        "AssetType", pd.Series("", index=working.index)
    ).fillna("").astype(str).str.strip().str.lower()
    is_etf = working.get(
        "IsETF", pd.Series(False, index=working.index)
    ).map(_truthy) | asset_type.eq("etf")

    if "ETFTheme" not in working:
        working["ETFTheme"] = ""
    working["ETFTheme"] = working["ETFTheme"].astype("object")
    missing_theme = is_etf & working["ETFTheme"].fillna("").astype(str).str.strip().eq("")
    if missing_theme.any():
        working.loc[missing_theme, "ETFTheme"] = working.loc[missing_theme].apply(
            _etf_theme_key, axis=1
        )

    if "ETFTrackingKey" not in working:
        working["ETFTrackingKey"] = ""
    working["ETFTrackingKey"] = working["ETFTrackingKey"].astype("object")
    missing_tracking = is_etf & working["ETFTrackingKey"].fillna("").astype(str).str.strip().eq("")
    if missing_tracking.any():
        working.loc[missing_tracking, "ETFTrackingKey"] = working.loc[missing_tracking].apply(
            lambda row: etf_tracking_key(
                name=row.get("Name", ""),
                industry=row.get("Industry", ""),
                sector="",
                ticker=row.get("Ticker", ""),
            ),
            axis=1,
        )

    if "ThemeCluster" not in working:
        working["ThemeCluster"] = ""
    working["ThemeCluster"] = working["ThemeCluster"].astype("object")
    missing_cluster = working["ThemeCluster"].fillna("").astype(str).str.strip().eq("")
    if missing_cluster.any():
        working.loc[missing_cluster, "ThemeCluster"] = working.loc[missing_cluster].apply(
            lambda row: theme_cluster(
                is_etf=_truthy(row.get("IsETF", False))
                or str(row.get("AssetType", "")).strip().lower() == "etf",
                name=row.get("Name", ""),
                industry=row.get("Industry", ""),
                sector=row.get("Sector", ""),
                classification=row.get("ModelClassification", ""),
                ticker=row.get("Ticker", ""),
            ),
            axis=1,
        )
    return working


def _diversify_ranked_candidates(
    frame: pd.DataFrame,
    limit: int,
    max_per_theme: int = ETF_THEME_MAX_PER_TOP_LIST,
    max_per_stock_industry: int = STOCK_INDUSTRY_MAX_PER_TOP_LIST,
    diversity_prepared: bool = False,
) -> pd.DataFrame:
    if frame.empty or limit <= 0:
        return frame.head(0).copy()
    # refresh_candidate_exports() prepares these columns once for the full wide
    # frame.  Reusing them avoids repeated 200+ column copies for each view.
    working = frame if diversity_prepared else _ensure_diversity_columns(frame)
    row_count = len(working)
    asset_type = working.get(
        "AssetType", pd.Series("", index=working.index)
    ).fillna("").astype(str).str.strip().str.lower()
    is_etf = (
        working.get("IsETF", pd.Series(False, index=working.index)).map(_truthy)
        | asset_type.eq("etf")
    ).to_numpy(dtype=bool)

    def normalized_column(name: str) -> pd.Series:
        return working.get(
            name, pd.Series("", index=working.index)
        ).map(_clean_group_key)

    theme = normalized_column("ETFTheme").where(is_etf, "")
    tracking = normalized_column("ETFTrackingKey").where(is_etf, "")
    classification = normalized_column("ModelClassification")
    industry = normalized_column("Industry")
    sector = normalized_column("Sector")
    classification = classification.where(classification.ne(""), industry)
    classification = classification.where(classification.ne(""), sector)
    classification = classification.where(~is_etf, "")
    cluster = normalized_column("ThemeCluster")

    def factorize_groups(values: pd.Series) -> tuple[np.ndarray, np.ndarray]:
        codes, uniques = pd.factorize(values.where(values.ne("")), sort=False)
        return codes.astype(np.intp, copy=False), np.zeros(len(uniques), dtype=np.int64)

    theme_codes, theme_counts = factorize_groups(theme)
    tracking_codes, tracking_counts = factorize_groups(tracking)
    classification_codes, classification_counts = factorize_groups(classification)
    cluster_codes, cluster_counts = factorize_groups(cluster)

    rank_score = pd.to_numeric(
        working.get("RankingScore", working.get("CrossAssetScore", pd.Series(0.0, index=working.index))),
        errors="coerce",
    ).fillna(0.0).to_numpy(dtype=float)
    risk_filtered = working.get(
        "RankingEligibility", pd.Series("观察", index=working.index)
    ).fillna("观察").astype(str).eq("风险过滤").to_numpy(dtype=bool)
    risk_bucket_penalty = risk_filtered.astype(float) * 1_000_000_000.0

    active = np.ones(row_count, dtype=bool)
    selected_positions: list[int] = []
    selected_penalties: list[float] = []
    tracking_limit = max(1, int(ETF_TRACKING_MAX_PER_TOP_LIST))
    theme_limit = max(1, int(max_per_theme))
    stock_industry_limit = max(1, int(max_per_stock_industry))
    cluster_step = float(THEME_CLUSTER_SOFT_PENALTY)

    for _ in range(min(int(limit), row_count)):
        eligible = active.copy()

        grouped = is_etf & (tracking_codes >= 0)
        eligible[grouped] &= (
            tracking_counts[tracking_codes[grouped]] < tracking_limit
        )
        grouped = is_etf & (theme_codes >= 0)
        eligible[grouped] &= theme_counts[theme_codes[grouped]] < theme_limit
        grouped = (~is_etf) & (classification_codes >= 0)
        eligible[grouped] &= (
            classification_counts[classification_codes[grouped]]
            < stock_industry_limit
        )

        penalties = np.ones(row_count, dtype=float)
        grouped = cluster_codes >= 0
        penalties[grouped] = np.maximum(
            0.70,
            1.0 - cluster_step * cluster_counts[cluster_codes[grouped]],
        )
        values = rank_score * penalties - risk_bucket_penalty
        values[~eligible] = -np.inf
        best_position = int(np.argmax(values))
        if np.isneginf(values[best_position]):
            break

        active[best_position] = False
        selected_positions.append(best_position)
        selected_penalties.append(round(float(penalties[best_position]), 4))
        for codes, counts in (
            (theme_codes, theme_counts),
            (tracking_codes, tracking_counts),
            (classification_codes, classification_counts),
            (cluster_codes, cluster_counts),
        ):
            code = int(codes[best_position])
            if code >= 0:
                counts[code] += 1

    result = working.iloc[selected_positions].copy().reset_index(drop=True)
    result["ResearchDiversityPenalty"] = selected_penalties
    result["ResearchPoolRank"] = np.arange(1, len(result) + 1)
    return result


def _sort_export_rows(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    """Sort an export defensively so legacy CSVs with missing fields still load."""
    available = [column for column in columns if column in frame.columns]
    if not available:
        return frame.copy()
    return frame.sort_values(available, ascending=False, kind="mergesort")



DECISION_RESULT_COLUMNS: tuple[str, ...] = (
    "Ticker", "Name", "Sector", "Industry", "ETFTheme", "IsETF", "AssetType",
    "ModelClassification", "ThemeCluster", "ResearchEligible", "ResearchExclusionReason",
    "Close", "EntrySignal", "SignalStatus", "SignalDays", "EntryZone",
    "BreakoutBuyPrice", "StopLoss", "RankingEligibility", "RankingScore",
    "ResearchPoolRank", "OverallRank", "InstitutionalTier", "InstitutionalScore",
    "HardGatePassed", "DiagnosticFailedCount", "DiagnosticFailedNames",
    "QualityProfile", "ProfitTrendStatus", "CyclicalQualityOverride",
    "TradeReadinessReason", "RankingReason", "DecisionState", "BacktestRunMode",
    "BacktestMode", "BacktestStage", "BacktestSamples", "BacktestEffectiveSamples",
    "BacktestStatus", "BacktestConfidenceTier", "BacktestRequested",
    "BacktestEligibleForRanking", "BacktestSkipReason", "BacktestCacheHit",
    "BacktestDataCutoffDate", "BacktestFreshnessStatus",
    "GlobalCalibrationSamples", "GlobalCalibrationEffectiveSamples",
    "GlobalCalibrationConfidence", "GlobalCalibrationLevel", "TickerEvidence",
    "PeerCalibrationEvidence", "EvidenceStrengthScore", "EvidenceTier", "EvidenceReason",
    "DataAsOf", "DataTradingAgeDays", "RunId", "ModelVersion", "PipelineVersion",
)


def _decision_projection(frame: pd.DataFrame) -> pd.DataFrame:
    # Project first so the GUI path never copies the 200+ column research frame.
    working = frame.reindex(columns=DECISION_RESULT_COLUMNS).copy()
    working["ETFTheme"] = working["ETFTheme"].astype("object")
    missing_theme = working["ETFTheme"].fillna("").astype(str).str.strip().eq("")
    if missing_theme.any():
        inferred = working.loc[missing_theme].apply(_etf_theme_key, axis=1)
        ticker = working.loc[missing_theme, "Ticker"].fillna("").astype(str).str.strip()
        classification = (
            working.loc[missing_theme, "ModelClassification"]
            .fillna("")
            .astype(str)
            .str.strip()
        )
        inferred_text = inferred.fillna("").astype(str).str.strip()
        generic = inferred_text.eq("") | inferred_text.eq(ticker)
        inferred_text = inferred_text.where(
            ~generic | classification.eq(""), classification
        )
        working.loc[missing_theme, "ETFTheme"] = inferred_text
    return working


def write_decision_results(
    frame: pd.DataFrame, output_dir: Path | None = None
) -> Path:
    destination = output_dir if output_dir is not None else OUTPUT_DIR
    path = destination / "DecisionResults.csv"
    decision = _decision_projection(frame)
    _atomic_write_csv(decision, path)
    logger.info(
        "Exported lightweight decision surface: %d rows / %d columns to %s",
        len(decision), len(decision.columns), path,
    )
    return path


def _annotate_candidate_view(frame: pd.DataFrame, view: str) -> pd.DataFrame:
    """Attach canonical view metadata to every candidate export.

    All candidate files deliberately end with the same four columns.  Keeping
    both names and order stable matters for Parquet/CSV consumers that compare
    schemas before concatenating the different research views.
    """
    working = frame.copy().reset_index(drop=True)
    rank = np.arange(1, len(working) + 1)
    working["CandidateView"] = view
    working["CandidateViewRank"] = rank
    # ResearchPoolRank remains the compatibility rank used by older GUI/data
    # consumers.  Specialized views historically received it only when they
    # happened to pass through the diversity selector; v40 makes it explicit.
    working["ResearchPoolRank"] = rank
    if "ResearchDiversityPenalty" not in working.columns:
        working["ResearchDiversityPenalty"] = 1.0
    metadata_columns = (
        "CandidateView",
        "CandidateViewRank",
        "ResearchPoolRank",
        "ResearchDiversityPenalty",
    )
    regular_columns = [
        column for column in working.columns if column not in metadata_columns
    ]
    return working.loc[:, [*regular_columns, *metadata_columns]]

def refresh_candidate_exports(
    frame: pd.DataFrame,
    top_n_csv: int = TOP_N_REPORT,
    top_n_parquet: int = TOP_N_PARQUET,
    output_dir: Path | None = None,
    *,
    _prevalidated_ranked: pd.DataFrame | None = None,
) -> tuple[Path, Path, pd.DataFrame]:
    """Refresh every GUI-facing candidate export from one ranked result frame."""
    destination = output_dir if output_dir is not None else OUTPUT_DIR
    ranked = (
        _prevalidated_ranked.copy()
        if _prevalidated_ranked is not None
        else _rank_valid_candidates(frame)
    )
    ranked = _ensure_diversity_columns(ranked)
    ranked["ModelVersion"] = ranked.get("ModelVersion", pd.Series(SCORING_VERSION, index=ranked.index)).replace("", SCORING_VERSION).fillna(SCORING_VERSION)
    ranked["IndicatorCacheVersion"] = ranked.get("IndicatorCacheVersion", pd.Series(INDICATOR_CACHE_VERSION, index=ranked.index)).replace("", INDICATOR_CACHE_VERSION).fillna(INDICATOR_CACHE_VERSION)
    ranked["BacktestCacheVersion"] = ranked.get("BacktestCacheVersion", pd.Series(BACKTEST_CACHE_VERSION, index=ranked.index)).replace("", BACKTEST_CACHE_VERSION).fillna(BACKTEST_CACHE_VERSION)
    ranked["PipelineVersion"] = ranked.get("PipelineVersion", pd.Series(PIPELINE_VERSION, index=ranked.index)).replace("", PIPELINE_VERSION).fillna(PIPELINE_VERSION)
    if "BacktestStage" in ranked:
        ranked["CandidateGenerationStage"] = np.where(
            ranked["BacktestStage"].fillna("").astype(str).eq("EXACT_REFINEMENT"),
            "EXACT_REFINED", "FAST_SCREEN"
        )

    # The GUI's all-results view reads this compact projection instead of the
    # 200+ column research audit CSV.  AllResults.parquet remains the complete
    # machine-readable research artifact.
    write_decision_results(ranked, destination)

    csv_path = destination / f"Top{top_n_csv}.csv"
    research_pool = _diversify_ranked_candidates(
        ranked, top_n_csv, diversity_prepared=True
    )
    research_pool = _annotate_candidate_view(research_pool, "MIXED_RESEARCH")
    _atomic_write_csv(research_pool, csv_path)
    logger.info(
        "Exported diversified Top %d (%d rows) to %s",
        top_n_csv,
        len(research_pool),
        csv_path,
    )

    # TopN.csv is the compatibility alias of the explicit mixed research list.
    mixed_path = destination / f"Top{top_n_csv}Mixed.csv"
    _atomic_write_csv(research_pool, mixed_path)

    asset_type = ranked.get(
        "AssetType", pd.Series("", index=ranked.index)
    ).fillna("").astype(str).str.strip().str.lower()
    is_etf_mask = ranked.get(
        "IsETF", pd.Series(False, index=ranked.index)
    ).map(_truthy) | asset_type.eq("etf")

    # Dedicated asset lists are pure within-asset rankings.  They intentionally
    # do not inherit mixed-list diversity caps or trade-readiness thresholds.
    stock_path = destination / f"Top{top_n_csv}Stocks.csv"
    stock_pool = ranked.loc[~is_etf_mask].head(top_n_csv).copy()
    stock_pool = _annotate_candidate_view(stock_pool, "STOCK_RESEARCH")
    _atomic_write_csv(stock_pool, stock_path)

    etf_path = destination / f"Top{top_n_csv}ETF.csv"
    etf_pool = ranked.loc[is_etf_mask].head(top_n_csv).copy()
    etf_pool = _annotate_candidate_view(etf_pool, "ETF_RESEARCH")
    _atomic_write_csv(etf_pool, etf_path)
    logger.info(
        "Exported split research lists: mixed=%d, stocks=%d, ETF=%d.",
        len(research_pool),
        len(stock_pool),
        len(etf_pool),
    )

    trade_ready_path = destination / f"Top{top_n_csv}TradeReady.csv"
    trade_ready = ranked.loc[
        ranked.get(
            "RankingEligibility", pd.Series("观察", index=ranked.index)
        ).eq("推荐")
    ]
    trade_ready = _diversify_ranked_candidates(
        trade_ready, top_n_csv, diversity_prepared=True
    )
    trade_ready = _annotate_candidate_view(trade_ready, "TRADE_READY")
    _atomic_write_csv(trade_ready, trade_ready_path)
    logger.info(
        "Exported %d trade-ready candidates to %s",
        len(trade_ready),
        trade_ready_path,
    )

    parquet_path = destination / f"Top{top_n_parquet}.parquet"
    parquet_pool = _annotate_candidate_view(
        ranked.head(top_n_parquet), "RANKED_RESEARCH"
    )
    _atomic_write_parquet(parquet_pool, parquet_path)
    logger.info("Exported Top %d to %s", top_n_parquet, parquet_path)

    # Specialized research surfaces rank by their own purpose.  v39 still sent
    # these through the mixed RankingScore diversity selector, which could make
    # Opportunity and EntryCandidates byte-for-byte clones of Top50Mixed.
    non_risk = ranked.get(
        "RankingEligibility", pd.Series("观察", index=ranked.index)
    ).fillna("观察").astype(str).ne("风险过滤")

    opportunity_path = destination / f"Top{top_n_csv}Opportunity.csv"
    opportunity = _sort_export_rows(
        ranked.loc[non_risk],
        ("OpportunityScore", "RankingScore", "FinalScore"),
    ).head(top_n_csv)
    opportunity = _annotate_candidate_view(opportunity, "OPPORTUNITY")
    _atomic_write_csv(opportunity, opportunity_path)

    trigger_path = destination / f"Top{top_n_csv}BreakoutCandidates.csv"
    entry_signal = ranked.get(
        "EntrySignal", pd.Series("AVOID", index=ranked.index)
    ).fillna("AVOID").astype(str).str.upper()
    confirmed_breakout = (
        non_risk
        & entry_signal.eq("BREAKOUT_CONFIRM")
        & ranked.get("PriceBreakout", pd.Series(False, index=ranked.index)).map(_truthy)
        & ranked.get(
            "BreakoutVolumeConfirmed", pd.Series(False, index=ranked.index)
        ).map(_truthy)
        & ranked.get(
            "BreakoutFlowConfirmed", pd.Series(False, index=ranked.index)
        ).map(_truthy)
    )
    trigger = _sort_export_rows(
        ranked.loc[confirmed_breakout], ("BreakoutScore", "RankingScore")
    ).head(top_n_csv)
    trigger = _annotate_candidate_view(trigger, "CONFIRMED_BREAKOUT")
    _atomic_write_csv(trigger, trigger_path)

    entry_path = destination / f"Top{top_n_csv}EntryCandidates.csv"
    entry = ranked.loc[
        non_risk
        & entry_signal.isin(["BUY_NOW", "BREAKOUT_CONFIRM", "WAIT_PULLBACK"])
    ]
    entry = _sort_export_rows(
        entry, ("EntrySignalPriority", "EntryScore", "RankingScore")
    ).head(top_n_csv)
    entry = _annotate_candidate_view(entry, "ENTRY_SETUP")
    _atomic_write_csv(entry, entry_path)

    trap_path = destination / f"Top{top_n_csv}ValueTrapRisk.csv"
    trap = ranked.loc[
        pd.to_numeric(
            ranked.get("ValueTrapRisk", pd.Series(0.0, index=ranked.index)),
            errors="coerce",
        ).fillna(0) >= 60
    ]
    trap = _sort_export_rows(trap, ("ValueTrapRisk", "RankingScore")).head(top_n_csv)
    trap = _annotate_candidate_view(trap, "VALUE_TRAP_RISK")
    _atomic_write_csv(trap, trap_path)

    sustained_path = destination / f"Top{top_n_csv}SustainedSignals.csv"
    signal_days = pd.to_numeric(
        ranked.get("SignalDays", pd.Series(0.0, index=ranked.index)),
        errors="coerce",
    ).fillna(0)
    sustained = ranked.loc[non_risk & signal_days.gt(0)]
    sustained = _sort_export_rows(
        sustained, ("SignalDays", "OpportunityScore", "RankingScore")
    ).head(top_n_csv)
    sustained = _annotate_candidate_view(sustained, "SUSTAINED_SIGNAL")
    _atomic_write_csv(sustained, sustained_path)
    return csv_path, parquet_path, ranked


def export_top_csv(results: list[ScanResult], n: int = TOP_N_REPORT) -> Path:
    """
    Export the top *n* tickers to TopN.csv.

    Returns the path to the generated file.
    """
    df = _results_to_dataframe(_rankable_results(results))
    top = df.head(n)

    path = OUTPUT_DIR / f"Top{n}.csv"
    _atomic_write_csv(top, path)
    logger.info("Exported Top %d (%d rows) to %s", n, len(top), path)
    return path


def export_full_csv(results: list[ScanResult]) -> Path:
    """Export ALL scored tickers to AllResults.csv."""
    df = _results_to_dataframe(results)
    path = OUTPUT_DIR / "AllResults.csv"
    _atomic_write_csv(df, path)
    logger.info("Exported all %d results to %s", len(df), path)
    return path


# ======================================================================
# Parquet Export
# ======================================================================


def export_top_parquet(results: list[ScanResult], n: int = TOP_N_PARQUET) -> Path:
    """
    Export top *n* tickers to Top200.parquet.

    Returns the path to the generated file.
    """
    df = _results_to_dataframe(_rankable_results(results))
    top = df.head(n)

    path = OUTPUT_DIR / f"Top{n}.parquet"
    _atomic_write_parquet(top, path)
    logger.info("Exported Top %d to %s", n, path)
    return path


# ======================================================================
# Full export
# ======================================================================


def export_all(
    results: list[ScanResult],
    top_n_csv: int = TOP_N_REPORT,
    top_n_parquet: int = TOP_N_PARQUET,
    data_source: str = "eastmoney",
) -> tuple[Path, Path, Path, Path]:
    """Export CSV, Parquet, and full results. Returns (csv_path, parquet_path, full_csv, full_parquet)."""
    df = enrich_evidence_fields(
        _apply_research_policy(enrich_signal_lifecycle(_results_to_dataframe(results)))
    )
    if df.empty:
        research_history = refresh_research_outcomes(data_source)
        write_research_reports(research_history)
        csv_path = export_top_csv(results, n=top_n_csv)
        parquet_path = export_top_parquet(results, n=top_n_parquet)
        full_csv = export_full_csv(results)
        full_parquet_path = export_full_parquet(results)
        write_decision_results(df, OUTPUT_DIR)
        for name in (
            f"Top{top_n_csv}Mixed.csv",
            f"Top{top_n_csv}Stocks.csv",
            f"Top{top_n_csv}ETF.csv",
            f"Top{top_n_csv}Opportunity.csv",
            f"Top{top_n_csv}BreakoutCandidates.csv",
            f"Top{top_n_csv}EntryCandidates.csv",
            f"Top{top_n_csv}TradeReady.csv",
            f"Top{top_n_csv}ValueTrapRisk.csv",
            f"Top{top_n_csv}SustainedSignals.csv",
        ):
            _atomic_write_csv(df, OUTPUT_DIR / name)
        return csv_path, parquet_path, full_csv, full_parquet_path

    # Validate and prepare the complete candidate set before publishing even
    # AllResults.  Previously an integrity failure could replace the full
    # files while leaving every Top list on the prior run outside DAILY mode.
    # DAILY's transaction remains the outer safety net; this preflight makes a
    # normal scan fail before any result artifact is replaced as well.
    rankable = _rank_valid_candidates(df)
    research_history = refresh_research_outcomes(data_source)
    write_research_reports(research_history)

    full_csv = OUTPUT_DIR / "AllResults.csv"
    _atomic_write_csv(df, full_csv)
    logger.info("Exported all %d results to %s", len(df), full_csv)

    full_parquet_path = OUTPUT_DIR / "AllResults.parquet"
    _atomic_write_parquet(df, full_parquet_path)
    logger.info("Exported all %d results to %s", len(df), full_parquet_path)
    csv_path, parquet_path, rankable = refresh_candidate_exports(
        df,
        top_n_csv=top_n_csv,
        top_n_parquet=top_n_parquet,
        output_dir=OUTPUT_DIR,
        _prevalidated_ranked=rankable,
    )
    signal_counts = rankable.get("EntrySignal", pd.Series(dtype=str)).value_counts()
    logger.info(
        "最终候选：BUY_NOW=%d，BREAKOUT_CONFIRM=%d，WAIT_PULLBACK=%d，AVOID=%d；回测低可信度=%d，Quality UNKNOWN=%d，HardRisk过滤=%d。",
        int(signal_counts.get("BUY_NOW", 0)),
        int(signal_counts.get("BREAKOUT_CONFIRM", 0)),
        int(signal_counts.get("WAIT_PULLBACK", 0)),
        int(signal_counts.get("AVOID", 0)),
        int(rankable.get("BacktestConfidenceTier", pd.Series("", index=rankable.index)).isin(["样本不足", "低可信度"]).sum()),
        int(rankable.get("InstitutionHoldingStatus", pd.Series("", index=rankable.index)).eq("UNKNOWN").sum()),
        int(rankable.get("RankingEligibility", pd.Series("", index=rankable.index)).eq("风险过滤").sum()),
    )
    logger.info(
        "交易状态：就绪=%d，观察=%d，行情过期=%d，质量未通过=%d。",
        int(rankable.get("RankingEligibility", pd.Series("", index=rankable.index)).eq("推荐").sum()),
        int(rankable.get("RankingEligibility", pd.Series("", index=rankable.index)).eq("观察").sum()),
        int(rankable.get("DataFreshnessStatus", pd.Series("", index=rankable.index)).eq("过期").sum()),
        int(
            (
                ~rankable.get(
                    "QualityGate", pd.Series(True, index=rankable.index)
                )
                .astype(str)
                .str.strip()
                .str.lower()
                .isin({"true", "1", "yes", "y", "是"})
            ).sum()
        ),
    )

    return csv_path, parquet_path, full_csv, full_parquet_path


def export_full_parquet(results: list[ScanResult]) -> Path:
    """Export ALL scored tickers to AllResults.parquet."""
    df = _results_to_dataframe(results)
    path = OUTPUT_DIR / "AllResults.parquet"
    _atomic_write_parquet(df, path)
    logger.info("Exported all %d results to %s", len(df), path)
    return path


# ======================================================================
# Terminal Report
# ======================================================================


def _build_reasons(result: ScanResult) -> list[str]:
    """Build a list of human-readable reasons why this ticker scored well."""
    reasons: list[str] = []

    if result.filter_details.get("bear_market"):
        reasons.append("✓ MA200 declining, long-term bear market")

    if result.filter_details.get("volume_accumulation"):
        reasons.append(
            f"✓ Sustained volume accumulation ({result.volume_accum_days} days)"
        )

    if result.filter_details.get("obv_divergence"):
        reasons.append("✓ OBV Bullish Divergence detected")

    if result.filter_details.get("cmf_positive"):
        cmf_str = f"{result.cmf:.3f}" if not np.isnan(result.cmf) else "N/A"
        reasons.append(f"✓ CMF Positive ({cmf_str})")

    if result.filter_details.get("ad_slope"):
        reasons.append("✓ A/D Line rising")

    if result.filter_details.get("volatility_contraction"):
        reasons.append("✓ Volatility contraction (ATR/BB)")

    if result.filter_details.get("consolidation"):
        reasons.append("✓ Bottom consolidation pattern")

    if result.wyckoff_phase not in ("Unknown", ""):
        reasons.append(f"✓ Wyckoff: {result.wyckoff_phase}")

    return reasons


def print_terminal_report(results: list[ScanResult], n: int = TOP_N_REPORT) -> None:
    """
    Print a formatted Top-N report to stdout.

    Each entry shows rank, ticker, score, and specific reasons.
    """
    top = _rankable_results(results)[:n]
    if not top:
        print("\nNo tickers passed the accumulation filters.\n")
        return

    print()
    print("=" * 70)
    print(f"  INSTITUTIONAL ACCUMULATION SCANNER — TOP {min(n, len(top))}")
    print(
        f"  {datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M CST')}"
    )
    print("=" * 70)
    print()

    for i, result in enumerate(top, start=1):
        name_str = f" — {result.name}" if result.name else ""
        etf_tag = " [ETF]" if result.is_etf else ""
        sector_str = f" | {result.sector}" if result.sector else ""

        print(
            f"  {i:3d}. {result.ticker:<8s} Score: {result.score.total:5.1f}{etf_tag}"
        )
        if name_str.strip():
            print(f"      {name_str.strip()}{sector_str}")
        print(
            f"      Close: ¥{result.close:.2f} | "
            f"RSI14: {result.rsi14:.1f} | "
            f"ATR14: {result.atr14:.2f} | "
            f"Phase: {result.wyckoff_phase}"
        )

        reasons = _build_reasons(result)
        for reason in reasons:
            print(f"      {reason}")

        print(f"      {'-' * 60}")


def print_scan_summary(report: ScanReport) -> None:
    """Print a one-paragraph scan summary to stdout."""
    print()
    print(f"Scan complete in {report.elapsed_seconds:.1f} seconds.")
    print(f"  Total tickers:    {report.total_tickers}")
    print(f"  Scanned:          {report.successful + report.failed}")
    print(f"  Successful:       {report.successful}")
    print(f"  Failed/No data:   {report.failed}")
    print(f"  Passed filters:   {report.passed_filters}")
    print()
