"""Survivability and read-only gates for ``model_audit``.

Two claims are frozen here.

1. **Nothing in ``model_audit`` is dead.** A reachability sweep from the three
   entry points (``run_audit``, ``main``, ``_parser``) reaches all top-level
   definitions. This matters because ``test_model_audit_behaviour.py`` was
   written on the assumption that every assertion lands on live code; if a
   definition ever stops being reachable, that assumption breaks silently.

2. **The audit is read-only.** The module docstring promises it "never writes
   model parameters". That is what makes it safe to point at a production
   export, and nothing guarded it. Two complementary gates: a static one (no
   assignment through the ``config`` module anywhere in the source) and a
   dynamic one (every upper-case ``config`` attribute is unchanged across a
   full ``run_audit``).

Entry points are read statically via AST rather than imported, because
``analytics`` / ``main`` / ``scan_service`` run module-level ``install()``
calls; importing them just to check an import statement would install every
overlay in the test process.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

import config
import model_audit

REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = REPO_ROOT / "model_audit.py"
ENTRY_POINTS = {"run_audit", "main", "_parser"}
IMPORTERS = ("analytics.py", "main.py", "scan_service.py")


def _tree() -> ast.Module:
    return ast.parse(MODULE_PATH.read_text(encoding="utf-8"))


def _top_level_definitions() -> dict[str, ast.AST]:
    definitions: dict[str, ast.AST] = {}
    for node in _tree().body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definitions[node.name] = node
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    definitions[target.id] = node
    return definitions


def _reachable() -> set[str]:
    definitions = _top_level_definitions()
    edges = {
        name: {
            node.id
            for node in ast.walk(body)
            if isinstance(node, ast.Name) and node.id in definitions and node.id != name
        }
        for name, body in definitions.items()
    }
    seen = set(ENTRY_POINTS)
    stack = list(ENTRY_POINTS)
    while stack:
        for nxt in edges.get(stack.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen


def _config_snapshot() -> dict[str, object]:
    return {name: getattr(config, name) for name in dir(config) if name.isupper()}


# Captured at import time, i.e. during collection, before any test has run.
# Taking it inside the test instead would be defeated by test ordering: the
# behavioural file calls run_audit first, so a mutation that happens *inside*
# run_audit would already be baked into both the "before" and "after" snapshots
# and the gate would pass. Reverse-validation case E caught exactly that.
_CONFIG_BASELINE = _config_snapshot()


@pytest.fixture()
def minimal_export(tmp_path: Path) -> Path:
    path = tmp_path / "AllResults.csv"
    pd.DataFrame(
        {
            "Ticker": ["600000", "000001", "510300"],
            "IsETF": [False, False, True],
            "EntrySignal": ["BUY_NOW", "AVOID", "AVOID"],
            "RankingScore": [80.0, 60.0, 40.0],
            "CrossAssetScore": [80.0, 60.0, 40.0],
            "RankingUniverseSize": [3, 3, 3],
            "RankingScope": ["FULL_UNIVERSE"] * 3,
        }
    ).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def test_every_top_level_definition_is_reachable() -> None:
    unreachable = sorted(set(_top_level_definitions()) - _reachable())
    assert unreachable == [], f"dead definitions: {unreachable}"


def test_the_reachability_sweep_covers_the_whole_module() -> None:
    """Guards against the sweep silently shrinking to an empty graph."""
    definitions = _top_level_definitions()
    assert len(definitions) >= 27
    assert _reachable() >= {"run_audit", "build_scenarios", "threshold_report"}


@pytest.mark.parametrize("filename", IMPORTERS)
def test_each_production_entry_point_imports_run_audit(filename: str) -> None:
    tree = ast.parse((REPO_ROOT / filename).read_text(encoding="utf-8"))
    imported = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "model_audit"
        for alias in node.names
    }
    assert "run_audit" in imported, f"{filename} no longer imports run_audit"


def test_the_source_never_assigns_through_config() -> None:
    for node in ast.walk(_tree()):
        if not isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            chain = []
            cursor: ast.AST = target
            while isinstance(cursor, ast.Attribute):
                chain.append(cursor.attr)
                cursor = cursor.value
            if isinstance(cursor, ast.Name) and cursor.id == "config":
                pytest.fail(f"config mutation at line {node.lineno}: {'.'.join(reversed(chain))}")


def test_run_audit_leaves_configuration_untouched(
    minimal_export: Path,
    tmp_path: Path,
) -> None:
    model_audit.run_audit(minimal_export, tmp_path / "audit")

    after = _config_snapshot()
    assert after.keys() == _CONFIG_BASELINE.keys()
    assert after == _CONFIG_BASELINE
