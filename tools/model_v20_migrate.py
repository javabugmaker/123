from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if old not in text:
        raise RuntimeError(f"pattern not found in {path}: {old[:180]!r}")
    write(path, text.replace(old, new, 1))


def regex_once(path: str, pattern: str, replacement: str) -> None:
    text = read(path)
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"regex matched {count} times in {path}: {pattern[:180]!r}")
    write(path, new_text)


# ---------------------------------------------------------------------------
# config.py — asset-specific universe liquidity + v20 version
# ---------------------------------------------------------------------------
replace_once(
    "config.py",
    '''MIN_PRICE: float = 5.0  # Minimum close price (CNY) — ignore penny stocks\nMAX_PRICE: float = 800.0  # Maximum close price for A-shares\nMIN_VOLUME: int = 200_000  # Minimum daily volume (shares)\nMIN_MARKET_CAP: float = 1e8  # Minimum market cap (CNY) — ignore micro-caps\n''',
    '''MIN_STOCK_PRICE: float = 5.0  # A-share floor; not applicable to ETF unit prices\nMIN_PRICE: float = MIN_STOCK_PRICE  # compatibility alias\nETF_MIN_PRICE: float = 0.10  # ETF units commonly trade below CNY 5 without implying penny-stock risk\nMAX_PRICE: float = 800.0  # Maximum close price for A-shares\nMIN_VOLUME: int = 200_000  # Compatibility fallback when Amount is unavailable\nMIN_STOCK_AVG_AMOUNT_60D: float = 1_000_000.0  # CNY average daily turnover\nMIN_ETF_AVG_AMOUNT_60D: float = 500_000.0  # CNY; ETF liquidity is evaluated independently\nMIN_MARKET_CAP: float = 1e8  # Minimum stock market cap (CNY); ETFs skip this gate\n''',
)
replace_once(
    "config.py",
    'SCORING_VERSION: str = "2026-08-08-v19-exact-refinement-cross-asset"',
    'SCORING_VERSION: str = "2026-08-08-v20-consistency-performance"',
)
replace_once(
    "performance_cache.py",
    'BACKTEST_CACHE_VERSION = "v8"',
    'BACKTEST_CACHE_VERSION = "v9"',
)

# ---------------------------------------------------------------------------
# filters.py — separate stock/ETF price floor and prefer turnover Amount
# ---------------------------------------------------------------------------
replace_once(
    "filters.py",
    '''    MAX_PRICE,\n    MIN_MARKET_CAP,\n    MIN_PRICE,\n    MIN_VOLUME,\n''',
    '''    ETF_MIN_PRICE,\n    MAX_PRICE,\n    MIN_ETF_AVG_AMOUNT_60D,\n    MIN_MARKET_CAP,\n    MIN_STOCK_AVG_AMOUNT_60D,\n    MIN_STOCK_PRICE,\n    MIN_VOLUME,\n''',
)
regex_once(
    "filters.py",
    r'def filter_min_price\(df: pd\.DataFrame\) -> FilterResult:.*?(?=\ndef filter_min_volume)',
    '''def filter_min_price(df: pd.DataFrame, *, is_etf: bool = False) -> FilterResult:\n    """Reject invalid prices using asset-specific floors.\n\n    The CNY 5 stock floor is a micro/penny-stock heuristic and must not be\n    applied to ETF unit NAV prices, which commonly trade below CNY 5.\n    """\n    close = pd.to_numeric(df["Close"], errors="coerce")\n    if close.empty or pd.isna(close.iloc[-1]) or close.iloc[-1] <= 0:\n        return FilterResult(passed=False, reason="最新收盘价无效")\n    close_value = float(close.iloc[-1])\n    minimum = float(ETF_MIN_PRICE if is_etf else MIN_STOCK_PRICE)\n    maximum = float(MAX_PRICE)\n    passed = minimum <= close_value <= maximum\n    asset = "ETF" if is_etf else "股票"\n    return FilterResult(\n        passed=passed,\n        reason=f"{asset}收盘价 {close_value:.2f} 元，要求范围 {minimum:.2f}-{maximum:.2f} 元",\n        details={"close": close_value, "minimum": minimum, "asset_type": "etf" if is_etf else "stock"},\n    )\n\n''',
)
regex_once(
    "filters.py",
    r'def filter_min_volume\(df: pd\.DataFrame\) -> FilterResult:.*?(?=\ndef filter_sufficient_history)',
    '''def filter_min_volume(df: pd.DataFrame, *, is_etf: bool = False) -> FilterResult:\n    """Liquidity gate: prefer 60-day average turnover Amount over share volume.\n\n    Share counts are not comparable across a CNY 0.5 ETF and a CNY 100 stock.\n    TickFlow normally provides Amount; legacy/synthetic frames fall back to the\n    historical MIN_VOLUME contract.\n    """\n    if "Amount" in df.columns:\n        amount = pd.to_numeric(df["Amount"], errors="coerce").replace([np.inf, -np.inf], np.nan)\n        avg_amount = amount.rolling(60, min_periods=30).mean().iloc[-1]\n        if np.isfinite(avg_amount):\n            threshold = float(MIN_ETF_AVG_AMOUNT_60D if is_etf else MIN_STOCK_AVG_AMOUNT_60D)\n            passed = float(avg_amount) >= threshold\n            return FilterResult(\n                passed=passed,\n                reason=f"60日平均成交额 {avg_amount:,.0f} {'>=' if passed else '<'} {threshold:,.0f} 元",\n                details={"avg_amount_60": float(avg_amount), "liquidity_metric": "amount", "threshold": threshold},\n            )\n\n    volume = pd.to_numeric(df["Volume"], errors="coerce").replace([np.inf, -np.inf], np.nan)\n    vol_avg = volume.rolling(60, min_periods=30).mean().iloc[-1]\n    if not np.isfinite(vol_avg):\n        return FilterResult(passed=False, reason="成交额/成交量数据不足或无效")\n    passed = float(vol_avg) >= MIN_VOLUME\n    return FilterResult(\n        passed=passed,\n        reason=f"AvgVol {vol_avg:,.0f} {'>=' if passed else '<'} MIN_VOLUME {MIN_VOLUME:,}（Amount缺失回退）",\n        details={"avg_volume_60": float(vol_avg), "liquidity_metric": "volume_fallback"},\n    )\n\n''',
)
replace_once(
    "filters.py",
    '''def run_all_filters(\n    df: pd.DataFrame,\n    market_cap: float | None = None,\n    require_market_cap: bool = True,\n) -> AllFilterResults:\n''',
    '''def run_all_filters(\n    df: pd.DataFrame,\n    market_cap: float | None = None,\n    require_market_cap: bool = True,\n    is_etf: bool = False,\n) -> AllFilterResults:\n''',
)
replace_once(
    "filters.py",
    '''        min_price=filter_min_price(df),\n        min_volume=filter_min_volume(df),\n''',
    '''        min_price=filter_min_price(df, is_etf=is_etf),\n        min_volume=filter_min_volume(df, is_etf=is_etf),\n''',
)
replace_once(
    "scanner.py",
    '''        filter_results = run_all_filters(\n            df,\n            market_cap=market_cap,\n            require_market_cap=not ticker_info.is_etf,\n        )\n''',
    '''        filter_results = run_all_filters(\n            df,\n            market_cap=market_cap,\n            require_market_cap=not ticker_info.is_etf,\n            is_etf=ticker_info.is_etf,\n        )\n''',
)

# ---------------------------------------------------------------------------
# analytics.py — ATR provenance, dataframe defragmentation, final EXACT pass
# ---------------------------------------------------------------------------
replace_once(
    "analytics.py",
    '''def _finite_float(value: Any, default: float = np.nan) -> float:\n    try:\n        parsed = float(value)\n    except (TypeError, ValueError):\n        return default\n    return parsed if np.isfinite(parsed) else default\n\n\n''',
    '''def _finite_float(value: Any, default: float = np.nan) -> float:\n    try:\n        parsed = float(value)\n    except (TypeError, ValueError):\n        return default\n    return parsed if np.isfinite(parsed) else default\n\n\ndef _latest_atr_from_ohlc(frame: pd.DataFrame, period: int) -> float:\n    if frame is None or len(frame) < period or not {"High", "Low", "Close"}.issubset(frame.columns):\n        return np.nan\n    high = pd.to_numeric(frame["High"], errors="coerce")\n    low = pd.to_numeric(frame["Low"], errors="coerce")\n    close = pd.to_numeric(frame["Close"], errors="coerce")\n    previous_close = close.shift(1)\n    true_range = pd.concat(\n        [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1\n    ).max(axis=1)\n    return _finite_float(true_range.rolling(period, min_periods=period).mean().iloc[-1])\n\n\ndef _non_exact_tickers(frame: pd.DataFrame) -> list[str]:\n    if frame is None or frame.empty or "Ticker" not in frame:\n        return []\n    mode = frame.get("BacktestMode", pd.Series("", index=frame.index)).fillna("").astype(str).str.upper()\n    return frame.loc[~mode.eq("EXACT"), "Ticker"].dropna().astype(str).drop_duplicates().tolist()\n\n\n''',
)
replace_once(
    "analytics.py",
    '''    atr14 = _finite_float(enriched["ATR14"].iloc[-1]) if "ATR14" in enriched else np.nan\n    atr50 = _finite_float(enriched["ATR50"].iloc[-1]) if "ATR50" in enriched else np.nan\n    result.atr_expansion = (\n        atr14 / atr50\n        if np.isfinite(atr14) and np.isfinite(atr50) and atr50 > 0\n        else np.nan\n    )\n''',
    '''    atr14 = _finite_float(enriched["ATR14"].iloc[-1]) if "ATR14" in enriched else np.nan\n    atr50 = _finite_float(enriched["ATR50"].iloc[-1]) if "ATR50" in enriched else np.nan\n    source = "indicator"\n    if not np.isfinite(atr14):\n        atr14 = _finite_float(getattr(result, "atr14", np.nan))\n        source = "scanner" if np.isfinite(atr14) else source\n    if not np.isfinite(atr50):\n        atr50 = _finite_float(getattr(result, "atr50", np.nan))\n        source = "scanner" if np.isfinite(atr50) else source\n    if not np.isfinite(atr14):\n        atr14 = _latest_atr_from_ohlc(enriched, 14)\n        source = "ohlc_fallback"\n    if not np.isfinite(atr50):\n        atr50 = _latest_atr_from_ohlc(enriched, 50)\n        source = "ohlc_fallback"\n    result.atr14 = atr14\n    result.atr50 = atr50\n    result.atr_expansion = (\n        atr14 / atr50\n        if np.isfinite(atr14) and np.isfinite(atr50) and atr50 > 0\n        else _finite_float(getattr(result, "atr_expansion", np.nan))\n    )\n    result.atr_expansion_source = source if np.isfinite(result.atr_expansion) else "unavailable"\n''',
)
replace_once(
    "analytics.py",
    'def apply_backtest_ranking(summary: BacktestSummary, top_n: int = 50) -> None:',
    'def apply_backtest_ranking(summary: BacktestSummary, top_n: int = 50, _reconcile_depth: int = 0) -> None:',
)
replace_once(
    "analytics.py",
    '''    prior_institutional_score = pd.to_numeric(\n        frame.get("InstitutionalScore", pd.Series(np.nan, index=frame.index)),\n        errors="coerce",\n    )\n''',
    '''    baseline_column = "PreBacktestInstitutionalScore"\n    if baseline_column in frame:\n        prior_institutional_score = pd.to_numeric(frame[baseline_column], errors="coerce")\n    else:\n        prior_institutional_score = pd.to_numeric(\n            frame.get("InstitutionalScore", pd.Series(np.nan, index=frame.index)),\n            errors="coerce",\n        )\n        frame[baseline_column] = prior_institutional_score\n''',
)
replace_once(
    "analytics.py",
    '''    frame = frame.merge(\n        metrics,\n        on=["Ticker", "EntrySignal"],\n        how="left",\n        validate="one_to_one",\n    )\n    for column in (\n        "BacktestSamples",\n        "BacktestEffectiveSamples",\n        "BacktestScore",\n        *metric_columns.values(),\n    ):\n        if column not in frame:\n            frame[column] = np.nan\n\n''',
    '''    frame = frame.merge(\n        metrics,\n        on=["Ticker", "EntrySignal"],\n        how="left",\n        validate="one_to_one",\n    )\n    required_metric_columns = list(dict.fromkeys((\n        "BacktestSamples", "BacktestEffectiveSamples", "BacktestScore", *metric_columns.values()\n    )))\n    missing_metric_columns = [column for column in required_metric_columns if column not in frame]\n    if missing_metric_columns:\n        frame = pd.concat(\n            [frame, pd.DataFrame(np.nan, index=frame.index, columns=missing_metric_columns)], axis=1\n        )\n    # Consolidate pandas blocks once before the derived ranking columns are added.\n    frame = frame.copy()\n\n''',
)
replace_once(
    "analytics.py",
    '''    frame["GlobalCalibrationScore"] = peer_score.round(4)\n    frame["GlobalCalibrationConfidence"] = peer_confidence.round(4)\n    frame["GlobalCalibrationLevel"] = calibration_details["level"].astype(str)\n    frame["GlobalCalibrationSamples"] = calibration_details["samples"].astype(int)\n    frame["GlobalCalibrationEffectiveSamples"] = calibration_details["effective_samples"].round(4)\n    frame["GlobalCalibrationMeanExcess20D"] = calibration_details["mean_net_excess20"].round(4)\n    frame["GlobalCalibrationWinRate20D"] = calibration_details["win_rate_net_excess20"].round(4)\n    frame["GlobalCalibrationStartDate"] = calibration_details["start_date"].astype(str)\n    frame["GlobalCalibrationEndDate"] = calibration_details["end_date"].astype(str)\n''',
    '''    calibration_columns = pd.DataFrame({\n        "GlobalCalibrationScore": peer_score.round(4),\n        "GlobalCalibrationConfidence": peer_confidence.round(4),\n        "GlobalCalibrationLevel": calibration_details["level"].astype(str),\n        "GlobalCalibrationSamples": calibration_details["samples"].astype(int),\n        "GlobalCalibrationEffectiveSamples": calibration_details["effective_samples"].round(4),\n        "GlobalCalibrationMeanExcess20D": calibration_details["mean_net_excess20"].round(4),\n        "GlobalCalibrationWinRate20D": calibration_details["win_rate_net_excess20"].round(4),\n        "GlobalCalibrationStartDate": calibration_details["start_date"].astype(str),\n        "GlobalCalibrationEndDate": calibration_details["end_date"].astype(str),\n    }, index=frame.index)\n    frame = pd.concat([\n        frame.drop(columns=[column for column in calibration_columns.columns if column in frame], errors="ignore"),\n        calibration_columns,\n    ], axis=1).copy()\n''',
)
replace_once(
    "analytics.py",
    '''    frame = finalize_signal_ranking(frame)\n    frame.to_csv(path, index=False, encoding="utf-8-sig")\n    from report import refresh_candidate_exports\n\n    refresh_candidate_exports(frame, top_n_csv=top_n, output_dir=OUTPUT_DIR)\n    frame.to_parquet(OUTPUT_DIR / "AllResults.parquet", index=False)\n''',
    '''    # One consolidation before lifecycle/ranking avoids pandas block-fragmentation\n    # warnings on the 200+ column production result frame.\n    frame = finalize_signal_ranking(frame.copy())\n    frame.to_csv(path, index=False, encoding="utf-8-sig")\n    from report import refresh_candidate_exports\n\n    top_csv_path, _top_parquet_path, _ranked = refresh_candidate_exports(\n        frame, top_n_csv=top_n, output_dir=OUTPUT_DIR\n    )\n    frame.to_parquet(OUTPUT_DIR / "AllResults.parquet", index=False)\n\n    # Diversity and exact re-ranking can pull a previously unrefined ticker into\n    # the final TopN. Reconcile those few rows and rerank until the public TopN\n    # is entirely EXACT (bounded to avoid pathological loops).\n    if BACKTEST_AUTO_EXACT_REFINEMENT and _reconcile_depth < 3 and top_csv_path.exists():\n        try:\n            top_frame = pd.read_csv(top_csv_path, encoding="utf-8-sig")\n        except (OSError, UnicodeError, pd.errors.ParserError):\n            top_frame = pd.DataFrame()\n        reconcile_tickers = _non_exact_tickers(top_frame)\n        if reconcile_tickers:\n            logger.info(\n                "Final Top%d exact reconciliation: %d ticker(s) still not EXACT.",\n                top_n, len(reconcile_tickers),\n            )\n            exact = run_historical_backtest(\n                reconcile_tickers, source="tickflow", objective=summary.objective,\n                benchmark=summary.benchmark, commission=summary.commission,\n                stamp_duty=summary.stamp_duty, slippage=summary.slippage,\n                test_ratio=summary.test_ratio, validation_ratio=summary.validation_ratio,\n                mode="exact",\n            )\n            exact_rows = list(exact.by_ticker or [])\n            top_signal = dict(zip(\n                top_frame.get("Ticker", pd.Series(dtype=str)).astype(str),\n                top_frame.get("EntrySignal", pd.Series("UNKNOWN", index=top_frame.index)).fillna("UNKNOWN").astype(str).str.upper(),\n            ))\n            exact_keys = {(str(row.get("ticker", "")), str(row.get("entry_signal", "")).upper()) for row in exact_rows}\n            for ticker in reconcile_tickers:\n                key = (ticker, top_signal.get(ticker, "UNKNOWN"))\n                if key not in exact_keys:\n                    exact_rows.append({\n                        "ticker": ticker, "entry_signal": key[1], "samples": 0,\n                        "effective_samples": 0.0, "backtest_score": BACKTEST_NEUTRAL_SCORE,\n                        "backtest_mode": "EXACT", "backtest_cache_hit": False,\n                        "backtest_last_evaluated_date": exact.split_dates.get("global_end") or "",\n                        "backtest_engine": exact.engine,\n                    })\n            for row in exact_rows:\n                row["backtest_mode"] = "EXACT"\n                row["backtest_stage"] = "FINAL_EXACT_RECONCILIATION"\n            targets = set(reconcile_tickers)\n            summary.by_ticker = [\n                row for row in summary.by_ticker\n                if str(row.get("ticker", "")) not in targets\n            ] + exact_rows\n            summary.mode = "reconciled"\n            summary.engine = f"{summary.engine}+final-exact:{exact.engine}"\n            return apply_backtest_ranking(summary, top_n=top_n, _reconcile_depth=_reconcile_depth + 1)\n''',
)

# ---------------------------------------------------------------------------
# model_calibration.py — non-saturating confidence with hierarchy/time caps
# ---------------------------------------------------------------------------
replace_once(
    "model_calibration.py",
    '''SETUP_BUCKET_LABELS: tuple[str, ...] = ("<40", "40-55", "55-70", ">=70")\n''',
    '''SETUP_BUCKET_LABELS: tuple[str, ...] = ("<40", "40-55", "55-70", ">=70")\nCALIBRATION_LEVEL_CONFIDENCE_CAPS: dict[str, float] = {\n    "asset_signal_regime_score_setup": 1.00,\n    "asset_signal_regime_score": 0.97,\n    "asset_signal_regime": 0.93,\n    "asset_signal_bucket": 0.90,\n    "asset_signal": 0.87,\n    "signal_bucket": 0.82,\n    "signal": 0.78,\n    "asset": 0.72,\n    "global": 0.70,\n}\n''',
)
regex_once(
    "model_calibration.py",
    r'def _calibration_score\(mean_excess: float, win_rate: float, effective_samples: float, min_samples: int\) -> tuple\[float, float\]:.*?(?=\ndef build_global_calibration)',
    '''def _calibration_confidence(\n    effective_samples: float,\n    min_samples: int,\n    level: str,\n    start_date: pd.Timestamp | None = None,\n    end_date: pd.Timestamp | None = None,\n) -> float:\n    if not np.isfinite(effective_samples) or effective_samples <= 0:\n        return 0.0\n    scale = max(float(min_samples) * 2.5, 1.0)\n    sample_confidence = 1.0 - float(np.exp(-float(effective_samples) / scale))\n    level_cap = float(CALIBRATION_LEVEL_CONFIDENCE_CAPS.get(str(level), 0.70))\n    time_factor = 0.85\n    if start_date is not None and end_date is not None and pd.notna(start_date) and pd.notna(end_date):\n        span_days = max(0.0, float((pd.Timestamp(end_date) - pd.Timestamp(start_date)).days))\n        time_factor = 0.70 + 0.30 * float(np.clip(span_days / (365.25 * 3.0), 0.0, 1.0))\n    return round(float(np.clip(sample_confidence * level_cap * time_factor, 0.0, level_cap)), 4)\n\n\ndef _calibration_score(\n    mean_excess: float,\n    win_rate: float,\n    effective_samples: float,\n    min_samples: int,\n    *,\n    confidence: float | None = None,\n) -> tuple[float, float]:\n    if not np.isfinite(mean_excess) or not np.isfinite(win_rate) or effective_samples <= 0:\n        return 50.0, 0.0\n    if confidence is None:\n        confidence = float(np.clip(effective_samples / max(float(min_samples) * 2.0, 1.0), 0.0, 1.0))\n    confidence = float(np.clip(confidence, 0.0, 1.0))\n    raw = 50.0 + float(np.clip(mean_excess, -10.0, 10.0)) * 3.0 + float(np.clip(win_rate - 0.5, -0.3, 0.3)) * 40.0\n    score = 50.0 + (float(np.clip(raw, 0.0, 100.0)) - 50.0) * confidence\n    return round(score, 4), round(confidence, 4)\n\n''',
)
replace_once(
    "model_calibration.py",
    '''            mean20 = _weighted_mean(group["net_excess20"], weights)\n            mean60 = _weighted_mean(group["net_excess60"], weights)\n            win20 = _weighted_rate(group["net_excess20"], weights)\n            score, confidence = _calibration_score(mean20, win20, effective, min_samples)\n''',
    '''            mean20 = _weighted_mean(group["net_excess20"], weights)\n            mean60 = _weighted_mean(group["net_excess60"], weights)\n            win20 = _weighted_rate(group["net_excess20"], weights)\n            start_date = group["entry_date"].min() if group["entry_date"].notna().any() else None\n            end_date = group["entry_date"].max() if group["entry_date"].notna().any() else None\n            confidence = _calibration_confidence(effective, min_samples, level, start_date, end_date)\n            score, confidence = _calibration_score(\n                mean20, win20, effective, min_samples, confidence=confidence\n            )\n''',
)

# ---------------------------------------------------------------------------
# signal_lifecycle.py — UniverseEligible is a hard execution boundary
# ---------------------------------------------------------------------------
replace_once(
    "signal_lifecycle.py",
    '''    passed_filters = _bool_series(result, "PassedFilters", True)\n    signal_status = _text_series(result, "SignalStatus", "").str.upper()\n''',
    '''    passed_filters = _bool_series(result, "PassedFilters", True)\n    universe_eligible = _bool_series(result, "UniverseEligible", passed_filters)\n    signal_confirmed = _bool_series(result, "SignalConfirmed", passed_filters)\n    signal_status = _text_series(result, "SignalStatus", "").str.upper()\n''',
)
replace_once(
    "signal_lifecycle.py",
    '''    filter_override = (\n        ~passed_filters\n        & signal.eq("BREAKOUT_CONFIRM")\n        & _bool_series(result, "BreakoutVolumeConfirmed", False)\n        & _bool_series(result, "BreakoutFlowConfirmed", False)\n        & ~lifecycle_failed\n    )\n    ranking_penalty_reason = _append_reason(\n        ranking_penalty_reason,\n        filter_override,\n        "基础筛选未全通过，但量价资金突破满足严格覆盖条件",\n    )\n''',
    '''    signal_override = (\n        universe_eligible\n        & ~signal_confirmed\n        & signal.eq("BREAKOUT_CONFIRM")\n        & _bool_series(result, "BreakoutVolumeConfirmed", False)\n        & _bool_series(result, "BreakoutFlowConfirmed", False)\n        & ~lifecycle_failed\n    )\n    ranking_penalty_reason = _append_reason(\n        ranking_penalty_reason,\n        ~universe_eligible,\n        "基础准入未通过，不允许进入交易就绪组",\n    )\n    ranking_penalty_reason = _append_reason(\n        ranking_penalty_reason,\n        signal_override,\n        "基础准入通过；量价资金突破覆盖普通信号确认不足",\n    )\n''',
)
replace_once(
    "signal_lifecycle.py",
    '''        & (passed_filters | filter_override)\n''',
    '''        & universe_eligible\n        & (signal_confirmed | signal_override)\n''',
)
replace_once(
    "signal_lifecycle.py",
    '''    readiness_reason.loc[\n        ~passed_filters & ~filter_override & ~hard_filter & ~lifecycle_failed\n    ] = "基础筛选未全通过，转为观察"\n''',
    '''    readiness_reason.loc[\n        ~universe_eligible & ~hard_filter & ~lifecycle_failed\n    ] = "基础准入未通过，转为观察"\n    readiness_reason.loc[\n        universe_eligible & ~signal_confirmed & ~signal_override & ~hard_filter & ~lifecycle_failed\n    ] = "基础准入通过，但吸筹/结构信号确认不足，转为观察"\n''',
)
replace_once(
    "signal_lifecycle.py",
    '''        & (passed_filters | filter_override)\n    ] = "买点、质量、数据与综合评分均满足执行条件"\n''',
    '''        & universe_eligible\n        & (signal_confirmed | signal_override)\n    ] = "买点、基础准入、信号、质量、数据与综合评分均满足执行条件"\n''',
)

# ---------------------------------------------------------------------------
# GUI — keep main table compact; diagnostics remain in double-click details
# ---------------------------------------------------------------------------
regex_once(
    "gui.py",
    r'_core\.DISPLAY_COLUMNS = \(.*?\n\)\n\n_core\.COLUMN_NAMES\.update',
    '''_core.DISPLAY_COLUMNS = (\n    "OverallRank",\n    "Ticker",\n    "Name",\n    "Sector",\n    "Industry",\n    "Close",\n    "EntrySignal",\n    "EntryZone",\n    "BreakoutBuyPrice",\n    "StopLoss",\n    "RankingEligibility",\n    "RankingScore",\n    "InstitutionalTier",\n    "InstitutionalScore",\n    "FinalScore",\n    "QualityGate",\n    "BacktestConfidenceTier",\n    "ValueTrapRisk",\n    "DataAsOf",\n    "TradeReadinessReason",\n)\n\n_core.COLUMN_NAMES.update''',
)
replace_once(
    "gui.py",
    '''        "GlobalCalibrationLevel": "全局校准层级",\n''',
    '''        "GlobalCalibrationLevel": "全局校准层级",\n        "GlobalCalibrationSamples": "全局校准样本",\n        "GlobalCalibrationEffectiveSamples": "全局有效样本",\n        "GlobalCalibrationMeanExcess20D": "校准20日超额",\n        "GlobalCalibrationWinRate20D": "校准20日胜率",\n        "GlobalCalibrationStartDate": "校准起始日",\n        "GlobalCalibrationEndDate": "校准结束日",\n        "UniverseEligible": "基础准入",\n        "SignalConfirmed": "信号确认",\n        "FailedFilterCount": "未通过筛选数",\n        "FailedFilterNames": "未通过筛选",\n        "ATR50": "ATR50",\n        "ATRExpansion": "ATR扩张比",\n        "ATRExpansionSource": "ATR来源",\n        "BacktestStage": "回测阶段",\n        "ModelVersion": "模型版本",\n        "IndicatorCacheVersion": "指标缓存版本",\n        "BacktestCacheVersion": "回测缓存版本",\n        "PreBacktestInstitutionalScore": "回测前机构分",\n''',
)
replace_once(
    "gui_core.py",
    '''            "BacktestAdjustedScore",\n            "BacktestWinRate20D",\n''',
    '''            "BacktestAdjustedScore",\n            "BacktestMode",\n            "BacktestStage",\n            "BacktestStatus",\n            "GlobalCalibrationScore",\n            "GlobalCalibrationConfidence",\n            "GlobalCalibrationLevel",\n            "GlobalCalibrationSamples",\n            "GlobalCalibrationEffectiveSamples",\n            "GlobalCalibrationMeanExcess20D",\n            "GlobalCalibrationWinRate20D",\n            "GlobalCalibrationStartDate",\n            "GlobalCalibrationEndDate",\n            "BacktestWinRate20D",\n''',
)
replace_once(
    "gui_core.py",
    '''            "ATR14",\n            "MA20",\n''',
    '''            "ATR14",\n            "ATR50",\n            "ATRExpansion",\n            "ATRExpansionSource",\n            "MA20",\n''',
)
replace_once(
    "gui_core.py",
    '''            "PassedFilters",\n            "OBV_Div",\n''',
    '''            "PassedFilters",\n            "UniverseEligible",\n            "SignalConfirmed",\n            "FailedFilterCount",\n            "FailedFilterNames",\n            "ModelVersion",\n            "IndicatorCacheVersion",\n            "BacktestCacheVersion",\n            "PreBacktestInstitutionalScore",\n            "OBV_Div",\n''',
)

print("model v20 migration applied")
