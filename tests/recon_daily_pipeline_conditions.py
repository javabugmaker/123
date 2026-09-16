"""Ad-hoc reconnaissance: constant / redundant conditions in ``daily_pipeline_core``.

Run directly::

    python tests/recon_daily_pipeline_conditions.py

Static only. Reports three shapes:

1. ``if`` tests that are literal constants (always taken / never taken).
2. ``bool(A and A != B) or not A`` -- a redundancy pattern that reduces to
   ``A != B`` whenever ``B`` is a non-empty constant.
3. ``except`` tuples, listed so coverage gaps can be eyeballed.
"""

from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = ROOT / "daily_pipeline_core.py"


def describe(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover
        return "<unparseable>"


def main() -> int:
    tree = ast.parse(TARGET.read_text(encoding="utf-8"))

    print("=== constant-condition `if` tests ===")
    found = False
    for child in ast.walk(tree):
        if not isinstance(child, ast.If):
            continue
        if isinstance(child.test, ast.Constant):
            found = True
            print(f"  line {child.test.lineno}: if {describe(child.test)}")
    if not found:
        print("  (none)")

    print("\n=== `bool(A and A != B) or not A` redundancy ===")
    found = False
    for child in ast.walk(tree):
        if not isinstance(child, ast.BoolOp) or not isinstance(child.op, ast.Or):
            continue
        for value in child.values:
            if not isinstance(value, ast.UnaryOp) or not isinstance(value.op, ast.Not):
                continue
            target = value.operand
            if not isinstance(target, ast.Name):
                continue
            for other in child.values:
                if (
                    isinstance(other, ast.Call)
                    and isinstance(other.func, ast.Name)
                    and other.func.id == "bool"
                    and len(other.args) == 1
                    and isinstance(other.args[0], ast.BoolOp)
                    and isinstance(other.args[0].op, ast.And)
                ):
                    left = other.args[0].values[0]
                    if isinstance(left, ast.Name) and left.id == target.id:
                        found = True
                        print(f"  line {child.lineno}: {describe(child)}")
    if not found:
        print("  (none)")

    print("\n=== except tuples ===")
    for child in ast.walk(tree):
        if not isinstance(child, ast.ExceptHandler):
            continue
        names: list[str] = []
        if isinstance(child.type, ast.Tuple):
            names = [getattr(e, "id", "?") for e in child.type.elts]
        elif isinstance(child.type, ast.Name):
            names = [child.type.id]
        elif child.type is None:
            names = ["<bare>"]
        print(f"  line {child.lineno}: ({', '.join(names)})")

    print("\n=== unreachable statements after return/raise/continue ===")
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = node.body
        for index, stmt in enumerate(body[:-1]):
            if isinstance(stmt, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
                print(f"  {node.name}:{stmt.lineno} -> dead stmt at {body[index + 1].lineno}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
