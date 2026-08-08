from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"pattern not found in {path}: {old[:140]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# 1. Continuous fundamental quality plus industry metadata.
replace_once(
    "fundamental_quality.py",
    "class FundamentalQuality:\n    ticker: str\n    roe: float = np.nan\n",
    "class FundamentalQuality:\n    ticker: str\n    industry: str = \"\"\n    roe: float = np.nan\n",
)
replace_once(
    "fundamental_quality.py",
    "    normalized_ticker = _ticker(values.get(\"Ticker\", ticker))\n    numeric = {\n",
    "    normalized_ticker = _ticker(values.get(\"Ticker\", ticker))\n    industry = str(values.get(\"Industry\", \"\") or \"\").strip()\n    numeric = {\n",
)
replace_once(
    "fundamental_quality.py",
    '''    # Unknown factors must not disappear from the denominator and turn one\n    # observed passing factor into a false 100/100 quality score. Shrink the\n    # observed pass-rate toward neutral (50) according to data completeness.\n    if passed or failed:\n        observed_pass_rate = len(passed) / (len(passed) + len(failed)) * 100.0\n        quality_score = round(\n            50.0 + (observed_pass_rate - 50.0) * completeness,\n            4,\n        )\n    else:\n        quality_score = np.nan\n\n    return FundamentalQuality(\n        ticker=normalized_ticker,\n''',
    '''    # Keep the boolean gate conservative, but make the ranking score continuous.\n    # Missing evidence is shrunk toward neutral rather than disappearing from the\n    # denominator, while stronger ROE/margin/profit trends retain cross-sectional\n    # dispersion instead of collapsing most names into 75/87.5/100 buckets.\n    weighted_points = 0.0\n    available_weight = 0.0\n    if roe_available:\n        weighted_points += float(np.clip(numeric[\"ROE\"] / 20.0, 0.0, 1.0)) * 25.0\n        available_weight += 25.0\n    if gross_margin_available:\n        weighted_points += float(\n            np.clip(1.0 - numeric[\"IndustryGrossMarginPercentile\"], 0.0, 1.0)\n        ) * 20.0\n        available_weight += 20.0\n    if profit_available:\n        y1, y2, y3 = (\n            numeric[\"NetProfitY1\"],\n            numeric[\"NetProfitY2\"],\n            numeric[\"NetProfitY3\"],\n        )\n        growth_values: list[float] = []\n        if abs(y2) > 1e-9:\n            growth_values.append((y1 - y2) / abs(y2))\n        if abs(y3) > 1e-9:\n            growth_values.append((y2 - y3) / abs(y3))\n        mean_growth = float(np.mean(growth_values)) if growth_values else 0.0\n        profit_strength = float(np.clip(0.5 + mean_growth / 0.50, 0.0, 1.0))\n        weighted_points += profit_strength * 25.0\n        available_weight += 25.0\n    if holding_available:\n        weighted_points += 15.0 if holding_status == \"PASS\" else 0.0\n        available_weight += 15.0\n\n    if available_weight > 0:\n        observed_score = weighted_points / available_weight * 100.0\n        shrunk_factor_score = 50.0 + (observed_score - 50.0) * completeness\n        quality_score = round(\n            float(\n                np.clip(\n                    shrunk_factor_score * 0.85 + completeness * 100.0 * 0.15,\n                    0.0,\n                    100.0,\n                )\n            ),\n            4,\n        )\n    else:\n        quality_score = np.nan\n\n    return FundamentalQuality(\n        ticker=normalized_ticker,\n        industry=industry,\n''',
)

# 2. Reconcile missing TickFlow classification from the AkShare fundamental cache.
replace_once(
    "scanner.py",
    "        quality = get_quality(ticker, is_etf=ticker_info.is_etf)\n        breakout = _parse_float(getattr(sb, \"breakout_score\", np.nan), 0.0)\n",
    "        quality = get_quality(ticker, is_etf=ticker_info.is_etf)\n        resolved_industry = str(\n            ticker_info.industry or getattr(quality, \"industry\", \"\") or \"\"\n        ).strip()\n        # TickFlow Free metadata does not consistently expose a separate sector.\n        # Reuse the verified fundamental industry as a fallback instead of leaving\n        # both classification fields blank.\n        resolved_sector = str(ticker_info.sector or resolved_industry or \"\").strip()\n        breakout = _parse_float(getattr(sb, \"breakout_score\", np.nan), 0.0)\n",
)
replace_once(
    "scanner.py",
    "            sector=ticker_info.sector,\n            industry=ticker_info.industry,\n            is_etf=ticker_info.is_etf,\n            asset_type=ticker_info.asset_type,\n            close=close,\n",
    "            sector=resolved_sector,\n            industry=resolved_industry,\n            is_etf=ticker_info.is_etf,\n            asset_type=ticker_info.asset_type,\n            close=close,\n",
)

# 3. Entry zone/state consistency.  A WAIT_PULLBACK zone must be below price.
replace_once(
    "score.py",
    '''    atr = atr if _is_finite(atr) and atr > 0 else price * 0.03\n    low_zone = max(support, price - atr * 1.2)\n    high_zone = min(price + atr * 0.3, resistance)\n    if high_zone < low_zone:\n        low_zone = max(support, min(price, resistance))\n        high_zone = max(low_zone, min(price + atr * 0.3, resistance))\n''',
    '''    atr = atr if _is_finite(atr) and atr > 0 else price * 0.03\n    # Define a forward-looking support zone.  Anchoring it around the current\n    # close made nearly every WAIT_PULLBACK row already sit inside EntryZone.\n    support_anchor = support + atr * 0.55\n    if _is_finite(ma20) and ma20 <= price:\n        support_anchor = max(support_anchor, float(ma20))\n    support_anchor = min(support_anchor, price)\n    low_zone = max(support, support_anchor - atr * 0.35)\n    high_zone = min(resistance, support_anchor + atr * 0.35)\n    if high_zone < low_zone:\n        high_zone = low_zone\n''',
)
replace_once(
    "score.py",
    '''    elif score >= 70.0 and price <= support + atr * 1.5:\n        signal = \"BUY_NOW\"\n    elif _is_finite(ma20) and score >= 50.0 and price > ma20:\n        signal = \"WAIT_PULLBACK\"\n''',
    '''    elif score >= 70.0 and low_zone <= price <= high_zone:\n        signal = \"BUY_NOW\"\n    elif score >= 50.0 and price > high_zone:\n        signal = \"WAIT_PULLBACK\"\n    elif score >= 50.0 and low_zone <= price <= high_zone:\n        signal = \"HOLD_WAIT\"\n''',
)

# 4. Quality contributes exactly once and no longer dominates technical score.
replace_once(
    "config.py",
    "MODEL_QUALITY_WEIGHT: Final[float] = 0.30",
    "MODEL_QUALITY_WEIGHT: Final[float] = 0.20",
)
replace_once(
    "analytics.py",
    "    GLOBAL_CALIBRATION_MIN_SAMPLES,\n    INDICATOR_CACHE_ENABLED,\n",
    "    GLOBAL_CALIBRATION_MIN_SAMPLES,\n    INDICATOR_CACHE_ENABLED,\n    MODEL_QUALITY_WEIGHT,\n",
)
replace_once(
    "analytics.py",
    "        return float(score * 0.7 + quality * 0.3)\n",
    "        quality_weight = float(np.clip(MODEL_QUALITY_WEIGHT, 0.0, 0.5))\n        return float(score * (1.0 - quality_weight) + quality * quality_weight)\n",
)
replace_once(
    "analytics.py",
    '''        quality_multiplier = _finite_float(\n            getattr(result, \"quality_multiplier\", 1.0), 1.0\n        )\n        result.institutional_score = round(\n            quality_adjusted * np.clip(quality_multiplier, 0.0, 1.0), 4\n        )\n''',
    '''        # QualityGate/QualityMultiplier are decision gates, not a second score\n        # penalty.  The quality contribution is already present in the blend.\n        result.institutional_score = round(quality_adjusted, 4)\n''',
)
replace_once(
    "analytics.py",
    '''    legacy_institutional = pd.Series(\n        np.where(\n            quality_eligible,\n            institutional_component * 0.7 + quality_score * 0.3,\n            institutional_component,\n        ),\n        index=frame.index,\n    ).mul(frame[\"QualityMultiplier\"], axis=0)\n''',
    '''    quality_weight = float(np.clip(MODEL_QUALITY_WEIGHT, 0.0, 0.5))\n    legacy_institutional = pd.Series(\n        np.where(\n            quality_eligible,\n            institutional_component * (1.0 - quality_weight)\n            + quality_score * quality_weight,\n            institutional_component,\n        ),\n        index=frame.index,\n    )\n''',
)

# 5. One final DecisionResolver: failed lifecycle cannot be recommended and
#    filter misses require an explicit fully-confirmed breakout override.
replace_once(
    "signal_lifecycle.py",
    '''    quality_action_block = ~result[\"QualityGate\"] | (\n        result[\"QualityDataCompleteness\"].lt(QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE)\n        & ~is_etf\n    )\n''',
    '''    quality_action_block = ~result[\"QualityGate\"] | (\n        result[\"QualityDataCompleteness\"].lt(QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE)\n        & ~is_etf\n    )\n    # Missing legacy columns are treated as compatible; explicit False/FAILED\n    # values from current scans are authoritative.\n    passed_filters = _bool_series(result, \"PassedFilters\", True)\n    signal_status = _text_series(result, \"SignalStatus\", \"\").str.upper()\n    lifecycle_failed = signal_status.eq(\"FAILED\")\n''',
)
replace_once(
    "signal_lifecycle.py",
    '''    ranking_penalty_reason = _append_reason(\n        ranking_penalty_reason, freshness_status.eq(\"延迟\"), \"行情数据延迟\"\n    )\n''',
    '''    ranking_penalty_reason = _append_reason(\n        ranking_penalty_reason, freshness_status.eq(\"延迟\"), \"行情数据延迟\"\n    )\n    ranking_penalty_reason = _append_reason(\n        ranking_penalty_reason, lifecycle_failed, \"历史信号生命周期已失败\"\n    )\n''',
)
replace_once(
    "signal_lifecycle.py",
    '''    trade_ready = (\n        signal.isin({\"BUY_NOW\", \"BREAKOUT_CONFIRM\"})\n        & ~stage_risk\n        & ~trap_observe\n        & ~quality_action_block\n        & ~data_risk\n        & ~stale_data\n        & ~minimum_score_risk\n    )\n''',
    '''    filter_override = (\n        ~passed_filters\n        & signal.eq(\"BREAKOUT_CONFIRM\")\n        & _bool_series(result, \"BreakoutVolumeConfirmed\", False)\n        & _bool_series(result, \"BreakoutFlowConfirmed\", False)\n        & ~lifecycle_failed\n    )\n    ranking_penalty_reason = _append_reason(\n        ranking_penalty_reason,\n        filter_override,\n        \"基础筛选未全通过，但量价资金突破满足严格覆盖条件\",\n    )\n    result[\"RankingPenaltyReason\"] = ranking_penalty_reason\n\n    trade_ready = (\n        signal.isin({\"BUY_NOW\", \"BREAKOUT_CONFIRM\"})\n        & (passed_filters | filter_override)\n        & ~lifecycle_failed\n        & ~stage_risk\n        & ~trap_observe\n        & ~quality_action_block\n        & ~data_risk\n        & ~stale_data\n        & ~minimum_score_risk\n    )\n''',
)
replace_once(
    "signal_lifecycle.py",
    '''    readiness_reason.loc[hard_filter] = \"硬风险过滤，不纳入交易就绪组\"\n''',
    '''    readiness_reason.loc[hard_filter] = \"硬风险过滤，不纳入交易就绪组\"\n    readiness_reason.loc[lifecycle_failed & ~hard_filter] = \"历史信号生命周期已失败，转为观察\"\n    readiness_reason.loc[\n        ~passed_filters & ~filter_override & ~hard_filter & ~lifecycle_failed\n    ] = \"基础筛选未全通过，转为观察\"\n''',
)
replace_once(
    "signal_lifecycle.py",
    '''        & ~minimum_score_risk\n        & ~data_risk\n        & ~hard_filter\n        & ~quality_action_block\n        & ~trap_observe\n    ] = \"买点、质量、数据与综合评分均满足执行条件\"\n''',
    '''        & ~minimum_score_risk\n        & ~data_risk\n        & ~hard_filter\n        & ~quality_action_block\n        & ~trap_observe\n        & ~lifecycle_failed\n        & (passed_filters | filter_override)\n    ] = \"买点、质量、数据与综合评分均满足执行条件\"\n''',
)
replace_once(
    "signal_lifecycle.py",
    '''    rank_reason.loc[stale_data] = \"行情数据已过期，风险过滤\"\n''',
    '''    rank_reason.loc[stale_data] = \"行情数据已过期，风险过滤\"\n    rank_reason.loc[lifecycle_failed & ~stale_data] = \"历史信号生命周期失败，转为观察\"\n    rank_reason.loc[\n        ~passed_filters & ~filter_override & ~lifecycle_failed & ~stale_data\n    ] = \"基础筛选未全通过，转为观察\"\n    rank_reason.loc[filter_override] = \"量价资金确认突破，严格覆盖基础筛选缺口\"\n''',
)

# 6. Global Calibration v2: asset × signal × regime × score × setup, with
#    hierarchical fallback when the specific peer bucket is too small.
replace_once(
    "model_calibration.py",
    'SCORE_BUCKET_LABELS: tuple[str, ...] = ("<40", "40-50", "50-60", "60-70", "70-80", ">=80")\n',
    'SCORE_BUCKET_LABELS: tuple[str, ...] = ("<40", "40-50", "50-60", "60-70", "70-80", ">=80")\nSETUP_BUCKET_EDGES: tuple[float, ...] = (-np.inf, 40.0, 55.0, 70.0, np.inf)\nSETUP_BUCKET_LABELS: tuple[str, ...] = ("<40", "40-55", "55-70", ">=70")\n',
)
replace_once(
    "model_calibration.py",
    '''def _weighted_mean(values: pd.Series, weights: pd.Series) -> float:\n''',
    '''def _normalize_regime(value: Any) -> str:\n    text = str(value or \"UNKNOWN\").strip().upper()\n    if text in {\"RISK_ON\", \"风险偏好\", \"风险偏好环境\"}:\n        return \"RISK_ON\"\n    if text in {\"RISK_OFF\", \"风险规避\", \"风险规避环境\"}:\n        return \"RISK_OFF\"\n    if text in {\"NEUTRAL\", \"震荡\", \"震荡修复\", \"震荡转弱\"}:\n        return \"NEUTRAL\"\n    return \"UNKNOWN\"\n\n\ndef _weighted_mean(values: pd.Series, weights: pd.Series) -> float:\n''',
)
replace_once(
    "model_calibration.py",
    '''    result[\"entry_signal\"] = result.get(\"entry_signal\", pd.Series(\"UNKNOWN\", index=result.index)).fillna(\"UNKNOWN\").astype(str).str.upper()\n    result[\"score\"] = _numeric(result, \"score\")\n''',
    '''    result[\"entry_signal\"] = result.get(\"entry_signal\", pd.Series(\"UNKNOWN\", index=result.index)).fillna(\"UNKNOWN\").astype(str).str.upper()\n    result[\"market_regime\"] = result.get(\"market_regime\", pd.Series(\"UNKNOWN\", index=result.index)).map(_normalize_regime)\n    result[\"score\"] = _numeric(result, \"score\")\n    result[\"setup_score\"] = _numeric(result, \"setup_score\")\n''',
)
replace_once(
    "model_calibration.py",
    '''    result[\"score_bucket\"] = pd.cut(\n        result[\"score\"],\n        bins=SCORE_BUCKET_EDGES,\n        labels=SCORE_BUCKET_LABELS,\n        right=False,\n        include_lowest=True,\n    ).astype(\"object\")\n''',
    '''    result[\"score_bucket\"] = pd.cut(\n        result[\"score\"],\n        bins=SCORE_BUCKET_EDGES,\n        labels=SCORE_BUCKET_LABELS,\n        right=False,\n        include_lowest=True,\n    ).astype(\"object\")\n    result[\"setup_bucket\"] = pd.cut(\n        result[\"setup_score\"],\n        bins=SETUP_BUCKET_EDGES,\n        labels=SETUP_BUCKET_LABELS,\n        right=False,\n        include_lowest=True,\n    ).astype(\"object\")\n''',
)
replace_once(
    "model_calibration.py",
    '''    levels: tuple[tuple[str, tuple[str, ...]], ...] = (\n        (\"asset_signal_bucket\", (\"asset_type\", \"entry_signal\", \"score_bucket\")),\n        (\"asset_signal\", (\"asset_type\", \"entry_signal\")),\n        (\"signal_bucket\", (\"entry_signal\", \"score_bucket\")),\n        (\"signal\", (\"entry_signal\",)),\n        (\"asset\", (\"asset_type\",)),\n        (\"global\", tuple()),\n    )\n''',
    '''    levels: tuple[tuple[str, tuple[str, ...]], ...] = (\n        (\"asset_signal_regime_score_setup\", (\"asset_type\", \"entry_signal\", \"market_regime\", \"score_bucket\", \"setup_bucket\")),\n        (\"asset_signal_regime_score\", (\"asset_type\", \"entry_signal\", \"market_regime\", \"score_bucket\")),\n        (\"asset_signal_regime\", (\"asset_type\", \"entry_signal\", \"market_regime\")),\n        (\"asset_signal_bucket\", (\"asset_type\", \"entry_signal\", \"score_bucket\")),\n        (\"asset_signal\", (\"asset_type\", \"entry_signal\")),\n        (\"signal_bucket\", (\"entry_signal\", \"score_bucket\")),\n        (\"signal\", (\"entry_signal\",)),\n        (\"asset\", (\"asset_type\",)),\n        (\"global\", tuple()),\n    )\n''',
)
replace_once(
    "model_calibration.py",
    '''def resolve_global_calibration(\n    asset_type: str,\n    entry_signal: str,\n    score: float,\n    rows: list[dict[str, Any]] | None,\n) -> tuple[float, float, str]:\n''',
    '''def resolve_global_calibration(\n    asset_type: str,\n    entry_signal: str,\n    score: float,\n    rows: list[dict[str, Any]] | None,\n    market_regime: str = \"UNKNOWN\",\n    setup_score: float = np.nan,\n) -> tuple[float, float, str]:\n''',
)
replace_once(
    "model_calibration.py",
    '''    asset = str(asset_type or \"stock\").lower()\n    signal = str(entry_signal or \"UNKNOWN\").upper()\n''',
    '''    asset = str(asset_type or \"stock\").lower()\n    signal = str(entry_signal or \"UNKNOWN\").upper()\n    regime = _normalize_regime(market_regime)\n''',
)
replace_once(
    "model_calibration.py",
    '''    bucket = str(bucket_series.iloc[0]) if pd.notna(bucket_series.iloc[0]) else \"\"\n    priorities = (\n        (\"asset_signal_bucket\", {\"asset_type\": asset, \"entry_signal\": signal, \"score_bucket\": bucket}),\n''',
    '''    bucket = str(bucket_series.iloc[0]) if pd.notna(bucket_series.iloc[0]) else \"\"\n    setup_bucket_series = pd.cut(\n        pd.Series([setup_score], dtype=float),\n        bins=SETUP_BUCKET_EDGES,\n        labels=SETUP_BUCKET_LABELS,\n        right=False,\n        include_lowest=True,\n    )\n    setup_bucket = str(setup_bucket_series.iloc[0]) if pd.notna(setup_bucket_series.iloc[0]) else \"\"\n    priorities = (\n        (\"asset_signal_regime_score_setup\", {\"asset_type\": asset, \"entry_signal\": signal, \"market_regime\": regime, \"score_bucket\": bucket, \"setup_bucket\": setup_bucket}),\n        (\"asset_signal_regime_score\", {\"asset_type\": asset, \"entry_signal\": signal, \"market_regime\": regime, \"score_bucket\": bucket}),\n        (\"asset_signal_regime\", {\"asset_type\": asset, \"entry_signal\": signal, \"market_regime\": regime}),\n        (\"asset_signal_bucket\", {\"asset_type\": asset, \"entry_signal\": signal, \"score_bucket\": bucket}),\n''',
)
replace_once(
    "model_calibration.py",
    '''    model_scores = pd.to_numeric(\n        frame.get(\"FinalScore\", frame.get(\"score\", pd.Series(np.nan, index=frame.index))),\n        errors=\"coerce\",\n    )\n    for asset, signal, score in zip(asset_values, signal_values, model_scores):\n        value, confidence, _level = resolve_global_calibration(\n            str(asset), str(signal), float(score) if pd.notna(score) else np.nan, rows\n        )\n''',
    '''    model_scores = pd.to_numeric(\n        frame.get(\"FinalScore\", frame.get(\"score\", pd.Series(np.nan, index=frame.index))),\n        errors=\"coerce\",\n    )\n    regime_values = frame.get(\"MarketRegime\", frame.get(\"market_regime\", pd.Series(\"UNKNOWN\", index=frame.index)))\n    setup_values = pd.to_numeric(\n        frame.get(\"BaseScore\", frame.get(\"setup_score\", pd.Series(np.nan, index=frame.index))),\n        errors=\"coerce\",\n    )\n    for asset, signal, score, regime, setup in zip(\n        asset_values, signal_values, model_scores, regime_values, setup_values\n    ):\n        value, confidence, _level = resolve_global_calibration(\n            str(asset),\n            str(signal),\n            float(score) if pd.notna(score) else np.nan,\n            rows,\n            market_regime=str(regime),\n            setup_score=float(setup) if pd.notna(setup) else np.nan,\n        )\n''',
)

# Backtest samples carry point-in-time benchmark regime for pooled calibration.
replace_once(
    "analytics.py",
    '''    benchmark_close = None\n    if benchmark_frame is not None and not benchmark_frame.empty:\n        benchmark_close = benchmark_frame[\"Close\"].astype(float).sort_index()\n''',
    '''    benchmark_close = None\n    if benchmark_frame is not None and not benchmark_frame.empty:\n        benchmark_close = benchmark_frame[\"Close\"].astype(float).sort_index()\n\n    def historical_regime(at_date: pd.Timestamp) -> str:\n        if benchmark_close is None:\n            return \"UNKNOWN\"\n        history = benchmark_close.loc[:at_date].dropna()\n        if len(history) < 60:\n            return \"UNKNOWN\"\n        last = float(history.iloc[-1])\n        ma60 = float(history.iloc[-60:].mean())\n        ma200 = float(history.iloc[-200:].mean()) if len(history) >= 200 else ma60\n        ret60 = (\n            (last / float(history.iloc[-61]) - 1.0) * 100.0\n            if len(history) >= 61 and float(history.iloc[-61]) > 0\n            else 0.0\n        )\n        if last >= ma60 and last >= ma200 and ret60 > 3.0:\n            return \"RISK_ON\"\n        if last < ma60 and last < ma200 and ret60 < -3.0:\n            return \"RISK_OFF\"\n        return \"NEUTRAL\"\n''',
)
replace_once(
    "analytics.py",
    '''                \"entry_signal\": historical_signal,\n                \"signal_date\": signal_date.strftime(\"%Y-%m-%d\"),\n''',
    '''                \"entry_signal\": historical_signal,\n                \"market_regime\": historical_regime(signal_date),\n                \"signal_date\": signal_date.strftime(\"%Y-%m-%d\"),\n''',
)

# Derived cache/model versions only; raw TickFlow cache is untouched.
replace_once(
    "performance_cache.py",
    'BACKTEST_CACHE_VERSION = "v5"',
    'BACKTEST_CACHE_VERSION = "v6"',
)
replace_once(
    "config.py",
    'SCORING_VERSION: str = "2026-08-08-v16-model-v2-oos"',
    'SCORING_VERSION: str = "2026-08-08-v17-production-consistency"',
)

# Regression coverage for the production inconsistencies found in the latest CSV.
(ROOT / "test_model_v3_regressions.py").write_text(
    '''from __future__ import annotations\n\nimport unittest\nfrom unittest.mock import patch\n\nimport numpy as np\nimport pandas as pd\n\nfrom downloader import TickerInfo\nfrom fundamental_quality import calculate_quality\nfrom model_calibration import build_global_calibration, resolve_global_calibration\nfrom scanner import scan_single_from_df\nfrom score import entry_point\nfrom signal_lifecycle import finalize_signal_ranking\n\n\nclass ModelV3RegressionTests(unittest.TestCase):\n    def test_quality_score_is_continuous_and_preserves_industry(self):\n        a = calculate_quality({\n            \"Ticker\": \"600000.SH\", \"Industry\": \"银行\", \"ROE\": 11.0,\n            \"GrossMargin\": 30.0, \"IndustryGrossMarginPercentile\": 0.25,\n            \"InstitutionHoldingTrend\": \"increasing\", \"InstitutionHoldingPeriods\": 2,\n            \"NetProfitY1\": 110.0, \"NetProfitY2\": 100.0, \"NetProfitY3\": 95.0,\n        })\n        b = calculate_quality({\n            \"Ticker\": \"600001.SH\", \"Industry\": \"银行\", \"ROE\": 17.0,\n            \"GrossMargin\": 35.0, \"IndustryGrossMarginPercentile\": 0.10,\n            \"InstitutionHoldingTrend\": \"increasing\", \"InstitutionHoldingPeriods\": 2,\n            \"NetProfitY1\": 130.0, \"NetProfitY2\": 105.0, \"NetProfitY3\": 95.0,\n        })\n        self.assertEqual(a.industry, \"银行\")\n        self.assertNotEqual(a.quality_score, b.quality_score)\n        self.assertGreater(b.quality_score, a.quality_score)\n\n    def test_scanner_fills_industry_from_fundamentals_when_tickflow_metadata_is_blank(self):\n        index = pd.date_range(\"2024-01-01\", periods=320, freq=\"B\")\n        close = np.linspace(10.0, 12.0, len(index))\n        frame = pd.DataFrame({\n            \"Open\": close, \"High\": close * 1.01, \"Low\": close * 0.99,\n            \"Close\": close, \"Volume\": np.full(len(index), 1_000_000.0),\n        }, index=index)\n        quality = calculate_quality({\n            \"Ticker\": \"600000.SH\", \"Industry\": \"银行\", \"ROE\": 12.0,\n            \"GrossMargin\": 30.0, \"IndustryGrossMarginPercentile\": 0.20,\n            \"InstitutionHoldingTrend\": \"increasing\", \"InstitutionHoldingPeriods\": 2,\n            \"NetProfitY1\": 110.0, \"NetProfitY2\": 100.0, \"NetProfitY3\": 90.0,\n        })\n        with patch(\"scanner.get_quality\", return_value=quality), patch(\"scanner.get_market_cap\", return_value=1e10):\n            result = scan_single_from_df(TickerInfo(\"600000.SH\", name=\"测试\"), frame)\n        self.assertEqual(result.industry, \"银行\")\n        self.assertEqual(result.sector, \"银行\")\n\n    def test_wait_pullback_is_above_its_entry_zone(self):\n        index = pd.date_range(\"2024-01-01\", periods=260, freq=\"B\")\n        close = np.linspace(10.0, 15.0, len(index))\n        frame = pd.DataFrame(index=index)\n        frame[\"Close\"] = close\n        frame[\"High\"] = close * 1.01\n        frame[\"Low\"] = close * 0.99\n        frame[\"Volume\"] = 1_000_000.0\n        frame[\"ATR14\"] = 0.35\n        frame[\"MA20\"] = pd.Series(close, index=index).rolling(20).mean()\n        frame[\"MA50\"] = pd.Series(close, index=index).rolling(50).mean()\n        frame[\"RSI14\"] = 60.0\n        frame[\"CMF\"] = 0.05\n        frame[\"AD_Slope\"] = 1.0\n        result = entry_point(frame, breakout=50.0, volume_score=10.0, value_trap_risk_value=10.0)\n        if result[\"signal\"] == \"WAIT_PULLBACK\":\n            self.assertGreater(float(frame[\"Close\"].iloc[-1]), float(result[\"high\"]))\n\n    def test_failed_lifecycle_cannot_be_recommended(self):\n        frame = pd.DataFrame([{\n            \"Ticker\": \"600000.SH\", \"Score\": 80.0, \"FinalScore\": 80.0,\n            \"InstitutionalScore\": 80.0, \"EntrySignal\": \"BREAKOUT_CONFIRM\",\n            \"BreakoutVolumeConfirmed\": True, \"BreakoutFlowConfirmed\": True,\n            \"PassedFilters\": True, \"SignalStatus\": \"FAILED\",\n            \"LifecycleStage\": \"趋势确认\", \"QualityGate\": True,\n            \"QualityDataAvailable\": True, \"QualityDataCompleteness\": 1.0,\n            \"ROE\": 15.0, \"QualityROE\": True,\n            \"IndustryGrossMarginPercentile\": 0.2, \"QualityGrossMargin\": True,\n            \"NetProfitY1\": 3.0, \"NetProfitY2\": 2.0, \"NetProfitY3\": 1.0,\n            \"QualityNetProfit\": True, \"InstitutionHoldingStatus\": \"PASS\",\n            \"InstitutionHoldingPeriods\": 2, \"DataTradingAgeDays\": 0,\n            \"ScoreCoverage\": 1.0, \"RSI14\": 60.0, \"DistToLow52W\": 10.0,\n            \"DistToMA20\": 1.0, \"RecentReturn20D\": 5.0, \"ATRExpansion\": 1.0,\n        }])\n        ranked = finalize_signal_ranking(frame)\n        self.assertNotEqual(ranked.loc[0, \"RankingEligibility\"], \"推荐\")\n\n    def test_global_calibration_uses_regime_and_setup_peer_group(self):\n        rows = []\n        dates = pd.date_range(\"2020-01-01\", periods=80, freq=\"B\")\n        for index, date in enumerate(dates):\n            regime = \"RISK_ON\" if index < 40 else \"RISK_OFF\"\n            excess = 5.0 if regime == \"RISK_ON\" else -4.0\n            rows.append({\n                \"ticker\": f\"{index:06d}.SH\", \"asset_type\": \"stock\",\n                \"entry_signal\": \"BREAKOUT_CONFIRM\", \"market_regime\": regime,\n                \"score\": 65.0, \"setup_score\": 60.0, \"sample_weight\": 1.0,\n                \"net_return20\": excess, \"benchmark_return20\": 0.0,\n                \"net_return60\": excess, \"benchmark_return60\": 0.0,\n                \"entry_date\": date,\n            })\n        calibration = build_global_calibration(pd.DataFrame(rows), min_samples=20)\n        on_score, on_conf, on_level = resolve_global_calibration(\n            \"stock\", \"BREAKOUT_CONFIRM\", 65.0, calibration,\n            market_regime=\"风险偏好\", setup_score=60.0,\n        )\n        off_score, off_conf, off_level = resolve_global_calibration(\n            \"stock\", \"BREAKOUT_CONFIRM\", 65.0, calibration,\n            market_regime=\"风险规避\", setup_score=60.0,\n        )\n        self.assertGreater(on_conf, 0.0)\n        self.assertGreater(off_conf, 0.0)\n        self.assertGreater(on_score, off_score)\n        self.assertIn(\"regime\", on_level)\n        self.assertIn(\"regime\", off_level)\n\n\nif __name__ == \"__main__\":\n    unittest.main()\n''',
    encoding="utf-8",
)

print("model v3 production consistency upgrade applied")
