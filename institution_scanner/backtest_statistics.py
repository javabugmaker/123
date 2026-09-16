"""Backtest statistics and decision-quality helpers extracted from ``analytics_core``.

These 16 helpers are pure (or pure-enough) estimators that sat inside the
3837-line ``analytics_core``, which has 5 lines of byte budget left.  They were
extracted under the golden equivalence gate in
``tests/test_analytics_core_golden.py``: the fixture froze their behaviour
*before* the move and still matches *after* it.

Three rules shaped what could come across, all three learned the hard way:

* **Nothing here may be rebound by an overlay.**  ``analytics.py`` and 21 root
  overlays patch ``analytics_core`` at import time.  A moved helper that an
  overlay rebinds would silently keep its original while production kept
  patching ``analytics_core``.  ``_date_balanced_weights`` is exactly that case
  (rebound by ``backtest_math_integrity_v94`` through a *parameter*), which is
  why it and its caller ``_bucket_rows`` stayed behind.
* **This module must not import ``analytics_core``.**  ``analytics_core``
  imports back from here; the reverse edge would be a cycle.  Everything needed
  comes from ``config`` / ``indicators`` instead.
* **Anything an overlay rebinds must be resolved late, through the owning
  module's attribute.**  ``indicator_acceleration_v77`` rebinds
  ``compute_volume_profile`` on both ``indicators`` and ``analytics_core``
  *after* import.  Binding it by value here would freeze the un-accelerated
  original -- a silent performance regression that the golden value fixture
  cannot see, because both implementations return the same numbers.  See
  ``tests/test_analytics_core_golden.py::test_extracted_module_shares_overlay_patched_bindings``.

The logger reuses the ``institution_scanner.analytics`` channel so log records
are indistinguishable from before the move.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

# ``indicators`` is imported as a *module*, not by value, and every call goes
# through the attribute at call time.  ``indicator_acceleration_v77`` rebinds
# ``compute_volume_profile`` on both ``indicators`` and ``analytics_core`` after
# import.  While these helpers lived in analytics_core they saw the rebound
# version through the module global; a ``from indicators import
# compute_volume_profile`` here would freeze the original at import time and
# silently drop the accelerated path.  Late binding keeps the resolution rule
# identical to the one that existed before the move.
import indicators as _indicators
from config import (
    BACKTEST_FULL_WEIGHT_SAMPLES,
    BACKTEST_LOW_CONFIDENCE_MAX_SAMPLES,
    BACKTEST_MIN_SAMPLES_FOR_RANKING,
    BACKTEST_NORMAL_WEIGHT,
    BACKTEST_SCORE_WINDOW_BARS,
    ENABLE_VOLUME_PROFILE,
    QUALITY_MULTIPLIER_FAIL,
    QUALITY_MULTIPLIER_PASS,
    QUALITY_MULTIPLIER_UNKNOWN,
)

logger = logging.getLogger("institution_scanner.analytics")

def _finite_float(value: Any, default: float = np.nan) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if np.isfinite(parsed) else default


def _safe_return(series: pd.Series, periods: int) -> float:
    clean = (
        pd.to_numeric(series, errors="coerce")
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )
    if len(clean) <= periods:
        return np.nan
    start = float(clean.iloc[-periods - 1])
    end = float(clean.iloc[-1])
    return (end / start - 1.0) * 100 if start > 0 else np.nan


def _weighted_observations(
    values: pd.Series, weights: pd.Series | None = None
) -> pd.DataFrame:
    numeric = pd.to_numeric(values, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    if weights is None:
        weight = pd.Series(1.0, index=numeric.index, dtype=float)
    else:
        weight = pd.to_numeric(weights, errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
    return (
        pd.DataFrame({"value": numeric, "weight": weight})
        .dropna()
        .loc[lambda frame: frame["weight"].gt(0.0)]
    )


def _weighted_quantile(
    values: pd.Series, weights: pd.Series | None, quantile: float
) -> float:
    observations = _weighted_observations(values, weights).sort_values(
        "value", kind="mergesort"
    )
    if observations.empty:
        return float("nan")
    total = float(observations["weight"].sum())
    cutoff = float(np.clip(quantile, 0.0, 1.0)) * total
    cumulative = observations["weight"].cumsum().to_numpy(dtype=float)
    position = int(np.searchsorted(cumulative, cutoff, side="left"))
    position = min(position, len(observations) - 1)
    value = float(observations["value"].iloc[position])
    # Preserve the ordinary even-sample median when all weights are equal,
    # while still letting a genuinely dominant observation determine the
    # weighted median.
    if (
        position + 1 < len(observations)
        and np.isclose(cumulative[position], cutoff, rtol=0.0, atol=1e-12)
    ):
        value = (value + float(observations["value"].iloc[position + 1])) / 2.0
    return value


def _weighted_arrays(
    values: pd.Series,
    weights: pd.Series,
) -> tuple[np.ndarray, np.ndarray]:
    numeric = pd.to_numeric(values, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    weight = pd.to_numeric(weights, errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    valid = numeric.notna() & weight.notna() & weight.gt(0.0)
    return (
        numeric.loc[valid].to_numpy(dtype=np.float64),
        weight.loc[valid].to_numpy(dtype=np.float64),
    )


def _weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    numeric, weight = _weighted_arrays(values, weights)
    total = float(weight.sum())
    return float(np.dot(numeric, weight) / total) if total > 0.0 else float("nan")


def _weighted_rate(values: pd.Series, weights: pd.Series) -> float:
    numeric, weight = _weighted_arrays(values, weights)
    total = float(weight.sum())
    if total <= 0.0:
        return float("nan")
    return float(np.dot((numeric > 0.0).astype(np.float64), weight) / total)


def _weighted_robust_mean(values: pd.Series, weights: pd.Series) -> float:
    """Winsorized weighted mean used for dependent backtest observations."""
    numeric, weight = _weighted_arrays(values, weights)
    total = float(weight.sum())
    if total <= 0.0:
        return float("nan")
    if numeric.size < 5:
        return float(np.dot(numeric, weight) / total)
    order = np.argsort(numeric, kind="mergesort")
    ordered_values = numeric[order]
    ordered_weights = weight[order]
    mid_cdf = (np.cumsum(ordered_weights) - ordered_weights * 0.5) / total
    lower = float(np.interp(0.10, mid_cdf, ordered_values))
    upper = float(np.interp(0.90, mid_cdf, ordered_values))
    clipped = np.clip(numeric, lower, upper)
    return float(np.dot(clipped, weight) / total)


def _weighted_std(values: pd.Series, weights: pd.Series) -> float:
    numeric, weight = _weighted_arrays(values, weights)
    total = float(weight.sum())
    if total <= 0.0:
        return float("nan")
    mean = float(np.dot(numeric, weight) / total)
    variance = float(np.dot((numeric - mean) ** 2, weight) / total)
    return float(np.sqrt(max(variance, 0.0)))


def _benchmark_regime(frames: dict[str, pd.DataFrame]) -> tuple[str, str]:
    states: list[bool] = []
    returns: list[float] = []
    for frame in frames.values():
        enriched = _indicators.compute_all_indicators(frame.copy())
        if len(enriched) < 60:
            continue
        close = float(enriched["Close"].iloc[-1])
        ma60 = float(enriched["Close"].rolling(60, min_periods=30).mean().iloc[-1])
        ma200 = (
            float(enriched["MA200"].iloc[-1])
            if "MA200" in enriched
            else np.nan
        )
        long_average = ma200 if np.isfinite(ma200) else ma60
        states.append(bool(close >= ma60 and close >= long_average))
        value = _safe_return(enriched["Close"], 60)
        if np.isfinite(value):
            returns.append(value)
    if not states:
        return "未知", "基准数据不足"
    average_return = float(np.mean(returns)) if returns else 0.0
    required_positive = max(1, (len(states) * 2 + 2) // 3)
    if sum(states) >= required_positive and average_return > 3:
        return "风险偏好", f"基准60日平均收益 {average_return:.1f}%"
    if sum(states) == 0 and average_return < -3:
        return "风险规避", f"基准60日平均收益 {average_return:.1f}%"
    return "震荡", f"基准60日平均收益 {average_return:.1f}%"


def _benchmark_regime_components(
    frames: dict[str, pd.DataFrame], slow_regime: str, slow_reason: str
) -> tuple[str, str, str, float, str]:
    fast_states: list[bool] = []
    fast_returns: list[float] = []
    for frame in frames.values():
        enriched = _indicators.compute_all_indicators(frame.copy())
        if len(enriched) < 12 or "Close" not in enriched:
            continue
        close = _finite_float(enriched["Close"].iloc[-1])
        ma10 = _finite_float(
            enriched["Close"].rolling(10, min_periods=5).mean().iloc[-1]
        )
        ret10 = _safe_return(enriched["Close"], 10)
        if np.isfinite(close) and np.isfinite(ma10):
            fast_states.append(
                bool(close >= ma10 and (not np.isfinite(ret10) or ret10 >= 0))
            )
        if np.isfinite(ret10):
            fast_returns.append(ret10)
    if not fast_states:
        return slow_regime, slow_regime, slow_regime, 0.0, slow_reason
    average_return = float(np.mean(fast_returns)) if fast_returns else 0.0
    positive = sum(fast_states)
    if (
        positive >= max(1, (len(fast_states) * 2 + 2) // 3)
        and average_return > 0.8
    ):
        fast = "风险偏好"
    elif positive == 0 and average_return < -0.8:
        fast = "风险规避"
    else:
        fast = "震荡"
    if fast == slow_regime:
        combined = fast
    elif fast == "风险偏好" and slow_regime == "风险规避":
        combined = "震荡修复"
    elif fast == "风险规避" and slow_regime == "风险偏好":
        combined = "震荡转弱"
    else:
        combined = "震荡"
    confidence = min(
        1.0,
        abs(positive / len(fast_states) - 0.5) * 2.0 * 0.6
        + (0.4 if fast == slow_regime else 0.15),
    )
    reason = f"快线10日均收 {average_return:.1f}%；慢线：{slow_reason}"
    return fast, slow_regime, combined, round(float(confidence), 4), reason


def _backtest_scoring_window(
    enriched: pd.DataFrame,
    index: int,
    *,
    score_window: int | None = None,
    include_volume_profile: bool | None = None,
) -> pd.DataFrame:
    """Return the bounded, point-in-time frame consumed by score_ticker."""
    end = int(index) + 1
    window = int(score_window or BACKTEST_SCORE_WINDOW_BARS)
    start = max(0, end - max(252, window))
    historical = enriched.iloc[start:end].copy(deep=False)
    vp_columns = [
        "VP_HVN_Center",
        "DistToHVN_Pct",
        "Above_HVN",
        "VP_LVN_Center",
        "DistToLVN_Pct",
    ]
    historical = historical.drop(columns=vp_columns, errors="ignore")
    should_compute_vp = ENABLE_VOLUME_PROFILE if include_volume_profile is None else bool(include_volume_profile)
    if should_compute_vp:
        historical = historical.copy(deep=False)
        try:
            _indicators.compute_volume_profile(historical)
        except (ArithmeticError, TypeError, ValueError):
            logger.debug("Historical volume profile failed.", exc_info=True)
    return historical


def _candidate_endpoint_matrix(
    enriched: pd.DataFrame,
    *,
    fast_prefilter: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """Vectorize cheap endpoint features before any historical score_ticker call."""
    index = enriched.index
    def numeric(name: str) -> pd.Series:
        if name not in enriched.columns:
            return pd.Series(np.nan, index=index, dtype=float)
        return pd.to_numeric(enriched[name], errors="coerce")

    close = numeric("Close")
    high = numeric("High")
    low = numeric("Low")
    ma20 = numeric("MA20")
    ma50 = numeric("MA50")
    atr = numeric("ATR14")
    support = low.rolling(20, min_periods=20).min()
    resistance = high.shift(1).rolling(20, min_periods=20).max()
    effective_atr = atr.where(atr.gt(0), close * 0.03)
    near_support = close.le(support + effective_atr * 1.5)
    five_day_up = close.ge(close.shift(5))
    trend_candidate = close.gt(ma20) & (ma20.ge(ma50) | five_day_up | near_support)
    breakout_flag = close.gt(resistance).fillna(False)
    broad = (trend_candidate | near_support | breakout_flag).fillna(False)

    if fast_prefilter:
        volume = numeric("Volume")
        vol20 = numeric("VolMA20")
        volume_ratio = volume / vol20.replace(0, np.nan)
        cmf = numeric("CMF")
        ad_slope = numeric("AD_Slope")
        flow_ok = cmf.ge(-0.02) | ad_slope.gt(0)
        volume_ok = volume_ratio.ge(0.85)
        evidence_available = volume_ratio.notna() | cmf.notna() | ad_slope.notna()
        support_ready = near_support & (flow_ok | volume_ok)
        trend_ready = close.gt(ma20) & (ma20.ge(ma50) | five_day_up) & (flow_ok | volume_ok)
        filtered = (breakout_flag | support_ready | trend_ready).fillna(False)
        broad = broad.where(~evidence_available, filtered)

    candidates = np.flatnonzero(broad.to_numpy(dtype=bool))
    return candidates, breakout_flag.to_numpy(dtype=bool)


def _backtest_evidence(
    samples: int, effective_samples: float, return_std: float
) -> tuple[float, float, str]:
    count = max(0.0, float(samples))
    effective = min(count, max(0.0, float(effective_samples)))
    if count < BACKTEST_MIN_SAMPLES_FOR_RANKING:
        return 0.0, 0.0, "样本不足"
    if count < BACKTEST_LOW_CONFIDENCE_MAX_SAMPLES:
        support = 0.25 * (
            (count - BACKTEST_MIN_SAMPLES_FOR_RANKING + 1.0)
            / (
                BACKTEST_LOW_CONFIDENCE_MAX_SAMPLES
                - BACKTEST_MIN_SAMPLES_FOR_RANKING
                + 1.0
            )
        )
        tier = "低可信度"
    elif count < BACKTEST_FULL_WEIGHT_SAMPLES:
        support = 0.25 + 0.75 * (
            (count - BACKTEST_LOW_CONFIDENCE_MAX_SAMPLES)
            / (BACKTEST_FULL_WEIGHT_SAMPLES - BACKTEST_LOW_CONFIDENCE_MAX_SAMPLES)
        )
        tier = "中可信度"
    else:
        support = 1.0
        tier = "高可信度"
    independence = np.sqrt(effective / count) if count else 0.0
    dispersion = (
        float(np.clip(1.0 - abs(return_std) / 80.0, 0.55, 1.0))
        if np.isfinite(return_std)
        else 1.0
    )
    reliability = float(np.clip(support * independence * dispersion, 0.0, 1.0))
    return (
        round(reliability, 4),
        round(reliability * BACKTEST_NORMAL_WEIGHT, 4),
        tier,
    )


def _spearman(frame: pd.DataFrame, target: str) -> float:
    columns = ["score", target]
    if "sample_weight" in frame.columns:
        columns.append("sample_weight")
    data = frame[columns].replace([np.inf, -np.inf], np.nan).dropna(
        subset=["score", target]
    )
    if (
        len(data) < 2
        or data["score"].nunique() < 2
        or data[target].nunique() < 2
    ):
        return 0.0
    left = data["score"].rank(method="average").to_numpy(dtype=np.float64)
    right = data[target].rank(method="average").to_numpy(dtype=np.float64)
    weights = pd.to_numeric(
        data.get("sample_weight", pd.Series(1.0, index=data.index)),
        errors="coerce",
    ).fillna(0.0).clip(lower=0.0).to_numpy(dtype=np.float64)
    total = float(weights.sum())
    if total <= 0.0:
        return 0.0
    left_mean = float(np.dot(left, weights) / total)
    right_mean = float(np.dot(right, weights) / total)
    left_centered = left - left_mean
    right_centered = right - right_mean
    denominator = float(
        np.sqrt(
            np.dot(left_centered**2, weights)
            * np.dot(right_centered**2, weights)
        )
    )
    if denominator <= 0.0:
        return 0.0
    value = float(np.dot(left_centered * right_centered, weights) / denominator)
    return value if np.isfinite(value) else 0.0


def _entry_date_equal_weight_stats(sample_frame: pd.DataFrame) -> dict[str, Any]:
    if sample_frame.empty:
        return {"entry_dates": 0, "samples": 0}
    numeric_columns = [
        "return20",
        "return60",
        "benchmark_return20",
        "benchmark_return60",
        "net_return20",
        "net_return60",
        "drawdown20",
        "drawdown60",
    ]
    daily = sample_frame.groupby("entry_date", sort=True)[numeric_columns].mean()
    daily["excess20"] = daily["return20"] - daily["benchmark_return20"]
    daily["excess60"] = daily["return60"] - daily["benchmark_return60"]
    return {
        "entry_dates": len(daily),
        "samples": len(sample_frame),
        "average_return_20d": float(daily["return20"].mean()),
        "average_return_60d": float(daily["return60"].mean()),
        "average_benchmark_return_20d": float(daily["benchmark_return20"].mean()),
        "average_benchmark_return_60d": float(daily["benchmark_return60"].mean()),
        "average_excess_return_20d": float(daily["excess20"].mean()),
        "average_excess_return_60d": float(daily["excess60"].mean()),
        "average_net_return_20d": float(daily["net_return20"].mean()),
        "average_net_return_60d": float(daily["net_return60"].mean()),
        "maximum_drawdown_20d": float(daily["drawdown20"].min()),
        "maximum_drawdown_60d": float(daily["drawdown60"].min()),
    }


def _decision_quality_multiplier(
    frame: pd.DataFrame,
    *,
    is_etf: pd.Series,
    quality_available: pd.Series,
) -> pd.Series:
    """Reproduce Fundamental Gate multiplier semantics after backtesting.

    Moved out of ``analytics_core`` under the golden gate (§9.3 #3).  Safe to
    move for two reasons, both re-checked by
    ``test_move_candidates_are_not_patched_by_any_overlay``: no overlay rebinds
    the name on ``analytics_core``, and the three ``QUALITY_MULTIPLIER_*``
    constants it reads are defined only in ``config_core`` -- nothing patches
    them at runtime, so importing them by value here cannot freeze a stale
    value the way ``compute_volume_profile`` would.
    """
    quality_applicable = (
        frame.get("QualityApplicable", pd.Series(~is_etf, index=frame.index))
        .astype(str)
        .str.strip()
        .str.lower()
        .isin({"true", "1", "yes", "y", "是"})
        & ~is_etf
    )
    quality_gate = (
        frame.get("QualityGate", pd.Series(True, index=frame.index))
        .astype(str)
        .str.strip()
        .str.lower()
        .isin({"true", "1", "yes", "y", "是"})
    )
    if "QualityHardDataComplete" in frame:
        hard_data_complete = (
            frame["QualityHardDataComplete"]
            .astype(str)
            .str.strip()
            .str.lower()
            .isin({"true", "1", "yes", "y", "是"})
        )
    else:
        quality_profile = (
            frame.get("QualityProfile", pd.Series("GENERAL", index=frame.index))
            .fillna("GENERAL")
            .astype(str)
            .str.upper()
        )
        roe_available = pd.to_numeric(
            frame.get("ROE", pd.Series(np.nan, index=frame.index)),
            errors="coerce",
        ).notna()
        profit_available = pd.concat(
            [
                pd.to_numeric(
                    frame.get(column, pd.Series(np.nan, index=frame.index)),
                    errors="coerce",
                ).notna()
                for column in ("NetProfitY1", "NetProfitY2", "NetProfitY3")
            ],
            axis=1,
        ).all(axis=1)
        margin_available = pd.to_numeric(
            frame.get(
                "IndustryGrossMarginPercentile",
                pd.Series(np.nan, index=frame.index),
            ),
            errors="coerce",
        ).notna()
        margin_required = ~quality_profile.isin(
            {"FINANCIAL", "DEFENSIVE", "ETF"}
        )
        provider_name = (
            frame.get("FundamentalProvider", pd.Series("", index=frame.index))
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
        )
        metadata_required = provider_name.ne("") & provider_name.ne("legacy-cache")
        report_metadata_available = (
            frame.get("LatestReportPeriod", pd.Series("", index=frame.index))
            .fillna("")
            .astype(str)
            .str.strip()
            .ne("")
            & frame.get(
                "LatestAnnouncementDate",
                pd.Series("", index=frame.index),
            )
            .fillna("")
            .astype(str)
            .str.strip()
            .ne("")
        )
        report_status_usable = (
            frame.get(
                "FundamentalDataStatus",
                pd.Series("MISSING", index=frame.index),
            )
            .fillna("MISSING")
            .astype(str)
            .str.strip()
            .str.upper()
            .isin({"CURRENT", "AWAITING_RELEASE"})
        )
        hard_data_complete = (
            roe_available
            & profit_available
            & (~margin_required | margin_available)
            & (~metadata_required | (report_metadata_available & report_status_usable))
        )
    hard_gate_fail = quality_applicable & ~quality_gate
    quality_uncertain = quality_applicable & (
        ~quality_available | ~hard_data_complete
    )
    return pd.Series(
        np.select(
            [~quality_applicable, hard_gate_fail, quality_uncertain],
            [1.0, QUALITY_MULTIPLIER_FAIL, QUALITY_MULTIPLIER_UNKNOWN],
            default=QUALITY_MULTIPLIER_PASS,
        ),
        index=frame.index,
        dtype=float,
    )
