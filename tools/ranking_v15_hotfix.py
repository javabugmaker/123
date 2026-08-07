from pathlib import Path

path = Path("test_ranking_v15_regressions.py")
if path.exists():
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("\\n", "\n"), encoding="utf-8")

print("ranking v15 hotfix applied")
