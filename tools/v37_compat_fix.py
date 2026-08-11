from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str, label: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "test_v29_pipeline_reliability.py",
    '        self.assertLessEqual(len(projected.columns), 45)\n',
    '        # v37 adds evidence/research-integrity fields while keeping the GUI projection\n'
    '        # far smaller than the 200+ column audit surface.\n'
    '        self.assertLessEqual(len(projected.columns), 60)\n'
    '        self.assertIn("EvidenceTier", projected.columns)\n',
    "v29 lightweight decision projection",
)

replace_once(
    "test_v30_performance_workstation.py",
    '        self.assertIn("v30", config.GUI_VERSION)\n',
    '        self.assertTrue(\n'
    '            any(f"v{version}" in config.GUI_VERSION for version in range(30, 100))\n'
    '        )\n',
    "v30 GUI forward compatibility",
)

replace_once(
    "test_v35_model_integrity.py",
    '        self.assertIn("v35", config.PIPELINE_VERSION)\n',
    '        self.assertTrue(\n'
    '            any(f"v{version}" in config.PIPELINE_VERSION for version in range(35, 100))\n'
    '        )\n',
    "v35 pipeline forward compatibility",
)

replace_once(
    "test_v36_tickflow_volume_units.py",
    '        self.assertIn("v36", config.PIPELINE_VERSION)\n',
    '        self.assertTrue(\n'
    '            any(f"v{version}" in config.PIPELINE_VERSION for version in range(36, 100))\n'
    '        )\n',
    "v36 pipeline forward compatibility",
)

subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
