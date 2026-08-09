from __future__ import annotations

from pathlib import Path

path = Path("gui.py")
text = path.read_text(encoding="utf-8")

replacements = {
    '"今日一键更新：最新日K → 全市场扫描 → FAST回测 → EXACT精炼 → 最终Top50。\n"': '"今日一键更新：最新日K → 全市场扫描 → FAST回测 → EXACT精炼 → 最终Top50。\\n"',
    '"今日全流程完成：Top50Mixed.csv / Top50Stocks.csv / Top50ETF.csv 已刷新。\n"': '"今日全流程完成：Top50Mixed.csv / Top50Stocks.csv / Top50ETF.csv 已刷新。\\n"',
}

for old, new in replacements.items():
    if old not in text:
        raise RuntimeError(f"expected generated GUI string not found: {old!r}")
    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
print("fixed v27 generated GUI string escapes")
