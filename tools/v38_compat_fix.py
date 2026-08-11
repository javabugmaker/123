from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "test_regressions.py"
text = path.read_text(encoding="utf-8")
old = '        self.assertEqual(quality.quality_reason, "全部通过")\n'
new = (
    '        self.assertTrue(quality.quality_gate)\n'
    '        self.assertIn("通用严格模型", quality.quality_reason)\n'
    '        self.assertIn("硬门槛通过", quality.quality_reason)\n'
)
if text.count(old) != 1:
    raise RuntimeError(f"legacy quality reason assertion: expected one match, got {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
subprocess.run(["git", "add", "test_regressions.py"], cwd=ROOT, check=True)
