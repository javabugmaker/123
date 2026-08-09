from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

report_path = ROOT / "report.py"
text = report_path.read_text(encoding="utf-8")
old = '''    stock_path = destination / f"Top{top_n_csv}Stocks.csv"\n    stock_pool = _diversify_ranked_candidates(\n        ranked.loc[~is_etf_mask], top_n_csv\n    )\n    _atomic_write_csv(stock_pool, stock_path)\n\n    etf_path = destination / f"Top{top_n_csv}ETF.csv"\n    etf_pool = _diversify_ranked_candidates(\n        ranked.loc[is_etf_mask], top_n_csv\n    )\n    _atomic_write_csv(etf_pool, etf_path)\n'''
new = '''    # Split asset lists are pure within-asset rankings, not diversified research\n    # pools.  Diversity caps remain valuable for the mixed Top50, but must never\n    # truncate the dedicated stock/ETF pages below the number of valid assets.\n    # Trade eligibility is preserved as a display/decision field and does not\n    # decide whether a valid asset may appear in its research Top50.\n    stock_path = destination / f"Top{top_n_csv}Stocks.csv"\n    stock_pool = ranked.loc[~is_etf_mask].head(top_n_csv).copy().reset_index(drop=True)\n    stock_pool["ResearchDiversityPenalty"] = 1.0\n    stock_pool["ResearchPoolRank"] = np.arange(1, len(stock_pool) + 1)\n    _atomic_write_csv(stock_pool, stock_path)\n\n    etf_path = destination / f"Top{top_n_csv}ETF.csv"\n    etf_pool = ranked.loc[is_etf_mask].head(top_n_csv).copy().reset_index(drop=True)\n    etf_pool["ResearchDiversityPenalty"] = 1.0\n    etf_pool["ResearchPoolRank"] = np.arange(1, len(etf_pool) + 1)\n    _atomic_write_csv(etf_pool, etf_path)\n'''
if old not in text:
    raise RuntimeError("v32 split export block not found")
report_path.write_text(text.replace(old, new, 1), encoding="utf-8")

config_path = ROOT / "config.py"
config_text = config_path.read_text(encoding="utf-8")
pattern = re.compile(r'^(PIPELINE_VERSION\\s*(?::[^=]+)?=\\s*)["\\\'][^"\\\']+["\\\']', re.MULTILINE)
config_text, count = pattern.subn(r'\\1"2026-08-10-v32-asset-top50-ranking"', config_text, count=1)
if count != 1:
    raise RuntimeError("PIPELINE_VERSION assignment not found")
config_path.write_text(config_text, encoding="utf-8")

print("v32 migration applied")
