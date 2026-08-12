from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"{path}: expected exactly one contract match")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "test_v38_fundamental_gate2.py",
    '''    def test_v38_advances_model_but_preserves_v37_and_v36_provenance(self):\n        self.assertIn("v38", config.SCORING_VERSION)\n        self.assertIn("v38", config.PIPELINE_VERSION)\n        self.assertIn("v37", config.PIPELINE_VERSION)\n        self.assertIn("v37", config.GUI_VERSION)\n        self.assertIn("v36", config.MARKET_DATA_VERSION)\n''',
    '''    def test_v38_policy_survives_later_model_and_pipeline_versions(self):\n        self.assertTrue(\n            any(f"v{version}" in config.SCORING_VERSION for version in range(38, 100))\n        )\n        self.assertTrue(\n            any(f"v{version}" in config.PIPELINE_VERSION for version in range(38, 100))\n        )\n        self.assertIn("v38", config.FUNDAMENTAL_GATE_VERSION)\n        self.assertIn("v37", config.GUI_VERSION)\n        self.assertIn("v36", config.MARKET_DATA_VERSION)\n''',
)

replace_once(
    "test_v37_project_integrity.py",
    '''    def test_v37_does_not_change_scoring_model_version(self) -> None:\n        self.assertTrue(\n            any(f"v{version}" in config.SCORING_VERSION for version in range(35, 100))\n        )\n        self.assertIn("v37", config.PIPELINE_VERSION)\n        self.assertIn("v37", config.GUI_VERSION)\n''',
    '''    def test_v37_integrity_survives_later_pipeline_versions(self) -> None:\n        self.assertTrue(\n            any(f"v{version}" in config.SCORING_VERSION for version in range(35, 100))\n        )\n        self.assertTrue(\n            any(f"v{version}" in config.PIPELINE_VERSION for version in range(37, 100))\n        )\n        self.assertIn("v37", config.GUI_VERSION)\n''',
)
