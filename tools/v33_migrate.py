from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

report_path = ROOT / "report.py"
text = report_path.read_text(encoding="utf-8")

truthy_block = '''def _truthy(value: object) -> bool:\n    return str(value).strip().lower() in {"true", "1", "yes", "y", "是"}\n\n\n'''
helper_block = '''def _truthy(value: object) -> bool:\n    return str(value).strip().lower() in {"true", "1", "yes", "y", "是"}\n\n\ndef _clean_group_key(value: object) -> str:\n    """Normalize nullable categorical keys before diversity accounting."""\n    if value is None:\n        return ""\n    try:\n        if pd.isna(value):\n            return ""\n    except (TypeError, ValueError):\n        pass\n    text = str(value).strip()\n    if text.lower() in {"", "nan", "none", "nat", "<na>"}:\n        return ""\n    return text\n\n\n'''
if truthy_block not in text:
    raise RuntimeError("_truthy insertion point not found")
text = text.replace(truthy_block, helper_block, 1)

old_loop = '''        for index in remaining:\n            row = working.loc[index]\n            theme = str(row.get("ETFTheme", "") or "").strip()\n            tracking = str(row.get("ETFTrackingKey", "") or "").strip()\n            classification = str(row.get("ModelClassification", "") or row.get("Industry", "") or row.get("Sector", "") or "").strip()\n            cluster = str(row.get("ThemeCluster", "") or "").strip()\n            if tracking and tracking_counts.get(tracking, 0) >= max(1, int(ETF_TRACKING_MAX_PER_TOP_LIST)):\n                continue\n            if theme and theme_counts.get(theme, 0) >= max(1, int(max_per_theme)):\n                continue\n            if not theme and classification and classification.lower() not in {"nan", "none"} and stock_industry_counts.get(classification, 0) >= max(1, int(max_per_stock_industry)):\n                continue\n'''
new_loop = '''        for index in remaining:\n            row = working.loc[index]\n            row_is_etf = _truthy(row.get("IsETF", False)) or str(\n                row.get("AssetType", "")\n            ).strip().lower() == "etf"\n            # ETF-only provenance must never participate in stock diversity.\n            # In particular, np.nan used to stringify to "nan", making every\n            # stock look like the same ETF tracking product and capping stocks\n            # in the mixed Top50 at one row.\n            theme = _clean_group_key(row.get("ETFTheme", "")) if row_is_etf else ""\n            tracking = _clean_group_key(row.get("ETFTrackingKey", "")) if row_is_etf else ""\n            classification = (\n                _clean_group_key(row.get("ModelClassification", ""))\n                or _clean_group_key(row.get("Industry", ""))\n                or _clean_group_key(row.get("Sector", ""))\n            )\n            cluster = _clean_group_key(row.get("ThemeCluster", ""))\n            if row_is_etf and tracking and tracking_counts.get(tracking, 0) >= max(1, int(ETF_TRACKING_MAX_PER_TOP_LIST)):\n                continue\n            if row_is_etf and theme and theme_counts.get(theme, 0) >= max(1, int(max_per_theme)):\n                continue\n            if (not row_is_etf) and classification and stock_industry_counts.get(classification, 0) >= max(1, int(max_per_stock_industry)):\n                continue\n'''
if old_loop not in text:
    raise RuntimeError("v33 diversity selection block not found")
text = text.replace(old_loop, new_loop, 1)

old_count = '''        row = working.loc[best_index]\n        theme = str(row.get("ETFTheme", "") or "").strip()\n        tracking = str(row.get("ETFTrackingKey", "") or "").strip()\n        classification = str(row.get("ModelClassification", "") or row.get("Industry", "") or row.get("Sector", "") or "").strip()\n        cluster = str(row.get("ThemeCluster", "") or "").strip()\n        if theme:\n            theme_counts[theme] = theme_counts.get(theme, 0) + 1\n        if tracking:\n            tracking_counts[tracking] = tracking_counts.get(tracking, 0) + 1\n        if not theme and classification and classification.lower() not in {"nan", "none"}:\n            stock_industry_counts[classification] = stock_industry_counts.get(classification, 0) + 1\n'''
new_count = '''        row = working.loc[best_index]\n        row_is_etf = _truthy(row.get("IsETF", False)) or str(\n            row.get("AssetType", "")\n        ).strip().lower() == "etf"\n        theme = _clean_group_key(row.get("ETFTheme", "")) if row_is_etf else ""\n        tracking = _clean_group_key(row.get("ETFTrackingKey", "")) if row_is_etf else ""\n        classification = (\n            _clean_group_key(row.get("ModelClassification", ""))\n            or _clean_group_key(row.get("Industry", ""))\n            or _clean_group_key(row.get("Sector", ""))\n        )\n        cluster = _clean_group_key(row.get("ThemeCluster", ""))\n        if row_is_etf and theme:\n            theme_counts[theme] = theme_counts.get(theme, 0) + 1\n        if row_is_etf and tracking:\n            tracking_counts[tracking] = tracking_counts.get(tracking, 0) + 1\n        if (not row_is_etf) and classification:\n            stock_industry_counts[classification] = stock_industry_counts.get(classification, 0) + 1\n'''
if old_count not in text:
    raise RuntimeError("v33 diversity counting block not found")
text = text.replace(old_count, new_count, 1)
report_path.write_text(text, encoding="utf-8")

config_path = ROOT / "config.py"
config_text = config_path.read_text(encoding="utf-8")
old_version = 'PIPELINE_VERSION: str = "2026-08-10-v32-asset-top50-ranking"'
new_version = 'PIPELINE_VERSION: str = "2026-08-10-v33-mixed-diversity-nan"'
if old_version not in config_text:
    raise RuntimeError("v32 PIPELINE_VERSION not found")
config_path.write_text(config_text.replace(old_version, new_version, 1), encoding="utf-8")

print("v33 migration applied")
