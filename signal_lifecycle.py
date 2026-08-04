from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

from config import OUTPUT_DIR

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
        history = pd.read_csv(HISTORY_FILE, encoding="utf-8-sig", dtype={"Ticker": str})
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
    outcome_columns = {"Return20D", "MaxDrawdown20D", "Return60D", "MaxDrawdown60D"}
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
    accumulation = _number(frame.get("AccumulationScore", pd.Series(index=frame.index)))
    result = pd.Series("底部观察", index=frame.index)
    result.loc[stage.eq("正在吸筹") | ((accumulation >= 15) & (score >= 40))] = (
        "机构吸筹"
    )
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
            "FAILED" if previous is not None and _bool(previous["SignalActive"]) else ""
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
    active = _is_active(result)
    history = _load_history()
    history["Ticker"] = history["Ticker"].astype(str).str.strip().str.upper()
    history["TradeDate"] = history["TradeDate"].astype(str)
    trade_dates = (
        result.get("DataAsOf", pd.Series("", index=result.index)).fillna("").astype(str)
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
    signal_days: list[int] = []
    starts: list[str] = []
    statuses: list[str] = []
    strengths: list[str] = []
    for position, (_, row) in enumerate(result.iterrows()):
        ticker = str(row["Ticker"])
        trade_date_text = str(trade_dates.iloc[position])
        trade_date = pd.to_datetime(trade_date_text, errors="coerce")
        previous: pd.Series | None = None
        ticker_history = history_by_ticker.get(ticker)
        if not pd.isna(trade_date):
            previous_date = dated_history.loc[
                dated_history["_TradeDate"].lt(trade_date), "_TradeDate"
            ].max()
            if not pd.isna(previous_date):
                previous_rows = dated_history.loc[
                    dated_history["_TradeDate"].eq(previous_date)
                    & dated_history["Ticker"].eq(ticker)
                ]
                if not previous_rows.empty:
                    previous = previous_rows.iloc[-1]
        is_active = bool(active.iloc[position])
        prior_active = previous is not None and _bool(previous["SignalActive"])
        if is_active and prior_active and previous is not None:
            days = int(previous["SignalDays"]) + 1
            start = str(previous["SignalStartDate"])
        else:
            days = int(is_active)
            start = trade_date_text if is_active else ""
        statuses.append(
            _status(is_active, previous, float(row["OpportunityScore"]), days)
        )
        values = (
            ticker_history["OpportunityScore"].tail(29).tolist()
            if ticker_history is not None
            else []
        )
        strengths.append(
            "|".join(
                f"{value:.0f}" for value in [*values, float(row["OpportunityScore"])]
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
    prior_recency_factor = _number(
        result.get("SignalRecencyFactor", pd.Series(np.nan, index=result.index)),
        np.nan,
    )
    result["SignalRecencyFactor"] = np.where(
        valid_recency,
        np.maximum(0.7, 1.0 - recency_days / 100.0),
        1.0,
    )
    base_institutional = _number(
        result.get("InstitutionalScore", result["Score"]), default=np.nan
    )
    base_institutional = base_institutional.where(
        base_institutional.notna(), _number(result["Score"])
    )
    result["BreakoutQualityFactor"] = _number(
        result.get("BreakoutQualityFactor", pd.Series(1.0, index=result.index)), 1.0
    ).clip(0.0, 1.0)
    recency_multiplier = 0.8 + 0.2 * result["SignalRecencyFactor"]
    # Historical calibration already writes a non-neutral recency factor into
    # InstitutionalScore.  Preserve that result instead of applying the same
    # penalty a second time when reports are regenerated.
    already_adjusted = prior_recency_factor.notna() & prior_recency_factor.lt(0.9999)
    result["InstitutionalScore"] = pd.Series(
        np.where(
            already_adjusted,
            base_institutional,
            base_institutional * recency_multiplier,
        ),
        index=result.index,
    ).round(4)
    volume_confirmed = _number(
        result.get("VolumeScore", pd.Series(index=result.index))
    ).ge(15.0) | result.get(
        "VolAccum", pd.Series(False, index=result.index)
    ).map(_bool)
    result["InstitutionalTier"] = "D级陷阱池"
    result.loc[result["InstitutionalScore"].ge(65.0), "InstitutionalTier"] = "C级价值观察"
    result.loc[
        result["InstitutionalScore"].between(75.0, 85.0, inclusive="left"),
        "InstitutionalTier",
    ] = "B级观察"
    result.loc[
        result["InstitutionalScore"].gt(85.0)
        & result["SignalRecencyDays"].le(20)
        & volume_confirmed,
        "InstitutionalTier",
    ] = "A级机构启动"
    quality_failed = (
        ~result.get("IsETF", pd.Series(False, index=result.index)).map(_bool)
        & result.get("QualityDataAvailable", pd.Series(False, index=result.index)).map(_bool)
        & ~result.get("QualityGate", pd.Series(False, index=result.index)).map(_bool)
    )
    quality_tier_map = {
        "A级机构启动": "B级观察",
        "B级观察": "C级价值观察",
        "C级价值观察": "C级价值观察",
    }
    result.loc[quality_failed, "InstitutionalTier"] = result.loc[
        quality_failed, "InstitutionalTier"
    ].map(quality_tier_map).fillna("D级陷阱池")
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
        _number(result.get("ScoreConfidence", pd.Series(index=result.index)), 0.0) * 100
    ).round(0)
    snapshot = pd.DataFrame(
        {
            "TradeDate": trade_dates,
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
            "Close": _number(result.get("Close", pd.Series(index=result.index)), np.nan),
            "Score": _number(result["Score"]),
            "OpportunityScore": _number(result["OpportunityScore"]),
            "InstitutionalScore": _number(result["InstitutionalScore"], np.nan),
            "InstitutionalTier": result["InstitutionalTier"],
            "BreakoutQualityFactor": _number(result["BreakoutQualityFactor"], np.nan),
            "SignalRecencyFactor": _number(result["SignalRecencyFactor"], np.nan),
            "SectorConfirmationFactor": _number(
                result.get("SectorConfirmationFactor", pd.Series(index=result.index)), np.nan
            ),
            "FailureSignalFactor": _number(
                result.get("FailureSignalFactor", pd.Series(index=result.index)), np.nan
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
                result.get("IndustryRelativeStrength", pd.Series(index=result.index))
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
        snapshot if history.empty else pd.concat([history, snapshot], ignore_index=True)
    )
    history = history.drop_duplicates(["TradeDate", "Ticker"], keep="last").sort_values(
        ["TradeDate", "Ticker"]
    )
    _atomic_write(history[HISTORY_COLUMNS], HISTORY_FILE)
    tracking = result.loc[active].sort_values(
        ["SignalDays", "OpportunityScore"], ascending=False
    )
    _atomic_write(tracking, TRACKING_FILE)
    return result
