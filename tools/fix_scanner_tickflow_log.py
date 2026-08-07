from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "scanner.py"
text = path.read_text(encoding="utf-8")
old = '''    logger.info(\n        "Phase 1/2: downloading data for %d tickers (%d threads)...",\n        len(all_tickers),\n        )\n'''
new = '''    logger.info(\n        "Phase 1/2: preparing TickFlow data for %d tickers (batch workers=%d)...",\n        len(all_tickers),\n        TICKFLOW_MAX_WORKERS,\n    )\n'''
if old not in text:
    raise RuntimeError("scanner phase-one log target not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
(root / "tools/fix_scanner_tickflow_log.py").unlink(missing_ok=True)
(root / ".github/workflows/fix-scanner-tickflow-log.yml").unlink(missing_ok=True)
