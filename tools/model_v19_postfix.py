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
    '''        failed_filter_names = [\n            name\n            for name, state in {**base_filter_states, **accumulation_states, **structure_states}.items()\n            if not state\n        ]\n\n\n        style = classify_style(df, is_etf=ticker_info.is_etf)\n''',
    '''        failed_filter_names = [\n            name\n            for name, state in {**base_filter_states, **accumulation_states, **structure_states}.items()\n            if not state\n        ]\n\n        sb = score_ticker(df, is_etf=ticker_info.is_etf)\n        style = classify_style(df, is_etf=ticker_info.is_etf)\n''',
)

# Keep explicit status for rows that were exact-refined but generated no test
# samples. The generic status assignment must not erase that provenance.
replace_once(
    "analytics.py",
    '''    frame["BacktestStatus"] = np.where(observed.gt(0.0), "SAMPLES", "NO_SIGNAL_SAMPLES")\n''',
    '''    frame["BacktestStatus"] = np.where(observed.gt(0.0), "SAMPLES", "NO_SIGNAL_SAMPLES")\n    if "BacktestStage" not in frame:\n        frame["BacktestStage"] = np.where(\n            frame["BacktestMode"].astype(str).str.upper().eq("EXACT"),\n            "EXACT",\n            "FAST_SCREEN",\n        )\n''',
)

print("model v19 postfix applied")
