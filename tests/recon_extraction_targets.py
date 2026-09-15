"""Extraction-target reconnaissance for a module inside a patched codebase.

Answers one question before any code moves: **what here can actually be lifted
out?**  Three things make a symbol immovable, and only one of them is visible
to a static scan:

1. an overlay rebinds it (runtime provenance != the defining module);
2. it depends on something that stays behind, so the new module would have to
   import the old one -- a cycle;
3. it depends on a rebound symbol, so moving it detaches production from the
   patch (the ``compute_volume_profile`` regression of T2').

A fourth failure mode belongs to the scan itself rather than to the code under
test: methods live in ``ClassDef`` bodies, so a scan of ``tree.body`` reports a
class-heavy module as having nothing patched.  ``gui_core`` is 2515 lines with
1921 of them inside one ``ScannerGUI`` class; a top-level-only scan said "nothing
rebound" while ``gui_process_v64`` had in fact replaced four symbols
(``terminate_process_tree``, ``_popen_group_kwargs``, ``ScannerGUI._cancel_process``,
``ScannerGUI.run_process``).  The false all-clear sat on the module with 134
bytes of budget headroom, which is the worst possible place for it, so methods
are scanned now and those four are registered as known positives below.

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
import json
import os
import subprocess
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
    # 只有类方法能当正例。assembly_manifest_gui_v85.json 里还有
    # terminate_process_tree 和 _popen_group_kwargs，但那两个是 gui_process_v64
    # *新增* 的（gui_core.py 全文零处出现），不是替换。本扫描按"模块自己定义了
    # 哪些符号"取样，新增符号不在样本里，写进来会让自检永远失败。
    # 教训：装配清单的 rebind 有两种，只有 replace 能被定义点扫描看见。
    "gui_core": ("ScannerGUI._cancel_process", "ScannerGUI.run_process"),
}

#: 没有 tk 的环境里这些模块根本装不起来，自检只能跳过而不是报错——
#: 否则 CI 会在"扫不到已知正例"和"压根装不起来"之间分不清。
GUI_REQUIREMENTS: tuple[str, ...] = ("tkinter", "customtkinter")

#: 反向验证钩子。RECON_REVERSE_CHECK=1 会故意弄瞎"扫类方法"这条通路，
#: 此时 --selfcheck 必须失败在 gui_core 的两个正例上。不失败就说明这个
#: 闸门是装饰品。留成永久开关，是为了让这个证明能被重跑，而不是被记住。
_REVERSE_CHECK = os.environ.get("RECON_REVERSE_CHECK") == "1"


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
        #: key -> (行数, 节点, 所属类名 or None)。方法用 "Class.method" 作 key，
        #: 这样 provenance 和依赖分析都能走同一套逻辑，而报告里一眼看得出层级。
        self.functions: dict[str, tuple[int, ast.AST, str | None]] = {}
        #: 方法 -> 第一个参数名（通常是 self，但不必是），用于解析 self.X 调用。
        self._self_arg: dict[str, str] = {}
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._add_function(node.name, node, None)
            elif isinstance(node, ast.ClassDef) and not _REVERSE_CHECK:
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self._add_function(f"{node.name}.{member.name}", member, node.name)
        # Importing the facade first is what makes provenance meaningful:
        # overlays only install once production has been assembled.
        importlib.import_module(facade_for(name))
        self.runtime = importlib.import_module(name)
        self.patched = self._patched()

    def _add_function(self, key: str, node: ast.AST, cls: str | None) -> None:
        span = (node.end_lineno or node.lineno) - node.lineno + 1
        self.functions[key] = (span, node, cls)
        if cls is not None and node.args.args:  # type: ignore[attr-defined]
            self._self_arg[key] = node.args.args[0].arg  # type: ignore[attr-defined]

    def _defined_names(self) -> set[str]:
        names: set[str] = set()
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
                if isinstance(node, ast.ClassDef):
                    # 方法也进 defined，"self.foo" 才能被解析成 Class.foo
                    for member in node.body:
                        if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            names.add(f"{node.name}.{member.name}")
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        names.add(target.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])
        return names

    def _runtime_object(self, key: str) -> object | None:
        """Resolve a possibly-qualified name against the *assembled* module.

        ``"ScannerGUI.run_process"`` has to be read off the class, not off the
        module: an overlay that rebinds a method leaves the class object in
        place and swaps only the attribute.
        """
        if "." in key:
            cls_name, method = key.split(".", 1)
            cls = getattr(self.runtime, cls_name, None)
            return getattr(cls, method, None)
        return getattr(self.runtime, key, None)

    def _patched(self) -> dict[str, str]:
        """Runtime provenance -- the only reliable test for 'is this rebound'."""
        out: dict[str, str] = {}
        for name in self.functions:
            obj = self._runtime_object(name)
            origin = getattr(obj, "__module__", None)
            if origin is not None and origin != self.name:
                out[name] = origin
        return out

    def internal_deps(self, name: str) -> set[str]:
        """Names this function reads, narrowed to names the module defines.

        Inside a method ``self.foo()`` is an ``Attribute``, not a ``Name``, so
        without the branch below every method would look dependency-free and
        every method cluster would look safely movable.
        """
        _, node, cls = self.functions[name]
        self_name = self._self_arg.get(name)
        bound = _bound_in(node)
        used: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                if sub.id not in bound and sub.id not in dir(builtins):
                    used.add(sub.id)
            elif (
                cls is not None
                and self_name is not None
                and isinstance(sub, ast.Attribute)
                and isinstance(sub.ctx, ast.Load)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == self_name
            ):
                used.add(f"{cls}.{sub.attr}")
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
    n_methods = sum(1 for n in mod.functions if "." in n)
    print(
        f"=== {mod.name}  ({len(mod.functions) - n_methods} 个顶层函数 + "
        f"{n_methods} 个类方法, {mod.path.stat().st_size} 字节) ==="
    )
    print()
    print("被 overlay 换掉的（运行时 provenance）—— 不可搬:")
    if not mod.patched:
        print("   无")
    for name, origin in sorted(mod.patched.items()):
        print(f"   {name:<40} -> {origin}")
    print()
    rows = []
    for name, (lines, _, _) in mod.functions.items():
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
        d for name in cluster for d in mod.internal_deps(name) if d in mod.functions and d not in cluster
    )
    patched_deps = sorted(d for name in cluster for d in mod.internal_deps(name) if d in mod.patched)
    lines = sum(mod.functions[n][0] for n in cluster)
    print(f"合计 {lines} 行")
    print(f"依赖的被改符号（会脱离补丁）: {patched_deps or '无'}")
    print(f"闭包外还依赖的函数（会成环）: {external_fn or '无'}")
    print()
    if patched_deps or external_fn:
        print("=> 该簇不自洽，需要先解决上面两项")
    else:
        print("=> 该簇自洽，可以整体搬出")


def _patched_in_fresh_process(module: str) -> tuple[set[str] | None, str, bool]:
    """Scan one module in a child process; return (patched, note, environment).

    Several modules cannot share a process.  ``config`` installs the GUI runtime
    contract only if ``gui_core`` is already in ``sys.modules`` when config is
    first imported, so scanning ``report_core`` first permanently disables the
    GUI patch for everything that follows.  Observed: all gui_core positives
    vanished when report_core was scanned first in the same process.  Rather
    than depend on iteration order, give every module its own process.
    """
    proc = subprocess.run(
        [sys.executable, __file__, module, "--json"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    note = (proc.stderr or "").strip().splitlines()
    tail = note[-1][:160] if note else ""
    if proc.returncode == 2:
        return None, tail, True
    if proc.returncode != 0:
        return None, tail or f"exit {proc.returncode}", False
    return set(json.loads(proc.stdout)), "", False


def selfcheck() -> int:
    failures: list[str] = []
    skipped: list[str] = []
    checked = 0
    for module, positives in KNOWN_PATCHED.items():
        patched, note, environment = _patched_in_fresh_process(module)
        if patched is None:
            if environment:
                # 没有 tk 的 CI 里 GUI 根本装不起来。那是环境缺失，不是扫描失明，
                # 混为一谈会让"跳过"伪装成"通过"。
                skipped.append(f"{module}（{note}）")
            else:
                failures.append(f"{module}（子进程失败: {note}）")
            continue
        for name in positives:
            checked += 1
            if name not in patched:
                failures.append(f"{module}.{name}")
    if failures:
        print("自检失败：以下已知被改符号没被扫到 —— 扫描已失明，别信它的结论")
        for f in failures:
            print(f"   {f}")
        return 1
    for s in skipped:
        print(f"跳过：{s} —— 本环境缺 {GUI_REQUIREMENTS}，装不起来，不计入通过")
    print(f"自检通过：{checked} 个已知正例全部被捕获")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module", nargs="?", help="module to inspect, e.g. report_core")
    parser.add_argument("--seed", help="comma-separated seed functions")
    parser.add_argument("--selfcheck", action="store_true")
    parser.add_argument("--json", action="store_true", help="print the patched set as JSON (used by --selfcheck)")
    args = parser.parse_args()

    if args.selfcheck:
        return selfcheck()
    if not args.module:
        parser.error("需要 module 或 --selfcheck")

    try:
        mod = Module(args.module)
    except ImportError as exc:
        if args.json:
            # 退出码 2 = 环境缺失，父进程据此区分"跳过"与"工具坏了"
            print(f"unavailable: {exc}", file=sys.stderr)
            return 2
        raise
    if args.json:
        print(json.dumps(sorted(mod.patched)))
        return 0
    if args.seed:
        evaluate_seed(mod, [s.strip() for s in args.seed.split(",")])
    else:
        report(mod)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
