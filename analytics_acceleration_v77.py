"""v77 analytics/scoring orchestration acceleration.

The stable enrichment path already calculates each ticker's 60-day relative
return and classification in its threaded first pass, but then recalculates both
for every ticker in a second pass. Reuse those values. The module also installs
v77's equivalent NumPy score-availability fast path; no score formulas change.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd

import analytics_core as _core
import score_acceleration_v77 as _score_acceleration

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


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _score_acceleration.install()
    _core.enrich_results = enrich_results
    _INSTALLED = True


install()
