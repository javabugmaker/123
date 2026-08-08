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

# v18 already restored model_classification later in ScanResult(...). Keep the
# new v19 restore fields without adding the keyword twice.
replace_once(
    "scanner.py",
    '''                        quality_multiplier=_parse_float(\n                            row.get("QualityMultiplier", 0.95), 0.95\n                        ),\n                        model_classification=str(row.get("ModelClassification", "") or ""),\n                        etf_tracking_key=str(row.get("ETFTrackingKey", "") or ""),\n                        theme_cluster=str(row.get("ThemeCluster", "") or ""),\n                        technical_institutional_score=_parse_float(row.get("TechnicalInstitutionalScore", np.nan)),\n                        asset_percentile=_parse_float(row.get("AssetPercentile", np.nan)),\n                        cross_asset_score=_parse_float(row.get("CrossAssetScore", np.nan)),\n                        sector_confirmation_factor=_parse_float(\n''',
    '''                        quality_multiplier=_parse_float(\n                            row.get("QualityMultiplier", 0.95), 0.95\n                        ),\n                        etf_tracking_key=str(row.get("ETFTrackingKey", "") or ""),\n                        theme_cluster=str(row.get("ThemeCluster", "") or ""),\n                        technical_institutional_score=_parse_float(row.get("TechnicalInstitutionalScore", np.nan)),\n                        asset_percentile=_parse_float(row.get("AssetPercentile", np.nan)),\n                        cross_asset_score=_parse_float(row.get("CrossAssetScore", np.nan)),\n                        sector_confirmation_factor=_parse_float(\n''',
)

# Keep explicit status for rows that were exact-refined but generated no test
# samples. The generic status assignment must not erase that provenance.
replace_once(
    "analytics.py",
    '''    frame["BacktestStatus"] = np.where(observed.gt(0.0), "SAMPLES", "NO_SIGNAL_SAMPLES")\n''',
    '''    frame["BacktestStatus"] = np.where(observed.gt(0.0), "SAMPLES", "NO_SIGNAL_SAMPLES")\n    if "BacktestStage" not in frame:\n        frame["BacktestStage"] = np.where(\n            frame["BacktestMode"].astype(str).str.upper().eq("EXACT"),\n            "EXACT",\n            "FAST_SCREEN",\n        )\n''',
)

print("model v19 postfix applied")
