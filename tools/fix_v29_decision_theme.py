from __future__ import annotations

from pathlib import Path

path = Path(__file__).resolve().parents[1] / "report.py"
text = path.read_text(encoding="utf-8")
old = '''def _decision_projection(frame: pd.DataFrame) -> pd.DataFrame:
    working = frame.copy()
    if "ETFTheme" not in working:
        working["ETFTheme"] = working.apply(_etf_theme_key, axis=1)
    else:
        missing_theme = working["ETFTheme"].fillna("").astype(str).str.strip().eq("")
        if missing_theme.any():
            working.loc[missing_theme, "ETFTheme"] = working.loc[missing_theme].apply(
                _etf_theme_key, axis=1
            )
    return working.reindex(columns=DECISION_RESULT_COLUMNS)
'''
new = '''def _decision_projection(frame: pd.DataFrame) -> pd.DataFrame:
    working = frame.copy()
    if "ETFTheme" not in working:
        working["ETFTheme"] = ""
    missing_theme = working["ETFTheme"].fillna("").astype(str).str.strip().eq("")
    if missing_theme.any():
        inferred = working.loc[missing_theme].apply(_etf_theme_key, axis=1)
        ticker = working.loc[missing_theme].get(
            "Ticker", pd.Series("", index=working.loc[missing_theme].index)
        ).fillna("").astype(str).str.strip()
        classification = working.loc[missing_theme].get(
            "ModelClassification", pd.Series("", index=working.loc[missing_theme].index)
        ).fillna("").astype(str).str.strip()
        # A generic ETF name can make the classification helper fall through to
        # the ticker itself.  That is provenance, not a useful user-facing theme.
        # Prefer the model classification in that boundary case.
        inferred_text = inferred.fillna("").astype(str).str.strip()
        generic = inferred_text.eq("") | inferred_text.eq(ticker)
        inferred_text = inferred_text.where(~generic | classification.eq(""), classification)
        working.loc[missing_theme, "ETFTheme"] = inferred_text
    return working.reindex(columns=DECISION_RESULT_COLUMNS)
'''
if old not in text:
    raise RuntimeError("generated _decision_projection anchor not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("fixed v29 decision theme fallback")
