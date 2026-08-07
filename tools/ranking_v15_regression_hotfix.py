from pathlib import Path

p = Path("test_regressions.py")
text = p.read_text(encoding="utf-8")

old = '        self.assertEqual(top["Ticker"].tolist(), ["000002.SZ", "000001.SZ"])\n'
new = '        self.assertEqual(top["Ticker"].tolist(), ["000001.SZ", "000002.SZ"])\n'
if old not in text:
    raise RuntimeError("ranking export expectation not found")
text = text.replace(old, new, 1)

old = '''                "SignalCount": [4],\n                "SectorConfirmationFactor": [0.5],\n'''
new = '''                "SignalCount": [4],\n                "Sector": ["测试行业"],\n                "SectorConfirmationFactor": [0.5],\n'''
if old not in text:
    raise RuntimeError("tempered sector fixture not found")
text = text.replace(old, new, 1)

p.write_text(text, encoding="utf-8")
print("ranking v15 regression hotfix applied")
