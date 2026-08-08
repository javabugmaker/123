from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"pattern not found in {path}: {old!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Missing fundamental evidence should shrink the observed continuous score
# toward neutral 50, not create a separate missing-data penalty inside the score.
replace_once(
    "fundamental_quality.py",
    '''        quality_score = round(\n            float(\n                np.clip(\n                    shrunk_factor_score * 0.85 + completeness * 100.0 * 0.15,\n                    0.0,\n                    100.0,\n                )\n            ),\n            4,\n        )\n''',
    '''        quality_score = round(\n            float(np.clip(shrunk_factor_score, 0.0, 100.0)),\n            4,\n        )\n''',
)

# These assertions intentionally encoded the old discrete/pass-rate model.
# Keep the behavioral contracts (neutral shrink, gate pass, single quality blend)
# while updating exact values to the continuous v3 model.
replace_once(
    "test_hardening_regressions.py",
    "        self.assertAlmostEqual(quality.quality_score, 62.5)\n",
    "        self.assertAlmostEqual(quality.quality_score, 52.5)\n",
)
replace_once(
    "test_regressions.py",
    "        self.assertEqual(quality.quality_score, 100.0)\n",
    "        self.assertAlmostEqual(quality.quality_score, 81.1765, places=4)\n",
)
replace_once(
    "test_regressions.py",
    "        self.assertEqual(result.institutional_score, 58.9)\n",
    "        self.assertEqual(result.institutional_score, 68.0)\n",
)

print("model v3 compatibility assertions updated")
