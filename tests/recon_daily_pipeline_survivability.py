"""Ad-hoc reconnaissance: reachability map for ``daily_pipeline_core``.

Not a test module -- run it directly and read the output::

    python tests/recon_daily_pipeline_survivability.py

Method (matches the ``report_core`` round):

* nodes  = top-level defs/classes in ``daily_pipeline_core.py``
* edges  = calls made inside each node's body (recursively, incl. nested defs)
* roots  = (a) symbols referenced from *other* modules, where that other module
  does not itself ``def`` the same name, (b) calls issued at module scope,
  (c) names rebound onto the module by an overlay (proves somebody wanted it)
* reachable = BFS(roots); dead = nodes - reachable

The "exclude other modules that define the same name" rule matters: several
modules ship their own ``_atomic_write_json`` / ``_csv_profile``, and counting
those as external roots would mark unrelated functions as production entry
points.
"""

from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TARGET = ROOT / "daily_pipeline_core.py"
SKIP_DIRS = {"cache", "output", "tests", "__pycache__", ".git", ".workbuddy-ai"}


def iter_python_files() -> list[pathlib.Path]:
    out = []
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        out.append(path)
    return out


def top_level_defs(tree: ast.Module) -> dict[str, ast.AST]:
    """Map name -> node for module-level defs and classes."""
    found: dict[str, ast.AST] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found[node.name] = node
    return found


def body_calls(node: ast.AST) -> set[str]:
    """Every bare-name call inside ``node``, including nested defs."""
    calls: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
            calls.add(child.func.id)
        # decorators / default values referencing module-level names
        elif isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name):
            calls.add(child.value.id)
    return calls


def names_in_body(node: ast.AST) -> set[str]:
    """Bare names *read* inside ``node`` (not only calls).

    A top-level helper can be alive by being referenced as a value (passed as a
    callback, stored in a dict) rather than called.
    """
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load):
            names.add(child.id)
    return names


def module_scope_names(tree: ast.Module) -> set[str]:
    """Names referenced at module scope (outside any def/class)."""
    names: set[str] = set()
    scope_nodes = list(tree.body)
    for node in scope_nodes:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                names.add(child.id)
    return names


def _core_aliases(tree: ast.Module) -> set[str]:
    """Local names bound to ``daily_pipeline_core`` in this module."""
    aliases: set[str] = set()
    for child in ast.walk(tree):
        if isinstance(child, ast.Import):
            for alias in child.names:
                if alias.name == "daily_pipeline_core":
                    aliases.add(alias.asname or alias.name)
        elif isinstance(child, ast.ImportFrom):
            if (child.module or "").endswith("daily_pipeline_core"):
                for alias in child.names:
                    aliases.add(alias.asname or alias.name)
    return aliases


def external_roots(symbols: set[str]) -> dict[str, list[str]]:
    """Symbols reached from other modules *through* a real daily_pipeline_core alias.

    A bare ``Name`` match is rejected on purpose: ``institution_scanner``
    modules import their own ``_truthy`` / ``_read_json`` from ``_common``, and
    counting those would manufacture roots out of unrelated code.
    """
    roots: dict[str, list[str]] = {name: [] for name in symbols}
    for path in iter_python_files():
        if path.resolve() == TARGET.resolve():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        aliases = _core_aliases(tree)
        if not aliases:
            continue
        for child in ast.walk(tree):
            if (
                isinstance(child, ast.Attribute)
                and isinstance(child.value, ast.Name)
                and child.value.id in aliases
                and child.attr in symbols
            ):
                roots[child.attr].append(str(path.relative_to(ROOT)))
    return roots


def main() -> int:
    source = TARGET.read_text(encoding="utf-8")
    tree = ast.parse(source)
    defs = top_level_defs(tree)
    print(f"top-level defs: {len(defs)}")

    # Build the call/reference graph.
    edges: dict[str, set[str]] = {}
    for name, node in defs.items():
        refs = body_calls(node) | names_in_body(node)
        edges[name] = {ref for ref in refs if ref in defs and ref != name}

    scope = module_scope_names(tree)
    ext = external_roots(set(defs))

    roots: dict[str, str] = {}
    for name in defs:
        if ext.get(name):
            roots[name] = f"external: {', '.join(sorted(set(ext[name])))}"
        elif name in scope:
            roots[name] = "module-scope reference"
        else:
            roots[name] = ""

    # BFS
    seen: set[str] = set()
    stack = [n for n in defs if roots[n]]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(edges.get(cur, ()))

    alive = sorted(seen)
    dead = sorted(set(defs) - seen)

    print("\n=== ROOTS ===")
    for name in sorted(roots):
        if roots[name]:
            print(f"  {name:<32} {roots[name]}")

    print(f"\n=== ALIVE ({len(alive)}) ===")
    for name in alive:
        print(f"  {name}")

    print(f"\n=== DEAD / UNREACHABLE ({len(dead)}) ===")
    for name in dead:
        node = defs[name]
        span = f"{node.lineno}-{node.end_lineno}"  # type: ignore[union-attr]
        lines = int(node.end_lineno) - int(node.lineno) + 1  # type: ignore[union-attr]
        print(f"  {name:<32} lines {span} ({lines} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
