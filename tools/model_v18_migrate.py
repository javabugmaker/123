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
        raise RuntimeError(f"pattern not found in {path}: {old[:120]!r}")
    write(path, text.replace(old, new, 1))


def regex_once(path: str, pattern: str, replacement: str) -> None:
    text = read(path)
    new_text, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"regex replacement count={count} in {path}: {pattern[:100]!r}")
    write(path, new_text)


# ---------------------------------------------------------------------------
# Configuration/cache generation
# ---------------------------------------------------------------------------
replace_once(
    "config.py",
    'ETF_THEME_MAX_PER_TOP_LIST: Final[int] = 2\n\nSCORING_VERSION: str = "2026-08-08-v17-production-consistency"',
    'ETF_THEME_MAX_PER_TOP_LIST: Final[int] = 2\n'
    'STOCK_INDUSTRY_MAX_PER_TOP_LIST: Final[int] = 5\n'
    'SECTOR_CONFIRMATION_MIN_FACTOR: Final[float] = 0.72\n'
    'SECTOR_CONFIRMATION_INDUSTRY_WEIGHT: Final[float] = 0.45\n'
    'SECTOR_CONFIRMATION_RELATIVE_WEIGHT: Final[float] = 0.55\n\n'
    'SCORING_VERSION: str = "2026-08-08-v18-decision-ranking-calibration"',
)
replace_once(
    "performance_cache.py",
    'INDICATOR_CACHE_VERSION = "v3"\nBACKTEST_CACHE_VERSION = "v6"',
    'INDICATOR_CACHE_VERSION = "v4"\nBACKTEST_CACHE_VERSION = "v7"',
)
replace_once(
    "performance_cache.py",
    '_CUMULATIVE_COLUMNS = ("OBV", "AD")\n',
    '_CUMULATIVE_COLUMNS = ("OBV", "AD")\n'
    '_REQUIRED_INDICATOR_COLUMNS = {"MA20", "MA50", "MA200", "ATR14", "ATR50", "RSI14", "CMF", "OBV", "AD"}\n',
)
replace_once(
    "performance_cache.py",
    '    if cached is not None:\n        source_last = pd.Timestamp(source.index.max())',
    '    if cached is not None and not _REQUIRED_INDICATOR_COLUMNS.issubset(cached.columns):\n'
    '        cached = None\n\n'
    '    if cached is not None:\n        source_last = pd.Timestamp(source.index.max())',
)

# ---------------------------------------------------------------------------
# Fundamental applicability: ETFs are N/A, not fake 100% complete fundamentals.
# ---------------------------------------------------------------------------
replace_once(
    "fundamental_quality.py",
    'class FundamentalQuality:\n    ticker: str\n    industry: str = ""\n',
    'class FundamentalQuality:\n    ticker: str\n    industry: str = ""\n    applicable: bool = True\n',
)
replace_once(
    "fundamental_quality.py",
    '''    if is_etf:\n        return FundamentalQuality(\n            ticker=normalized_ticker,\n            quality_gate=True,\n            quality_reason="ETF跳过基本面门槛",\n            data_available=True,\n            institution_holding_status="PASS",\n            quality_data_completeness=1.0,\n            quality_gate_reason="ETF跳过基本面门槛",\n            quality_multiplier=QUALITY_MULTIPLIER_PASS,\n        )\n''',
    '''    if is_etf:\n        return FundamentalQuality(\n            ticker=normalized_ticker,\n            applicable=False,\n            quality_gate=True,\n            quality_reason="ETF基本面门槛不适用",\n            data_available=False,\n            institution_holding_status="UNKNOWN",\n            quality_data_completeness=0.0,\n            quality_gate_reason="ETF基本面门槛不适用",\n            quality_multiplier=QUALITY_MULTIPLIER_PASS,\n        )\n''',
)

# ---------------------------------------------------------------------------
# Entry location metrics: raw EntrySignal stays a technical price-state fact.
# ---------------------------------------------------------------------------
replace_once(
    "score.py",
    '''            "price_breakout": False,\n        }\n    atr = atr if _is_finite(atr) and atr > 0 else price * 0.03\n''',
    '''            "price_breakout": False,\n            "zone_distance_pct": np.nan,\n            "zone_distance_atr": np.nan,\n            "pullback_quality": 0.0,\n        }\n    atr = atr if _is_finite(atr) and atr > 0 else price * 0.03\n''',
)
replace_once(
    "score.py",
    '''    if high_zone < low_zone:\n        high_zone = low_zone\n    score = 0.0\n''',
    '''    if high_zone < low_zone:\n        high_zone = low_zone\n    if low_zone <= price <= high_zone:\n        zone_distance = 0.0\n    elif price > high_zone:\n        zone_distance = price - high_zone\n    else:\n        zone_distance = price - low_zone\n    zone_distance_pct = zone_distance / price * 100.0 if price > 0 else np.nan\n    zone_distance_atr = zone_distance / atr if atr > 0 else np.nan\n    if zone_distance == 0.0:\n        pullback_quality = 100.0\n    elif zone_distance > 0.0:\n        pullback_quality = _clamp(100.0 - max(zone_distance_atr, 0.0) * 30.0, 0.0, 100.0)\n    else:\n        pullback_quality = _clamp(65.0 - abs(zone_distance_atr) * 25.0, 0.0, 65.0)\n    score = 0.0\n''',
)
replace_once(
    "score.py",
    '''        "flow_confirmed": flow_confirmed,\n        "price_breakout": price_breakout,\n    }\n''',
    '''        "flow_confirmed": flow_confirmed,\n        "price_breakout": price_breakout,\n        "zone_distance_pct": zone_distance_pct,\n        "zone_distance_atr": zone_distance_atr,\n        "pullback_quality": pullback_quality,\n    }\n''',
)

# ---------------------------------------------------------------------------
# Scanner: robust ATR expansion, ETF theme classification, new provenance fields.
# ---------------------------------------------------------------------------
replace_once(
    "scanner.py",
    'import pandas as pd\nfrom tqdm import tqdm\n\nfrom analytics import enrich_results\n',
    'import pandas as pd\nfrom tqdm import tqdm\n\nfrom analytics import enrich_results\nfrom classification import etf_theme_key, model_classification\n',
)
replace_once(
    "scanner.py",
    '    entry_signal: str = "AVOID"\n    entry_zone: str = ""\n',
    '    entry_signal: str = "AVOID"\n    raw_entry_signal: str = "AVOID"\n    entry_zone: str = ""\n    entry_zone_distance_pct: float = np.nan\n    entry_zone_distance_atr: float = np.nan\n    pullback_quality_score: float = np.nan\n',
)
replace_once(
    "scanner.py",
    '    quality_data_available: bool = False\n    quality_institution_holding_status: str = "UNKNOWN"\n',
    '    quality_data_available: bool = False\n    quality_applicable: bool = True\n    quality_institution_holding_status: str = "UNKNOWN"\n',
)
replace_once(
    "scanner.py",
    '    backtest_engine: str = ""\n    composite_score: float = np.nan\n',
    '    backtest_engine: str = ""\n    backtest_status: str = ""\n    global_calibration_score: float = np.nan\n    global_calibration_confidence: float = 0.0\n    global_calibration_level: str = "none"\n    composite_score: float = np.nan\n',
)
replace_once(
    "scanner.py",
    '    ranking_reason: str = ""\n    institutional_percentile: float = np.nan\n',
    '    ranking_reason: str = ""\n    decision_state: str = "OBSERVE"\n    decision_reason: str = ""\n    trade_readiness: str = "观察"\n    research_tier: str = ""\n    model_classification: str = ""\n    institutional_percentile: float = np.nan\n',
)
replace_once(
    "scanner.py",
    '''def _parse_float(value: Any, default: float = np.nan) -> float:\n    try:\n        parsed = float(value)\n    except (TypeError, ValueError):\n        return default\n    return parsed if np.isfinite(parsed) else default\n\n\ndef _checkpoint_trade_date''',
    '''def _parse_float(value: Any, default: float = np.nan) -> float:\n    try:\n        parsed = float(value)\n    except (TypeError, ValueError):\n        return default\n    return parsed if np.isfinite(parsed) else default\n\n\ndef _latest_atr_from_ohlc(df: pd.DataFrame, period: int) -> float:\n    if df is None or len(df) < period or not {"High", "Low", "Close"}.issubset(df.columns):\n        return np.nan\n    high = pd.to_numeric(df["High"], errors="coerce")\n    low = pd.to_numeric(df["Low"], errors="coerce")\n    close = pd.to_numeric(df["Close"], errors="coerce")\n    prev_close = close.shift(1)\n    true_range = pd.concat(\n        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1\n    ).max(axis=1)\n    value = true_range.rolling(period, min_periods=period).mean().iloc[-1]\n    return _parse_float(value, np.nan)\n\n\ndef _checkpoint_trade_date''',
)
replace_once(
    "scanner.py",
    '''        atr50_value = (\n            _parse_float(df["ATR50"].iloc[-1], np.nan)\n            if "ATR50" in df.columns\n            else np.nan\n        )\n        atr_expansion = (\n            atr14_val / atr50_value\n            if np.isfinite(atr14_val)\n            and np.isfinite(atr50_value)\n            and atr50_value > 0\n            else np.nan\n        )\n''',
    '''        if not np.isfinite(atr14_val):\n            atr14_val = _latest_atr_from_ohlc(df, 14)\n        atr50_value = (\n            _parse_float(df["ATR50"].iloc[-1], np.nan)\n            if "ATR50" in df.columns\n            else np.nan\n        )\n        if not np.isfinite(atr50_value):\n            atr50_value = _latest_atr_from_ohlc(df, 50)\n        atr_expansion = (\n            atr14_val / atr50_value\n            if np.isfinite(atr14_val)\n            and np.isfinite(atr50_value)\n            and atr50_value > 0\n            else np.nan\n        )\n''',
)
replace_once(
    "scanner.py",
    '''        resolved_industry = str(\n            ticker_info.industry or getattr(quality, "industry", "") or ""\n        ).strip()\n        # TickFlow Free metadata does not consistently expose a separate sector.\n        # Reuse the verified fundamental industry as a fallback instead of leaving\n        # both classification fields blank.\n        resolved_sector = str(ticker_info.sector or resolved_industry or "").strip()\n''',
    '''        resolved_industry = str(\n            ticker_info.industry or getattr(quality, "industry", "") or ""\n        ).strip()\n        if ticker_info.is_etf:\n            resolved_sector = str(ticker_info.sector or "").strip() or etf_theme_key(\n                name=ticker_info.name,\n                industry=resolved_industry,\n                sector=ticker_info.sector,\n                ticker=ticker,\n            )\n        else:\n            # TickFlow Free metadata does not consistently expose a separate sector.\n            resolved_sector = str(ticker_info.sector or resolved_industry or "").strip()\n        resolved_classification = model_classification(\n            is_etf=ticker_info.is_etf,\n            name=ticker_info.name,\n            industry=resolved_industry,\n            sector=resolved_sector,\n            ticker=ticker,\n        )\n''',
)
replace_once(
    "scanner.py",
    '''            entry_score=_parse_float(entry["score"], 0.0),\n            entry_signal=entry["signal"],\n            entry_zone=entry_zone,\n''',
    '''            entry_score=_parse_float(entry["score"], 0.0),\n            entry_signal=entry["signal"],\n            raw_entry_signal=entry["signal"],\n            entry_zone=entry_zone,\n            entry_zone_distance_pct=_parse_float(entry.get("zone_distance_pct")),\n            entry_zone_distance_atr=_parse_float(entry.get("zone_distance_atr")),\n            pullback_quality_score=_parse_float(entry.get("pullback_quality")),\n''',
)
replace_once(
    "scanner.py",
    '''            quality_reason=quality.quality_reason,\n            quality_data_available=quality.data_available,\n            quality_institution_holding_status=quality.institution_holding_status,\n''',
    '''            quality_reason=quality.quality_reason,\n            quality_data_available=quality.data_available,\n            quality_applicable=getattr(quality, "applicable", not ticker_info.is_etf),\n            quality_institution_holding_status=quality.institution_holding_status,\n''',
)
replace_once(
    "scanner.py",
    '''            quality_multiplier=quality.quality_multiplier,\n        )\n''',
    '''            quality_multiplier=quality.quality_multiplier,\n            model_classification=resolved_classification,\n        )\n''',
)
# Resume compatibility for newly-added fields.
replace_once(
    "scanner.py",
    '''                        entry_signal=str(row.get("EntrySignal", "AVOID") or "AVOID"),\n                        entry_zone=str(row.get("EntryZone", "") or ""),\n''',
    '''                        entry_signal=str(row.get("EntrySignal", "AVOID") or "AVOID"),\n                        raw_entry_signal=str(row.get("RawEntrySignal", row.get("EntrySignal", "AVOID")) or "AVOID"),\n                        entry_zone=str(row.get("EntryZone", "") or ""),\n                        entry_zone_distance_pct=_parse_float(row.get("EntryZoneDistancePct", np.nan)),\n                        entry_zone_distance_atr=_parse_float(row.get("EntryZoneDistanceATR", np.nan)),\n                        pullback_quality_score=_parse_float(row.get("PullbackQualityScore", np.nan)),\n''',
)
replace_once(
    "scanner.py",
    '''                        quality_data_available=_parse_bool(\n                            row.get("QualityDataAvailable", False)\n                        ),\n                        quality_institution_holding_status=str(\n''',
    '''                        quality_data_available=_parse_bool(\n                            row.get("QualityDataAvailable", False)\n                        ),\n                        quality_applicable=_parse_bool(\n                            row.get("QualityApplicable", not _parse_bool(row.get("IsETF", False))),\n                            not _parse_bool(row.get("IsETF", False)),\n                        ),\n                        quality_institution_holding_status=str(\n''',
)
replace_once(
    "scanner.py",
    '''                        backtest_engine=str(\n                            row.get("BacktestEngine", "") or ""\n                        ),\n''',
    '''                        backtest_engine=str(\n                            row.get("BacktestEngine", "") or ""\n                        ),\n                        backtest_status=str(row.get("BacktestStatus", "") or ""),\n                        global_calibration_score=_parse_float(row.get("GlobalCalibrationScore", np.nan)),\n                        global_calibration_confidence=_parse_float(row.get("GlobalCalibrationConfidence", 0.0), 0.0),\n                        global_calibration_level=str(row.get("GlobalCalibrationLevel", "none") or "none"),\n''',
)
replace_once(
    "scanner.py",
    '''                        ranking_reason=str(row.get("RankingReason", "") or ""),\n                        institutional_percentile=_parse_float(\n''',
    '''                        ranking_reason=str(row.get("RankingReason", "") or ""),\n                        decision_state=str(row.get("DecisionState", "OBSERVE") or "OBSERVE"),\n                        decision_reason=str(row.get("DecisionReason", "") or ""),\n                        trade_readiness=str(row.get("TradeReadiness", row.get("RankingEligibility", "观察")) or "观察"),\n                        research_tier=str(row.get("ResearchTier", "") or ""),\n                        model_classification=str(row.get("ModelClassification", row.get("Industry", row.get("Sector", ""))) or ""),\n                        institutional_percentile=_parse_float(\n''',
)

# ---------------------------------------------------------------------------
# Analytics: unified model classification, relative-strength-aware sector factor,
# breakout quality only on breakout states, calibration provenance.
# ---------------------------------------------------------------------------
replace_once(
    "analytics.py",
    'import pandas as pd\n\nfrom config import (',
    'import pandas as pd\n\nfrom classification import model_classification\nfrom config import (',
)
replace_once(
    "analytics.py",
    '    SCAN_THREADS,\n)',
    '    SCAN_THREADS,\n    SECTOR_CONFIRMATION_MIN_FACTOR,\n    SECTOR_CONFIRMATION_INDUSTRY_WEIGHT,\n    SECTOR_CONFIRMATION_RELATIVE_WEIGHT,\n)',
)
replace_once(
    "analytics.py",
    '    calibration_scores_for_frame,\n',
    '    calibration_details_for_frame,\n    calibration_scores_for_frame,\n',
)
replace_once(
    "analytics.py",
    '''def _bounded_score(value: float, low: float, high: float) -> float:\n    if not np.isfinite(value) or high <= low:\n        return 0.5\n    return float(np.clip((value - low) / (high - low), 0.0, 1.0))\n\n\ndef _finite_float''',
    '''def _bounded_score(value: float, low: float, high: float) -> float:\n    if not np.isfinite(value) or high <= low:\n        return 0.5\n    return float(np.clip((value - low) / (high - low), 0.0, 1.0))\n\n\ndef _sector_confirmation_factor(peer_return: float, relative_strength: float) -> float:\n    if not np.isfinite(peer_return):\n        return 1.0\n    industry_component = _bounded_score(peer_return, -20.0, 20.0)\n    relative_component = (\n        _bounded_score(relative_strength, -15.0, 15.0)\n        if np.isfinite(relative_strength)\n        else 0.5\n    )\n    total_weight = max(\n        float(SECTOR_CONFIRMATION_INDUSTRY_WEIGHT + SECTOR_CONFIRMATION_RELATIVE_WEIGHT),\n        1e-9,\n    )\n    combined = (\n        industry_component * float(SECTOR_CONFIRMATION_INDUSTRY_WEIGHT)\n        + relative_component * float(SECTOR_CONFIRMATION_RELATIVE_WEIGHT)\n    ) / total_weight\n    floor = float(np.clip(SECTOR_CONFIRMATION_MIN_FACTOR, 0.0, 1.0))\n    return round(float(np.clip(floor + (1.0 - floor) * combined, floor, 1.0)), 4)\n\n\ndef _finite_float''',
)
replace_once(
    "analytics.py",
    '''                classification = str(result.industry or result.sector or "").strip()\n                if classification and np.isfinite(relative):\n                    industry_returns.setdefault(classification, {})[result.ticker] = relative\n''',
    '''                classification = model_classification(\n                    is_etf=bool(result.is_etf),\n                    name=result.name,\n                    industry=result.industry,\n                    sector=result.sector,\n                    ticker=result.ticker,\n                )\n                result.model_classification = classification\n                if result.is_etf and not str(result.sector or "").strip() and classification:\n                    result.sector = classification\n                if classification and np.isfinite(relative):\n                    industry_returns.setdefault(classification, {})[result.ticker] = relative\n''',
)
replace_once(
    "analytics.py",
    '''        classification = str(result.industry or result.sector or "").strip()\n        if not classification:\n''',
    '''        classification = model_classification(\n            is_etf=bool(result.is_etf),\n            name=result.name,\n            industry=result.industry,\n            sector=result.sector,\n            ticker=result.ticker,\n        )\n        result.model_classification = classification\n        if result.is_etf and not str(result.sector or "").strip() and classification:\n            result.sector = classification\n        if not classification:\n''',
)
replace_once(
    "analytics.py",
    '''        if np.isfinite(peer):\n            result.sector_confirmation_factor = round(\n                float(\n                    np.clip(\n                        0.2 + _bounded_score(peer, -20.0, 20.0) * 0.8,\n                        0.2,\n                        1.0,\n                    )\n                ),\n                4,\n            )\n        else:\n            result.sector_confirmation_factor = 1.0\n''',
    '''        if np.isfinite(peer):\n            relative_strength = value - peer if np.isfinite(value) else np.nan\n            result.sector_confirmation_factor = _sector_confirmation_factor(\n                peer, relative_strength\n            )\n        else:\n            result.sector_confirmation_factor = 1.0\n''',
)
replace_once(
    "analytics.py",
    '''        breakout_factor = float(\n            np.clip(_finite_float(result.breakout_quality_factor, 1.0), 0.0, 1.0)\n        )\n        technical_score = (\n            base_score\n            * (0.7 + 0.3 * sector_factor)\n            * (0.8 + 0.2 * breakout_factor)\n        )\n''',
    '''        breakout_factor = float(\n            np.clip(_finite_float(result.breakout_quality_factor, 1.0), 0.0, 1.0)\n        )\n        breakout_state = str(result.entry_signal or "").upper() in {\n            "BREAKOUT_CONFIRM", "PRICE_BREAKOUT", "WAIT_VOLUME_CONFIRM"\n        }\n        effective_breakout_factor = breakout_factor if breakout_state else 1.0\n        technical_score = (\n            base_score\n            * (0.7 + 0.3 * sector_factor)\n            * (0.8 + 0.2 * effective_breakout_factor)\n        )\n''',
)
# Backtest provenance is defined even when a ticker produced zero historical signals.
replace_once(
    "analytics.py",
    '''    observed = pd.to_numeric(frame["BacktestSamples"], errors="coerce").fillna(0.0)\n    effective_observed = (\n''',
    '''    observed = pd.to_numeric(frame["BacktestSamples"], errors="coerce").fillna(0.0)\n    frame["BacktestMode"] = (\n        frame.get("BacktestMode", pd.Series("", index=frame.index))\n        .fillna("").astype(str).str.strip().replace("", str(summary.mode).upper())\n    )\n    frame["BacktestEngine"] = (\n        frame.get("BacktestEngine", pd.Series("", index=frame.index))\n        .fillna("").astype(str).str.strip().replace("", str(summary.engine))\n    )\n    frame["BacktestCacheHit"] = frame.get(\n        "BacktestCacheHit", pd.Series(False, index=frame.index)\n    ).fillna(False).astype(bool)\n    frame["BacktestStatus"] = np.where(observed.gt(0.0), "SAMPLES", "NO_SIGNAL_SAMPLES")\n    effective_observed = (\n''',
)
replace_once(
    "analytics.py",
    '''    peer_score, peer_confidence = calibration_scores_for_frame(\n        frame, getattr(summary, "global_calibration", None)\n    )\n    peer_available = peer_confidence.gt(0.0)\n    peer_anchor = peer_score.where(peer_available, BACKTEST_NEUTRAL_SCORE)\n''',
    '''    calibration_details = calibration_details_for_frame(\n        frame, getattr(summary, "global_calibration", None)\n    )\n    peer_score = calibration_details["score"]\n    peer_confidence = calibration_details["confidence"]\n    frame["GlobalCalibrationScore"] = peer_score.round(4)\n    frame["GlobalCalibrationConfidence"] = peer_confidence.round(4)\n    frame["GlobalCalibrationLevel"] = calibration_details["level"].astype(str)\n    peer_available = peer_confidence.gt(0.0)\n    peer_anchor = peer_score.where(peer_available, BACKTEST_NEUTRAL_SCORE)\n''',
)
replace_once(
    "analytics.py",
    '''    breakout_factor = pd.to_numeric(\n        frame.get("BreakoutQualityFactor", pd.Series(1.0, index=frame.index)),\n        errors="coerce",\n    ).fillna(1.0).clip(0.0, 1.0)\n    frame["BreakoutQualityFactor"] = breakout_factor\n    institutional_component = (\n        frame["FailureAdjustedScore"]\n        * sector_multiplier\n        * recency_multiplier\n        * (0.8 + 0.2 * breakout_factor)\n    )\n''',
    '''    breakout_factor = pd.to_numeric(\n        frame.get("BreakoutQualityFactor", pd.Series(1.0, index=frame.index)),\n        errors="coerce",\n    ).fillna(1.0).clip(0.0, 1.0)\n    frame["BreakoutQualityFactor"] = breakout_factor\n    breakout_state = frame["EntrySignal"].isin(\n        {"BREAKOUT_CONFIRM", "PRICE_BREAKOUT", "WAIT_VOLUME_CONFIRM"}\n    )\n    effective_breakout_factor = breakout_factor.where(breakout_state, 1.0)\n    institutional_component = (\n        frame["FailureAdjustedScore"]\n        * sector_multiplier\n        * recency_multiplier\n        * (0.8 + 0.2 * effective_breakout_factor)\n    )\n''',
)
# Ensure repeated backtest application does not preserve stale v18 provenance.
replace_once(
    "analytics.py",
    '        "BacktestEngine",\n        "InstitutionalTier",',
    '        "BacktestEngine",\n        "BacktestStatus",\n        "GlobalCalibrationScore",\n        "GlobalCalibrationConfidence",\n        "GlobalCalibrationLevel",\n        "InstitutionalTier",',
)

# ---------------------------------------------------------------------------
# Calibration details: keep old 2-Series API and add provenance-aware API.
# ---------------------------------------------------------------------------
regex_once(
    "model_calibration.py",
    r'def calibration_scores_for_frame\(\n    frame: pd\.DataFrame,\n    rows: list\[dict\[str, Any\]\] \| None,\n\) -> tuple\[pd\.Series, pd\.Series\]:\n.*?return pd\.Series\(scores, index=frame\.index, dtype=float\), pd\.Series\(confidences, index=frame\.index, dtype=float\)\n',
    '''def calibration_details_for_frame(\n    frame: pd.DataFrame,\n    rows: list[dict[str, Any]] | None,\n) -> pd.DataFrame:\n    if frame.empty:\n        return pd.DataFrame(\n            {\n                "score": pd.Series(dtype=float),\n                "confidence": pd.Series(dtype=float),\n                "level": pd.Series(dtype=str),\n            },\n            index=frame.index,\n        )\n    if not rows:\n        return pd.DataFrame(\n            {\n                "score": pd.Series(50.0, index=frame.index, dtype=float),\n                "confidence": pd.Series(0.0, index=frame.index, dtype=float),\n                "level": pd.Series("none", index=frame.index, dtype=str),\n            }\n        )\n    scores: list[float] = []\n    confidences: list[float] = []\n    levels: list[str] = []\n    asset_values = frame.get("AssetType", frame.get("asset_type", pd.Series("stock", index=frame.index)))\n    signal_values = frame.get("EntrySignal", frame.get("entry_signal", pd.Series("UNKNOWN", index=frame.index)))\n    model_scores = pd.to_numeric(\n        frame.get("FinalScore", frame.get("score", pd.Series(np.nan, index=frame.index))),\n        errors="coerce",\n    )\n    regime_values = frame.get("MarketRegime", frame.get("market_regime", pd.Series("UNKNOWN", index=frame.index)))\n    setup_values = pd.to_numeric(\n        frame.get("BaseScore", frame.get("setup_score", pd.Series(np.nan, index=frame.index))),\n        errors="coerce",\n    )\n    for asset, signal, score, regime, setup in zip(\n        asset_values, signal_values, model_scores, regime_values, setup_values\n    ):\n        value, confidence, level = resolve_global_calibration(\n            str(asset),\n            str(signal),\n            float(score) if pd.notna(score) else np.nan,\n            rows,\n            market_regime=str(regime),\n            setup_score=float(setup) if pd.notna(setup) else np.nan,\n        )\n        scores.append(value)\n        confidences.append(confidence)\n        levels.append(level)\n    return pd.DataFrame(\n        {\n            "score": pd.Series(scores, index=frame.index, dtype=float),\n            "confidence": pd.Series(confidences, index=frame.index, dtype=float),\n            "level": pd.Series(levels, index=frame.index, dtype=str),\n        }\n    )\n\n\ndef calibration_scores_for_frame(\n    frame: pd.DataFrame,\n    rows: list[dict[str, Any]] | None,\n) -> tuple[pd.Series, pd.Series]:\n    details = calibration_details_for_frame(frame, rows)\n    return details["score"], details["confidence"]\n''',
)

# ---------------------------------------------------------------------------
# Signal lifecycle: EntrySignal is immutable technical state; decision state is
# resolved separately from quality/lifecycle/risk gates.
# ---------------------------------------------------------------------------
regex_once(
    "signal_lifecycle.py",
    r'def validate_signal_consistency\(frame: pd\.DataFrame\) -> pd\.DataFrame:\n.*?\n\ndef finalize_signal_ranking',
    '''def validate_signal_consistency(frame: pd.DataFrame) -> pd.DataFrame:\n    """Validate technical confirmation without rewriting the raw EntrySignal."""\n    result = frame.copy()\n    signal = _text_series(result, "EntrySignal", "AVOID").str.upper()\n    result["RawEntrySignal"] = _text_series(result, "RawEntrySignal", "")\n    result.loc[result["RawEntrySignal"].eq(""), "RawEntrySignal"] = signal\n    adjustments = _text_series(result, "SignalAdjustmentReason", "")\n\n    volume_ratio = _number(\n        result.get("BreakoutVolumeRatio", pd.Series(np.nan, index=result.index)), np.nan\n    )\n    volume_score = _number(\n        result.get("VolumeScore", pd.Series(np.nan, index=result.index)), np.nan\n    )\n    observed_volume_confirmation = volume_ratio.ge(\n        BREAKOUT_CONFIRM_MIN_VOLUME_RATIO\n    ) | volume_score.ge(BREAKOUT_CONFIRM_MIN_VOLUME_SCORE)\n    volume_metrics_available = volume_ratio.notna() | volume_score.notna()\n    if "BreakoutVolumeConfirmed" in result:\n        volume_confirmed = _bool_series(result, "BreakoutVolumeConfirmed") & (\n            ~volume_metrics_available | observed_volume_confirmation\n        )\n    else:\n        volume_confirmed = observed_volume_confirmation\n\n    cmf_positive = _bool_series(result, "CMF_Pos") | _number(\n        result.get("CMF", pd.Series(np.nan, index=result.index)), np.nan\n    ).gt(0.0)\n    ad_positive = _bool_series(result, "AD_SlopePos") | _number(\n        result.get("AD_Slope", pd.Series(np.nan, index=result.index)), np.nan\n    ).gt(0.0)\n    observed_flow_confirmation = (\n        cmf_positive | ad_positive | _bool_series(result, "OBV_Div")\n    )\n    flow_metrics_available = any(\n        column in result\n        for column in ("CMF_Pos", "CMF", "AD_SlopePos", "AD_Slope", "OBV_Div")\n    )\n    if "BreakoutFlowConfirmed" in result:\n        flow_confirmed = _bool_series(result, "BreakoutFlowConfirmed") & (\n            (not flow_metrics_available) | observed_flow_confirmation\n        )\n    else:\n        flow_confirmed = observed_flow_confirmation\n\n    weak_breakout = signal.eq("BREAKOUT_CONFIRM") & ~(\n        volume_confirmed & flow_confirmed\n    )\n    adjustments = _append_reason(\n        adjustments, weak_breakout, "突破状态缺少量能或资金确认，决策层转观察"\n    )\n    result["EntrySignal"] = signal\n    result["BreakoutVolumeConfirmed"] = volume_confirmed\n    result["BreakoutFlowConfirmed"] = flow_confirmed\n    result["PriceBreakout"] = result.get(\n        "PriceBreakout",\n        signal.isin({"PRICE_BREAKOUT", "BREAKOUT_CONFIRM"}),\n    ).fillna(False)\n    result["SignalAdjustmentReason"] = adjustments\n    return result\n\n\ndef finalize_signal_ranking''',
)
replace_once(
    "signal_lifecycle.py",
    '''    if "InstitutionalScore" not in result:\n        result["InstitutionalScore"] = _number(result["FinalScore"])\n\n    status = _holding_status(result)\n''',
    '''    if "InstitutionalScore" not in result:\n        result["InstitutionalScore"] = _number(result["FinalScore"])\n\n    is_etf = _bool_series(result, "IsETF") | _text_series(\n        result, "AssetType", ""\n    ).str.lower().eq("etf")\n    quality_applicable = (\n        _bool_series(result, "QualityApplicable", True)\n        if "QualityApplicable" in result\n        else ~is_etf\n    )\n    quality_applicable = quality_applicable & ~is_etf\n    result["QualityApplicable"] = quality_applicable\n\n    status = _holding_status(result)\n''',
)
replace_once(
    "signal_lifecycle.py",
    '''    known_fail = (\n        (roe_available & ~_bool_series(result, "QualityROE", True))\n        | (margin_available & ~_bool_series(result, "QualityGrossMargin", True))\n        | (profit_available & ~_bool_series(result, "QualityNetProfit", True))\n        | status.eq("FAIL")\n        | supplied_quality_fail\n    )\n    any_unknown = status.eq("UNKNOWN") | ~(\n        roe_available & margin_available & profit_available\n    )\n    result["QualityGate"] = ~known_fail\n    result["QualityMultiplier"] = np.select(\n        [known_fail, any_unknown],\n        [QUALITY_MULTIPLIER_FAIL, QUALITY_MULTIPLIER_UNKNOWN],\n        default=QUALITY_MULTIPLIER_PASS,\n    )\n''',
    '''    known_fail = quality_applicable & (\n        (roe_available & ~_bool_series(result, "QualityROE", True))\n        | (margin_available & ~_bool_series(result, "QualityGrossMargin", True))\n        | (profit_available & ~_bool_series(result, "QualityNetProfit", True))\n        | status.eq("FAIL")\n        | supplied_quality_fail\n    )\n    any_unknown = quality_applicable & (\n        status.eq("UNKNOWN") | ~(roe_available & margin_available & profit_available)\n    )\n    result["QualityGate"] = ~known_fail\n    result["QualityMultiplier"] = np.select(\n        [~quality_applicable, known_fail, any_unknown],\n        [1.0, QUALITY_MULTIPLIER_FAIL, QUALITY_MULTIPLIER_UNKNOWN],\n        default=QUALITY_MULTIPLIER_PASS,\n    )\n''',
)
# Remove the later duplicate is_etf declaration and make quality block applicability-aware.
replace_once(
    "signal_lifecycle.py",
    '''    is_etf = _bool_series(result, "IsETF") | _text_series(\n        result, "AssetType", ""\n    ).str.lower().eq("etf")\n    quality_action_block = ~result["QualityGate"] | (\n        result["QualityDataCompleteness"].lt(QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE)\n        & ~is_etf\n    )\n''',
    '''    quality_action_block = quality_applicable & (\n        ~result["QualityGate"]\n        | result["QualityDataCompleteness"].lt(QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE)\n    )\n''',
)
# Decision resolver: keep EntrySignal immutable and gate execution separately.
replace_once(
    "signal_lifecycle.py",
    '''    trade_ready = (\n        signal.isin({"BUY_NOW", "BREAKOUT_CONFIRM"})\n        & (passed_filters | filter_override)\n        & ~lifecycle_failed\n        & ~stage_risk\n        & ~trap_observe\n        & ~quality_action_block\n        & ~data_risk\n        & ~stale_data\n        & ~minimum_score_risk\n    )\n    result["RankingEligibility"] = np.select(\n        [\n            avoid\n            | trap_risk\n            | lifecycle.isin({"派发", "DISTRIBUTION"})\n            | stale_data,\n            trade_ready,\n        ],\n        ["风险过滤", "推荐"],\n        default="观察",\n    )\n''',
    '''    breakout_confirmation_ok = (\n        ~signal.eq("BREAKOUT_CONFIRM")\n        | (\n            _bool_series(result, "BreakoutVolumeConfirmed", False)\n            & _bool_series(result, "BreakoutFlowConfirmed", False)\n        )\n    )\n    trade_ready = (\n        signal.isin({"BUY_NOW", "BREAKOUT_CONFIRM"})\n        & breakout_confirmation_ok\n        & (passed_filters | filter_override)\n        & ~lifecycle_failed\n        & ~stage_risk\n        & ~trap_observe\n        & ~quality_action_block\n        & ~data_risk\n        & ~stale_data\n        & ~minimum_score_risk\n    )\n    hard_decision_block = (\n        avoid\n        | trap_risk\n        | lifecycle.isin({"派发", "DISTRIBUTION"})\n        | stale_data\n    )\n    decision_state = pd.Series("OBSERVE", index=result.index)\n    decision_state.loc[hard_decision_block] = "BLOCKED"\n    decision_state.loc[trade_ready] = "READY"\n    result["DecisionState"] = decision_state\n    result["RankingEligibility"] = decision_state.map(\n        {"READY": "推荐", "OBSERVE": "观察", "BLOCKED": "风险过滤"}\n    ).fillna("观察")\n    result["TradeReadiness"] = result["RankingEligibility"]\n''',
)
replace_once(
    "signal_lifecycle.py",
    '''    result["TradeReadinessReason"] = readiness_reason\n    result["OpportunityStage"] = lifecycle\n''',
    '''    result["TradeReadinessReason"] = readiness_reason\n    result["DecisionReason"] = readiness_reason\n    operation_advice = _text_series(result, "OperationAdvice", "")\n    operation_advice.loc[decision_state.eq("BLOCKED")] = "当前存在硬风险条件，暂不参与。"\n    operation_advice.loc[decision_state.eq("OBSERVE") & signal.eq("BUY_NOW")] = (\n        "价格已进入买入区间，但质量、过滤或生命周期条件未满足，继续观察。"\n    )\n    operation_advice.loc[decision_state.eq("OBSERVE") & signal.eq("BREAKOUT_CONFIRM")] = (\n        "突破技术状态成立，但执行门槛未全部满足，等待条件完善。"\n    )\n    operation_advice.loc[decision_state.eq("OBSERVE") & signal.eq("WAIT_PULLBACK")] = (\n        "等待价格回踩买入区间，不追高。"\n    )\n    operation_advice.loc[decision_state.eq("READY") & signal.eq("BUY_NOW")] = (\n        "价格处于买入区间且执行门槛满足，可按计划分批执行。"\n    )\n    result["OperationAdvice"] = operation_advice\n    result["OpportunityStage"] = lifecycle\n''',
)
replace_once(
    "signal_lifecycle.py",
    '''    data_confidence = (\n        (\n            0.90\n            + 0.06 * result["QualityDataCompleteness"]\n            + 0.04 * score_coverage.clip(0.0, 1.0)\n        ).clip(0.85, 1.0)\n        * freshness_factor\n    ).clip(DATA_FRESHNESS_STALE_FACTOR, 1.0)\n''',
    '''    stock_data_confidence = (\n        0.90\n        + 0.06 * result["QualityDataCompleteness"]\n        + 0.04 * score_coverage.clip(0.0, 1.0)\n    ).clip(0.85, 1.0)\n    etf_data_confidence = (0.96 + 0.04 * score_coverage.clip(0.0, 1.0)).clip(0.85, 1.0)\n    data_confidence = pd.Series(\n        np.where(quality_applicable, stock_data_confidence, etf_data_confidence),\n        index=result.index,\n    )\n    data_confidence = (data_confidence * freshness_factor).clip(\n        DATA_FRESHNESS_STALE_FACTOR, 1.0\n    )\n''',
)
replace_once(
    "signal_lifecycle.py",
    '''    result["RankingScore"] = (\n        base_score\n        * entry_factor\n        * hard_penalty\n        * chase_factor\n        * data_confidence\n        * recency_multiplier\n    ).round(4)\n''',
    '''    decision_factor = decision_state.map(\n        {"READY": 1.0, "OBSERVE": 0.95, "BLOCKED": 0.75}\n    ).fillna(0.95)\n    result["RankingScore"] = (\n        base_score\n        * entry_factor\n        * hard_penalty\n        * chase_factor\n        * data_confidence\n        * recency_multiplier\n        * decision_factor\n    ).round(4)\n''',
)
replace_once(
    "signal_lifecycle.py",
    '''        & result["QualityDataCompleteness"].ge(\n            INSTITUTIONAL_TIER_MIN_DATA_CONFIDENCE\n        )\n    ] = "A级机构启动"\n''',
    '''        & (\n            ~quality_applicable\n            | result["QualityDataCompleteness"].ge(\n                INSTITUTIONAL_TIER_MIN_DATA_CONFIDENCE\n            )\n        )\n    ] = "A级机构启动"\n''',
)
replace_once(
    "signal_lifecycle.py",
    '''    result["InstitutionalTier"] = tier\n    result["InstitutionalTierReason"] = "分位和绝对分均未达到门槛"\n''',
    '''    result["InstitutionalTier"] = tier\n    result["ResearchTier"] = tier.map(\n        {\n            "A级机构启动": "A",\n            "B级观察": "B",\n            "C级价值观察": "C",\n            INSTITUTIONAL_TIER_WAIT_LABEL: "WAIT",\n            INSTITUTIONAL_TIER_TRAP_LABEL: "TRAP",\n        }\n    ).fillna("WAIT")\n    result["InstitutionalTierReason"] = "分位和绝对分均未达到研究等级门槛"\n''',
)
replace_once(
    "signal_lifecycle.py",
    '        "市场前10%且满足绝对质量、时效与信号门槛"\n',
    '        "市场前10%且满足研究质量、时效与信号门槛"\n',
)

# ---------------------------------------------------------------------------
# Report/export: preserve old columns and append v18 decision/provenance fields.
# ---------------------------------------------------------------------------
replace_once(
    "report.py",
    '    ETF_THEME_MAX_PER_TOP_LIST,\n    OUTPUT_DIR,',
    '    ETF_THEME_MAX_PER_TOP_LIST,\n    STOCK_INDUSTRY_MAX_PER_TOP_LIST,\n    OUTPUT_DIR,',
)
replace_once(
    "report.py",
    'from scanner import ScanReport, ScanResult\n',
    'from classification import etf_theme_key\nfrom scanner import ScanReport, ScanResult\n',
)
replace_once(
    "report.py",
    '''                "EntrySignal": r.entry_signal,\n                "EntryZone": r.entry_zone,\n''',
    '''                "EntrySignal": r.entry_signal,\n                "RawEntrySignal": r.raw_entry_signal or r.entry_signal,\n                "DecisionState": r.decision_state,\n                "DecisionReason": r.decision_reason,\n                "EntryZone": r.entry_zone,\n                "EntryZoneDistancePct": round(r.entry_zone_distance_pct, 4) if np.isfinite(r.entry_zone_distance_pct) else None,\n                "EntryZoneDistanceATR": round(r.entry_zone_distance_atr, 4) if np.isfinite(r.entry_zone_distance_atr) else None,\n                "PullbackQualityScore": round(r.pullback_quality_score, 2) if np.isfinite(r.pullback_quality_score) else None,\n''',
)
replace_once(
    "report.py",
    '''                "QualityDataAvailable": r.quality_data_available,\n                "InstitutionHoldingStatus": r.quality_institution_holding_status,\n''',
    '''                "QualityDataAvailable": r.quality_data_available,\n                "QualityApplicable": r.quality_applicable,\n                "InstitutionHoldingStatus": r.quality_institution_holding_status,\n''',
)
replace_once(
    "report.py",
    '''                "BacktestEngine": r.backtest_engine,\n                "UniverseType": r.universe_type,\n''',
    '''                "BacktestEngine": r.backtest_engine,\n                "BacktestStatus": r.backtest_status,\n                "GlobalCalibrationScore": round(r.global_calibration_score, 4) if np.isfinite(r.global_calibration_score) else None,\n                "GlobalCalibrationConfidence": round(r.global_calibration_confidence, 4),\n                "GlobalCalibrationLevel": r.global_calibration_level,\n                "UniverseType": r.universe_type,\n''',
)
replace_once(
    "report.py",
    '''                "RankingPenaltyReason": r.ranking_penalty_reason,\n                "SignalAdjustmentReason": r.signal_adjustment_reason,\n                "OpportunityStage": r.opportunity_stage,\n''',
    '''                "RankingPenaltyReason": r.ranking_penalty_reason,\n                "DecisionState": r.decision_state,\n                "DecisionReason": r.decision_reason,\n                "TradeReadiness": r.trade_readiness or r.ranking_eligibility,\n                "ResearchTier": r.research_tier,\n                "ModelClassification": r.model_classification,\n                "SignalAdjustmentReason": r.signal_adjustment_reason,\n                "OpportunityStage": r.opportunity_stage,\n''',
)
# Shared ETF theme helper eliminates literal NAN fragments.
regex_once(
    "report.py",
    r'def _etf_theme_key\(row: pd\.Series\) -> str:\n.*?\n\ndef _diversify_ranked_candidates',
    '''def _etf_theme_key(row: pd.Series) -> str:\n    if not (_truthy(row.get("IsETF", False)) or str(row.get("AssetType", "")).strip().lower() == "etf"):\n        return ""\n    return etf_theme_key(\n        name=row.get("Name", ""),\n        industry=row.get("Industry", ""),\n        sector=row.get("Sector", ""),\n        ticker=row.get("Ticker", ""),\n    )\n\n\ndef _diversify_ranked_candidates''',
)
replace_once(
    "report.py",
    '''def _diversify_ranked_candidates(\n    frame: pd.DataFrame,\n    limit: int,\n    max_per_theme: int = ETF_THEME_MAX_PER_TOP_LIST,\n) -> pd.DataFrame:\n''',
    '''def _diversify_ranked_candidates(\n    frame: pd.DataFrame,\n    limit: int,\n    max_per_theme: int = ETF_THEME_MAX_PER_TOP_LIST,\n    max_per_stock_industry: int = STOCK_INDUSTRY_MAX_PER_TOP_LIST,\n) -> pd.DataFrame:\n''',
)
replace_once(
    "report.py",
    '''    theme_counts: dict[str, int] = {}\n    selected: list[int] = []\n    for index, row in working.iterrows():\n        theme = str(row.get("ETFTheme", "") or "").strip()\n        if theme:\n            if theme_counts.get(theme, 0) >= max(1, int(max_per_theme)):\n                continue\n            theme_counts[theme] = theme_counts.get(theme, 0) + 1\n        selected.append(index)\n''',
    '''    theme_counts: dict[str, int] = {}\n    stock_industry_counts: dict[str, int] = {}\n    selected: list[int] = []\n    for index, row in working.iterrows():\n        theme = str(row.get("ETFTheme", "") or "").strip()\n        if theme:\n            if theme_counts.get(theme, 0) >= max(1, int(max_per_theme)):\n                continue\n            theme_counts[theme] = theme_counts.get(theme, 0) + 1\n        else:\n            classification = str(\n                row.get("ModelClassification", "")\n                or row.get("Industry", "")\n                or row.get("Sector", "")\n                or ""\n            ).strip()\n            if classification and classification.lower() not in {"nan", "none"}:\n                if stock_industry_counts.get(classification, 0) >= max(1, int(max_per_stock_industry)):\n                    continue\n                stock_industry_counts[classification] = stock_industry_counts.get(classification, 0) + 1\n        selected.append(index)\n''',
)

# ---------------------------------------------------------------------------
# GUI metadata labels for new fields (no filter compatibility break).
# ---------------------------------------------------------------------------
replace_once(
    "gui.py",
    '''        "ResearchPoolRank": "研究池排名",\n    }\n)\n''',
    '''        "ResearchPoolRank": "研究池排名",\n        "DecisionState": "决策状态",\n        "DecisionReason": "决策说明",\n        "TradeReadiness": "交易就绪",\n        "ResearchTier": "研究等级",\n        "ModelClassification": "模型分类",\n        "EntryZoneDistancePct": "距买区%",\n        "EntryZoneDistanceATR": "距买区ATR",\n        "PullbackQualityScore": "回踩质量",\n        "QualityApplicable": "基本面适用",\n        "BacktestStatus": "回测状态",\n        "GlobalCalibrationScore": "全局校准分",\n        "GlobalCalibrationConfidence": "全局校准可信度",\n        "GlobalCalibrationLevel": "全局校准层级",\n    }\n)\n''',
)

print("model v18 migration applied")
