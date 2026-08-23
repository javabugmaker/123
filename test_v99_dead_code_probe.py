from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ENTRYPOINTS = {
    "main",
    "gui",
    "daily_pipeline",
    "publish_web_report",
}
PROBE = "test_v99_dead_code_probe"


def _imports(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".", 1)[0])
    return found


class DeadCodeProbe(unittest.TestCase):
    def test_report_zero_production_inbound_modules(self) -> None:
        paths = sorted(ROOT.glob("*.py"))
        stems = {path.stem for path in paths}
        prod = {
            path.stem: path
            for path in paths
            if not path.stem.startswith("test_") and path.stem != PROBE
        }
        tests = {
            path.stem: path
            for path in paths
            if path.stem.startswith("test_") and path.stem != PROBE
        }
        inbound_prod = {name: set() for name in prod}
        inbound_test = {name: set() for name in prod}
        for source, path in prod.items():
            for target in _imports(path):
                if target in inbound_prod and target != source:
                    inbound_prod[target].add(source)
        for source, path in tests.items():
            for target in _imports(path):
                if target in inbound_test:
                    inbound_test[target].add(source)

        candidates = []
        for name in sorted(prod):
            if name in ENTRYPOINTS:
                continue
            if inbound_prod[name]:
                continue
            candidates.append(
                {
                    "module": name,
                    "test_inbound": sorted(inbound_test[name]),
                    "test_inbound_count": len(inbound_test[name]),
                }
            )
        # Keep the probe intentionally red so CI logs provide the complete
        # reference report. This file is deleted immediately after diagnosis.
        self.fail(json.dumps(candidates, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    unittest.main()
