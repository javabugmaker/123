from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"postfix pattern not found in {path}: {old[:140]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# model_v19_migrate intentionally inserts filter diagnostics next to the old
# score_ticker marker. Restore the score call after the diagnostics block.
replace_once(
    "scanner.py",
    '''        failed_filter_names = [\n            name\n            for name, state in {**base_filter_states, **accumulation_states, **structure_states}.items()\n            if not state\n        ]\n\n        style = classify_style(df, is_etf=ticker_info.is_etf)\n''',
    '''        failed_filter_names = [\n            name\n            for name, state in {**base_filter_states, **accumulation_states, **structure_states}.items()\n            if not state\n        ]\n\n        sb = score_ticker(df, is_etf=ticker_info.is_etf)\n        style = classify_style(df, is_etf=ticker_info.is_etf)\n''',
)

# pandas/numpy boolean reductions can return np.bool_. Persist ordinary bools in
# ScanResult so JSON/CSV consumers and type contracts stay stable.
replace_once(
    "scanner.py",
    '''        signal_confirmed = sum(accumulation_states.values()) >= 2 and any(structure_states.values())\n''',
    '''        signal_confirmed = bool(\n            sum(bool(value) for value in accumulation_states.values()) >= 2\n            and any(bool(value) for value in structure_states.values())\n        )\n''',
)

# v18 already restored model_classification later in ScanResult(...). Keep the
# new v19 restore fields without adding the keyword twice.
replace_once(
    "scanner.py",
    '''                        quality_multiplier=_parse_float(\n                            row.get("QualityMultiplier", 0.95), 0.95\n                        ),\n                        model_classification=str(row.get("ModelClassification", "") or ""),\n                        etf_tracking_key=str(row.get("ETFTrackingKey", "") or ""),\n                        theme_cluster=str(row.get("ThemeCluster", "") or ""),\n                        technical_institutional_score=_parse_float(row.get("TechnicalInstitutionalScore", np.nan)),\n                        asset_percentile=_parse_float(row.get("AssetPercentile", np.nan)),\n                        cross_asset_score=_parse_float(row.get("CrossAssetScore", np.nan)),\n                        sector_confirmation_factor=_parse_float(\n''',
    '''                        quality_multiplier=_parse_float(\n                            row.get("QualityMultiplier", 0.95), 0.95\n                        ),\n                        etf_tracking_key=str(row.get("ETFTrackingKey", "") or ""),\n                        theme_cluster=str(row.get("ThemeCluster", "") or ""),\n                        technical_institutional_score=_parse_float(row.get("TechnicalInstitutionalScore", np.nan)),\n                        asset_percentile=_parse_float(row.get("AssetPercentile", np.nan)),\n                        cross_asset_score=_parse_float(row.get("CrossAssetScore", np.nan)),\n                        sector_confirmation_factor=_parse_float(\n''',
)

# Cross-asset percentiles are meaningful only with a sufficiently populated
# asset cohort and a real positive institutional score. Small synthetic/user
# subsets fall back to the absolute score. The hard trade-ready floor always
# uses the absolute InstitutionalScore so relative rank cannot rescue a weak
# setup into an executable state.
replace_once(
    "signal_lifecycle.py",
    '''    institutional_raw = _number(\n        result.get(\n            "InstitutionalScore",\n            result.get("FinalScore", result.get("Score", pd.Series(0.0, index=result.index))),\n        ),\n        0.0,\n    )\n    technical_raw = _number(\n        result.get("TechnicalInstitutionalScore", institutional_raw), 0.0\n    )\n    result["TechnicalInstitutionalScore"] = technical_raw.round(4)\n    asset_group = pd.Series(np.where(is_etf, "ETF", "STOCK"), index=result.index)\n    asset_percentile = institutional_raw.groupby(asset_group).rank(method="average", pct=True) * 100.0\n    result["AssetPercentile"] = asset_percentile.round(2)\n    # Percentile normalization supplies a common stock/ETF scale while keeping\n    # 30% of the calibrated absolute score so meaningful score gaps survive.\n    cross_asset_score = (asset_percentile * 0.70 + institutional_raw.clip(0.0, 100.0) * 0.30).clip(0.0, 100.0)\n    result["CrossAssetScore"] = cross_asset_score.round(4)\n    base_score = cross_asset_score\n    minimum_score_risk = base_score.lt(TRADE_READY_MIN_INSTITUTIONAL_SCORE)\n''',
    '''    institutional_raw = _number(\n        result.get(\n            "InstitutionalScore",\n            result.get("FinalScore", result.get("Score", pd.Series(0.0, index=result.index))),\n        ),\n        0.0,\n    )\n    technical_raw = _number(\n        result.get("TechnicalInstitutionalScore", institutional_raw), 0.0\n    )\n    result["TechnicalInstitutionalScore"] = technical_raw.round(4)\n    asset_group = pd.Series(np.where(is_etf, "ETF", "STOCK"), index=result.index)\n    valid_asset_score = institutional_raw.gt(0.0) & np.isfinite(institutional_raw)\n    valid_group_size = valid_asset_score.groupby(asset_group).transform("sum")\n    ranked_input = institutional_raw.where(valid_asset_score)\n    asset_percentile = ranked_input.groupby(asset_group).rank(method="average", pct=True) * 100.0\n    result["AssetPercentile"] = asset_percentile.round(2)\n    use_cross_asset_normalization = valid_asset_score & valid_group_size.ge(5) & asset_percentile.notna()\n    normalized_score = (\n        asset_percentile * 0.70\n        + institutional_raw.clip(0.0, 100.0) * 0.30\n    ).clip(0.0, 100.0)\n    cross_asset_score = institutional_raw.clip(0.0, 100.0).where(\n        ~use_cross_asset_normalization, normalized_score\n    )\n    result["CrossAssetScore"] = cross_asset_score.round(4)\n    base_score = cross_asset_score\n    minimum_score_risk = institutional_raw.lt(TRADE_READY_MIN_INSTITUTIONAL_SCORE)\n''',
)

# Keep explicit status for rows that were exact-refined but generated no test
# samples. The generic status assignment must not erase that provenance.
replace_once(
    "analytics.py",
    '''    frame["BacktestStatus"] = np.where(observed.gt(0.0), "SAMPLES", "NO_SIGNAL_SAMPLES")\n''',
    '''    frame["BacktestStatus"] = np.where(observed.gt(0.0), "SAMPLES", "NO_SIGNAL_SAMPLES")\n    if "BacktestStage" not in frame:\n        frame["BacktestStage"] = np.where(\n            frame["BacktestMode"].astype(str).str.upper().eq("EXACT"),\n            "EXACT",\n            "FAST_SCREEN",\n        )\n''',
)

print("model v19 postfix applied")
