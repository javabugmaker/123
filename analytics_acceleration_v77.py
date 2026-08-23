"""Analytics orchestration acceleration shared by the canonical runtime.

The original v77 layer removed repeated enrichment work. v97 extends the same
module instead of adding another facade: exact-refinement evidence lookup is now
DataFrame/groupby/merge based, and backtest freshness evaluates the trading
calendar only once per unique (cutoff, as-of) pair rather than once per ticker.

Object mutation and calendar lookup remain side-effect/scalar boundaries; all
cross-sectional preparation around them is bulk Pandas/NumPy work.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

import analytics_core as _core
import backtest_acceleration_v77 as _backtest_acceleration
import score_acceleration_v77 as _score_acceleration

ANALYTICS_BULK_ACCELERATION_VERSION = (
    "2026-08-23-v97-refinement-freshness-bulk-v1"
)
_INSTALLED = False


def enrich_results(
    results: list[object],
    source: str,
    frames: dict[str, pd.DataFrame] | None = None,
) -> None:
    benchmark_frames = _core._load_benchmark_frames(source)
    slow_regime, slow_reason = _core._benchmark_regime(benchmark_frames)
    (
        regime_fast,
        regime_slow,
        regime,
        regime_confidence,
        regime_reason,
    ) = _core._benchmark_regime_components(
        benchmark_frames, slow_regime, slow_reason
    )
    realtime_prices: dict[str, float] | None = None

    industry_returns: dict[str, dict[str, float]] = {}
    relative_returns: dict[str, float] = {}
    total = len(results)
    completed = 0
    workers = min(max(1, int(_core.SCAN_THREADS)), max(1, total))
    _core.logger.info(
        "Enrichment started: %d results, %d threads (v77 reuse path).",
        total,
        workers,
    )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _core._enrich_one_result,
                result,
                source,
                regime,
                regime_reason,
                regime_fast,
                regime_slow,
                regime_confidence,
                frames,
                realtime_prices,
            ): result
            for result in results
        }
        for future in as_completed(futures):
            source_result = futures[future]
            try:
                result, enriched, relative = future.result()
            except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
                completed += 1
                _core.logger.warning(
                    "Enrichment failed for %s: %s", source_result.ticker, exc
                )
                continue
            completed += 1
            if enriched is not None:
                classification = _core.model_classification(
                    is_etf=bool(result.is_etf),
                    name=result.name,
                    industry=result.industry,
                    sector=result.sector,
                    ticker=result.ticker,
                )
                result.model_classification = classification
                result.etf_tracking_key = (
                    _core.etf_tracking_key(
                        name=result.name,
                        industry=result.industry,
                        sector="",
                        ticker=result.ticker,
                    )
                    if result.is_etf
                    else ""
                )
                result.theme_cluster = _core.theme_cluster(
                    is_etf=bool(result.is_etf),
                    name=result.name,
                    industry=result.industry,
                    sector=result.sector,
                    classification=classification,
                    ticker=result.ticker,
                )
                if (
                    result.is_etf
                    and not str(result.sector or "").strip()
                    and classification
                ):
                    result.sector = classification
                relative_returns[result.ticker] = float(relative)
                if classification and np.isfinite(relative):
                    industry_returns.setdefault(classification, {})[
                        result.ticker
                    ] = float(relative)
            if completed == total or completed % 100 == 0:
                _core.logger.info(
                    "Enrichment progress: %d/%d results.", completed, total
                )

    industry_totals = {
        industry: (float(sum(values.values())), len(values))
        for industry, values in industry_returns.items()
        if values
    }
    for result in results:
        value = relative_returns.get(result.ticker, np.nan)
        classification = str(result.model_classification or "")
        if not classification:
            result.industry_relative_strength = np.nan
            result.industry_momentum_60d = np.nan
            result.sector_confirmation_factor = 1.0
            continue
        total_return, count = industry_totals.get(classification, (0.0, 0))
        peer = (
            (total_return - value) / (count - 1)
            if np.isfinite(value) and count >= 2
            else np.nan
        )
        result.industry_relative_strength = (
            round(value - peer, 2)
            if np.isfinite(value) and np.isfinite(peer)
            else np.nan
        )
        result.industry_momentum_60d = (
            round(peer, 2) if np.isfinite(peer) else np.nan
        )
        if np.isfinite(peer):
            relative_strength = value - peer if np.isfinite(value) else np.nan
            result.sector_confirmation_factor = _core._sector_confirmation_factor(
                peer, relative_strength
            )
        else:
            result.sector_confirmation_factor = 1.0

    for result in results:
        base_score = _core._finite_float(result.failure_adjusted_score)
        if not np.isfinite(base_score):
            base_score = _core._finite_float(result.final_score)
        if not np.isfinite(base_score):
            base_score = _core._finite_float(result.score.total, 0.0)
        sector_factor = float(
            np.clip(
                _core._finite_float(result.sector_confirmation_factor, 1.0),
                0.0,
                1.0,
            )
        )
        breakout_factor = float(
            np.clip(
                _core._finite_float(result.breakout_quality_factor, 1.0),
                0.0,
                1.0,
            )
        )
        breakout_state = str(result.entry_signal or "").upper() in {
            "BREAKOUT_CONFIRM",
            "PRICE_BREAKOUT",
            "WAIT_VOLUME_CONFIRM",
        }
        effective_breakout_factor = breakout_factor if breakout_state else 1.0
        technical_score = (
            base_score
            * (0.7 + 0.3 * sector_factor)
            * (0.8 + 0.2 * effective_breakout_factor)
        )
        result.technical_institutional_score = round(technical_score, 4)
        quality_adjusted = _core._quality_adjusted_score(
            technical_score,
            result.quality_score,
            result.quality_data_available,
            result.is_etf,
        )
        result.institutional_score = round(quality_adjusted, 4)


def _select_exact_refinement_pool(
    frame: pd.DataFrame,
    fast_rows: list[dict[str, object]],
    top_n: int = 50,
) -> pd.DataFrame:
    """Vectorized equivalent of the stable FAST→EXACT promotion policy."""
    if frame.empty:
        return frame.head(0).copy()

    working = frame.copy()
    working["_TickerKey"] = (
        working.get("Ticker", pd.Series("", index=working.index))
        .fillna("")
        .astype(str)
        .str.strip()
    )
    working["_CurrentSignal"] = (
        working.get("EntrySignal", pd.Series("UNKNOWN", index=working.index))
        .fillna("UNKNOWN")
        .astype(str)
        .str.strip()
        .str.upper()
        .replace("", "UNKNOWN")
    )
    working["_Eligibility"] = (
        working.get("RankingEligibility", pd.Series("观察", index=working.index))
        .fillna("观察")
        .astype(str)
        .str.strip()
    )
    working["_RefineMetric"] = pd.to_numeric(
        working.get(
            "RankingScore",
            working.get(
                "InstitutionalScore",
                working.get("FinalScore", pd.Series(np.nan, index=working.index)),
            ),
        ),
        errors="coerce",
    ).replace([np.inf, -np.inf], np.nan).fillna(-np.inf)

    fast = pd.DataFrame.from_records(fast_rows)
    if fast.empty or "ticker" not in fast.columns:
        working["_FastSamples"] = 0
        working["_FastEffectiveSamples"] = np.nan
    else:
        fast = fast.copy()
        fast["_TickerKey"] = (
            fast["ticker"].fillna("").astype(str).str.strip()
        )
        fast = fast.loc[fast["_TickerKey"].ne("")].copy()
        fast["_SignalKey"] = (
            fast.get("entry_signal", pd.Series("UNKNOWN", index=fast.index))
            .fillna("UNKNOWN")
            .astype(str)
            .str.strip()
            .str.upper()
            .replace("", "UNKNOWN")
        )
        fast["_Samples"] = (
            pd.to_numeric(
                fast.get("samples", pd.Series(0.0, index=fast.index)),
                errors="coerce",
            )
            .fillna(0.0)
            .clip(lower=0.0)
            .astype(np.int64)
        )
        fast["_Effective"] = pd.to_numeric(
            fast.get("effective_samples", pd.Series(np.nan, index=fast.index)),
            errors="coerce",
        ).replace([np.inf, -np.inf], np.nan).clip(lower=0.0)

        key_stats = (
            fast.groupby(["_TickerKey", "_SignalKey"], sort=False, as_index=False)
            .agg(_KeySamples=("_Samples", "max"), _KeyEffective=("_Effective", "max"))
        )
        ticker_stats = (
            fast.groupby("_TickerKey", sort=False, as_index=False)
            .agg(
                _TickerSamples=("_Samples", "max"),
                _TickerEffective=("_Effective", "max"),
            )
        )
        working = working.merge(
            key_stats,
            left_on=["_TickerKey", "_CurrentSignal"],
            right_on=["_TickerKey", "_SignalKey"],
            how="left",
            validate="many_to_one",
        ).merge(
            ticker_stats,
            on="_TickerKey",
            how="left",
            validate="many_to_one",
        )
        working["_FastSamples"] = (
            working["_KeySamples"]
            .fillna(working["_TickerSamples"])
            .fillna(0.0)
            .astype(np.int64)
        )
        working["_FastEffectiveSamples"] = working["_KeyEffective"].fillna(
            working["_TickerEffective"]
        )
        working = working.drop(
            columns=[
                "_SignalKey",
                "_KeySamples",
                "_KeyEffective",
                "_TickerSamples",
                "_TickerEffective",
            ],
            errors="ignore",
        )

    ranked = (
        working.loc[~working["_Eligibility"].eq("风险过滤")]
        .sort_values("_RefineMetric", ascending=False, kind="mergesort")
        .copy()
    )
    if ranked.empty:
        return ranked.drop(columns="_TickerKey", errors="ignore")
    ranked["_RefineRank"] = np.arange(1, len(ranked) + 1)
    ranked["_PriorityEligibility"] = ranked["_Eligibility"].isin(
        {"推荐", "谨慎候选"}
    )
    minimum_fast_samples = _core._minimum_fast_samples_for_exact_refinement()
    top_limit = max(1, int(top_n))
    candidate_cap = max(
        1,
        min(int(_core.BACKTEST_EXACT_REFINEMENT_CANDIDATES), top_limit),
    )
    selected = ranked.loc[
        ranked["_FastSamples"].ge(minimum_fast_samples)
        & (
            ranked["_FastEffectiveSamples"].isna()
            | ranked["_FastEffectiveSamples"].ge(
                _core.BACKTEST_MIN_SAMPLES_FOR_RANKING
            )
        )
        & (ranked["_PriorityEligibility"] | ranked["_RefineRank"].le(top_limit))
    ].copy()
    return (
        selected.sort_values(
            ["_PriorityEligibility", "_RefineMetric"],
            ascending=[False, False],
            kind="mergesort",
        )
        .head(candidate_cap)
        .drop(columns="_TickerKey", errors="ignore")
        .copy()
    )


def _apply_backtest_freshness(
    frame: pd.DataFrame,
    summary: object,
) -> pd.DataFrame:
    """Bulk freshness classification with calendar work deduplicated by date pair."""
    result = frame.copy()
    requested = result.get(
        "BacktestRequested", pd.Series(False, index=result.index)
    ).fillna(False).astype(bool)
    legacy_cutoff = (
        result.get("BacktestLastEvaluatedDate", pd.Series("", index=result.index))
        .fillna("")
        .astype(str)
        .str.strip()
    )
    explicit_cutoff = (
        result.get("BacktestDataCutoffDate", pd.Series("", index=result.index))
        .fillna("")
        .astype(str)
        .str.strip()
    )
    cutoff = explicit_cutoff.where(explicit_cutoff.ne(""), legacy_cutoff)
    run_cutoff = str((getattr(summary, "split_dates", {}) or {}).get("global_end") or "")
    cutoff = cutoff.where(cutoff.ne("") | ~requested, run_cutoff)
    result["BacktestDataCutoffDate"] = cutoff
    result["BacktestLastEvaluatedDate"] = cutoff

    data_asof = pd.to_datetime(
        result.get("DataAsOf", pd.Series(pd.NaT, index=result.index)),
        errors="coerce",
    ).dt.normalize()
    cutoff_dates = pd.to_datetime(cutoff, errors="coerce").dt.normalize()
    delayed_limit = max(0, int(_core.BACKTEST_FRESHNESS_DELAYED_TRADING_DAYS))
    stale_limit = max(
        delayed_limit, int(_core.BACKTEST_FRESHNESS_STALE_TRADING_DAYS)
    )

    valid_pair = requested & cutoff_dates.notna() & data_asof.notna()
    future = valid_pair & cutoff_dates.gt(data_asof)
    normal = valid_pair & ~future
    gap = pd.Series(np.nan, index=result.index, dtype=float)
    gap.loc[future] = 0.0

    if normal.any():
        pairs = pd.DataFrame(
            {
                "cutoff": cutoff_dates.loc[normal],
                "asof": data_asof.loc[normal],
            }
        ).drop_duplicates()
        pairs["gap"] = [
            float(_core._trading_days_between(cutoff.date(), asof.date()))
            for cutoff, asof in zip(pairs["cutoff"], pairs["asof"])
        ]
        pair_index = pd.MultiIndex.from_frame(pairs[["cutoff", "asof"]])
        pair_values = pd.Series(pairs["gap"].to_numpy(), index=pair_index)
        row_index = pd.MultiIndex.from_arrays(
            [cutoff_dates.loc[normal], data_asof.loc[normal]]
        )
        gap.loc[normal] = pair_values.reindex(row_index).to_numpy(dtype=float)

    missing_cutoff = requested & cutoff_dates.isna()
    missing_asof = requested & cutoff_dates.notna() & data_asof.isna()
    sync = normal & gap.le(delayed_limit)
    delayed = normal & gap.gt(delayed_limit) & gap.le(stale_limit)
    stale = normal & gap.gt(stale_limit)

    status = pd.Series("未知", index=result.index, dtype=object)
    status.loc[~requested] = "未请求"
    status.loc[future] = "异常"
    status.loc[sync] = "同步"
    status.loc[delayed] = "延迟"
    status.loc[stale] = "过期"

    cutoff_text = cutoff_dates.dt.strftime("%Y-%m-%d").fillna("")
    asof_text = data_asof.dt.strftime("%Y-%m-%d").fillna("")
    gap_text = gap.fillna(0.0).astype(int).astype(str)
    reason = pd.Series("", index=result.index, dtype=object)
    reason.loc[~requested] = "本标的不在本轮回测范围"
    reason.loc[missing_cutoff] = "未取得回测基准数据截止日"
    reason.loc[missing_asof] = "行情数据日期缺失，无法判断回测时效"
    reason.loc[future] = (
        "回测基准数据截止日 "
        + cutoff_text.loc[future]
        + " 晚于行情日期 "
        + asof_text.loc[future]
    )
    reason.loc[sync] = (
        "回测基准数据截至 "
        + cutoff_text.loc[sync]
        + "，与行情日期相差 "
        + gap_text.loc[sync]
        + " 个交易日"
    )
    reason.loc[delayed] = (
        "回测基准数据比行情日期落后 "
        + gap_text.loc[delayed]
        + " 个交易日，仅作历史校准"
    )
    reason.loc[stale] = (
        "回测基准数据比行情日期落后 "
        + gap_text.loc[stale]
        + " 个交易日，请刷新基准缓存后重跑"
    )

    result["BacktestFreshnessTradingDays"] = gap
    result["BacktestFreshnessStatus"] = status
    result["BacktestFreshnessReason"] = reason
    return result


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _backtest_acceleration.install()
    _score_acceleration.install()
    _core.enrich_results = enrich_results
    _core._select_exact_refinement_pool = _select_exact_refinement_pool
    _core._apply_backtest_freshness = _apply_backtest_freshness
    _core.ANALYTICS_BULK_ACCELERATION_VERSION = ANALYTICS_BULK_ACCELERATION_VERSION
    _INSTALLED = True


install()
