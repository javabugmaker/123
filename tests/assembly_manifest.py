"""Capture the import-time assembly performed by the ``install()`` overlay kernels.

Why this exists
---------------
The project assembles itself by monkey-patching: 63 modules define an
``install()`` (or sibling) function, and a large number of them call it from
module scope so that merely importing a name rewrites other modules' symbols.
That makes "what does ``score_core.entry_point`` actually point to?" a question
whose answer depends on import order, not on the source of ``score_core``.

This module freezes the answer.  It imports a production entry point under a
call tracer, snapshots every project module's namespace before and after each
``install()`` call, and records the delta.  The result is a contract: drop a
module-level ``install()``, add a new overlay, or let two overlays swap order
and the recorded manifest changes.

Design notes
------------
* **Delta, not census.**  An earlier design walked every project module and
  flagged attributes whose ``__module__`` differed from the owning module.  That
  produces false positives: ``from collections.abc import Callable`` looks
  identical to an overlay rebinding, and every facade
  (``sys.modules[__name__] = _core``) marks its entire API as "replaced".
  Snapshotting around each ``install()`` call captures only what the overlay
  itself wrote.
* **Subprocess per entry point.**  Importing a production entry point mutates
  global state permanently, so a second entry point imported in the same
  interpreter sees an already-assembled world and its installs mostly no-op.
  Each entry point is therefore captured in a fresh interpreter.
* **Determinism.**  Deltas are keyed by ``module.attribute`` and stored sorted;
  no set iteration, no ``id()``, no timestamps, no hashes of unordered
  containers leak into the output.

Regenerate the fixtures after a deliberate change::

    python tests/assembly_manifest.py --write
"""

from __future__ import annotations

import ast
import importlib
import json
import sys
from pathlib import Path
from types import FrameType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: Production entry points whose assembly is frozen.  Each is a facade guarded
#: by ``if __name__ == "__main__"``, so importing them is side-effect free apart
#: from the installs themselves.
CORE_ENTRY_POINTS: tuple[str, ...] = ("scanner", "main", "daily_pipeline", "scan_service")

#: The research terminal.  A separate, deliberately narrow assembly path: it
#: imports only GUI modules and never pulls ``scanner``/``analytics``/``score``,
#: so its whole overlay surface is three symbols.  It is the *only* entry point
#: that reaches ``gui_process_v64``.
GUI_ENTRY_POINTS: tuple[str, ...] = ("gui_v85",)

ENTRY_POINTS: tuple[str, ...] = CORE_ENTRY_POINTS + GUI_ENTRY_POINTS

#: ``gui_v85`` needs both of these; the CI venv is built from a Python without
#: tk support, so the GUI path is skipped there rather than failing the suite.
GUI_REQUIREMENTS: tuple[str, ...] = ("tkinter", "customtkinter")


def gui_environment_available() -> bool:
    import importlib.util  # noqa: PLC0415

    return all(importlib.util.find_spec(name) is not None for name in GUI_REQUIREMENTS)

#: Functions treated as assembly hooks.  ``install`` is the dominant one; the
#: rest are the explicitly named variants used by the facades.
INSTALL_NAMES: frozenset[str] = frozenset(
    {
        "install",
        "install_pre_facade",
        "install_post_facade",
        "install_analytics_alignment",
        "install_single_recency_ranking_guard",
        "install_v84_presentation",
        "install_reliability",
    }
)

MANIFEST_FORMAT = 1

_project_file_cache: dict[str, bool] = {}


def is_project_file(path: str | None) -> bool:
    """True when *path* is a tracked ``.py`` file outside ``tests/``.

    Memoised because this runs thousands of times per capture: an unmemoised
    ``Path.resolve()`` per module per snapshot is what made the first prototype
    take 53s instead of 3s.
    """
    if not path:
        return False
    cached = _project_file_cache.get(path)
    if cached is not None:
        return cached
    try:
        resolved = Path(path).resolve()
    except OSError:
        _project_file_cache[path] = False
        return False
    ok = resolved.suffix == ".py" and resolved.is_relative_to(ROOT)
    if ok and resolved.relative_to(ROOT).parts[:1] == ("tests",):
        ok = False
    _project_file_cache[path] = ok
    return ok


def _relative(path: str) -> str:
    return Path(path).resolve().relative_to(ROOT).as_posix()


def _project_modules() -> dict[str, Any]:
    """Map ``sys.modules`` key -> module, restricted to tracked project files.

    The *key* is used rather than ``module.__name__`` because the facades rebind
    ``sys.modules[name]`` to a different module object; for
    ``sys.modules["score"]`` the key is ``"score"`` while ``__name__`` is
    ``"score_core"``.
    """
    found: dict[str, Any] = {}
    for name, module in list(sys.modules.items()):
        if module is None:
            continue
        if is_project_file(getattr(module, "__file__", None)):
            found[name] = module
    return found


def _snapshot(modules: dict[str, Any]) -> dict[str, dict[str, int]]:
    return {name: {k: id(v) for k, v in vars(module).items()} for name, module in modules.items()}


def provenance(value: Any) -> str:  # noqa: ANN401
    """Stable description of *what a symbol now points at*.

    Callables report ``module.qualname`` — enough to see an overlay swap.  Plain
    scalars (the ``_INSTALLED`` flags and migrated config thresholds) report
    their repr, because "bool" alone cannot distinguish ``True`` from ``False``.
    Everything else degrades to a type name; that is deliberately lossy, since
    ``repr`` of a live object can embed an address and would make the manifest
    irreproducible.
    """
    module = getattr(value, "__module__", None)
    qualname = getattr(value, "__qualname__", None) or getattr(value, "__name__", None)
    if isinstance(module, str) and isinstance(qualname, str):
        return f"{module}.{qualname}"
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        text = repr(value)
        if len(text) > 160:
            text = text[:157] + "..."
        return f"{type(value).__name__}:{text}"
    return type(value).__name__


def capture(entry_point: str) -> dict[str, Any]:
    """Import *entry_point* under a call tracer and record every install delta."""
    if ROOT.as_posix() not in [Path(p).as_posix() for p in sys.path]:
        sys.path.insert(0, str(ROOT))

    steps: list[dict[str, Any]] = []

    def global_trace(frame: FrameType, event: str, arg: Any) -> Any:  # noqa: ANN401
        if event != "call":
            return None
        code = frame.f_code
        if code.co_name not in INSTALL_NAMES:
            return None
        if not is_project_file(code.co_filename):
            return None
        caller = frame.f_back
        caller_file = caller.f_code.co_filename if caller is not None else None
        step: dict[str, Any] = {
            "func": f"{_relative(code.co_filename)}:{code.co_firstlineno}:{code.co_name}",
            "call_site": (
                f"{_relative(caller_file)}:{caller.f_lineno}"
                if caller is not None and is_project_file(caller_file)
                else "<external>"
            ),
            "module_level": bool(caller is not None and caller.f_code.co_name == "<module>"),
            "before": _snapshot(_project_modules()),
        }
        steps.append(step)

        def local_trace(f: FrameType, e: str, a: Any) -> Any:  # noqa: ANN401
            if e == "return":
                # Resolve the new *objects* here rather than after the import.
                # An earlier version stored only the delta's keys and read the
                # provenance in a second pass once ``import`` had finished, which
                # made every step report the FINAL value — ``score_core.entry_point``
                # looked like it was assigned ``score_cache_guard_v80.entry_point``
                # four times, hiding that v79 owned it in between.  Holding a
                # reference keeps the object alive so ``provenance`` can be
                # resolved later without ``id()`` reuse risk.
                before = step.pop("before")
                after = _snapshot(_project_modules())
                values: dict[str, Any] = {}
                for module_name, attributes in after.items():
                    previous = before.get(module_name)
                    if previous is None:
                        continue  # module did not exist yet; not a rebinding
                    for attribute, value_id in attributes.items():
                        if previous.get(attribute) == value_id:
                            continue
                        values[f"{module_name}.{attribute}"] = getattr(sys.modules[module_name], attribute)
                step["values"] = values
            return local_trace

        return local_trace

    sys.settrace(global_trace)
    try:
        importlib.import_module(entry_point)
    finally:
        sys.settrace(None)

    touched: dict[str, str] = {}
    ordered: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        delta = {target: provenance(value) for target, value in step["values"].items()}
        ordered.append(
            {
                "seq": index,
                "func": step["func"],
                "call_site": step["call_site"],
                "module_level": step["module_level"],
                "rebinds": dict(sorted(delta.items())),
            }
        )
        touched.update(delta)

    # ``final`` re-reads the provenance *after* the whole import finished, so a
    # later overlay that overwrites an earlier one is reflected even when the
    # later overlay's own delta was empty.
    final: dict[str, str] = {}
    for target in sorted(touched):
        module_name, attribute = target.rsplit(".", 1)
        module = sys.modules.get(module_name)
        if module is None:
            continue
        final[target] = provenance(getattr(module, attribute))

    return {
        "format": MANIFEST_FORMAT,
        "entry_point": entry_point,
        "install_calls": len(ordered),
        "module_level_calls": sum(1 for s in ordered if s["module_level"]),
        "rebound_symbols": len(final),
        "steps": ordered,
        "final": final,
    }


def module_level_install_sites() -> list[str]:
    """Static inventory of every ``install*()`` call at module scope.

    Complements :func:`capture`: the runtime manifest only sees what a given
    entry point imports, and 19 of the 55 module-scope call sites live in
    entry-point or lazily-imported modules that ``import scanner`` never
    reaches.  Freezing the static list stops those from drifting unnoticed.
    """
    sites: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT)
        if relative.parts[0] in {"tests", ".workbuddy-ai", "__pycache__"}:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        # Resolve import aliases first.  ``from x import install as _install_y``
        # is used twice in the repo (downloader_v51.py:154, report_v51.py:212)
        # and a plain name match silently missed both.
        aliases: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name in INSTALL_NAMES:
                        aliases[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in INSTALL_NAMES:
                        aliases[alias.asname or alias.name] = alias.name

        for node in tree.body:
            if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
                continue
            func = node.value.func
            if isinstance(func, ast.Name):
                name = aliases.get(func.id, func.id)
            elif isinstance(func, ast.Attribute):
                name = aliases.get(func.attr, func.attr)
            else:
                continue
            if name in INSTALL_NAMES:
                sites.append(f"{relative.as_posix()}:{node.lineno}:{name}")
    return sorted(sites)


def self_install_sites() -> list[str]:
    """Module-scope calls where a module installs *itself*.

    Distinct from :func:`module_level_install_sites`, which counts every
    module-scope call including the desirable ones — the central assembly points
    in ``analytics_runtime``, ``backtest_acceleration_v77`` and the facades that
    deliberately wire other modules together.  This one is the Stage 3 target:
    a module that installs itself the moment it is imported, so the caller
    cannot choose the order.

    Classification: a bare ``install()`` (no receiver) whose name is neither an
    import alias nor absent from this file's own definitions.  ``_x.install()``
    and ``from y import install as _install_z`` both install *other* modules and
    are therefore not self-installs.
    """
    found: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT)
        if relative.parts[0] in {"tests", ".workbuddy-ai", "__pycache__"}:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue

        defined = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in INSTALL_NAMES
        }
        aliases: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                aliases.update(a.asname or a.name for a in node.names if a.name in INSTALL_NAMES)
            elif isinstance(node, ast.Import):
                aliases.update(a.asname or a.name for a in node.names if a.name in INSTALL_NAMES)

        for node in tree.body:
            if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
                continue
            func = node.value.func
            if isinstance(func, ast.Name) and func.id not in aliases and func.id in defined:
                found.append(f"{relative.as_posix()}:{node.lineno}:{func.id}")
    return sorted(found)


def fixture_path(entry_point: str) -> Path:
    return FIXTURES / f"assembly_manifest_{entry_point}.json"


def sites_fixture_path() -> Path:
    return FIXTURES / "assembly_module_level_sites.json"


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _pin_universe_cache() -> None:
    """Make the manifest independent of whether this machine has run before.

    ``universe_cache_acceleration_v78.install()`` sets ``_LAST_FILE_STATE`` to
    ``_file_state()``, which returns ``None`` when
    ``cache/_tickflow_universe.json`` is missing -- the same value the module
    already holds, so the before/after diff sees no change and records no
    rebind.  On a warm machine the file exists and the rebind is recorded.

    Same code, two different manifests.  That was caught by exporting a commit
    with ``git archive`` and running the suite on the clean tree: four entry
    points went red purely because the cache directory was absent.

    The fix is to pin the precondition, not the expectation -- otherwise the
    fixture silently means "whatever this machine happens to look like".
    """
    path = ROOT / "cache" / "_tickflow_universe.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text('{"stocks": []}', encoding="utf-8")


def capture_subprocess(entry_point: str) -> dict[str, Any]:
    """Run :func:`capture` for *entry_point* in a fresh interpreter.

    Isolation is not optional.  ``import main`` transitively imports
    ``scan_service``, so capturing several entry points in one process makes
    every capture after the first a no-op: ``importlib.import_module`` returns
    the cached module and none of its installs run again.  (This was observed,
    not assumed: an in-process loop reported ``scan_service`` with 0 install
    calls.)
    """
    import subprocess  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    _pin_universe_cache()

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "manifest.json"
        subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--out",
                str(target),
                entry_point,
            ],
            check=True,
            cwd=ROOT,
            capture_output=True,
        )
        return json.loads(target.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if "--out" in argv:
        out_index = argv.index("--out")
        destination = Path(argv[out_index + 1])
        entry = argv[out_index + 2] if len(argv) > out_index + 2 else "scanner"
        _write(destination, capture(entry))
        return 0
    if "--write" in argv:
        for entry in ENTRY_POINTS:
            if entry in GUI_ENTRY_POINTS and not gui_environment_available():
                print(
                    f"skipped {entry}: needs {' + '.join(GUI_REQUIREMENTS)}. "
                    "Regenerate its fixture with an interpreter that has them "
                    "(on this machine: the system Python 3.14)."
                )
                continue
            payload = capture_subprocess(entry)
            _write(fixture_path(entry), payload)
            print(
                f"wrote {fixture_path(entry).name}: "
                f"{payload['install_calls']} calls "
                f"({payload['module_level_calls']} module-level), "
                f"{payload['rebound_symbols']} symbols"
            )
        sites = module_level_install_sites()
        _write(sites_fixture_path(), sites)
        print(f"wrote {sites_fixture_path().name}: {len(sites)} module-level call sites")
        return 0
    if "--sites" in argv:
        print(json.dumps(module_level_install_sites(), indent=2))
        return 0
    entry = argv[1] if len(argv) > 1 else "scanner"
    print(json.dumps(capture_subprocess(entry), indent=2, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
