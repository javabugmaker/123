from __future__ import annotations

from pathlib import Path

path = Path(__file__).resolve().parents[1] / "main.py"
text = path.read_text(encoding="utf-8")
old_import = "from analytics import apply_backtest_ranking, enrich_results, run_historical_backtest\n"
new_import = "from analytics import BacktestSummary, apply_backtest_ranking, enrich_results, run_historical_backtest\n"
if old_import not in text:
    raise RuntimeError("generated analytics import anchor not found")
text = text.replace(old_import, new_import, 1)
old = '''    # run_historical_backtest writes an initial summary before EXACT refinement.
    # Persist it again after ranking so HYBRID/provenance/performance counters are
    # the final values consumed by the daily manifest and GUI.
    summary_path = OUTPUT_DIR / "BacktestSummary.json"
    temporary_summary = summary_path.with_name(".BacktestSummary.json.tmp")
    temporary_summary.write_text(
        json.dumps(summary.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary_summary, summary_path)
'''
new = '''    # run_historical_backtest writes an initial summary before EXACT refinement.
    # Persist it again after ranking so HYBRID/provenance/performance counters are
    # the final values consumed by the daily manifest and GUI.  Some compatibility
    # tests intentionally replace the engine with a Mock; those are not real
    # BacktestSummary objects and must not create a fake JSON artifact.
    if isinstance(summary, BacktestSummary):
        summary_path = OUTPUT_DIR / "BacktestSummary.json"
        temporary_summary = summary_path.with_name(".BacktestSummary.json.tmp")
        temporary_summary.write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary_summary, summary_path)
'''
if old not in text:
    raise RuntimeError("generated final summary persistence anchor not found")
text = text.replace(old, new, 1)
path.write_text(text, encoding="utf-8")
print("fixed v29 final summary compatibility")
