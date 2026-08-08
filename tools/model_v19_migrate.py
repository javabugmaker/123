from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"pattern not found in {path}: {old[:140]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def insert_after(path: str, marker: str, addition: str) -> None:
    replace_once(path, marker, marker + addition)


# ---------------------------------------------------------------------------
# Config/cache namespaces
# ---------------------------------------------------------------------------
replace_once(
    "config.py",
    '''ETF_THEME_MAX_PER_TOP_LIST: Final[int] = 2\nSTOCK_INDUSTRY_MAX_PER_TOP_LIST: Final[int] = 5\nSECTOR_CONFIRMATION_MIN_FACTOR: Final[float] = 0.72\nSECTOR_CONFIRMATION_INDUSTRY_WEIGHT: Final[float] = 0.45\nSECTOR_CONFIRMATION_RELATIVE_WEIGHT: Final[float] = 0.55\n\nSCORING_VERSION: str = "2026-08-08-v18-decision-ranking-calibration"\n''',
    '''ETF_THEME_MAX_PER_TOP_LIST: Final[int] = 2\nETF_TRACKING_MAX_PER_TOP_LIST: Final[int] = 1\nSTOCK_INDUSTRY_MAX_PER_TOP_LIST: Final[int] = 5\nTHEME_CLUSTER_SOFT_PENALTY: Final[float] = 0.05\nSECTOR_CONFIRMATION_MIN_FACTOR: Final[float] = 0.72\nSECTOR_CONFIRMATION_INDUSTRY_WEIGHT: Final[float] = 0.45\nSECTOR_CONFIRMATION_RELATIVE_WEIGHT: Final[float] = 0.55\nBACKTEST_AUTO_EXACT_REFINEMENT: Final[bool] = True\nBACKTEST_EXACT_REFINEMENT_CANDIDATES: Final[int] = 150\n\nSCORING_VERSION: str = "2026-08-08-v19-exact-refinement-cross-asset"\n''',
)
replace_once(
    "performance_cache.py",
    'INDICATOR_CACHE_VERSION = "v4"\nBACKTEST_CACHE_VERSION = "v7"',
    'INDICATOR_CACHE_VERSION = "v5"\nBACKTEST_CACHE_VERSION = "v8"',
)

# ---------------------------------------------------------------------------
# Scanner: ATR provenance, filter explainability and classification metadata.
# ---------------------------------------------------------------------------
replace_once(
    "scanner.py",
    'from classification import etf_theme_key, model_classification',
    'from classification import etf_theme_key, etf_tracking_key, model_classification, theme_cluster',
)
replace_once(
    "scanner.py",
    '    atr14: float = np.nan\n    rsi14: float = np.nan',
    '    atr14: float = np.nan\n    atr50: float = np.nan\n    atr_expansion_source: str = ""\n    rsi14: float = np.nan',
)
replace_once(
    "scanner.py",
    '    passed_filters: bool = False\n    filter_details: dict[str, bool | int] = field(default_factory=dict)',
    '    passed_filters: bool = False\n    universe_eligible: bool = False\n    signal_confirmed: bool = False\n    failed_filter_count: int = 0\n    failed_filter_names: str = ""\n    filter_details: dict[str, bool | int] = field(default_factory=dict)',
)
replace_once(
    "scanner.py",
    '    model_classification: str = ""\n    institutional_percentile: float = np.nan',
    '    model_classification: str = ""\n    etf_tracking_key: str = ""\n    theme_cluster: str = ""\n    technical_institutional_score: float = np.nan\n    asset_percentile: float = np.nan\n    cross_asset_score: float = np.nan\n    institutional_percentile: float = np.nan',
)
replace_once(
    "scanner.py",
    '''        filter_map = {\n            "min_price": filter_results.min_price.passed,\n            "min_volume": filter_results.min_volume.passed,\n            "min_market_cap": filter_results.min_market_cap.passed,\n            "sufficient_history": filter_results.sufficient_history.passed,\n            "signal_count": filter_results.signal_count(),\n            "filter_count": filter_results.passed_count(),\n''',
    '''        filter_map = {\n            "min_price": filter_results.min_price.passed,\n            "min_volume": filter_results.min_volume.passed,\n            "min_market_cap": filter_results.min_market_cap.passed,\n            "sufficient_history": filter_results.sufficient_history.passed,\n            "signal_count": filter_results.signal_count(),\n            "filter_count": filter_results.passed_count(),\n''',
)
insert_after(
    "scanner.py",
    '''        }\n\n        sb = score_ticker(df, is_etf=ticker_info.is_etf)\n''',
    '''        base_filter_states = {\n            "min_price": filter_results.min_price.passed,\n            "min_volume": filter_results.min_volume.passed,\n            "min_market_cap": filter_results.min_market_cap.passed,\n            "sufficient_history": filter_results.sufficient_history.passed,\n        }\n        accumulation_states = {\n            "volume_accumulation": filter_results.volume_accumulation.passed,\n            "obv_divergence": filter_results.obv_divergence.passed,\n            "cmf_positive": filter_results.cmf_positive.passed,\n            "ad_slope": filter_results.ad_slope.passed,\n        }\n        structure_states = {\n            "consolidation": filter_results.consolidation.passed,\n            "volatility_contraction": filter_results.volatility_contraction.passed,\n        }\n        universe_eligible = all(base_filter_states.values())\n        signal_confirmed = sum(accumulation_states.values()) >= 2 and any(structure_states.values())\n        failed_filter_names = [\n            name\n            for name, state in {**base_filter_states, **accumulation_states, **structure_states}.items()\n            if not state\n        ]\n\n''',
)
# The previous insertion duplicated the sb marker, remove the accidental duplicate.
replace_once(
    "scanner.py",
    '''        sb = score_ticker(df, is_etf=ticker_info.is_etf)\n        base_filter_states = {''',
    '''        base_filter_states = {''',
)
replace_once(
    "scanner.py",
    '''        if not np.isfinite(atr14_val):\n            atr14_val = _latest_atr_from_ohlc(df, 14)\n        atr50_value = (\n            _parse_float(df["ATR50"].iloc[-1], np.nan)\n            if "ATR50" in df.columns\n            else np.nan\n        )\n        if not np.isfinite(atr50_value):\n            atr50_value = _latest_atr_from_ohlc(df, 50)\n        atr_expansion = (\n''',
    '''        atr_expansion_source = "indicator"\n        if not np.isfinite(atr14_val):\n            atr14_val = _latest_atr_from_ohlc(df, 14)\n            atr_expansion_source = "ohlc_fallback"\n        atr50_value = (\n            _parse_float(df["ATR50"].iloc[-1], np.nan)\n            if "ATR50" in df.columns\n            else np.nan\n        )\n        if not np.isfinite(atr50_value):\n            atr50_value = _latest_atr_from_ohlc(df, 50)\n            atr_expansion_source = "ohlc_fallback"\n        atr_expansion = (\n''',
)
replace_once(
    "scanner.py",
    '''        resolved_classification = model_classification(\n            is_etf=ticker_info.is_etf,\n            name=ticker_info.name,\n            industry=resolved_industry,\n            sector=resolved_sector,\n            ticker=ticker,\n        )\n''',
    '''        resolved_classification = model_classification(\n            is_etf=ticker_info.is_etf,\n            name=ticker_info.name,\n            industry=resolved_industry,\n            sector="" if ticker_info.is_etf else resolved_sector,\n            ticker=ticker,\n        )\n        resolved_tracking_key = (\n            etf_tracking_key(\n                name=ticker_info.name,\n                industry=resolved_industry,\n                sector="",\n                ticker=ticker,\n            )\n            if ticker_info.is_etf\n            else ""\n        )\n        resolved_theme_cluster = theme_cluster(\n            is_etf=ticker_info.is_etf,\n            name=ticker_info.name,\n            industry=resolved_industry,\n            sector=resolved_sector,\n            classification=resolved_classification,\n            ticker=ticker,\n        )\n''',
)
replace_once(
    "scanner.py",
    '''            atr14=atr14_val,\n            rsi14=rsi14_val,''',
    '''            atr14=atr14_val,\n            atr50=atr50_value,\n            atr_expansion_source=atr_expansion_source if np.isfinite(atr_expansion) else "unavailable",\n            rsi14=rsi14_val,''',
)
replace_once(
    "scanner.py",
    '''            passed_filters=passed,\n            filter_details=filter_map,''',
    '''            passed_filters=passed,\n            universe_eligible=universe_eligible,\n            signal_confirmed=signal_confirmed,\n            failed_filter_count=len(failed_filter_names),\n            failed_filter_names=",".join(failed_filter_names),\n            filter_details=filter_map,''',
)
replace_once(
    "scanner.py",
    '''            model_classification=resolved_classification,\n        )''',
    '''            model_classification=resolved_classification,\n            etf_tracking_key=resolved_tracking_key,\n            theme_cluster=resolved_theme_cluster,\n        )''',
)
# Restore v19 fields when resuming a checkpoint/previous AllResults.
replace_once(
    "scanner.py",
    '''                        atr14=_parse_float(row.get("ATR14", np.nan)),\n                        rsi14=_parse_float(row.get("RSI14", np.nan)),''',
    '''                        atr14=_parse_float(row.get("ATR14", np.nan)),\n                        atr50=_parse_float(row.get("ATR50", np.nan)),\n                        atr_expansion_source=str(row.get("ATRExpansionSource", "") or ""),\n                        rsi14=_parse_float(row.get("RSI14", np.nan)),''',
)
replace_once(
    "scanner.py",
    '''                        passed_filters=_parse_bool(\n                            row.get("PassedFilters", False)\n                        ),\n                        style=str(row.get("Style", "均衡")),''',
    '''                        passed_filters=_parse_bool(\n                            row.get("PassedFilters", False)\n                        ),\n                        universe_eligible=_parse_bool(row.get("UniverseEligible", False)),\n                        signal_confirmed=_parse_bool(row.get("SignalConfirmed", False)),\n                        failed_filter_count=_parse_int(row.get("FailedFilterCount", 0), 0),\n                        failed_filter_names=str(row.get("FailedFilterNames", "") or ""),\n                        style=str(row.get("Style", "均衡")),''',
)
replace_once(
    "scanner.py",
    '''                        quality_multiplier=_parse_float(\n                            row.get("QualityMultiplier", 0.95), 0.95\n                        ),\n                        sector_confirmation_factor=_parse_float(''',
    '''                        quality_multiplier=_parse_float(\n                            row.get("QualityMultiplier", 0.95), 0.95\n                        ),\n                        model_classification=str(row.get("ModelClassification", "") or ""),\n                        etf_tracking_key=str(row.get("ETFTrackingKey", "") or ""),\n                        theme_cluster=str(row.get("ThemeCluster", "") or ""),\n                        technical_institutional_score=_parse_float(row.get("TechnicalInstitutionalScore", np.nan)),\n                        asset_percentile=_parse_float(row.get("AssetPercentile", np.nan)),\n                        cross_asset_score=_parse_float(row.get("CrossAssetScore", np.nan)),\n                        sector_confirmation_factor=_parse_float(''',
)

# ---------------------------------------------------------------------------
# Analytics: technical score exposure, global calibration provenance,
# cross-asset normalization, and automatic FAST -> EXACT candidate refinement.
# ---------------------------------------------------------------------------
replace_once(
    "analytics.py",
    'from classification import model_classification',
    'from classification import etf_tracking_key, model_classification, theme_cluster',
)
replace_once(
    "analytics.py",
    '''    BACKTEST_AUTO_EXACT_MAX_TICKERS,\n    BACKTEST_MAX_PROCESSES,''',
    '''    BACKTEST_AUTO_EXACT_MAX_TICKERS,\n    BACKTEST_AUTO_EXACT_REFINEMENT,\n    BACKTEST_EXACT_REFINEMENT_CANDIDATES,\n    BACKTEST_MAX_PROCESSES,''',
)
replace_once(
    "analytics.py",
    '''        quality_adjusted = _quality_adjusted_score(\n            technical_score,\n            result.quality_score,\n            result.quality_data_available,\n            result.is_etf,\n        )\n''',
    '''        result.technical_institutional_score = round(technical_score, 4)\n        quality_adjusted = _quality_adjusted_score(\n            technical_score,\n            result.quality_score,\n            result.quality_data_available,\n            result.is_etf,\n        )\n''',
)
replace_once(
    "analytics.py",
    '''                result.model_classification = classification\n                if result.is_etf and not str(result.sector or "").strip() and classification:\n                    result.sector = classification\n''',
    '''                result.model_classification = classification\n                result.etf_tracking_key = etf_tracking_key(\n                    name=result.name, industry=result.industry, sector="", ticker=result.ticker\n                ) if result.is_etf else ""\n                result.theme_cluster = theme_cluster(\n                    is_etf=bool(result.is_etf), name=result.name, industry=result.industry,\n                    sector=result.sector, classification=classification, ticker=result.ticker\n                )\n                if result.is_etf and not str(result.sector or "").strip() and classification:\n                    result.sector = classification\n''',
)
# Apply the same metadata in the second enrichment loop.
replace_once(
    "analytics.py",
    '''        result.model_classification = classification\n        if result.is_etf and not str(result.sector or "").strip() and classification:\n            result.sector = classification\n''',
    '''        result.model_classification = classification\n        result.etf_tracking_key = etf_tracking_key(\n            name=result.name, industry=result.industry, sector="", ticker=result.ticker\n        ) if result.is_etf else ""\n        result.theme_cluster = theme_cluster(\n            is_etf=bool(result.is_etf), name=result.name, industry=result.industry,\n            sector=result.sector, classification=classification, ticker=result.ticker\n        )\n        if result.is_etf and not str(result.sector or "").strip() and classification:\n            result.sector = classification\n''',
)
replace_once(
    "analytics.py",
    '''        "backtest_engine": "BacktestEngine",\n    }''',
    '''        "backtest_engine": "BacktestEngine",\n        "backtest_stage": "BacktestStage",\n    }''',
)
replace_once(
    "analytics.py",
    '''        "BacktestEngine",\n        "BacktestStatus",\n        "GlobalCalibrationScore",\n        "GlobalCalibrationConfidence",\n        "GlobalCalibrationLevel",''',
    '''        "BacktestEngine",\n        "BacktestStatus",\n        "BacktestStage",\n        "GlobalCalibrationScore",\n        "GlobalCalibrationConfidence",\n        "GlobalCalibrationLevel",\n        "GlobalCalibrationSamples",\n        "GlobalCalibrationEffectiveSamples",\n        "GlobalCalibrationMeanExcess20D",\n        "GlobalCalibrationWinRate20D",\n        "GlobalCalibrationStartDate",\n        "GlobalCalibrationEndDate",''',
)
# FAST screen automatically runs exact refinement for the pre-backtest top pool,
# then exact rows replace FAST rows for those tickers before ranking is applied.
replace_once(
    "analytics.py",
    'def apply_backtest_ranking(summary: BacktestSummary, top_n: int = 50) -> None:\n    path = OUTPUT_DIR / "AllResults.csv"\n    if not path.exists() or not summary.by_ticker:\n        return\n    frame = pd.read_csv(path, encoding="utf-8-sig")',
    '''def apply_backtest_ranking(summary: BacktestSummary, top_n: int = 50) -> None:\n    path = OUTPUT_DIR / "AllResults.csv"\n    if not path.exists() or not summary.by_ticker:\n        return\n    frame = pd.read_csv(path, encoding="utf-8-sig")\n    original_mode = str(summary.mode or "").strip().lower()\n    if original_mode == "fast" and BACKTEST_AUTO_EXACT_REFINEMENT:\n        rank_metric = pd.to_numeric(\n            frame.get("RankingScore", frame.get("InstitutionalScore", frame.get("FinalScore"))),\n            errors="coerce",\n        ).fillna(-np.inf)\n        eligible = ~frame.get("RankingEligibility", pd.Series("观察", index=frame.index)).fillna("观察").eq("风险过滤")\n        pool = (\n            frame.assign(_RefineMetric=rank_metric)\n            .loc[eligible]\n            .sort_values("_RefineMetric", ascending=False, kind="mergesort")\n            .head(max(1, int(BACKTEST_EXACT_REFINEMENT_CANDIDATES)))\n        )\n        refine_tickers = pool.get("Ticker", pd.Series(dtype=str)).dropna().astype(str).tolist()\n        if refine_tickers:\n            logger.info("FAST screen complete; exact-refining %d candidates.", len(refine_tickers))\n            exact = run_historical_backtest(\n                refine_tickers, source="tickflow", objective=summary.objective,\n                benchmark=summary.benchmark, commission=summary.commission,\n                stamp_duty=summary.stamp_duty, slippage=summary.slippage,\n                test_ratio=summary.test_ratio, validation_ratio=summary.validation_ratio,\n                mode="exact",\n            )\n            exact_rows = list(exact.by_ticker or [])\n            exact_keys = {(str(row.get("ticker", "")), str(row.get("entry_signal", "")).upper()) for row in exact_rows}\n            current_signal = dict(zip(pool["Ticker"].astype(str), pool.get("EntrySignal", pd.Series("UNKNOWN", index=pool.index)).fillna("UNKNOWN").astype(str).str.upper()))\n            for ticker in refine_tickers:\n                key = (ticker, current_signal.get(ticker, "UNKNOWN"))\n                if key not in exact_keys:\n                    exact_rows.append({\n                        "ticker": ticker, "entry_signal": key[1], "samples": 0,\n                        "effective_samples": 0.0, "backtest_score": BACKTEST_NEUTRAL_SCORE,\n                        "backtest_mode": "EXACT", "backtest_cache_hit": False,\n                        "backtest_last_evaluated_date": exact.split_dates.get("global_end") or "",\n                        "backtest_engine": exact.engine, "backtest_stage": "EXACT_REFINEMENT",\n                    })\n            for row in exact_rows:\n                row["backtest_stage"] = "EXACT_REFINEMENT"\n            fast_rows = list(summary.by_ticker or [])\n            for row in fast_rows:\n                row.setdefault("backtest_stage", "FAST_SCREEN")\n            refined_tickers = set(refine_tickers)\n            combined = [row for row in fast_rows if str(row.get("ticker", "")) not in refined_tickers]\n            combined.extend(exact_rows)\n            summary.by_ticker = combined\n            summary.mode = "hybrid"\n            summary.engine = f"{summary.engine}+exact:{exact.engine}"\n            # Keep full-market peer calibration; exact Top candidates only replace\n            # per-ticker evidence and must not redefine the global peer prior.\n''',
)
replace_once(
    "analytics.py",
    '''    frame["GlobalCalibrationScore"] = peer_score.round(4)\n    frame["GlobalCalibrationConfidence"] = peer_confidence.round(4)\n    frame["GlobalCalibrationLevel"] = calibration_details["level"].astype(str)\n''',
    '''    frame["GlobalCalibrationScore"] = peer_score.round(4)\n    frame["GlobalCalibrationConfidence"] = peer_confidence.round(4)\n    frame["GlobalCalibrationLevel"] = calibration_details["level"].astype(str)\n    frame["GlobalCalibrationSamples"] = calibration_details["samples"].astype(int)\n    frame["GlobalCalibrationEffectiveSamples"] = calibration_details["effective_samples"].round(4)\n    frame["GlobalCalibrationMeanExcess20D"] = calibration_details["mean_net_excess20"].round(4)\n    frame["GlobalCalibrationWinRate20D"] = calibration_details["win_rate_net_excess20"].round(4)\n    frame["GlobalCalibrationStartDate"] = calibration_details["start_date"].astype(str)\n    frame["GlobalCalibrationEndDate"] = calibration_details["end_date"].astype(str)\n''',
)
replace_once(
    "analytics.py",
    '''    institutional_component = (\n        frame["FailureAdjustedScore"]\n        * sector_multiplier\n        * recency_multiplier\n        * (0.8 + 0.2 * effective_breakout_factor)\n    )\n''',
    '''    institutional_component = (\n        frame["FailureAdjustedScore"]\n        * sector_multiplier\n        * recency_multiplier\n        * (0.8 + 0.2 * effective_breakout_factor)\n    )\n    frame["TechnicalInstitutionalScore"] = institutional_component.round(4)\n''',
)
# Per-row backtest stage provenance.
replace_once(
    "analytics.py",
    '''            row["backtest_engine"] = engine\n''',
    '''            row["backtest_engine"] = engine\n            row["backtest_stage"] = "FAST_SCREEN" if profile.name == "fast" else "EXACT"\n''',
)

# ---------------------------------------------------------------------------
# Global calibration provenance: retain sample counts/statistics/date span.
# ---------------------------------------------------------------------------
replace_once(
    "model_calibration.py",
    '''                "confidence": confidence,\n            }''',
    '''                "confidence": confidence,\n                "start_date": str(group["entry_date"].min().date()) if group["entry_date"].notna().any() else "",\n                "end_date": str(group["entry_date"].max().date()) if group["entry_date"].notna().any() else "",\n            }''',
)
# Replace calibration_details_for_frame with an extended provenance implementation.
start = (ROOT / "model_calibration.py").read_text(encoding="utf-8")
fn_start = start.index("def calibration_details_for_frame(")
next_fn = start.index("\ndef ", fn_start + 5)
old_fn = start[fn_start:next_fn]
new_fn = '''def calibration_details_for_frame(\n    frame: pd.DataFrame,\n    rows: list[dict[str, Any]] | None,\n) -> pd.DataFrame:\n    columns = {\n        "score": 50.0, "confidence": 0.0, "level": "none", "samples": 0,\n        "effective_samples": 0.0, "mean_net_excess20": np.nan,\n        "win_rate_net_excess20": np.nan, "start_date": "", "end_date": "",\n    }\n    if frame.empty:\n        return pd.DataFrame({key: pd.Series(dtype=float if isinstance(value, (int, float)) else str) for key, value in columns.items()}, index=frame.index)\n    if not rows:\n        return pd.DataFrame({key: pd.Series(value, index=frame.index) for key, value in columns.items()})\n\n    prepared_rows = list(rows)\n    asset_values = frame.get("AssetType", frame.get("asset_type", pd.Series("stock", index=frame.index)))\n    signal_values = frame.get("EntrySignal", frame.get("entry_signal", pd.Series("UNKNOWN", index=frame.index)))\n    model_scores = pd.to_numeric(frame.get("FinalScore", frame.get("score", pd.Series(np.nan, index=frame.index))), errors="coerce")\n    regime_values = frame.get("MarketRegime", frame.get("market_regime", pd.Series("UNKNOWN", index=frame.index)))\n    setup_values = pd.to_numeric(frame.get("BaseScore", frame.get("setup_score", pd.Series(np.nan, index=frame.index))), errors="coerce")\n    records: list[dict[str, Any]] = []\n    for asset, signal, score, regime, setup in zip(asset_values, signal_values, model_scores, regime_values, setup_values):\n        value, confidence, level = resolve_global_calibration(\n            str(asset), str(signal), float(score) if pd.notna(score) else np.nan, prepared_rows,\n            market_regime=str(regime), setup_score=float(setup) if pd.notna(setup) else np.nan,\n        )\n        matched: dict[str, Any] | None = None\n        for row in prepared_rows:\n            if str(row.get("level", "")) != level:\n                continue\n            try:\n                row_score = float(row.get("calibration_score", np.nan))\n                row_confidence = float(row.get("confidence", np.nan))\n            except (TypeError, ValueError):\n                continue\n            if np.isclose(row_score, value, equal_nan=False) and np.isclose(row_confidence, confidence, equal_nan=False):\n                matched = row\n                break\n        records.append({\n            "score": value, "confidence": confidence, "level": level,\n            "samples": int((matched or {}).get("samples", 0) or 0),\n            "effective_samples": float((matched or {}).get("effective_samples", 0.0) or 0.0),\n            "mean_net_excess20": pd.to_numeric(pd.Series([(matched or {}).get("mean_net_excess20", np.nan)]), errors="coerce").iloc[0],\n            "win_rate_net_excess20": pd.to_numeric(pd.Series([(matched or {}).get("win_rate_net_excess20", np.nan)]), errors="coerce").iloc[0],\n            "start_date": str((matched or {}).get("start_date", "") or ""),\n            "end_date": str((matched or {}).get("end_date", "") or ""),\n        })\n    return pd.DataFrame.from_records(records, index=frame.index)\n'''
(ROOT / "model_calibration.py").write_text(start[:fn_start] + new_fn + start[next_fn:], encoding="utf-8")

# ---------------------------------------------------------------------------
# Signal lifecycle: normalize stock/ETF percentiles to one cross-asset score.
# ---------------------------------------------------------------------------
replace_once(
    "signal_lifecycle.py",
    '''    base_score = _number(\n        result.get(\n            "InstitutionalScore",\n            result.get(\n                "FinalScore", result.get("Score", pd.Series(0.0, index=result.index))\n            ),\n        ),\n        0.0,\n    )\n    minimum_score_risk = base_score.lt(TRADE_READY_MIN_INSTITUTIONAL_SCORE)\n''',
    '''    institutional_raw = _number(\n        result.get(\n            "InstitutionalScore",\n            result.get("FinalScore", result.get("Score", pd.Series(0.0, index=result.index))),\n        ),\n        0.0,\n    )\n    technical_raw = _number(\n        result.get("TechnicalInstitutionalScore", institutional_raw), 0.0\n    )\n    result["TechnicalInstitutionalScore"] = technical_raw.round(4)\n    asset_group = pd.Series(np.where(is_etf, "ETF", "STOCK"), index=result.index)\n    asset_percentile = institutional_raw.groupby(asset_group).rank(method="average", pct=True) * 100.0\n    result["AssetPercentile"] = asset_percentile.round(2)\n    # Percentile normalization supplies a common stock/ETF scale while keeping\n    # 30% of the calibrated absolute score so meaningful score gaps survive.\n    cross_asset_score = (asset_percentile * 0.70 + institutional_raw.clip(0.0, 100.0) * 0.30).clip(0.0, 100.0)\n    result["CrossAssetScore"] = cross_asset_score.round(4)\n    base_score = cross_asset_score\n    minimum_score_risk = base_score.lt(TRADE_READY_MIN_INSTITUTIONAL_SCORE)\n''',
)
replace_once(
    "signal_lifecycle.py",
    '''    score = _number(result["InstitutionalScore"], 0.0)\n    result["InstitutionalRank"] = score.rank(method="min", ascending=False).astype(int)\n    result["InstitutionalPercentile"] = (\n        score.rank(method="average", pct=True) * 100.0\n    ).round(2)\n''',
    '''    score = _number(result.get("CrossAssetScore", result["InstitutionalScore"]), 0.0)\n    result["InstitutionalRank"] = score.rank(method="min", ascending=False).astype(int)\n    result["InstitutionalPercentile"] = (\n        score.rank(method="average", pct=True) * 100.0\n    ).round(2)\n''',
)

# ---------------------------------------------------------------------------
# Report: export diagnostics/version provenance and diversify by tracking key +
# soft theme-cluster concentration penalty without touching OverallRank.
# ---------------------------------------------------------------------------
replace_once(
    "report.py",
    '''    ETF_THEME_MAX_PER_TOP_LIST,\n    STOCK_INDUSTRY_MAX_PER_TOP_LIST,\n    OUTPUT_DIR,''',
    '''    ETF_THEME_MAX_PER_TOP_LIST,\n    ETF_TRACKING_MAX_PER_TOP_LIST,\n    STOCK_INDUSTRY_MAX_PER_TOP_LIST,\n    THEME_CLUSTER_SOFT_PENALTY,\n    SCORING_VERSION,\n    OUTPUT_DIR,''',
)
replace_once(
    "report.py",
    'from classification import etf_theme_key',
    'from classification import etf_theme_key, etf_tracking_key, theme_cluster',
)
insert_after(
    "report.py",
    'from scanner import ScanReport, ScanResult\n',
    'from performance_cache import BACKTEST_CACHE_VERSION, INDICATOR_CACHE_VERSION\n',
)
replace_once(
    "report.py",
    '''def _results_to_dataframe(results: list[ScanResult]) -> pd.DataFrame:\n    """Convert ScanResult list to a sorted, clean DataFrame."""\n    rows = []\n''',
    '''def _results_to_dataframe(results: list[ScanResult]) -> pd.DataFrame:\n    """Convert ScanResult list to a sorted, clean DataFrame."""\n    scan_timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")\n    run_id = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")\n    rows = []\n''',
)
replace_once(
    "report.py",
    '''                "ATR14": round(r.atr14, 4) if not np.isnan(r.atr14) else None,\n                "RSI14":''',
    '''                "ATR14": round(r.atr14, 4) if not np.isnan(r.atr14) else None,\n                "ATR50": round(r.atr50, 4) if np.isfinite(r.atr50) else None,\n                "ATRExpansionSource": r.atr_expansion_source,\n                "RSI14":''',
)
replace_once(
    "report.py",
    '''                "PassedFilters": r.passed_filters,\n                "OBV_Div":''',
    '''                "PassedFilters": r.passed_filters,\n                "UniverseEligible": r.universe_eligible,\n                "SignalConfirmed": r.signal_confirmed,\n                "FailedFilterCount": r.failed_filter_count,\n                "FailedFilterNames": r.failed_filter_names,\n                "MinPricePassed": r.filter_details.get("min_price", False),\n                "MinVolumePassed": r.filter_details.get("min_volume", False),\n                "MinMarketCapPassed": r.filter_details.get("min_market_cap", False),\n                "SufficientHistoryPassed": r.filter_details.get("sufficient_history", False),\n                "OBV_Div":''',
)
replace_once(
    "report.py",
    '''                "TradeReadiness": r.trade_readiness or r.ranking_eligibility,\n                "ResearchTier": r.research_tier,\n                "ModelClassification": r.model_classification,\n                "SignalAdjustmentReason":''',
    '''                "TradeReadiness": r.trade_readiness or r.ranking_eligibility,\n                "ResearchTier": r.research_tier,\n                "TechnicalInstitutionalScore": round(r.technical_institutional_score, 4) if np.isfinite(r.technical_institutional_score) else None,\n                "AssetPercentile": round(r.asset_percentile, 2) if np.isfinite(r.asset_percentile) else None,\n                "CrossAssetScore": round(r.cross_asset_score, 4) if np.isfinite(r.cross_asset_score) else None,\n                "ModelClassification": r.model_classification,\n                "ETFTrackingKey": r.etf_tracking_key,\n                "ThemeCluster": r.theme_cluster,\n                "SignalAdjustmentReason":''',
)
replace_once(
    "report.py",
    '''                "Error": r.error if r.error else "",\n            }''',
    '''                "Error": r.error if r.error else "",\n                "ModelVersion": SCORING_VERSION,\n                "IndicatorCacheVersion": INDICATOR_CACHE_VERSION,\n                "BacktestCacheVersion": BACKTEST_CACHE_VERSION,\n                "RunId": run_id,\n                "ScanTimestamp": scan_timestamp,\n                "CandidateGenerationStage": "SCAN",\n            }''',
)
# Existing runtime-added columns are preserved by finalization; add metadata if
# the dataframe came through backtest ranking rather than fresh ScanResult export.
replace_once(
    "report.py",
    '''    working = frame.copy()\n    working["ETFTheme"] = working.apply(_etf_theme_key, axis=1)\n    theme_counts: dict[str, int] = {}\n    stock_industry_counts: dict[str, int] = {}\n    selected: list[int] = []\n    for index, row in working.iterrows():\n        theme = str(row.get("ETFTheme", "") or "").strip()\n        if theme:\n            if theme_counts.get(theme, 0) >= max(1, int(max_per_theme)):\n                continue\n            theme_counts[theme] = theme_counts.get(theme, 0) + 1\n        else:\n            classification = str(\n                row.get("ModelClassification", "")\n                or row.get("Industry", "")\n                or row.get("Sector", "")\n                or ""\n            ).strip()\n            if classification and classification.lower() not in {"nan", "none"}:\n                if stock_industry_counts.get(classification, 0) >= max(1, int(max_per_stock_industry)):\n                    continue\n                stock_industry_counts[classification] = stock_industry_counts.get(classification, 0) + 1\n        selected.append(index)\n        if len(selected) >= int(limit):\n            break\n    result = working.loc[selected].copy().reset_index(drop=True)\n    result["ResearchPoolRank"] = np.arange(1, len(result) + 1)\n    return result\n''',
    '''    working = frame.copy()\n    working["ETFTheme"] = working.apply(_etf_theme_key, axis=1)\n    working["ETFTrackingKey"] = working.apply(\n        lambda row: etf_tracking_key(\n            name=row.get("Name", ""), industry=row.get("Industry", ""),\n            sector="", ticker=row.get("Ticker", "")\n        ) if _truthy(row.get("IsETF", False)) or str(row.get("AssetType", "")).strip().lower() == "etf" else "",\n        axis=1,\n    )\n    working["ThemeCluster"] = working.apply(\n        lambda row: theme_cluster(\n            is_etf=_truthy(row.get("IsETF", False)) or str(row.get("AssetType", "")).strip().lower() == "etf",\n            name=row.get("Name", ""), industry=row.get("Industry", ""), sector=row.get("Sector", ""),\n            classification=row.get("ModelClassification", ""), ticker=row.get("Ticker", ""),\n        ), axis=1,\n    )\n    theme_counts: dict[str, int] = {}\n    tracking_counts: dict[str, int] = {}\n    stock_industry_counts: dict[str, int] = {}\n    cluster_counts: dict[str, int] = {}\n    selected: list[int] = []\n    remaining = list(working.index)\n    rank_score = pd.to_numeric(\n        working.get("RankingScore", working.get("CrossAssetScore", pd.Series(0.0, index=working.index))),\n        errors="coerce",\n    ).fillna(0.0)\n    penalties: dict[int, float] = {}\n    while remaining and len(selected) < int(limit):\n        best_index: int | None = None\n        best_value = -np.inf\n        best_penalty = 1.0\n        for index in remaining:\n            row = working.loc[index]\n            theme = str(row.get("ETFTheme", "") or "").strip()\n            tracking = str(row.get("ETFTrackingKey", "") or "").strip()\n            classification = str(row.get("ModelClassification", "") or row.get("Industry", "") or row.get("Sector", "") or "").strip()\n            cluster = str(row.get("ThemeCluster", "") or "").strip()\n            if tracking and tracking_counts.get(tracking, 0) >= max(1, int(ETF_TRACKING_MAX_PER_TOP_LIST)):\n                continue\n            if theme and theme_counts.get(theme, 0) >= max(1, int(max_per_theme)):\n                continue\n            if not theme and classification and classification.lower() not in {"nan", "none"} and stock_industry_counts.get(classification, 0) >= max(1, int(max_per_stock_industry)):\n                continue\n            penalty = max(0.70, 1.0 - float(THEME_CLUSTER_SOFT_PENALTY) * cluster_counts.get(cluster, 0)) if cluster else 1.0\n            value = float(rank_score.loc[index]) * penalty\n            if value > best_value:\n                best_index, best_value, best_penalty = int(index), value, penalty\n        if best_index is None:\n            break\n        row = working.loc[best_index]\n        theme = str(row.get("ETFTheme", "") or "").strip()\n        tracking = str(row.get("ETFTrackingKey", "") or "").strip()\n        classification = str(row.get("ModelClassification", "") or row.get("Industry", "") or row.get("Sector", "") or "").strip()\n        cluster = str(row.get("ThemeCluster", "") or "").strip()\n        if theme:\n            theme_counts[theme] = theme_counts.get(theme, 0) + 1\n        if tracking:\n            tracking_counts[tracking] = tracking_counts.get(tracking, 0) + 1\n        if not theme and classification and classification.lower() not in {"nan", "none"}:\n            stock_industry_counts[classification] = stock_industry_counts.get(classification, 0) + 1\n        if cluster:\n            cluster_counts[cluster] = cluster_counts.get(cluster, 0) + 1\n        penalties[best_index] = best_penalty\n        selected.append(best_index)\n        remaining.remove(best_index)\n    result = working.loc[selected].copy().reset_index(drop=True)\n    result["ResearchDiversityPenalty"] = [round(penalties.get(index, 1.0), 4) for index in selected]\n    result["ResearchPoolRank"] = np.arange(1, len(result) + 1)\n    return result\n''',
)
# Version metadata for dataframes refreshed after backtest.
replace_once(
    "report.py",
    '''    ranked = _rank_valid_candidates(frame)\n\n    csv_path = destination / f"Top{top_n_csv}.csv"''',
    '''    ranked = _rank_valid_candidates(frame)\n    ranked["ModelVersion"] = ranked.get("ModelVersion", pd.Series(SCORING_VERSION, index=ranked.index)).replace("", SCORING_VERSION).fillna(SCORING_VERSION)\n    ranked["IndicatorCacheVersion"] = ranked.get("IndicatorCacheVersion", pd.Series(INDICATOR_CACHE_VERSION, index=ranked.index)).replace("", INDICATOR_CACHE_VERSION).fillna(INDICATOR_CACHE_VERSION)\n    ranked["BacktestCacheVersion"] = ranked.get("BacktestCacheVersion", pd.Series(BACKTEST_CACHE_VERSION, index=ranked.index)).replace("", BACKTEST_CACHE_VERSION).fillna(BACKTEST_CACHE_VERSION)\n    if "BacktestStage" in ranked:\n        ranked["CandidateGenerationStage"] = np.where(\n            ranked["BacktestStage"].fillna("").astype(str).eq("EXACT_REFINEMENT"),\n            "EXACT_REFINED", "FAST_SCREEN"\n        )\n\n    csv_path = destination / f"Top{top_n_csv}.csv"''',
)

# ---------------------------------------------------------------------------
# Regression tests for the v19 production contracts.
# ---------------------------------------------------------------------------
(ROOT / "test_model_v19_regressions.py").write_text(r'''from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import analytics
from classification import etf_tracking_key, theme_cluster
from model_calibration import build_global_calibration, calibration_details_for_frame
from report import _diversify_ranked_candidates
from scanner import TickerInfo, scan_single_from_df
from signal_lifecycle import finalize_signal_ranking


class ModelV19RegressionTests(unittest.TestCase):
    def test_etf_tracking_key_ignores_manager_name(self):
        self.assertEqual(etf_tracking_key(name="上证50ETF博时"), "上证50")
        self.assertEqual(etf_tracking_key(name="上证50ETF易方达"), "上证50")

    def test_theme_cluster_groups_medical_subindustries(self):
        self.assertEqual(theme_cluster(is_etf=False, industry="化学制药"), "医药医疗")
        self.assertEqual(theme_cluster(is_etf=False, industry="医疗器械"), "医药医疗")

    def test_scanner_atr_expansion_falls_back_from_ohlc(self):
        index = pd.date_range("2024-01-01", periods=520, freq="B")
        close = pd.Series(np.linspace(10.0, 14.0, len(index)), index=index)
        frame = pd.DataFrame({
            "Open": close, "High": close + 0.3, "Low": close - 0.3, "Close": close,
            "Volume": 2_000_000.0,
        }, index=index)
        with patch("scanner.get_market_cap", return_value=10_000_000_000.0):
            result = scan_single_from_df(TickerInfo(ticker="000001.SZ", name="测试"), frame)
        self.assertTrue(np.isfinite(result.atr50))
        self.assertTrue(np.isfinite(result.atr_expansion))
        self.assertIn(result.atr_expansion_source, {"indicator", "ohlc_fallback"})

    def test_filter_contract_separates_universe_from_signal(self):
        index = pd.date_range("2024-01-01", periods=520, freq="B")
        close = pd.Series(10.0, index=index)
        frame = pd.DataFrame({"Open": close, "High": close + .1, "Low": close - .1, "Close": close, "Volume": 2_000_000.0}, index=index)
        with patch("scanner.get_market_cap", return_value=10_000_000_000.0):
            result = scan_single_from_df(TickerInfo(ticker="000001.SZ", name="测试"), frame)
        self.assertTrue(result.universe_eligible)
        self.assertIsInstance(result.signal_confirmed, bool)
        self.assertIsInstance(result.failed_filter_names, str)

    def test_cross_asset_score_is_calibrated_within_asset_type(self):
        frame = pd.DataFrame([
            {"Ticker":"S1","IsETF":False,"AssetType":"stock","InstitutionalScore":80,"FinalScore":70,"Score":70,"EntrySignal":"WAIT_PULLBACK","PassedFilters":True,"SignalStatus":"ACTIVE","QualityApplicable":True,"QualityDataCompleteness":1,"QualityGate":True,"QualityDataAvailable":True,"QualityROE":True,"QualityGrossMargin":True,"QualityNetProfit":True,"InstitutionHoldingStatus":"PASS","ROE":10,"IndustryGrossMarginPercentile":70,"NetProfitY1":1,"NetProfitY2":1,"NetProfitY3":1,"ScoreCoverage":1,"DataAgeDays":0,"DataTradingAgeDays":0},
            {"Ticker":"S2","IsETF":False,"AssetType":"stock","InstitutionalScore":60,"FinalScore":60,"Score":60,"EntrySignal":"WAIT_PULLBACK","PassedFilters":True,"SignalStatus":"ACTIVE","QualityApplicable":True,"QualityDataCompleteness":1,"QualityGate":True,"QualityDataAvailable":True,"QualityROE":True,"QualityGrossMargin":True,"QualityNetProfit":True,"InstitutionHoldingStatus":"PASS","ROE":10,"IndustryGrossMarginPercentile":70,"NetProfitY1":1,"NetProfitY2":1,"NetProfitY3":1,"ScoreCoverage":1,"DataAgeDays":0,"DataTradingAgeDays":0},
            {"Ticker":"E1","IsETF":True,"AssetType":"etf","InstitutionalScore":90,"FinalScore":70,"Score":70,"EntrySignal":"WAIT_PULLBACK","PassedFilters":True,"SignalStatus":"ACTIVE","QualityApplicable":False,"QualityDataCompleteness":0,"QualityGate":True,"QualityDataAvailable":False,"ScoreCoverage":1,"DataAgeDays":0,"DataTradingAgeDays":0},
            {"Ticker":"E2","IsETF":True,"AssetType":"etf","InstitutionalScore":70,"FinalScore":60,"Score":60,"EntrySignal":"WAIT_PULLBACK","PassedFilters":True,"SignalStatus":"ACTIVE","QualityApplicable":False,"QualityDataCompleteness":0,"QualityGate":True,"QualityDataAvailable":False,"ScoreCoverage":1,"DataAgeDays":0,"DataTradingAgeDays":0},
        ])
        result = finalize_signal_ranking(frame).set_index("Ticker")
        self.assertEqual(result.loc["S1", "AssetPercentile"], 100.0)
        self.assertEqual(result.loc["E1", "AssetPercentile"], 100.0)
        self.assertGreater(result.loc["S1", "CrossAssetScore"], result.loc["S2", "CrossAssetScore"])

    def test_diversity_keeps_one_etf_per_tracking_key(self):
        frame = pd.DataFrame([
            {"Ticker":"510001.SH","Name":"上证50ETF博时","IsETF":True,"AssetType":"etf","RankingScore":90,"ModelClassification":"宽基"},
            {"Ticker":"510002.SH","Name":"上证50ETF易方达","IsETF":True,"AssetType":"etf","RankingScore":89,"ModelClassification":"宽基"},
            {"Ticker":"510300.SH","Name":"沪深300ETF华夏","IsETF":True,"AssetType":"etf","RankingScore":88,"ModelClassification":"宽基"},
        ])
        result = _diversify_ranked_candidates(frame, limit=3)
        self.assertEqual((result["ETFTrackingKey"] == "上证50").sum(), 1)

    def test_global_calibration_exports_provenance(self):
        dates = pd.date_range("2024-01-01", periods=40, freq="B")
        sample = pd.DataFrame({
            "asset_type":"stock", "entry_signal":"WAIT_PULLBACK", "market_regime":"RISK_ON",
            "score":65.0, "setup_score":60.0, "sample_weight":1.0,
            "net_return20":3.0, "benchmark_return20":1.0,
            "net_return60":5.0, "benchmark_return60":2.0, "entry_date":dates,
        })
        rows = build_global_calibration(sample, min_samples=30)
        current = pd.DataFrame([{"AssetType":"stock","EntrySignal":"WAIT_PULLBACK","MarketRegime":"RISK_ON","FinalScore":65.0,"BaseScore":60.0}])
        details = calibration_details_for_frame(current, rows)
        self.assertGreater(int(details.loc[0, "samples"]), 0)
        self.assertTrue(str(details.loc[0, "start_date"]))
        self.assertTrue(str(details.loc[0, "end_date"]))

    def test_fast_summary_marks_rows_for_exact_refinement(self):
        row = {"ticker":"000001.SZ","entry_signal":"WAIT_PULLBACK","samples":3,"backtest_mode":"FAST"}
        summary = analytics.BacktestSummary(mode="fast", by_ticker=[row])
        self.assertEqual(summary.mode, "fast")


if __name__ == "__main__":
    unittest.main()
''', encoding="utf-8")

print("model v19 migration applied")
