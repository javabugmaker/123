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
import re
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from analytics import refresh_research_outcomes, write_research_reports
from config import (
    INSTITUTIONAL_TIER_A_SCORE,
    INSTITUTIONAL_TIER_B_SCORE,
    INSTITUTIONAL_TIER_C_SCORE,
    INSTITUTIONAL_TIER_TRAP_LABEL,
    INSTITUTIONAL_TIER_WAIT_LABEL,
    ETF_THEME_MAX_PER_TOP_LIST,
    ETF_TRACKING_MAX_PER_TOP_LIST,
    STOCK_INDUSTRY_MAX_PER_TOP_LIST,
    THEME_CLUSTER_SOFT_PENALTY,
    SCORING_VERSION,
    OUTPUT_DIR,
    TOP_N_PARQUET,
    TOP_N_REPORT,
    VALUE_TRAP_RISK_THRESHOLD,
)
from classification import etf_theme_key, etf_tracking_key, theme_cluster
from scanner import ScanReport, ScanResult
from performance_cache import BACKTEST_CACHE_VERSION, INDICATOR_CACHE_VERSION
from signal_lifecycle import enrich_signal_lifecycle, finalize_signal_ranking

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


def _results_to_dataframe(results: list[ScanResult]) -> pd.DataFrame:
    """Convert ScanResult list to a sorted, clean DataFrame."""
    scan_timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    run_id = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    rows = []
    for r in results:
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
                "Score": round(r.score.total, 2),
                "BaseScore": round(r.base_score, 2) if np.isfinite(r.base_score) else None,
                "TriggerScore": round(r.trigger_score, 2) if np.isfinite(r.trigger_score) else None,
                "FinalScore": round(r.final_score, 2) if np.isfinite(r.final_score) else None,
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
                "BreakoutBuyPrice": round(r.breakout_buy_price, 2) if np.isfinite(r.breakout_buy_price) else None,
                "BreakoutVolumeRatio": round(r.breakout_volume_ratio, 4) if np.isfinite(r.breakout_volume_ratio) else None,
                "BreakoutVolumeConfirmed": r.breakout_volume_confirmed,
                "BreakoutFlowConfirmed": r.breakout_flow_confirmed,
                "PriceBreakout": r.price_breakout,
                "StopLoss": round(r.stop_loss, 2) if np.isfinite(r.stop_loss) else None,
                "ValueTrapRisk": round(r.value_trap_risk, 2) if np.isfinite(r.value_trap_risk) else None,
                "RiskWarning": r.risk_warning,
                "OperationAdvice": r.operation_advice,
                "BacktestScore": round(r.backtest_score, 2)
                if np.isfinite(r.backtest_score)
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
                "QualityGateReason": r.quality_gate_reason,
                "QualityMultiplier": round(r.quality_multiplier, 4),
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
                "PassedFilters": r.passed_filters,
                "UniverseEligible": r.universe_eligible,
                "SignalConfirmed": r.signal_confirmed,
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
                "DecisionState": r.decision_state,
                "DecisionReason": r.decision_reason,
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


def _rank_valid_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    """Return valid results in the same order used by every candidate export."""
    if frame.empty:
        return frame.copy()
    valid = frame.loc[
        frame.get("Error", pd.Series("", index=frame.index))
        .fillna("")
        .astype(str)
        .str.strip()
        .eq("")
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


def _etf_theme_key(row: pd.Series) -> str:
    if not (_truthy(row.get("IsETF", False)) or str(row.get("AssetType", "")).strip().lower() == "etf"):
        return ""
    return etf_theme_key(
        name=row.get("Name", ""),
        industry=row.get("Industry", ""),
        sector=row.get("Sector", ""),
        ticker=row.get("Ticker", ""),
    )


def _diversify_ranked_candidates(
    frame: pd.DataFrame,
    limit: int,
    max_per_theme: int = ETF_THEME_MAX_PER_TOP_LIST,
    max_per_stock_industry: int = STOCK_INDUSTRY_MAX_PER_TOP_LIST,
) -> pd.DataFrame:
    if frame.empty or limit <= 0:
        return frame.head(0).copy()
    working = frame.copy()
    working["ETFTheme"] = working.apply(_etf_theme_key, axis=1)
    working["ETFTrackingKey"] = working.apply(
        lambda row: etf_tracking_key(
            name=row.get("Name", ""), industry=row.get("Industry", ""),
            sector="", ticker=row.get("Ticker", "")
        ) if _truthy(row.get("IsETF", False)) or str(row.get("AssetType", "")).strip().lower() == "etf" else "",
        axis=1,
    )
    working["ThemeCluster"] = working.apply(
        lambda row: theme_cluster(
            is_etf=_truthy(row.get("IsETF", False)) or str(row.get("AssetType", "")).strip().lower() == "etf",
            name=row.get("Name", ""), industry=row.get("Industry", ""), sector=row.get("Sector", ""),
            classification=row.get("ModelClassification", ""), ticker=row.get("Ticker", ""),
        ), axis=1,
    )
    theme_counts: dict[str, int] = {}
    tracking_counts: dict[str, int] = {}
    stock_industry_counts: dict[str, int] = {}
    cluster_counts: dict[str, int] = {}
    selected: list[int] = []
    remaining = list(working.index)
    rank_score = pd.to_numeric(
        working.get("RankingScore", working.get("CrossAssetScore", pd.Series(0.0, index=working.index))),
        errors="coerce",
    ).fillna(0.0)
    penalties: dict[int, float] = {}
    while remaining and len(selected) < int(limit):
        best_index: int | None = None
        best_value = -np.inf
        best_penalty = 1.0
        for index in remaining:
            row = working.loc[index]
            theme = str(row.get("ETFTheme", "") or "").strip()
            tracking = str(row.get("ETFTrackingKey", "") or "").strip()
            classification = str(row.get("ModelClassification", "") or row.get("Industry", "") or row.get("Sector", "") or "").strip()
            cluster = str(row.get("ThemeCluster", "") or "").strip()
            if tracking and tracking_counts.get(tracking, 0) >= max(1, int(ETF_TRACKING_MAX_PER_TOP_LIST)):
                continue
            if theme and theme_counts.get(theme, 0) >= max(1, int(max_per_theme)):
                continue
            if not theme and classification and classification.lower() not in {"nan", "none"} and stock_industry_counts.get(classification, 0) >= max(1, int(max_per_stock_industry)):
                continue
            penalty = max(0.70, 1.0 - float(THEME_CLUSTER_SOFT_PENALTY) * cluster_counts.get(cluster, 0)) if cluster else 1.0
            value = float(rank_score.loc[index]) * penalty
            if value > best_value:
                best_index, best_value, best_penalty = int(index), value, penalty
        if best_index is None:
            break
        row = working.loc[best_index]
        theme = str(row.get("ETFTheme", "") or "").strip()
        tracking = str(row.get("ETFTrackingKey", "") or "").strip()
        classification = str(row.get("ModelClassification", "") or row.get("Industry", "") or row.get("Sector", "") or "").strip()
        cluster = str(row.get("ThemeCluster", "") or "").strip()
        if theme:
            theme_counts[theme] = theme_counts.get(theme, 0) + 1
        if tracking:
            tracking_counts[tracking] = tracking_counts.get(tracking, 0) + 1
        if not theme and classification and classification.lower() not in {"nan", "none"}:
            stock_industry_counts[classification] = stock_industry_counts.get(classification, 0) + 1
        if cluster:
            cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1
        penalties[best_index] = best_penalty
        selected.append(best_index)
        remaining.remove(best_index)
    result = working.loc[selected].copy().reset_index(drop=True)
    result["ResearchDiversityPenalty"] = [round(penalties.get(index, 1.0), 4) for index in selected]
    result["ResearchPoolRank"] = np.arange(1, len(result) + 1)
    return result


def _sort_export_rows(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    """Sort an export defensively so legacy CSVs with missing fields still load."""
    available = [column for column in columns if column in frame.columns]
    if not available:
        return frame.copy()
    return frame.sort_values(available, ascending=False, kind="mergesort")


def refresh_candidate_exports(
    frame: pd.DataFrame,
    top_n_csv: int = TOP_N_REPORT,
    top_n_parquet: int = TOP_N_PARQUET,
    output_dir: Path | None = None,
) -> tuple[Path, Path, pd.DataFrame]:
    """Refresh every GUI-facing candidate export from one ranked result frame."""
    destination = output_dir if output_dir is not None else OUTPUT_DIR
    ranked = _rank_valid_candidates(frame)
    ranked["ModelVersion"] = ranked.get("ModelVersion", pd.Series(SCORING_VERSION, index=ranked.index)).replace("", SCORING_VERSION).fillna(SCORING_VERSION)
    ranked["IndicatorCacheVersion"] = ranked.get("IndicatorCacheVersion", pd.Series(INDICATOR_CACHE_VERSION, index=ranked.index)).replace("", INDICATOR_CACHE_VERSION).fillna(INDICATOR_CACHE_VERSION)
    ranked["BacktestCacheVersion"] = ranked.get("BacktestCacheVersion", pd.Series(BACKTEST_CACHE_VERSION, index=ranked.index)).replace("", BACKTEST_CACHE_VERSION).fillna(BACKTEST_CACHE_VERSION)
    if "BacktestStage" in ranked:
        ranked["CandidateGenerationStage"] = np.where(
            ranked["BacktestStage"].fillna("").astype(str).eq("EXACT_REFINEMENT"),
            "EXACT_REFINED", "FAST_SCREEN"
        )

    csv_path = destination / f"Top{top_n_csv}.csv"
    research_pool = _diversify_ranked_candidates(ranked, top_n_csv)
    _atomic_write_csv(research_pool, csv_path)
    logger.info(
        "Exported diversified Top %d (%d rows) to %s",
        top_n_csv,
        len(research_pool),
        csv_path,
    )

    # Keep TopN.csv as the compatibility alias while publishing explicit
    # mixed / stock / ETF research lists so one asset class cannot hide the
    # other in the GUI.
    mixed_path = destination / f"Top{top_n_csv}Mixed.csv"
    _atomic_write_csv(research_pool, mixed_path)

    asset_type = ranked.get(
        "AssetType", pd.Series("", index=ranked.index)
    ).fillna("").astype(str).str.strip().str.lower()
    is_etf_mask = ranked.get(
        "IsETF", pd.Series(False, index=ranked.index)
    ).map(_truthy) | asset_type.eq("etf")

    stock_path = destination / f"Top{top_n_csv}Stocks.csv"
    stock_pool = _diversify_ranked_candidates(
        ranked.loc[~is_etf_mask], top_n_csv
    )
    _atomic_write_csv(stock_pool, stock_path)

    etf_path = destination / f"Top{top_n_csv}ETF.csv"
    etf_pool = _diversify_ranked_candidates(
        ranked.loc[is_etf_mask], top_n_csv
    )
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
    trade_ready = _diversify_ranked_candidates(trade_ready, top_n_csv)
    _atomic_write_csv(trade_ready, trade_ready_path)
    logger.info(
        "Exported %d trade-ready candidates to %s",
        len(trade_ready),
        trade_ready_path,
    )

    parquet_path = destination / f"Top{top_n_parquet}.parquet"
    _atomic_write_parquet(ranked.head(top_n_parquet), parquet_path)
    logger.info("Exported Top %d to %s", top_n_parquet, parquet_path)

    opportunity_path = destination / f"Top{top_n_csv}Opportunity.csv"
    opportunity = _sort_export_rows(
        ranked,
        ("RankingScore", "FinalScore", "TriggerScore")
        if "FinalScore" in ranked.columns
        else ("OpportunityScore", "Score"),
    )
    opportunity = _diversify_ranked_candidates(opportunity, top_n_csv)
    _atomic_write_csv(opportunity, opportunity_path)

    trigger_path = destination / f"Top{top_n_csv}BreakoutCandidates.csv"
    trigger = ranked.loc[
        (
            pd.to_numeric(
                ranked.get("BreakoutScore", pd.Series(0.0, index=ranked.index)),
                errors="coerce",
            ).fillna(0) >= 55
        )
        & ranked.get(
            "SmartMoneyStage", pd.Series("NONE", index=ranked.index)
        ).isin(["ACCUMULATION", "BREAKOUT"])
    ]
    trigger = _sort_export_rows(trigger, ("RankingScore", "BreakoutScore"))
    trigger = _diversify_ranked_candidates(trigger, top_n_csv)
    _atomic_write_csv(trigger, trigger_path)

    entry_path = destination / f"Top{top_n_csv}EntryCandidates.csv"
    entry = ranked.loc[
        ranked.get("EntrySignal", pd.Series("AVOID", index=ranked.index)).isin(
            ["BUY_NOW", "BREAKOUT_CONFIRM", "WAIT_PULLBACK"]
        )
    ]
    entry = _sort_export_rows(entry, ("RankingScore", "EntryScore"))
    entry = _diversify_ranked_candidates(entry, top_n_csv)
    _atomic_write_csv(entry, entry_path)

    trap_path = destination / f"Top{top_n_csv}ValueTrapRisk.csv"
    trap = ranked.loc[
        pd.to_numeric(
            ranked.get("ValueTrapRisk", pd.Series(0.0, index=ranked.index)),
            errors="coerce",
        ).fillna(0) >= 60
    ]
    trap = _sort_export_rows(trap, ("ValueTrapRisk", "RankingScore"))
    _atomic_write_csv(trap.head(top_n_csv), trap_path)

    sustained_path = destination / f"Top{top_n_csv}SustainedSignals.csv"
    sustained = ranked.loc[
        pd.to_numeric(
            ranked.get("SignalDays", pd.Series(0.0, index=ranked.index)),
            errors="coerce",
        ).fillna(0).gt(0)
    ]
    sustained = _sort_export_rows(sustained, ("SignalDays", "OpportunityScore"))
    _atomic_write_csv(sustained.head(top_n_csv), sustained_path)
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
    df = enrich_signal_lifecycle(_results_to_dataframe(results))
    research_history = refresh_research_outcomes(data_source)
    write_research_reports(research_history)
    if df.empty:
        csv_path = export_top_csv(results, n=top_n_csv)
        parquet_path = export_top_parquet(results, n=top_n_parquet)
        full_csv = export_full_csv(results)
        full_parquet_path = export_full_parquet(results)
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
