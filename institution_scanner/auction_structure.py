"""Point-in-time Auction / Structure shadow model.

The model is a research challenger.  It deliberately does not mutate the
production Champion score, rank, eligibility, or publication contract.

Only confirmed daily OHLCV bars are accepted.  Every rolling value, confirmed
pivot, structure event, plan transition, and backtest entry uses information
available at or before the corresponding signal close.  Entries occur at the
next tradable open; exits occur no earlier than the following trading day.

The decision spine is intentionally compact::

    Market -> RS -> Trend -> Value -> Structure -> Volume -> Risk -> Lifecycle

Auction levels are transparent OHLCV estimates, not exchange volume profile or
order-flow data.  Each bar contributes 25% of its volume at Low, 50% at HLC3,
and 25% at High.  No Footprint, bid/ask Delta, L2, or paid data is inferred.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Final

import numpy as np
import pandas as pd

MODEL_VERSION: Final = "2026-08-29-auction-structure-shadow-v1"
MODEL_ROLE: Final = "SHADOW_CHALLENGER"
MODEL_PRODUCTION_APPLIED: Final = False

SCORE_WEIGHTS: Final[dict[str, float]] = {
    "market": 10.0,
    "relative_strength": 15.0,
    "trend": 15.0,
    "value": 15.0,
    "structure": 20.0,
    "volume": 15.0,
    "risk": 10.0,
}

_REQUIRED_OHLCV: Final = frozenset({"Open", "High", "Low", "Close", "Volume"})


@dataclass(frozen=True)
class AuctionStructureConfig:
    """Stable parameters shared by scoring and historical simulation."""

    profile_days: int = 100
    profile_rows: int = 24
    value_area_fraction: float = 0.70
    migration_lag: int = 10
    migration_atr_threshold: float = 0.12
    pivot_left: int = 3
    pivot_right: int = 3
    atr_period: int = 14
    volume_period: int = 20
    pressure_period: int = 8
    displacement_atr: float = 1.0
    expansion_volume_ratio: float = 1.35
    dry_up_volume_ratio: float = 0.75
    setup_expiry_bars: int = 30
    minimum_reward_risk: float = 1.50
    minimum_turnover: float = 30_000_000.0
    structure_state_bars: int = 20
    event_identity_bars: int = 20

    def __post_init__(self) -> None:
        if self.profile_days < 40:
            raise ValueError("profile_days must be at least 40")
        if self.profile_rows < 8:
            raise ValueError("profile_rows must be at least 8")
        if not 0.5 <= self.value_area_fraction <= 0.9:
            raise ValueError("value_area_fraction must be between 0.5 and 0.9")
        if self.pivot_left < 1 or self.pivot_right < 1:
            raise ValueError("pivot confirmation widths must be positive")
        if self.minimum_reward_risk <= 0.0:
            raise ValueError("minimum_reward_risk must be positive")
        if self.minimum_turnover < 0.0:
            raise ValueError("minimum_turnover must be non-negative")
        if self.setup_expiry_bars < 3:
            raise ValueError("setup_expiry_bars must be at least 3")


@dataclass(frozen=True)
class AuctionStructureSnapshot:
    """Latest decision state suitable for a scan/export row."""

    model_version: str
    model_role: str
    production_applied: bool
    score: float
    coverage: float
    market_score: float
    relative_strength_score: float
    trend_score: float
    value_score: float
    structure_score: float
    volume_score: float
    risk_score: float
    market: str
    relative_strength: str
    trend: str
    value: str
    structure: str
    volume: str
    risk: str
    candidate_setup: str
    current_setup: str
    active_plan: str
    last_plan: str
    poc: float
    vah: float
    val: float
    avwap: float
    value_migration_atr: float
    rs20: float
    rs60: float
    atr: float
    relative_volume: float
    average_turnover20: float
    hard_block_reason: str
    reward_risk: float
    entry_low: float
    entry_high: float
    invalidation: float
    target: float

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class AuctionBacktestResult:
    """Shadow backtest samples and a JSON-serialisable summary."""

    samples: pd.DataFrame
    summary: dict[str, object]


def _normalise_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=sorted(_REQUIRED_OHLCV))
    result = frame.copy()
    aliases = {str(column).strip().lower(): column for column in result.columns}
    rename: dict[object, str] = {}
    for canonical in (*sorted(_REQUIRED_OHLCV), "Amount", "Date"):
        source = aliases.get(canonical.lower())
        if source is not None and source != canonical:
            rename[source] = canonical
    if rename:
        result = result.rename(columns=rename)
    if not isinstance(result.index, pd.DatetimeIndex):
        if "Date" not in result.columns:
            raise ValueError("OHLCV frame requires a DatetimeIndex or Date column")
        result.index = pd.to_datetime(result.pop("Date"), errors="coerce")
    else:
        result.index = pd.to_datetime(result.index, errors="coerce")
    if result.index.tz is not None:
        result.index = result.index.tz_convert(None)
    missing = sorted(_REQUIRED_OHLCV.difference(result.columns))
    if missing:
        raise ValueError(f"OHLCV frame missing columns: {missing}")
    numeric_columns = [*sorted(_REQUIRED_OHLCV)]
    if "Amount" in result.columns:
        numeric_columns.append("Amount")
    for column in numeric_columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.loc[~result.index.isna()].sort_index()
    result = result.loc[~result.index.duplicated(keep="last")]
    result = result.replace([np.inf, -np.inf], np.nan)
    result = result.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    result = result.loc[result["High"].ge(result["Low"]) & result["Close"].gt(0.0) & result["Volume"].ge(0.0)]
    return result


def _true_range(frame: pd.DataFrame) -> pd.Series:
    previous_close = frame["Close"].shift(1)
    values = pd.concat(
        [
            frame["High"] - frame["Low"],
            (frame["High"] - previous_close).abs(),
            (frame["Low"] - previous_close).abs(),
        ],
        axis=1,
    )
    return values.max(axis=1)


def _atr(frame: pd.DataFrame, period: int) -> pd.Series:
    return (
        _true_range(frame)
        .ewm(
            alpha=1.0 / float(period),
            adjust=False,
            min_periods=period,
        )
        .mean()
    )


def _rolling_auction_levels(
    frame: pd.DataFrame,
    config: AuctionStructureConfig,
) -> pd.DataFrame:
    """Return causal rolling POC/VAH/VAL estimates.

    The loop is bounded by the configured profile window and row count.  It is
    intentionally kept out of the production scanner hot path while the model
    remains a shadow challenger.
    """
    size = len(frame)
    poc = np.full(size, np.nan, dtype=float)
    vah = np.full(size, np.nan, dtype=float)
    val = np.full(size, np.nan, dtype=float)
    profile_high = np.full(size, np.nan, dtype=float)
    profile_low = np.full(size, np.nan, dtype=float)
    highs = frame["High"].to_numpy(dtype=float)
    lows = frame["Low"].to_numpy(dtype=float)
    closes = frame["Close"].to_numpy(dtype=float)
    volumes = frame["Volume"].to_numpy(dtype=float)
    window = int(config.profile_days)
    rows = int(config.profile_rows)

    for end in range(window - 1, size):
        start = end - window + 1
        window_high = highs[start : end + 1]
        window_low = lows[start : end + 1]
        window_close = closes[start : end + 1]
        window_volume = np.maximum(volumes[start : end + 1], 0.0)
        low_bound = float(np.min(window_low))
        high_bound = float(np.max(window_high))
        profile_low[end] = low_bound
        profile_high[end] = high_bound
        if not np.isfinite(low_bound + high_bound) or high_bound <= low_bound:
            poc[end] = low_bound
            vah[end] = high_bound
            val[end] = low_bound
            continue
        total_volume = float(window_volume.sum())
        if total_volume <= 0.0:
            continue
        edges = np.linspace(low_bound, high_bound, rows + 1, dtype=float)
        hlc3 = (window_high + window_low + window_close) / 3.0
        prices = np.concatenate((window_low, hlc3, window_high))
        weights = np.concatenate((window_volume * 0.25, window_volume * 0.50, window_volume * 0.25))
        histogram = np.histogram(prices, bins=edges, weights=weights)[0]
        poc_index = int(np.argmax(histogram))
        low_index = poc_index
        high_index = poc_index
        cumulative = float(histogram[poc_index])
        target = total_volume * float(config.value_area_fraction)
        while cumulative < target and (low_index > 0 or high_index < rows - 1):
            below = float(histogram[low_index - 1]) if low_index > 0 else -1.0
            above = float(histogram[high_index + 1]) if high_index < rows - 1 else -1.0
            if above >= below and high_index < rows - 1:
                high_index += 1
                cumulative += max(above, 0.0)
            elif low_index > 0:
                low_index -= 1
                cumulative += max(below, 0.0)
            else:
                break
        poc[end] = float((edges[poc_index] + edges[poc_index + 1]) / 2.0)
        val[end] = float(edges[low_index])
        vah[end] = float(edges[high_index + 1])

    return pd.DataFrame(
        {
            "POC": poc,
            "VAH": vah,
            "VAL": val,
            "ProfileHigh": profile_high,
            "ProfileLow": profile_low,
        },
        index=frame.index,
    )


def _confirmed_pivots(
    frame: pd.DataFrame,
    left: int,
    right: int,
) -> pd.DataFrame:
    """Confirm a pivot only ``right`` bars after its source bar."""
    size = len(frame)
    highs = frame["High"].to_numpy(dtype=float)
    lows = frame["Low"].to_numpy(dtype=float)
    high_event = np.zeros(size, dtype=bool)
    low_event = np.zeros(size, dtype=bool)
    high_level = np.full(size, np.nan, dtype=float)
    low_level = np.full(size, np.nan, dtype=float)
    source_bar = np.full(size, -1, dtype=int)
    for confirmed in range(left + right, size):
        candidate = confirmed - right
        start = candidate - left
        stop = candidate + right + 1
        high_window = highs[start:stop]
        low_window = lows[start:stop]
        if highs[candidate] >= float(np.max(high_window)):
            high_event[confirmed] = True
            high_level[confirmed] = highs[candidate]
            source_bar[confirmed] = candidate
        if lows[candidate] <= float(np.min(low_window)):
            low_event[confirmed] = True
            low_level[confirmed] = lows[candidate]
            source_bar[confirmed] = candidate

    last_high = np.full(size, np.nan, dtype=float)
    previous_high = np.full(size, np.nan, dtype=float)
    last_low = np.full(size, np.nan, dtype=float)
    previous_low = np.full(size, np.nan, dtype=float)
    last_high_source = np.full(size, -1, dtype=int)
    last_low_source = np.full(size, -1, dtype=int)
    high_now = high_before = low_now = low_before = np.nan
    high_source = low_source = -1
    for index in range(size):
        if high_event[index]:
            high_before = high_now
            high_now = high_level[index]
            high_source = source_bar[index]
        if low_event[index]:
            low_before = low_now
            low_now = low_level[index]
            low_source = source_bar[index]
        last_high[index] = high_now
        previous_high[index] = high_before
        last_low[index] = low_now
        previous_low[index] = low_before
        last_high_source[index] = high_source
        last_low_source[index] = low_source

    return pd.DataFrame(
        {
            "PivotHighConfirmed": high_event,
            "PivotLowConfirmed": low_event,
            "PivotHighLevel": high_level,
            "PivotLowLevel": low_level,
            "LastSwingHigh": last_high,
            "PreviousSwingHigh": previous_high,
            "LastSwingLow": last_low,
            "PreviousSwingLow": previous_low,
            "LastSwingHighSource": last_high_source,
            "LastSwingLowSource": last_low_source,
        },
        index=frame.index,
    )


def _weekly_direction(close: pd.Series) -> pd.Series:
    weekly = close.resample("W-FRI").last().dropna()
    fast = weekly.ewm(span=10, adjust=False, min_periods=10).mean()
    slow = weekly.ewm(span=30, adjust=False, min_periods=30).mean()
    direction = pd.Series(0, index=weekly.index, dtype=int)
    direction.loc[(weekly > fast) & (fast > slow) & (fast > fast.shift(2))] = 1
    direction.loc[(weekly < fast) & (fast < slow) & (fast < fast.shift(2))] = -1
    return direction.reindex(close.index, method="ffill").fillna(0).astype(int)


def _aligned_benchmark_close(
    benchmark: pd.DataFrame | None,
    index: pd.DatetimeIndex,
) -> pd.Series:
    if benchmark is None or benchmark.empty:
        return pd.Series(np.nan, index=index, dtype=float)
    normalised = _normalise_ohlcv(benchmark)
    if normalised.empty:
        return pd.Series(np.nan, index=index, dtype=float)
    return normalised["Close"].reindex(index).ffill()


def _anchored_vwap(frame: pd.DataFrame, anchor_event: pd.Series) -> pd.Series:
    typical = (frame["High"] + frame["Low"] + frame["Close"]) / 3.0
    volumes = frame["Volume"].clip(lower=0.0)
    output = np.full(len(frame), np.nan, dtype=float)
    cumulative_pv = 0.0
    cumulative_volume = 0.0
    for index, reset in enumerate(anchor_event.to_numpy(dtype=bool)):
        if reset:
            cumulative_pv = 0.0
            cumulative_volume = 0.0
        volume = float(volumes.iloc[index])
        cumulative_pv += float(typical.iloc[index]) * volume
        cumulative_volume += volume
        if cumulative_volume > 0.0:
            output[index] = cumulative_pv / cumulative_volume
    return pd.Series(output, index=frame.index, dtype=float)


def _nearest_below(reference: np.ndarray, candidates: list[np.ndarray]) -> np.ndarray:
    output = np.full(len(reference), np.nan, dtype=float)
    for index in range(len(reference)):
        valid = [
            values[index] for values in candidates if np.isfinite(values[index]) and values[index] < reference[index]
        ]
        if valid:
            output[index] = float(max(valid))
    return output


def _nearest_above(reference: np.ndarray, candidates: list[np.ndarray]) -> np.ndarray:
    output = np.full(len(reference), np.nan, dtype=float)
    for index in range(len(reference)):
        valid = [
            values[index] for values in candidates if np.isfinite(values[index]) and values[index] > reference[index]
        ]
        if valid:
            output[index] = float(min(valid))
    return output


def _recent_event_bar(events: np.ndarray) -> np.ndarray:
    output = np.full(len(events), -1, dtype=int)
    current = -1
    for index, event in enumerate(events):
        if bool(event):
            current = index
        output[index] = current
    return output


def _price_limit_fraction(ticker: str, is_etf: bool, is_st: bool) -> float:
    if is_etf:
        return 0.10
    digits = "".join(character for character in str(ticker) if character.isdigit())
    if is_st:
        return 0.05
    if digits.startswith(("30", "68")):
        return 0.20
    if digits.startswith(("4", "8", "92")):
        return 0.30
    return 0.10


def _one_price_limit_mask(
    frame: pd.DataFrame,
    ticker: str,
    is_etf: bool,
    is_st: bool,
) -> tuple[pd.Series, pd.Series]:
    previous = frame["Close"].shift(1)
    fraction = _price_limit_fraction(ticker, is_etf, is_st)
    tolerance = 0.003
    one_price = (frame["High"] - frame["Low"]).abs() <= frame["Close"].abs().clip(lower=1e-9) * 0.001
    change = frame["Close"] / previous - 1.0
    return (
        one_price & change.ge(fraction - tolerance),
        one_price & change.le(-fraction + tolerance),
    )


def _score_components(features: pd.DataFrame) -> pd.DataFrame:
    market = features["MarketRegimeCode"]
    market_score = market.map({"RISK_ON": 10.0, "MIXED": 5.0, "RISK_OFF": 0.0}).fillna(0.0)

    rs20 = features["RS20"]
    rs60 = features["RS60"]
    rs_score = (
        ((rs20.clip(-0.08, 0.08) + 0.08) / 0.16 * 7.5).fillna(0.0)
        + ((rs60.clip(-0.16, 0.16) + 0.16) / 0.32 * 7.5).fillna(0.0)
    ).clip(0.0, 15.0)

    daily = features["DailyTrend"]
    weekly = features["WeeklyTrend"]
    trend_score = (
        daily.map({1: 6.0, 0: 3.0, -1: 0.0}).fillna(0.0)
        + weekly.map({1: 6.0, 0: 3.0, -1: 0.0}).fillna(0.0)
        + np.where(features["Close"] >= features["EMA200"], 3.0, 0.0)
    )
    trend_score = pd.Series(trend_score, index=features.index).clip(0.0, 15.0)

    value_direction = features["ValueDirection"]
    value_score = value_direction.map({1: 5.0, 0: 3.0, -1: 0.0}).fillna(0.0)
    value_score += np.where(features["Close"] >= features["POC"], 3.0, 0.0)
    value_score += np.where(features["Close"] >= features["AVWAP"], 4.0, 0.0)
    value_score += (
        features["ValueStateCode"]
        .map(
            {
                "ABOVE_VALUE": 3.0,
                "TESTING_HIGH": 2.0,
                "BALANCE": 2.0,
                "HIGHER_VALUE": 3.0,
                "TESTING_LOW": 1.0,
                "LOWER_VALUE": 0.0,
                "BELOW_VALUE": 0.0,
            }
        )
        .fillna(0.0)
    )
    value_score = value_score.clip(0.0, 15.0)

    structure_score = (
        features["StructureStateCode"]
        .map(
            {
                "MSS_UP": 20.0,
                "BREAKOUT": 19.0,
                "RETEST": 18.0,
                "SPRING": 18.0,
                "DISPLACEMENT_UP": 16.0,
                "BULLISH": 13.0,
                "BALANCE": 8.0,
                "DISPLACEMENT_DOWN": 3.0,
                "BEARISH": 2.0,
                "MSS_DOWN": 0.0,
            }
        )
        .fillna(0.0)
    )

    volume_score = (
        features["VolumeBehaviorCode"]
        .map(
            {
                "DEMAND": 13.0,
                "ABSORPTION": 14.0,
                "DRY_UP": 10.0,
                "NEUTRAL": 7.0,
                "SUPPLY": 0.0,
            }
        )
        .fillna(0.0)
    )
    volume_score += np.where(
        features["VolumeBehaviorCode"].eq("DEMAND"),
        np.minimum(np.maximum(features["RelativeVolume"] - 1.0, 0.0) * 2.0, 2.0),
        0.0,
    )
    volume_score = pd.Series(volume_score, index=features.index).clip(0.0, 15.0)

    risk_score = features["ChaseRiskCode"].map({"LOW": 10.0, "MODERATE": 6.0, "HIGH": 2.0, "BLOCKED": 0.0}).fillna(0.0)
    risk_score = np.where(features["HardBlock"], 0.0, risk_score)
    risk_score = pd.Series(risk_score, index=features.index).clip(0.0, 10.0)

    coverage = pd.DataFrame(
        {
            "market": features["MarketDataReady"],
            "relative_strength": features["RSReady"],
            "trend": features["TrendDataReady"],
            "value": features["ProfileReady"],
            "structure": features["ATR"].notna() & features["LastSwingHigh"].notna(),
            "volume": features["RelativeVolume"].notna(),
            "risk": features["ATR"].notna() & features["AverageTurnover20"].notna(),
        },
        index=features.index,
    )
    weighted_available = sum(coverage[name].astype(float) * weight for name, weight in SCORE_WEIGHTS.items())
    coverage_ratio = weighted_available / 100.0
    raw = market_score + rs_score + trend_score + value_score + structure_score + volume_score + risk_score
    score = np.minimum(raw, 40.0 + 60.0 * coverage_ratio).clip(0.0, 100.0)
    return pd.DataFrame(
        {
            "MarketComponent": market_score,
            "RSComponent": rs_score,
            "TrendComponent": trend_score,
            "ValueComponent": value_score,
            "StructureComponent": structure_score,
            "VolumeComponent": volume_score,
            "RiskComponent": risk_score,
            "AuctionStructureCoverage": coverage_ratio,
            "AuctionStructureScore": score,
        },
        index=features.index,
    )


def _apply_lifecycle(features: pd.DataFrame, config: AuctionStructureConfig) -> pd.DataFrame:
    size = len(features)
    lifecycle_event = np.full(size, "NONE", dtype=object)
    lifecycle_state = np.full(size, "NONE", dtype=object)
    current_setup = np.full(size, "NONE", dtype=object)
    active_setup_output = np.full(size, "NONE", dtype=object)
    last_plan_output = np.full(size, "NONE", dtype=object)
    active_entry_low = np.full(size, np.nan, dtype=float)
    active_entry_high = np.full(size, np.nan, dtype=float)
    active_invalidation = np.full(size, np.nan, dtype=float)
    active_target = np.full(size, np.nan, dtype=float)
    active_rr = np.full(size, np.nan, dtype=float)

    pending_setup = "NONE"
    pending_source = -1
    pending_seen = -1
    active_setup = "NONE"
    active_source = -1
    active_plan_bar = -1
    active_zone_tested = False
    active_zone_test_bar = -1
    active_confirmed = False
    entry_low = entry_high = invalidation = target = reward_risk = np.nan
    retired_setup = "NONE"
    retired_source = -2
    retired_outcome = "NONE"

    for index, row in enumerate(features.itertuples(index=False)):
        terminal = False
        if active_setup != "NONE":
            close = float(row.Close)
            if close < invalidation:
                retired_outcome = "INVALIDATED"
                terminal = True
            elif close >= target:
                retired_outcome = "TARGET" if active_confirmed else "MISSED"
                terminal = True
            elif index - active_plan_bar > int(config.setup_expiry_bars):
                retired_outcome = "EXPIRED"
                terminal = True
            if terminal:
                retired_setup = active_setup
                retired_source = active_source
                lifecycle_event[index] = retired_outcome
                active_setup = "NONE"
                active_source = -1
                active_plan_bar = -1
                active_zone_tested = False
                active_zone_test_bar = -1
                active_confirmed = False
                entry_low = entry_high = invalidation = target = reward_risk = np.nan

        candidate_setup = str(row.CandidateSetupCode)
        candidate_source = int(row.CandidateSourceBar)
        candidate_valid = bool(row.CandidatePlanValid)

        if not terminal and active_setup == "NONE":
            same_pending = (
                pending_setup != "NONE" and candidate_setup == pending_setup and candidate_source == pending_source
            )
            same_retired = (
                retired_setup != "NONE" and candidate_setup == retired_setup and candidate_source == retired_source
            )
            if same_pending and candidate_valid and index > pending_seen:
                active_setup = candidate_setup
                active_source = candidate_source
                active_plan_bar = index
                entry_low = float(row.CandidateEntryLow)
                entry_high = float(row.CandidateEntryHigh)
                invalidation = float(row.CandidateInvalidation)
                target = float(row.CandidateTarget)
                reward_risk = float(row.CandidateRewardRisk)
                lifecycle_event[index] = "PLAN_LOCKED"
                pending_setup = "NONE"
                pending_source = -1
                pending_seen = -1
            elif candidate_valid and not same_retired and not same_pending:
                pending_setup = candidate_setup
                pending_source = candidate_source
                pending_seen = index
                lifecycle_event[index] = "SETUP"
            elif pending_setup != "NONE" and (not candidate_valid or not same_pending):
                pending_setup = "NONE"
                pending_source = -1
                pending_seen = -1

        if active_setup != "NONE" and lifecycle_event[index] != "PLAN_LOCKED":
            atr = float(row.ATR) if np.isfinite(row.ATR) else 0.0
            zone_touched = (
                index > active_plan_bar
                and float(row.Low) <= entry_high
                and float(row.High) >= entry_low
                and float(row.Close) >= entry_low - 0.08 * atr
                and float(row.Close) <= entry_high + 0.25 * atr
            )
            if zone_touched and not active_zone_tested:
                active_zone_tested = True
                active_zone_test_bar = index
                lifecycle_event[index] = "ZONE_TESTED"

            if active_zone_tested and index > active_zone_test_bar and not active_confirmed:
                volume = str(row.VolumeBehaviorCode)
                direction_pass = (
                    bool(row.HigherTimeframeLong)
                    if active_setup
                    in {
                        "BREAKOUT",
                        "PULLBACK",
                    }
                    else bool(row.HigherTimeframePermissive)
                )
                if active_setup == "REVERSAL":
                    setup_confirmation = str(row.StructureStateCode) in {"MSS_UP", "BULLISH", "SPRING"} and volume in {
                        "DEMAND",
                        "ABSORPTION",
                    }
                elif active_setup == "BREAKOUT":
                    setup_confirmation = float(row.Close) >= entry_low and volume in {"DEMAND", "ABSORPTION", "DRY_UP"}
                elif active_setup == "PULLBACK":
                    setup_confirmation = float(row.CloseLocation) >= 0.52 and volume in {
                        "DEMAND",
                        "ABSORPTION",
                        "DRY_UP",
                    }
                else:
                    setup_confirmation = float(row.CloseLocation) >= 0.55 and volume in {"DEMAND", "ABSORPTION"}
                confirm = (
                    direction_pass
                    and setup_confirmation
                    and reward_risk >= config.minimum_reward_risk
                    and str(row.MarketRegimeCode) != "RISK_OFF"
                    and not bool(row.HardBlock)
                    and str(row.ChaseRiskCode) != "HIGH"
                    and volume != "SUPPLY"
                )
                if confirm:
                    active_confirmed = True
                    lifecycle_event[index] = "CONFIRMED"

        current_setup[index] = pending_setup
        active_setup_output[index] = active_setup
        last_plan_output[index] = retired_outcome
        if active_setup != "NONE":
            lifecycle_state[index] = (
                "CONFIRMED" if active_confirmed else "ZONE_TESTED" if active_zone_tested else "PLAN_LOCKED"
            )
            active_entry_low[index] = entry_low
            active_entry_high[index] = entry_high
            active_invalidation[index] = invalidation
            active_target[index] = target
            active_rr[index] = reward_risk
        elif lifecycle_event[index] == "SETUP":
            lifecycle_state[index] = "SETUP"
        elif terminal:
            lifecycle_state[index] = retired_outcome

    return pd.DataFrame(
        {
            "LifecycleEventCode": lifecycle_event,
            "LifecycleStateCode": lifecycle_state,
            "CurrentSetupCode": current_setup,
            "ActiveSetupCode": active_setup_output,
            "LastPlanCode": last_plan_output,
            "ActiveEntryLow": active_entry_low,
            "ActiveEntryHigh": active_entry_high,
            "ActiveInvalidation": active_invalidation,
            "ActiveTarget": active_target,
            "ActiveRewardRisk": active_rr,
        },
        index=features.index,
    )


_MARKET_ZH: Final = {
    "RISK_ON": "风险偏好",
    "MIXED": "震荡混合",
    "RISK_OFF": "风险规避",
    "NO_DATA": "基准不足",
}
_VALUE_ZH: Final = {
    "HIGHER_VALUE": "价值上移",
    "LOWER_VALUE": "价值下移",
    "BALANCE": "价值平衡",
    "ABOVE_VALUE": "站上价值区",
    "BELOW_VALUE": "跌破价值区",
    "TESTING_HIGH": "测试价值区上沿",
    "TESTING_LOW": "测试价值区下沿",
    "NO_DATA": "价值数据不足",
}
_STRUCTURE_ZH: Final = {
    "MSS_UP": "结构转强",
    "MSS_DOWN": "结构转弱",
    "BREAKOUT": "突破确认",
    "RETEST": "突破后回踩",
    "SPRING": "下探收回",
    "DISPLACEMENT_UP": "向上位移",
    "DISPLACEMENT_DOWN": "向下位移",
    "BULLISH": "结构偏强",
    "BEARISH": "结构偏弱",
    "BALANCE": "结构平衡",
}
_VOLUME_ZH: Final = {
    "DEMAND": "需求主导",
    "SUPPLY": "供给主导",
    "ABSORPTION": "承接吸收",
    "DRY_UP": "成交缩量",
    "NEUTRAL": "量价中性",
}
_SETUP_ZH: Final = {
    "REVERSAL": "反转观察",
    "BREAKOUT": "突破观察",
    "PULLBACK": "趋势回踩",
    "BALANCE": "区间观察",
    "NONE": "暂无",
}
_LIFECYCLE_ZH: Final = {
    "SETUP": "形态观察",
    "PLAN_LOCKED": "计划锁定",
    "ZONE_TESTED": "观察区已测试",
    "CONFIRMED": "计划已确认",
    "TARGET": "目标达成",
    "INVALIDATED": "计划失效",
    "MISSED": "未介入已到目标",
    "EXPIRED": "计划过期",
    "NONE": "无",
}


def compute_auction_structure(
    frame: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
    *,
    ticker: str = "",
    name: str = "",
    is_etf: bool = False,
    config: AuctionStructureConfig | None = None,
) -> pd.DataFrame:
    """Compute the full causal shadow state for every confirmed daily bar."""
    active = config or AuctionStructureConfig()
    data = _normalise_ohlcv(frame)
    if data.empty:
        return pd.DataFrame(index=data.index)
    result = data.copy()
    result["ATR"] = _atr(data, active.atr_period)
    result["EMA20"] = data["Close"].ewm(span=20, adjust=False, min_periods=20).mean()
    result["EMA50"] = data["Close"].ewm(span=50, adjust=False, min_periods=50).mean()
    result["EMA200"] = data["Close"].ewm(span=200, adjust=False, min_periods=200).mean()
    result["DailyTrend"] = 0
    result.loc[
        (result["Close"] > result["EMA20"])
        & (result["EMA20"] > result["EMA50"])
        & (result["EMA20"] > result["EMA20"].shift(5)),
        "DailyTrend",
    ] = 1
    result.loc[
        (result["Close"] < result["EMA20"])
        & (result["EMA20"] < result["EMA50"])
        & (result["EMA20"] < result["EMA20"].shift(5)),
        "DailyTrend",
    ] = -1
    result["WeeklyTrend"] = _weekly_direction(result["Close"])
    result["TrendDataReady"] = result[["EMA20", "EMA50"]].notna().all(axis=1)

    benchmark_close = _aligned_benchmark_close(benchmark, result.index)
    benchmark_ema50 = benchmark_close.ewm(span=50, adjust=False, min_periods=50).mean()
    benchmark_ema200 = benchmark_close.ewm(span=200, adjust=False, min_periods=200).mean()
    benchmark_weekly = _weekly_direction(benchmark_close.dropna()).reindex(result.index).fillna(0)
    benchmark_daily = pd.Series(0, index=result.index, dtype=int)
    benchmark_daily.loc[
        (benchmark_close > benchmark_ema50)
        & (benchmark_ema50 > benchmark_ema200)
        & (benchmark_ema50 > benchmark_ema50.shift(5))
    ] = 1
    benchmark_daily.loc[
        (benchmark_close < benchmark_ema50)
        & (benchmark_ema50 < benchmark_ema200)
        & (benchmark_ema50 < benchmark_ema50.shift(5))
    ] = -1
    market_ready = benchmark_ema200.notna()
    result["MarketDataReady"] = market_ready
    result["MarketRegimeCode"] = "NO_DATA"
    result.loc[market_ready, "MarketRegimeCode"] = "MIXED"
    result.loc[
        market_ready & benchmark_daily.gt(0) & benchmark_weekly.ge(0),
        "MarketRegimeCode",
    ] = "RISK_ON"
    result.loc[
        market_ready & benchmark_daily.lt(0) & benchmark_weekly.le(0),
        "MarketRegimeCode",
    ] = "RISK_OFF"
    result["RS20"] = np.log(result["Close"] / result["Close"].shift(20)) - np.log(
        benchmark_close / benchmark_close.shift(20)
    )
    result["RS60"] = np.log(result["Close"] / result["Close"].shift(60)) - np.log(
        benchmark_close / benchmark_close.shift(60)
    )
    result["RSReady"] = result[["RS20", "RS60"]].notna().all(axis=1)

    pivots = _confirmed_pivots(data, active.pivot_left, active.pivot_right)
    result = result.join(pivots)
    profile = _rolling_auction_levels(data, active)
    result = result.join(profile)
    result["ProfileReady"] = result[["POC", "VAH", "VAL"]].notna().all(axis=1)

    bar_range = (result["High"] - result["Low"]).clip(lower=1e-12)
    result["CloseLocation"] = ((result["Close"] - result["Low"]) / bar_range).clip(0.0, 1.0)
    candle_body = (result["Close"] - result["Open"]).abs()
    lower_wick = np.minimum(result["Open"], result["Close"]) - result["Low"]
    upper_wick = result["High"] - np.maximum(result["Open"], result["Close"])
    average_volume = result["Volume"].rolling(active.volume_period, min_periods=active.volume_period).mean()
    result["RelativeVolume"] = result["Volume"] / average_volume.replace(0.0, np.nan)
    raw_pressure = ((2.0 * result["Close"] - result["High"] - result["Low"]) / bar_range).clip(-1.0, 1.0)
    signed_volume = (
        (result["Volume"] * raw_pressure)
        .ewm(span=active.pressure_period, adjust=False, min_periods=active.pressure_period)
        .mean()
    )
    smoothed_volume = (
        result["Volume"].ewm(span=active.pressure_period, adjust=False, min_periods=active.pressure_period).mean()
    )
    result["PressureRatio"] = signed_volume / smoothed_volume.replace(0.0, np.nan)
    bullish_displacement = (
        result["Close"].gt(result["Open"])
        & candle_body.ge(result["ATR"] * active.displacement_atr)
        & result["CloseLocation"].ge(0.72)
    )
    bearish_displacement = (
        result["Close"].lt(result["Open"])
        & candle_body.ge(result["ATR"] * active.displacement_atr)
        & result["CloseLocation"].le(0.28)
    )
    volume_expansion = result["RelativeVolume"].ge(active.expansion_volume_ratio) & bar_range.ge(result["ATR"] * 0.75)
    absorption = (
        result["RelativeVolume"].ge(1.30)
        & lower_wick.ge(candle_body * 0.70)
        & result["CloseLocation"].ge(0.60)
        & bar_range.le(result["ATR"] * 1.60)
    )
    dry_up = result["RelativeVolume"].le(active.dry_up_volume_ratio) & bar_range.le(result["ATR"] * 0.95)
    demand = (
        absorption
        | (result["PressureRatio"].ge(0.12) & result["CloseLocation"].ge(0.60) & result["RelativeVolume"].ge(0.90))
        | (bullish_displacement & result["RelativeVolume"].ge(0.85))
        | (volume_expansion & result["PressureRatio"].gt(0.0) & result["CloseLocation"].ge(0.65))
    )
    supply = (
        (result["PressureRatio"].le(-0.12) & result["CloseLocation"].le(0.40) & result["RelativeVolume"].ge(0.90))
        | (upper_wick.ge(candle_body * 0.70) & result["CloseLocation"].le(0.40) & result["RelativeVolume"].ge(1.20))
        | (bearish_displacement & result["RelativeVolume"].ge(0.85))
    )
    result["VolumeBehaviorCode"] = "NEUTRAL"
    result.loc[dry_up, "VolumeBehaviorCode"] = "DRY_UP"
    result.loc[supply, "VolumeBehaviorCode"] = "SUPPLY"
    result.loc[demand, "VolumeBehaviorCode"] = "DEMAND"
    result.loc[absorption, "VolumeBehaviorCode"] = "ABSORPTION"

    prior_low20 = result["Low"].shift(1).rolling(20, min_periods=20).min()
    prior_high20 = result["High"].shift(1).rolling(20, min_periods=20).max()
    simple_spring = result["Low"].lt(prior_low20) & result["Close"].gt(prior_low20) & result["CloseLocation"].ge(0.60)
    simple_breakout = (
        result["Close"].gt(prior_high20)
        & result["RelativeVolume"].ge(active.expansion_volume_ratio)
        & result["CloseLocation"].ge(0.65)
    )
    anchor_event = result["PivotLowConfirmed"] | simple_spring | simple_breakout
    result["AVWAP"] = _anchored_vwap(result, anchor_event)

    value_mid = (result["VAH"] + result["VAL"]) / 2.0
    result["ValueMigrationATR"] = (value_mid - value_mid.shift(active.migration_lag)) / result["ATR"].replace(
        0.0, np.nan
    )
    result["ValueDirection"] = 0
    result.loc[
        result["ValueMigrationATR"].gt(active.migration_atr_threshold),
        "ValueDirection",
    ] = 1
    result.loc[
        result["ValueMigrationATR"].lt(-active.migration_atr_threshold),
        "ValueDirection",
    ] = -1
    value_buffer = result["ATR"] * 0.08
    inside_value = result["Close"].between(result["VAL"], result["VAH"])
    accepted_above = result["Close"].gt(result["VAH"] + value_buffer) & result["Close"].shift(1).gt(
        result["VAH"].shift(1) + value_buffer.shift(1)
    )
    accepted_below = result["Close"].lt(result["VAL"] - value_buffer) & result["Close"].shift(1).lt(
        result["VAL"].shift(1) - value_buffer.shift(1)
    )
    result["ValueStateCode"] = "NO_DATA"
    ready = result["ProfileReady"]
    result.loc[ready & inside_value, "ValueStateCode"] = "BALANCE"
    result.loc[ready & inside_value & result["ValueDirection"].gt(0), "ValueStateCode"] = "HIGHER_VALUE"
    result.loc[ready & inside_value & result["ValueDirection"].lt(0), "ValueStateCode"] = "LOWER_VALUE"
    result.loc[ready & accepted_above, "ValueStateCode"] = "ABOVE_VALUE"
    result.loc[ready & accepted_below, "ValueStateCode"] = "BELOW_VALUE"
    result.loc[
        ready & ~inside_value & ~accepted_above & ~accepted_below & result["Close"].gt(result["VAH"]),
        "ValueStateCode",
    ] = "TESTING_HIGH"
    result.loc[
        ready & ~inside_value & ~accepted_above & ~accepted_below & result["Close"].le(result["VAH"]),
        "ValueStateCode",
    ] = "TESTING_LOW"

    atr_values = result["ATR"].to_numpy(dtype=float)
    close_values = result["Close"].to_numpy(dtype=float)
    prior_close_values = result["Close"].shift(1).to_numpy(dtype=float)
    swing_high = result["LastSwingHigh"].shift(1).to_numpy(dtype=float)
    swing_low = result["LastSwingLow"].shift(1).to_numpy(dtype=float)
    structure_buffer = atr_values * 0.10
    bull_break = (
        np.isfinite(swing_high)
        & (close_values > swing_high + structure_buffer)
        & (prior_close_values <= swing_high + structure_buffer)
    )
    bear_break = (
        np.isfinite(swing_low)
        & (close_values < swing_low - structure_buffer)
        & (prior_close_values >= swing_low - structure_buffer)
    )
    structure_direction = np.zeros(len(result), dtype=int)
    current_direction = 0
    bullish_mss = np.zeros(len(result), dtype=bool)
    bearish_mss = np.zeros(len(result), dtype=bool)
    for index in range(len(result)):
        if bull_break[index]:
            bullish_mss[index] = current_direction <= 0
            current_direction = 1
        elif bear_break[index]:
            bearish_mss[index] = current_direction >= 0
            current_direction = -1
        structure_direction[index] = current_direction
    result["StructureDirection"] = structure_direction

    sweep_low_reference = _nearest_below(
        np.maximum(close_values, prior_close_values),
        [
            swing_low,
            result["PreviousSwingLow"].shift(1).to_numpy(dtype=float),
            result["VAL"].shift(1).to_numpy(dtype=float),
            result["POC"].shift(1).to_numpy(dtype=float),
            result["AVWAP"].shift(1).to_numpy(dtype=float),
            result["ProfileLow"].shift(1).to_numpy(dtype=float),
        ],
    )
    sweep_high_reference = _nearest_above(
        np.minimum(close_values, prior_close_values),
        [
            swing_high,
            result["PreviousSwingHigh"].shift(1).to_numpy(dtype=float),
            result["VAH"].shift(1).to_numpy(dtype=float),
            result["ProfileHigh"].shift(1).to_numpy(dtype=float),
        ],
    )
    raw_sweep_low = (
        np.isfinite(sweep_low_reference)
        & (result["Low"].to_numpy(dtype=float) < sweep_low_reference - structure_buffer)
        & (close_values > sweep_low_reference + structure_buffer * 0.20)
        & result["CloseLocation"].to_numpy(dtype=float).clip(0.0, 1.0).__ge__(0.55)
    )
    raw_sweep_high = (
        np.isfinite(sweep_high_reference)
        & (result["High"].to_numpy(dtype=float) > sweep_high_reference + structure_buffer)
        & (close_values < sweep_high_reference - structure_buffer * 0.20)
        & result["CloseLocation"].to_numpy(dtype=float).clip(0.0, 1.0).__le__(0.45)
    )
    unique_sweep_low = np.zeros(len(result), dtype=bool)
    unique_sweep_high = np.zeros(len(result), dtype=bool)
    last_low_bar = last_high_bar = -10_000
    last_low_reference = last_high_reference = np.nan
    for index in range(len(result)):
        identity_atr = atr_values[index] if np.isfinite(atr_values[index]) else 0.0
        if raw_sweep_low[index]:
            same = (
                index - last_low_bar <= 12
                and np.isfinite(last_low_reference)
                and abs(sweep_low_reference[index] - last_low_reference) <= 0.25 * identity_atr
            )
            if not same:
                unique_sweep_low[index] = True
                last_low_bar = index
                last_low_reference = sweep_low_reference[index]
        if raw_sweep_high[index]:
            same = (
                index - last_high_bar <= 12
                and np.isfinite(last_high_reference)
                and abs(sweep_high_reference[index] - last_high_reference) <= 0.25 * identity_atr
            )
            if not same:
                unique_sweep_high[index] = True
                last_high_bar = index
                last_high_reference = sweep_high_reference[index]

    breakout_level = _nearest_above(
        prior_close_values,
        [
            result["VAH"].shift(1).to_numpy(dtype=float),
            swing_high,
            prior_high20.to_numpy(dtype=float),
            result["ProfileHigh"].shift(1).to_numpy(dtype=float),
        ],
    )
    raw_breakout = (
        np.isfinite(breakout_level)
        & (close_values > breakout_level + value_buffer.to_numpy(dtype=float))
        & (prior_close_values <= breakout_level + value_buffer.to_numpy(dtype=float))
    )
    breakout_event = np.zeros(len(result), dtype=bool)
    active_breakout_level = np.full(len(result), np.nan, dtype=float)
    active_breakout_bar = np.full(len(result), -1, dtype=int)
    retest_event = np.zeros(len(result), dtype=bool)
    last_identity_bar = -10_000
    last_identity_level = np.nan
    active_level = np.nan
    active_bar = -1
    active_retested = False
    for index in range(len(result)):
        if raw_breakout[index]:
            identity_atr = atr_values[index] if np.isfinite(atr_values[index]) else 0.0
            same = (
                index - last_identity_bar <= active.event_identity_bars
                and np.isfinite(last_identity_level)
                and abs(breakout_level[index] - last_identity_level) <= 0.35 * identity_atr
            )
            if not same:
                breakout_event[index] = True
                last_identity_bar = index
                last_identity_level = breakout_level[index]
                active_level = breakout_level[index]
                active_bar = index
                active_retested = False
        if np.isfinite(active_level) and index > active_bar:
            atr = atr_values[index] if np.isfinite(atr_values[index]) else 0.0
            if close_values[index] < active_level - 0.45 * atr or index - active_bar > 30:
                active_level = np.nan
                active_bar = -1
                active_retested = False
            elif (
                not active_retested
                and float(result["Low"].iloc[index]) <= active_level + 0.30 * atr
                and close_values[index] >= active_level - float(value_buffer.iloc[index])
            ):
                retest_event[index] = True
                active_retested = True
        active_breakout_level[index] = active_level
        active_breakout_bar[index] = active_bar

    behavior = result["VolumeBehaviorCode"].to_numpy(dtype=object)
    spring_event = unique_sweep_low & np.isin(behavior, ["ABSORPTION", "DRY_UP", "DEMAND"])
    spring_bar = _recent_event_bar(spring_event)
    recent_spring = (spring_bar >= 0) & (np.arange(len(result)) - spring_bar <= 12)
    structure_state = np.full(len(result), "BALANCE", dtype=object)
    last_state = "BALANCE"
    last_state_bar = -10_000
    for index in range(len(result)):
        event_state = ""
        if bullish_mss[index]:
            event_state = "MSS_UP"
        elif bearish_mss[index]:
            event_state = "MSS_DOWN"
        elif breakout_event[index]:
            event_state = "BREAKOUT"
        elif retest_event[index]:
            event_state = "RETEST"
        elif spring_event[index]:
            event_state = "SPRING"
        elif bull_break[index]:
            event_state = "BULLISH"
        elif bear_break[index]:
            event_state = "BEARISH"
        elif bullish_displacement.iloc[index]:
            event_state = "DISPLACEMENT_UP"
        elif bearish_displacement.iloc[index]:
            event_state = "DISPLACEMENT_DOWN"
        if event_state:
            last_state = event_state
            last_state_bar = index
        if index - last_state_bar <= active.structure_state_bars:
            structure_state[index] = last_state
        else:
            structure_state[index] = (
                "BULLISH"
                if structure_direction[index] > 0
                else "BEARISH"
                if structure_direction[index] < 0
                else "BALANCE"
            )
    result["SweepLowInternal"] = unique_sweep_low
    result["SweepHighInternal"] = unique_sweep_high
    result["SpringEvent"] = spring_event
    result["BreakoutEvent"] = breakout_event
    result["RetestEvent"] = retest_event
    result["MSSUpEvent"] = bullish_mss
    result["MSSDownEvent"] = bearish_mss
    result["DisplacementUpEvent"] = bullish_displacement
    result["DisplacementDownEvent"] = bearish_displacement
    result["ActiveBreakoutLevel"] = active_breakout_level
    result["ActiveBreakoutBar"] = active_breakout_bar
    result["StructureStateCode"] = structure_state

    turnover = result["Amount"] if "Amount" in result.columns else result["Close"] * result["Volume"]
    result["AverageTurnover20"] = turnover.rolling(20, min_periods=20).mean()
    is_st = (not is_etf) and ("ST" in str(name).upper())
    one_price_up, one_price_down = _one_price_limit_mask(result, ticker, is_etf, is_st)
    limit_fraction = _price_limit_fraction(ticker, is_etf, is_st)
    daily_change = result["Close"] / result["Close"].shift(1) - 1.0
    near_limit_down = daily_change.le(-limit_fraction + 0.01)
    limit_up = daily_change.ge(limit_fraction - 0.01)
    limit_up_streak = np.zeros(len(result), dtype=int)
    streak = 0
    for index, value in enumerate(limit_up.fillna(False).to_numpy(dtype=bool)):
        streak = streak + 1 if value else 0
        limit_up_streak[index] = streak
    liquidity_block = result["AverageTurnover20"].lt(active.minimum_turnover)
    core_ready = (
        result["TrendDataReady"]
        & result["MarketDataReady"]
        & result["RSReady"]
        & result["ProfileReady"]
        & result["ATR"].notna()
    )
    result["HardBlock"] = (
        ~core_ready
        | liquidity_block.fillna(True)
        | is_st
        | one_price_up
        | one_price_down
        | near_limit_down.fillna(False)
        | pd.Series(limit_up_streak >= 2, index=result.index)
    )
    result["HardBlockReason"] = "无"
    result.loc[~core_ready, "HardBlockReason"] = "核心数据不足"
    result.loc[liquidity_block.fillna(True), "HardBlockReason"] = "流动性不足"
    if is_st:
        result["HardBlockReason"] = "ST限制"
    result.loc[one_price_up, "HardBlockReason"] = "一字涨停"
    result.loc[one_price_down, "HardBlockReason"] = "一字跌停"
    result.loc[near_limit_down.fillna(False), "HardBlockReason"] = "跌停附近"
    result.loc[
        pd.Series(limit_up_streak >= 2, index=result.index),
        "HardBlockReason",
    ] = "连续涨停"
    support_reference = _nearest_below(
        close_values,
        [
            result["AVWAP"].to_numpy(dtype=float),
            result["POC"].to_numpy(dtype=float),
            result["EMA20"].to_numpy(dtype=float),
            active_breakout_level,
            result["VAL"].to_numpy(dtype=float),
            result["LastSwingLow"].to_numpy(dtype=float),
        ],
    )
    extension_atr = (close_values - support_reference) / np.where(atr_values > 0.0, atr_values, np.nan)
    result["ChaseRiskCode"] = "LOW"
    result.loc[pd.Series(extension_atr >= 1.20, index=result.index), "ChaseRiskCode"] = "MODERATE"
    result.loc[
        pd.Series(extension_atr >= 2.00, index=result.index) | limit_up.fillna(False),
        "ChaseRiskCode",
    ] = "HIGH"
    result.loc[result["HardBlock"], "ChaseRiskCode"] = "BLOCKED"

    result["HigherTimeframeLong"] = (
        result["MarketRegimeCode"].ne("RISK_OFF")
        & result["WeeklyTrend"].ge(0)
        & result["DailyTrend"].gt(0)
        & result["RS20"].gt(-0.01)
    )
    result["HigherTimeframePermissive"] = (
        result["MarketRegimeCode"].ne("RISK_OFF") & result["WeeklyTrend"].ge(0) & result["RS20"].gt(-0.02)
    )
    support_touched = (
        np.isfinite(support_reference)
        & (result["Low"].to_numpy(dtype=float) <= support_reference + atr_values * 0.25)
        & (close_values >= support_reference - atr_values * 0.10)
    )
    pullback_event = (
        result["HigherTimeframeLong"].to_numpy(dtype=bool)
        & support_touched
        & np.isin(behavior, ["DEMAND", "ABSORPTION", "DRY_UP"])
        & (behavior != "SUPPLY")
    )
    balance_event = (
        result["HigherTimeframePermissive"].to_numpy(dtype=bool)
        & result["ValueStateCode"].eq("BALANCE").to_numpy(dtype=bool)
        & inside_value.to_numpy(dtype=bool)
        & (unique_sweep_low | support_touched)
        & np.isin(behavior, ["DEMAND", "ABSORPTION", "DRY_UP"])
    )
    pullback_bar = _recent_event_bar(pullback_event)
    balance_bar = _recent_event_bar(balance_event)
    recent_pullback = (pullback_bar >= 0) & (np.arange(len(result)) - pullback_bar <= 20)
    recent_balance = (balance_bar >= 0) & (np.arange(len(result)) - balance_bar <= 20)

    spring_reference = np.full(len(result), np.nan, dtype=float)
    spring_extreme = np.full(len(result), np.nan, dtype=float)
    last_reference = last_extreme = np.nan
    for index in range(len(result)):
        if spring_event[index]:
            last_reference = sweep_low_reference[index]
            last_extreme = float(result["Low"].iloc[index])
        spring_reference[index] = last_reference
        spring_extreme[index] = last_extreme
    pullback_reference = np.full(len(result), np.nan, dtype=float)
    pullback_extreme = np.full(len(result), np.nan, dtype=float)
    balance_reference = np.full(len(result), np.nan, dtype=float)
    balance_extreme = np.full(len(result), np.nan, dtype=float)
    last_pullback_reference = last_pullback_extreme = np.nan
    last_balance_reference = last_balance_extreme = np.nan
    for index in range(len(result)):
        if pullback_event[index]:
            last_pullback_reference = support_reference[index]
            last_pullback_extreme = float(result["Low"].iloc[index])
        if balance_event[index]:
            last_balance_reference = support_reference[index]
            last_balance_extreme = float(result["Low"].iloc[index])
        pullback_reference[index] = last_pullback_reference
        pullback_extreme[index] = last_pullback_extreme
        balance_reference[index] = last_balance_reference
        balance_extreme[index] = last_balance_extreme

    breakout_setup = (
        result["HigherTimeframeLong"].to_numpy(dtype=bool)
        & np.isfinite(active_breakout_level)
        & (close_values >= active_breakout_level - value_buffer.to_numpy(dtype=float))
    )
    reversal_setup = result["HigherTimeframePermissive"].to_numpy(dtype=bool) & recent_spring
    pullback_setup = recent_pullback & result["HigherTimeframeLong"].to_numpy(dtype=bool)
    balance_setup = (
        recent_balance
        & result["HigherTimeframePermissive"].to_numpy(dtype=bool)
        & result["ValueStateCode"].eq("BALANCE").to_numpy(dtype=bool)
    )
    candidate_setup = np.full(len(result), "NONE", dtype=object)
    candidate_setup[balance_setup] = "BALANCE"
    candidate_setup[pullback_setup] = "PULLBACK"
    candidate_setup[reversal_setup] = "REVERSAL"
    candidate_setup[breakout_setup] = "BREAKOUT"
    result["CandidateSetupCode"] = candidate_setup
    candidate_source = np.where(
        candidate_setup == "BREAKOUT",
        active_breakout_bar,
        np.where(
            candidate_setup == "REVERSAL",
            spring_bar,
            np.where(candidate_setup == "PULLBACK", pullback_bar, balance_bar),
        ),
    ).astype(int)
    candidate_reference = np.where(
        candidate_setup == "BREAKOUT",
        active_breakout_level,
        np.where(
            candidate_setup == "REVERSAL",
            spring_reference,
            np.where(candidate_setup == "PULLBACK", pullback_reference, balance_reference),
        ),
    )
    candidate_extreme = np.where(
        candidate_setup == "REVERSAL",
        spring_extreme,
        np.where(candidate_setup == "PULLBACK", pullback_extreme, balance_extreme),
    )
    entry_low = candidate_reference - atr_values * 0.20
    entry_high = candidate_reference + atr_values * 0.35
    invalidation = np.where(
        candidate_setup == "BREAKOUT",
        candidate_reference - atr_values * 0.35,
        candidate_extreme - atr_values * 0.08,
    )
    entry_mid = (entry_low + entry_high) / 2.0
    risk = entry_mid - invalidation
    structural_target = _nearest_above(
        entry_mid,
        [
            result["VAH"].to_numpy(dtype=float),
            result["LastSwingHigh"].to_numpy(dtype=float),
            result["ProfileHigh"].to_numpy(dtype=float),
        ],
    )
    fallback_target = entry_mid + risk * 2.0
    target = np.where(np.isfinite(structural_target), structural_target, fallback_target)
    reward_risk = (target - entry_mid) / np.where(risk > 0.0, risk, np.nan)
    candidate_plan_valid = (
        (candidate_setup != "NONE")
        & ~result["HardBlock"].to_numpy(dtype=bool)
        & (candidate_source >= 0)
        & np.isfinite(candidate_reference)
        & np.isfinite(invalidation)
        & (risk > 0.0)
        & np.isfinite(target)
        & (target > close_values)
        & (reward_risk >= active.minimum_reward_risk)
    )
    result["CandidateSourceBar"] = candidate_source
    result["CandidateEntryLow"] = entry_low
    result["CandidateEntryHigh"] = entry_high
    result["CandidateInvalidation"] = invalidation
    result["CandidateTarget"] = target
    result["CandidateRewardRisk"] = reward_risk
    result["CandidatePlanValid"] = candidate_plan_valid

    result = result.join(_score_components(result))
    result = result.join(_apply_lifecycle(result, active))

    result["MarketState"] = result["MarketRegimeCode"].map(_MARKET_ZH).fillna("基准不足")
    result["RelativeStrengthState"] = "数据不足"
    rs_ready = result["RSReady"]
    result.loc[rs_ready, "RelativeStrengthState"] = "中性"
    result.loc[rs_ready & result["RS20"].gt(0.02) & result["RS60"].gt(0.04), "RelativeStrengthState"] = "相对强"
    result.loc[rs_ready & result["RS20"].lt(-0.02) & result["RS60"].lt(-0.04), "RelativeStrengthState"] = "相对弱"
    result["TrendState"] = (
        "周线 "
        + result["WeeklyTrend"].map({1: "↑", 0: "→", -1: "↓"}).fillna("→")
        + "｜日线 "
        + result["DailyTrend"].map({1: "↑", 0: "→", -1: "↓"}).fillna("→")
    )
    result["ValueState"] = result["ValueStateCode"].map(_VALUE_ZH).fillna("价值数据不足")
    result["StructureState"] = result["StructureStateCode"].map(_STRUCTURE_ZH).fillna("结构平衡")
    result["VolumeBehavior"] = result["VolumeBehaviorCode"].map(_VOLUME_ZH).fillna("量价中性")
    result["RiskState"] = result["ChaseRiskCode"].map(
        {"LOW": "追高风险低", "MODERATE": "追高风险中", "HIGH": "追高风险高", "BLOCKED": "存在硬限制"}
    )
    blocked = result["HardBlock"]
    result.loc[blocked, "RiskState"] = "硬限制：" + result.loc[blocked, "HardBlockReason"].astype(str)
    result["CandidateSetup"] = result["CandidateSetupCode"].map(_SETUP_ZH).fillna("暂无")
    result["CurrentSetup"] = result["CurrentSetupCode"].map(_SETUP_ZH).fillna("暂无")
    result["ActivePlan"] = result["LifecycleStateCode"].map(_LIFECYCLE_ZH).fillna("无")
    result["LastPlan"] = result["LastPlanCode"].map(_LIFECYCLE_ZH).fillna("无")
    result["AuctionStructureModelVersion"] = MODEL_VERSION
    result["AuctionStructureModelRole"] = MODEL_ROLE
    result["AuctionStructureProductionApplied"] = MODEL_PRODUCTION_APPLIED
    return result


def latest_auction_structure(
    frame: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
    *,
    ticker: str = "",
    name: str = "",
    is_etf: bool = False,
    config: AuctionStructureConfig | None = None,
) -> AuctionStructureSnapshot:
    features = compute_auction_structure(
        frame,
        benchmark,
        ticker=ticker,
        name=name,
        is_etf=is_etf,
        config=config,
    )
    return snapshot_from_features(features)


def snapshot_from_features(features: pd.DataFrame) -> AuctionStructureSnapshot:
    """Build one scan snapshot without recomputing an existing feature frame."""
    if features.empty:
        raise ValueError("no valid confirmed OHLCV bars")
    row = features.iloc[-1]

    def finite(name_: str) -> float:
        value = pd.to_numeric(pd.Series([row.get(name_, np.nan)]), errors="coerce").iloc[0]
        return float(value) if pd.notna(value) and np.isfinite(float(value)) else np.nan

    return AuctionStructureSnapshot(
        model_version=MODEL_VERSION,
        model_role=MODEL_ROLE,
        production_applied=MODEL_PRODUCTION_APPLIED,
        score=finite("AuctionStructureScore"),
        coverage=finite("AuctionStructureCoverage"),
        market_score=finite("MarketComponent"),
        relative_strength_score=finite("RSComponent"),
        trend_score=finite("TrendComponent"),
        value_score=finite("ValueComponent"),
        structure_score=finite("StructureComponent"),
        volume_score=finite("VolumeComponent"),
        risk_score=finite("RiskComponent"),
        market=str(row.get("MarketState", "基准不足")),
        relative_strength=str(row.get("RelativeStrengthState", "数据不足")),
        trend=str(row.get("TrendState", "周线 →｜日线 →")),
        value=str(row.get("ValueState", "价值数据不足")),
        structure=str(row.get("StructureState", "结构平衡")),
        volume=str(row.get("VolumeBehavior", "量价中性")),
        risk=str(row.get("RiskState", "存在硬限制")),
        candidate_setup=str(row.get("CandidateSetup", "暂无")),
        current_setup=str(row.get("CurrentSetup", "暂无")),
        active_plan=str(row.get("ActivePlan", "无")),
        last_plan=str(row.get("LastPlan", "无")),
        poc=finite("POC"),
        vah=finite("VAH"),
        val=finite("VAL"),
        avwap=finite("AVWAP"),
        value_migration_atr=finite("ValueMigrationATR"),
        rs20=finite("RS20"),
        rs60=finite("RS60"),
        atr=finite("ATR"),
        relative_volume=finite("RelativeVolume"),
        average_turnover20=finite("AverageTurnover20"),
        hard_block_reason=str(row.get("HardBlockReason", "核心数据不足")),
        reward_risk=finite("ActiveRewardRisk"),
        entry_low=finite("ActiveEntryLow"),
        entry_high=finite("ActiveEntryHigh"),
        invalidation=finite("ActiveInvalidation"),
        target=finite("ActiveTarget"),
    )


def summarize_auction_backtest(samples: pd.DataFrame) -> dict[str, object]:
    """Summarise non-overlapping train/validation/test shadow samples."""
    summary: dict[str, object] = {
        "model_version": MODEL_VERSION,
        "model_role": MODEL_ROLE,
        "production_applied": MODEL_PRODUCTION_APPLIED,
        "score_weights": dict(SCORE_WEIGHTS),
        "data_contract": "CONFIRMED_DAILY_OHLCV_PLUS_FREE_BENCHMARK",
        "order_flow_claim": "NONE_OHLCV_PROXY_ONLY",
        "point_in_time": True,
        "entry_timing": "NEXT_TRADABLE_OPEN",
        "exit_timing": "CONFIRMED_CLOSE_T_PLUS_1_OR_LATER",
        "samples": len(samples),
        "metric_samples": 0,
        "purged_samples": 0,
        "by_split": {},
    }
    if samples.empty:
        summary.update(
            {
                "win_rate": None,
                "average_net_return": None,
                "median_net_return": None,
                "profit_factor": None,
                "average_mae": None,
                "average_mfe": None,
                "outcomes": {},
            }
        )
        return summary

    metric_samples = samples.loc[samples.get("Split", pd.Series("train", index=samples.index)).ne("purged")].copy()
    summary["metric_samples"] = len(metric_samples)
    summary["purged_samples"] = len(samples) - len(metric_samples)
    net = pd.to_numeric(metric_samples["NetReturnPct"], errors="coerce").dropna()
    gains = float(net[net > 0.0].sum())
    losses = float(-net[net < 0.0].sum())
    summary.update(
        {
            "win_rate": float(net.gt(0.0).mean()) if not net.empty else None,
            "average_net_return": float(net.mean()) if not net.empty else None,
            "median_net_return": float(net.median()) if not net.empty else None,
            "profit_factor": gains / losses if losses > 0.0 else None,
            "average_mae": float(metric_samples["MAEPct"].mean()),
            "average_mfe": float(metric_samples["MFEPct"].mean()),
            "outcomes": {
                str(key): int(value) for key, value in metric_samples["Outcome"].value_counts().sort_index().items()
            },
        }
    )
    split_payload: dict[str, dict[str, object]] = {}
    for split, group in metric_samples.groupby("Split", sort=False):
        values = pd.to_numeric(group["NetReturnPct"], errors="coerce").dropna()
        split_payload[str(split)] = {
            "samples": len(group),
            "win_rate": float(values.gt(0.0).mean()) if not values.empty else None,
            "average_net_return": float(values.mean()) if not values.empty else None,
            "median_net_return": float(values.median()) if not values.empty else None,
        }
    summary["by_split"] = split_payload
    return summary


def backtest_auction_structure(
    frame: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
    *,
    ticker: str = "",
    name: str = "",
    is_etf: bool = False,
    config: AuctionStructureConfig | None = None,
    commission: float = 0.00008499999,
    stamp_duty: float = 0.0005,
    slippage: float = 0.001,
    validation_ratio: float = 0.20,
    test_ratio: float = 0.20,
    features: pd.DataFrame | None = None,
) -> AuctionBacktestResult:
    """Backtest confirmed plans with next-open execution and T+1 exits."""
    if commission < 0.0 or stamp_duty < 0.0 or slippage < 0.0:
        raise ValueError("trading costs must be non-negative")
    if validation_ratio < 0.0 or test_ratio <= 0.0 or validation_ratio + test_ratio >= 1.0:
        raise ValueError("validation/test ratios must be non-negative and sum below one")
    active = config or AuctionStructureConfig()
    evaluated = (
        compute_auction_structure(
            frame,
            benchmark,
            ticker=ticker,
            name=name,
            is_etf=is_etf,
            config=active,
        )
        if features is None
        else features.copy()
    )
    if evaluated.empty:
        empty = pd.DataFrame()
        return AuctionBacktestResult(empty, summarize_auction_backtest(empty))

    is_st = (not is_etf) and ("ST" in str(name).upper())
    one_price_up, one_price_down = _one_price_limit_mask(evaluated, ticker, is_etf, is_st)
    benchmark_close = _aligned_benchmark_close(benchmark, evaluated.index)
    records: list[dict[str, object]] = []
    confirmed = np.flatnonzero(evaluated["LifecycleEventCode"].eq("CONFIRMED").to_numpy())
    for signal_index in confirmed:
        entry_index = int(signal_index) + 1
        if entry_index >= len(evaluated):
            continue
        if bool(one_price_up.iloc[entry_index]) or bool(one_price_down.iloc[entry_index]):
            continue
        signal = evaluated.iloc[signal_index]
        entry_open = float(evaluated["Open"].iloc[entry_index])
        zone_high = float(signal["ActiveEntryHigh"])
        atr = float(signal["ATR"])
        if not np.isfinite(entry_open) or entry_open <= 0.0:
            continue
        if np.isfinite(zone_high) and np.isfinite(atr) and entry_open > zone_high + 0.25 * atr:
            continue
        entry_price = entry_open * (1.0 + slippage)
        invalidation = float(signal["ActiveInvalidation"])
        target = float(signal["ActiveTarget"])
        if not all(np.isfinite(value) for value in (entry_price, invalidation, target)):
            continue
        if invalidation >= entry_price or target <= entry_price:
            continue

        last_index = min(len(evaluated) - 1, entry_index + active.setup_expiry_bars)
        exit_index = last_index
        outcome = "EXPIRED"
        # T+1: the entry day can never be an exit day.
        for candidate in range(entry_index + 1, last_index + 1):
            close = float(evaluated["Close"].iloc[candidate])
            candidate_outcome = ""
            if close < invalidation:
                candidate_outcome = "INVALIDATED"
            elif close >= target:
                candidate_outcome = "TARGET"
            if not candidate_outcome:
                continue
            resolved = candidate
            while resolved <= last_index and bool(one_price_down.iloc[resolved]):
                resolved += 1
            if resolved <= last_index:
                exit_index = resolved
                outcome = candidate_outcome
                break

        exit_close = float(evaluated["Close"].iloc[exit_index])
        exit_price = exit_close * (1.0 - slippage)
        gross_return = (exit_close / entry_open - 1.0) * 100.0
        execution_return = (exit_price / entry_price - 1.0) * 100.0
        explicit_cost = (commission * 2.0 + (0.0 if is_etf else stamp_duty)) * 100.0
        net_return = execution_return - explicit_cost
        total_cost = gross_return - net_return
        holding_low = float(evaluated["Low"].iloc[entry_index : exit_index + 1].min())
        holding_high = float(evaluated["High"].iloc[entry_index : exit_index + 1].max())
        benchmark_return = np.nan
        benchmark_entry = benchmark_close.iloc[entry_index]
        benchmark_exit = benchmark_close.iloc[exit_index]
        if pd.notna(benchmark_entry) and pd.notna(benchmark_exit) and benchmark_entry > 0.0:
            benchmark_return = (float(benchmark_exit) / float(benchmark_entry) - 1.0) * 100.0
        records.append(
            {
                "Ticker": ticker,
                "ModelVersion": MODEL_VERSION,
                "ModelRole": MODEL_ROLE,
                "ProductionApplied": MODEL_PRODUCTION_APPLIED,
                "SignalDate": evaluated.index[signal_index].strftime("%Y-%m-%d"),
                "EntryDate": evaluated.index[entry_index].strftime("%Y-%m-%d"),
                "ExitDate": evaluated.index[exit_index].strftime("%Y-%m-%d"),
                "Setup": str(signal["ActiveSetupCode"]),
                "SetupZh": _SETUP_ZH.get(str(signal["ActiveSetupCode"]), "暂无"),
                "Score": float(signal["AuctionStructureScore"]),
                "Coverage": float(signal["AuctionStructureCoverage"]),
                "EntryPrice": entry_price,
                "Invalidation": invalidation,
                "Target": target,
                "PlannedRR": float(signal["ActiveRewardRisk"]),
                "Outcome": outcome,
                "OutcomeZh": _LIFECYCLE_ZH[outcome],
                "HoldingBars": int(exit_index - entry_index),
                "GrossReturnPct": gross_return,
                "TradingCostPct": total_cost,
                "NetReturnPct": net_return,
                "BenchmarkReturnPct": benchmark_return,
                "NetExcessReturnPct": net_return - benchmark_return,
                "MAEPct": (holding_low / entry_price - 1.0) * 100.0,
                "MFEPct": (holding_high / entry_price - 1.0) * 100.0,
            }
        )

    samples = pd.DataFrame.from_records(records)
    if not samples.empty:
        samples = samples.sort_values(["SignalDate", "Ticker"], kind="stable").reset_index(drop=True)
        validation_position = min(
            len(evaluated) - 1,
            max(0, int(np.floor(len(evaluated) * (1.0 - validation_ratio - test_ratio)))),
        )
        test_position = min(
            len(evaluated) - 1,
            max(0, int(np.floor(len(evaluated) * (1.0 - test_ratio)))),
        )
        validation_date = pd.Timestamp(evaluated.index[validation_position])
        test_date = pd.Timestamp(evaluated.index[test_position])
        labels: list[str] = []
        for row in samples.itertuples(index=False):
            entry_date = pd.Timestamp(row.EntryDate)
            exit_date = pd.Timestamp(row.ExitDate)
            if exit_date < validation_date:
                labels.append("train")
            elif entry_date >= validation_date and exit_date < test_date:
                labels.append("validation")
            elif entry_date >= test_date:
                labels.append("test")
            else:
                # A holding period crossing a split boundary is retained for
                # audit but excluded from every metric.
                labels.append("purged")
        samples["Split"] = labels
    return AuctionBacktestResult(samples, summarize_auction_backtest(samples))


__all__ = [
    "AuctionBacktestResult",
    "AuctionStructureConfig",
    "AuctionStructureSnapshot",
    "MODEL_PRODUCTION_APPLIED",
    "MODEL_ROLE",
    "MODEL_VERSION",
    "SCORE_WEIGHTS",
    "backtest_auction_structure",
    "compute_auction_structure",
    "latest_auction_structure",
    "snapshot_from_features",
    "summarize_auction_backtest",
]
