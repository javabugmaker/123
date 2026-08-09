from pathlib import Path

path = Path("gui.py")
text = path.read_text(encoding="utf-8")
old = '''            reason = f"{reason}

历史样本不足，回测暂不作为主要排序依据。"
'''
new = '''            reason = f"{reason}\\n\\n历史样本不足，回测暂不作为主要排序依据。"
'''
if old not in text:
    raise SystemExit("v28 generated GUI escape pattern not found")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("fixed v28 generated GUI escape")
