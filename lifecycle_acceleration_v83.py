"""v83 vectorised lifecycle/history reconciliation.

The stable lifecycle policy remains authoritative. This module replaces only
the row-by-row history lookup/status construction with one searchsorted/merge
pass plus NumPy masks. Ranking, persistence columns and signal semantics stay
identical to the stable engine.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

LIFECYCLE_ACCELERATION_VERSION = "2026-08-21-v83-vectorized-lifecycle-history-v1"


def _truthy_series(values: pd.Series, default: bool = False) -> pd.Series:
    """Parse bool-like values without object-dtype fillna downcast warnings."""
    if pd.api.types.is_bool_dtype(values.dtype):
        return values.astype("boolean").fillna(default).astype(bool)
    normalized = values.astype("string").fillna("true" if default else "false")
    return normalized.str.strip().str.lower().isin({"true", "1", "yes", "y", "是"})


def vectorized_lifecycle_state(
    result: pd.DataFrame,
    history: pd.DataFrame,
    active: pd.Series,
    trade_dates: pd.Series,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Return days/start/status/strength history without a current-row Python loop."""
    index = result.index
    row_count = len(result)
    if row_count == 0:
        empty_object = pd.Series(dtype=object, index=index)
        return (
            pd.Series(dtype=int, index=index),
            empty_object,
            empty_object.copy(),
            empty_object.copy(),
        )

    ticker = result["Ticker"].fillna("").astype(str).str.strip().str.upper()
    parsed_trade_dates = pd.to_datetime(trade_dates, errors="coerce")
    opportunity = pd.to_numeric(result["OpportunityScore"], errors="coerce")
    active_values = _truthy_series(active, False).to_numpy(dtype=bool)

    prior_history = history.copy()
    if "Ticker" not in prior_history.columns:
        prior_history["Ticker"] = ""
    if "TradeDate" not in prior_history.columns:
        prior_history["TradeDate"] = ""
    prior_history["Ticker"] = (
        prior_history["Ticker"].fillna("").astype(str).str.strip().str.upper()
    )
    prior_history["_TradeDate"] = pd.to_datetime(
        prior_history["TradeDate"], errors="coerce"
    )
    dated_history = prior_history.dropna(subset=["_TradeDate"])
    deduped = dated_history.drop_duplicates(["_TradeDate", "Ticker"], keep="last")

    previous_dates = np.full(
        row_count,
        np.datetime64("NaT", "ns"),
        dtype="datetime64[ns]",
    )
    if not dated_history.empty:
        historical_dates = np.sort(
            dated_history["_TradeDate"].dropna().unique().astype("datetime64[ns]")
        )
        current_dates = parsed_trade_dates.to_numpy(dtype="datetime64[ns]")
        positions = np.searchsorted(historical_dates, current_dates, side="left") - 1
        usable = ~np.isnat(current_dates) & (positions >= 0)
        if np.any(usable):
            previous_dates[usable] = historical_dates[positions[usable]]

    lookup = pd.DataFrame(
        {
            "_Position": np.arange(row_count, dtype=np.int64),
            "Ticker": ticker.to_numpy(dtype=object),
            "_PreviousTradeDate": pd.to_datetime(previous_dates),
        }
    )
    previous_columns = [
        column
        for column in (
            "SignalActive",
            "SignalDays",
            "SignalStartDate",
            "OpportunityScore",
        )
        if column in deduped.columns
    ]
    previous = deduped[["_TradeDate", "Ticker", *previous_columns]].copy()
    previous = previous.rename(
        columns={
            "_TradeDate": "_PreviousTradeDate",
            "SignalActive": "_PrevSignalActive",
            "SignalDays": "_PrevSignalDays",
            "SignalStartDate": "_PrevSignalStartDate",
            "OpportunityScore": "_PrevOpportunityScore",
        }
    )
    previous["_PrevExists"] = True
    merged = lookup.merge(
        previous,
        on=["_PreviousTradeDate", "Ticker"],
        how="left",
        sort=False,
        validate="many_to_one",
    ).sort_values("_Position", kind="mergesort")

    if "_PrevExists" in merged.columns:
        prev_exists = merged["_PrevExists"].eq(True)  # noqa: E712
    else:
        prev_exists = pd.Series(False, index=merged.index, dtype=bool)
    prev_active_raw = merged.get(
        "_PrevSignalActive", pd.Series(False, index=merged.index, dtype=bool)
    )
    prior_active = (
        prev_exists.to_numpy(dtype=bool)
        & _truthy_series(prev_active_raw, False).to_numpy(dtype=bool)
    )
    prev_days = pd.to_numeric(
        merged.get("_PrevSignalDays", pd.Series(0.0, index=merged.index)),
        errors="coerce",
    ).fillna(0.0).to_numpy(dtype=np.float64)
    days = np.where(
        active_values & prior_active,
        prev_days.astype(np.int64) + 1,
        active_values.astype(np.int64),
    )

    trade_date_text = trade_dates.fillna("").astype(str).to_numpy(dtype=str)
    prev_start = (
        merged.get("_PrevSignalStartDate", pd.Series("", index=merged.index))
        .astype("string")
        .fillna("")
        .to_numpy(dtype=str)
    )
    starts = np.where(
        active_values & prior_active,
        prev_start,
        np.where(active_values, trade_date_text, ""),
    )

    current_opportunity = opportunity.to_numpy(dtype=np.float64)
    previous_opportunity = pd.to_numeric(
        merged.get("_PrevOpportunityScore", pd.Series(np.nan, index=merged.index)),
        errors="coerce",
    ).to_numpy(dtype=np.float64)
    continued = active_values & prior_active
    confirmed = continued & (days >= 5) & (
        current_opportunity >= previous_opportunity - 1.0
    )
    strengthen = continued & ~confirmed & (
        current_opportunity >= previous_opportunity + 2.0
    )
    weaken = continued & ~confirmed & ~strengthen & (
        current_opportunity <= previous_opportunity - 2.0
    )
    watch = continued & ~confirmed & ~strengthen & ~weaken
    statuses = np.full(row_count, "", dtype=object)
    statuses[~active_values & prior_active] = "FAILED"
    statuses[active_values & ~prior_active] = "NEW"
    statuses[confirmed] = "CONFIRMED"
    statuses[strengthen] = "STRENGTHEN"
    statuses[weaken] = "WEAKEN"
    statuses[watch] = "WATCH"

    current_strength = np.char.mod("%.0f", current_opportunity)
    if dated_history.empty or "OpportunityScore" not in dated_history.columns:
        strengths = current_strength.astype(object)
    else:
        tail = dated_history.groupby("Ticker", sort=False).tail(29)[
            ["Ticker", "OpportunityScore"]
        ].copy()
        tail_values = pd.to_numeric(tail["OpportunityScore"], errors="coerce").to_numpy(
            dtype=np.float64
        )
        tail["_Strength"] = np.char.mod("%.0f", tail_values)
        prefix = tail.groupby("Ticker", sort=False)["_Strength"].agg("|".join)
        mapped_prefix = (
            ticker.map(prefix).astype("string").fillna("").to_numpy(dtype=str)
        )
        strengths = np.where(
            mapped_prefix != "",
            np.char.add(np.char.add(mapped_prefix, "|"), current_strength),
            current_strength,
        ).astype(object)

    return (
        pd.Series(days, index=index, dtype=int),
        pd.Series(starts, index=index, dtype=object),
        pd.Series(statuses, index=index, dtype=object),
        pd.Series(strengths, index=index, dtype=object),
    )


def _build_enricher(core: Any):
    def enrich_signal_lifecycle(frame: pd.DataFrame) -> pd.DataFrame:
        """Stable lifecycle semantics with vectorised current/history reconciliation."""
        if frame.empty:
            return frame
        result = frame.copy()
        result["Ticker"] = result["Ticker"].astype(str).str.strip().str.upper()
        short, middle, long = core._period_scores(result)
        result["ShortTermScore"] = short
        result["MediumTermScore"] = middle
        result["LongTermScore"] = long
        result["OpportunityScore"] = core._opportunity_score(short, middle, long)
        (
            result["LifecycleStage"],
            result["ActionSuggestion"],
            result["RiskNote"],
        ) = core._stage(result)
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

        result = core.validate_signal_consistency(result)
        active = core._is_active(result)
        history = core._load_history()
        history["Ticker"] = history["Ticker"].astype(str).str.strip().str.upper()
        history["TradeDate"] = history["TradeDate"].astype(str)
        trade_dates = (
            result.get("DataAsOf", pd.Series("", index=result.index))
            .fillna("")
            .astype(str)
        )
        signal_days, starts, statuses, strengths = vectorized_lifecycle_state(
            result,
            history,
            active,
            trade_dates,
        )
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
        result["BreakoutQualityFactor"] = core._number(
            result.get(
                "BreakoutQualityFactor", pd.Series(1.0, index=result.index)
            ),
            1.0,
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
            core._number(
                result.get("ScoreConfidence", pd.Series(index=result.index)), 0.0
            )
            * 100
        ).round(0)
        result["LifecycleAccelerationVersion"] = LIFECYCLE_ACCELERATION_VERSION

        result["_LifecycleInputOrder"] = np.arange(len(result))
        result = core.finalize_signal_ranking(result)
        result = (
            result.sort_values("_LifecycleInputOrder", kind="mergesort")
            .drop(columns="_LifecycleInputOrder")
            .reset_index(drop=True)
        )

        snapshot = pd.DataFrame(
            {
                "TradeDate": core._text_series(result, "DataAsOf", ""),
                "Return20D": core._number(
                    result.get("Return20D", pd.Series(index=result.index)), np.nan
                ),
                "MaxDrawdown20D": core._number(
                    result.get("MaxDrawdown20D", pd.Series(index=result.index)), np.nan
                ),
                "Return60D": core._number(
                    result.get("Return60D", pd.Series(index=result.index)), np.nan
                ),
                "MaxDrawdown60D": core._number(
                    result.get("MaxDrawdown60D", pd.Series(index=result.index)), np.nan
                ),
                "Ticker": result["Ticker"],
                "Name": result.get("Name", pd.Series("", index=result.index)),
                "Close": core._number(
                    result.get("Close", pd.Series(index=result.index)), np.nan
                ),
                "Score": core._number(result["Score"]),
                "OpportunityScore": core._number(result["OpportunityScore"]),
                "InstitutionalScore": core._number(
                    result["InstitutionalScore"], np.nan
                ),
                "InstitutionalTier": result["InstitutionalTier"],
                "BreakoutQualityFactor": core._number(
                    result["BreakoutQualityFactor"], np.nan
                ),
                "SignalRecencyFactor": core._number(
                    result["SignalRecencyFactor"], np.nan
                ),
                "SectorConfirmationFactor": core._number(
                    result.get(
                        "SectorConfirmationFactor", pd.Series(index=result.index)
                    ),
                    np.nan,
                ),
                "FailureSignalFactor": core._number(
                    result.get("FailureSignalFactor", pd.Series(index=result.index)),
                    np.nan,
                ),
                "ScoreConfidence": core._number(
                    result.get("ScoreConfidence", pd.Series(index=result.index))
                ),
                "SignalActive": active.map(bool),
                "SignalStatus": result["SignalStatus"],
                "SignalDays": result["SignalDays"],
                "SignalStartDate": result["SignalStartDate"],
                "Stage": result["LifecycleStage"],
                "TrendScore": core._number(
                    result.get("TrendScore", pd.Series(index=result.index))
                ),
                "AccumulationScore": core._number(
                    result.get("AccumulationScore", pd.Series(index=result.index))
                ),
                "IndustryRelativeStrength": core._number(
                    result.get(
                        "IndustryRelativeStrength", pd.Series(index=result.index)
                    )
                ),
                "SignalCount": core._number(
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
        core._atomic_write(history[core.HISTORY_COLUMNS], core.HISTORY_FILE)
        active_after = core._is_active(result)
        tracking = result.loc[active_after].sort_values(
            ["SignalDays", "OpportunityScore"], ascending=False
        )
        core._atomic_write(tracking, core.TRACKING_FILE)
        return result

    enrich_signal_lifecycle.__name__ = "enrich_signal_lifecycle"
    enrich_signal_lifecycle.__module__ = core.__name__
    return enrich_signal_lifecycle


def install(core: Any) -> None:
    """Install once onto the stable lifecycle module exported by the facade."""
    if getattr(core, "_v83_lifecycle_acceleration_installed", False):
        return
    if not hasattr(core, "_v83_legacy_enrich_signal_lifecycle"):
        core._v83_legacy_enrich_signal_lifecycle = core.enrich_signal_lifecycle
    core.enrich_signal_lifecycle = _build_enricher(core)
    core.LIFECYCLE_ACCELERATION_VERSION = LIFECYCLE_ACCELERATION_VERSION
    core._v83_lifecycle_acceleration_installed = True
