"""Extraction-target reconnaissance for a module inside a patched codebase.

Answers one question before any code moves: **what here can actually be lifted
out?**  Three things make a symbol immovable, and only one of them is visible
to a static scan:

1. an overlay rebinds it (runtime provenance != the defining module);
2. it depends on something that stays behind, so the new module would have to
   import the old one -- a cycle;
3. it depends on a rebound symbol, so moving it detaches production from the
   patch (the ``compute_volume_profile`` regression of T2').

Usage::

    python tests/recon_extraction_targets.py report_core
    python tests/recon_extraction_targets.py report_core --seed a,b,c
    python tests/recon_extraction_targets.py --selfcheck

The ``--selfcheck`` mode is the point.  Five earlier drafts of this kind of
scan each returned "all clear" while a known violation sat right there, because
their alias detection could not see how the overlay obtained the module
(``sys.modules.get``, a function parameter, ...).  A scan whose verdict can be
"nothing to worry about" must first prove it still finds violations that are
known to exist.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: Known positives: symbols that ARE rebound by an overlay, per module.  If the
#: scan stops finding any of these it has gone blind.  Sourced from runtime
#: observations, not from a static scan -- that was the whole lesson.
KNOWN_PATCHED: dict[str, tuple[str, ...]] = {
    "report_core": ("_rankable_results", "_results_to_dataframe", "export_all"),
    "analytics_core": ("_date_balanced_weights", "apply_backtest_ranking"),
}

#: Root facade that triggers the production assembly for a given core module.
def facade_for(module: str) -> str:
    return module[: -len("_core")] if module.endswith("_core") else module


def _bound_in(func: ast.AST) -> set[str]:
    """Every name bound inside *func* -- locals, params, imports, nested defs."""
    bound: set[str] = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                for arg in [
                    *args.posonlyargs,
                    *args.args,
                    *args.kwonlyargs,
                    args.vararg,
                    args.kwarg,
                ]:
                    if arg is not None:
                        bound.add(arg.arg)
        elif isinstance(node, ast.Lambda):
            a = node.args
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
                if arg is not None:
                    bound.add(arg.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.Global):
            bound.update(node.names)
    return bound


class Module:
    def __init__(self, name: str) -> None:
        self.name = name
        self.path = ROOT / f"{name}.py"
        self.source = self.path.read_text(encoding="utf-8")
        self.tree = ast.parse(self.source)
        self.defined = self._defined_names()
        self.functions = {
            n.name: ((n.end_lineno or n.lineno) - n.lineno + 1, n)
            for n in self.tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        # Importing the facade first is what makes provenance meaningful:
        # overlays only install once production has been assembled.
        importlib.import_module(facade_for(name))
        self.runtime = importlib.import_module(name)
        self.patched = self._patched()

    def _defined_names(self) -> set[str]:
        names: set[str] = set()
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])
        return names

    def _patched(self) -> dict[str, str]:
        """Runtime provenance -- the only reliable test for 'is this rebound'."""
        out: dict[str, str] = {}
        for name in self.functions:
            obj = getattr(self.runtime, name, None)
            origin = getattr(obj, "__module__", None)
            if origin is not None and origin != self.name:
                out[name] = origin
        return out

    def internal_deps(self, name: str) -> set[str]:
        """Module-level names this function reads, narrowed to defined names."""
        node = self.functions[name][1]
        bound = _bound_in(node)
        used: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                if sub.id not in bound and sub.id not in dir(builtins):
                    used.add(sub.id)
        return {u for u in used if u in self.defined}

    def closure(self, seeds: list[str]) -> set[str]:
        """Transitive closure of *seeds* over internal function dependencies."""
        result: set[str] = set()
        pending = [s for s in seeds if s in self.functions]
        while pending:
            name = pending.pop()
            if name in result:
                continue
            result.add(name)
            for dep in self.internal_deps(name):
                if dep in self.functions and dep not in result:
                    pending.append(dep)
        return result


def report(mod: Module) -> None:
    print(f"=== {mod.name}  ({len(mod.functions)} 个顶层函数, "
          f"{mod.path.stat().st_size} 字节) ===")
    print()
    print("被 overlay 换掉的（运行时 provenance）—— 不可搬:")
    if not mod.patched:
        print("   无")
    for name, origin in sorted(mod.patched.items()):
        print(f"   {name:<40} -> {origin}")
    print()
    rows = []
    for name, (lines, _) in mod.functions.items():
        if name in mod.patched:
            continue
        deps = mod.internal_deps(name)
        blocked = sorted(d for d in deps if d in mod.patched)
        fn_deps = sorted(d for d in deps if d in mod.functions)
        rows.append((lines, name, fn_deps, blocked))
    print("未被动过的函数（按行数降序）:")
    for lines, name, fn_deps, blocked in sorted(rows, reverse=True):
        flag = f"  !! 依赖被改符号 {blocked}" if blocked else ""
        dep = f"  依赖函数 {fn_deps}" if fn_deps else ""
        print(f"   {name:<40} {lines:>5} 行{flag}{dep}")


def evaluate_seed(mod: Module, seeds: list[str]) -> None:
    cluster = mod.closure(seeds)
    print(f"=== 闭包: seeds={seeds} ===")
    print(f"闭包成员 ({len(cluster)}): {sorted(cluster)}")
    print()
    external_fn = sorted(
        d
        for name in cluster
        for d in mod.internal_deps(name)
        if d in mod.functions and d not in cluster
    )
    patched_deps = sorted(
        d for name in cluster for d in mod.internal_deps(name) if d in mod.patched
    )
    lines = sum(mod.functions[n][0] for n in cluster)
    print(f"合计 {lines} 行")
    print(f"依赖的被改符号（会脱离补丁）: {patched_deps or '无'}")
    print(f"闭包外还依赖的函数（会成环）: {external_fn or '无'}")
    print()
    if patched_deps or external_fn:
        print("=> 该簇不自洽，需要先解决上面两项")
    else:
        print("=> 该簇自洽，可以整体搬出")


def selfcheck() -> int:
    failures: list[str] = []
    for module, positives in KNOWN_PATCHED.items():
        mod = Module(module)
        for name in positives:
            if name not in mod.patched:
                failures.append(f"{module}.{name}")
    if failures:
        print("自检失败：以下已知被改符号没被扫到 —— 扫描已失明，别信它的结论")
        for f in failures:
            print(f"   {f}")
        return 1
    total = sum(len(v) for v in KNOWN_PATCHED.values())
    print(f"自检通过：{total} 个已知正例全部被捕获")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", nargs="?", help="module to inspect, e.g. report_core")
    parser.add_argument("--seed", help="comma-separated seed functions")
    parser.add_argument("--selfcheck", action="store_true")
    args = parser.parse_args()

    if args.selfcheck:
        return selfcheck()
    if not args.module:
        parser.error("需要 module 或 --selfcheck")

    mod = Module(args.module)
    if args.seed:
        evaluate_seed(mod, [s.strip() for s in args.seed.split(",")])
    else:
        report(mod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
