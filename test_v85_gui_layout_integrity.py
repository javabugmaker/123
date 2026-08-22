from __future__ import annotations

import ast
import unittest
from pathlib import Path


class V85GuiLayoutIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.path = Path(__file__).with_name("gui_v85.py")
        cls.source = cls.path.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.gui_class = next(
            node
            for node in cls.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "ResearchBriefingGUI"
        )

    def test_v85_reuses_stable_parent_geometry_shell(self) -> None:
        methods = {
            node.name
            for node in self.gui_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertNotIn(
            "_build_ui",
            methods,
            "v85 must inherit the proven v84/DecisionScannerGUI geometry shell",
        )

    def test_no_custom_scrollable_sidebar_regression(self) -> None:
        self.assertNotIn("CTkScrollableFrame", self.source)
        self.assertNotIn("_sidebar.pack_propagate(False)", self.source)

    def test_compact_header_has_explicit_height(self) -> None:
        self.assertIn("height=72", self.source)
        self.assertIn("header.pack_propagate(False)", self.source)

    def test_scanner_alias_remains_public(self) -> None:
        self.assertIn("ScannerGUI = ResearchBriefingGUI", self.source)


if __name__ == "__main__":
    unittest.main()
