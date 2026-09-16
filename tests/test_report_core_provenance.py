"""Which parts of ``report_core`` production runs -- and why the answer is "all of them".

``report_core.py`` is 2,090 lines with 24 top-level definitions.  It is the
second-largest coverage gap after ``scanner_core``, so the same rule applies:
the score_core lesson says find out what is alive *before* writing behavioural
tests, or half the effort lands on code nothing calls.

The answer here is the opposite of ``scanner_core``'s, and it is worth
recording because it changes what the follow-up work should be:

* ``scanner_core`` -- **53% dead**.  Overlays *replace* bodies
  (``run_scan``'s 828 lines rebound to a closure), so the loser is dead.
* ``report_core`` -- **0% dead**.  Every layer *calls through* to the one
  below it, so nothing is displaced.

The three-layer chain
---------------------
::

    report_core.py   2090 lines   the implementation
      report_v51.py    213 lines  overrides _results_to_dataframe, installs
                                  report_determinism, then replaces itself
      report.py        423 lines  overrides _results_to_dataframe + export_all,
                                  then replaces itself

Both wrappers end with ``sys.modules[__name__] = _core``, so after import all
three names are the *same module object*: ``report_core``.  That is asserted
rather than assumed -- it is what makes the overlays work at all, since they
patch the module object the implementation resolves its globals from.

Why nothing in ``report_core`` is dead
--------------------------------------
Each layer saves the layer below *before* replacing it and then calls it:

* ``report_v51.py:24`` ``_legacy_results_to_dataframe = _core._results_to_dataframe``
  and ``report_v51.py:57`` ``frame = _legacy_results_to_dataframe(results)``
* ``report.py:31`` ``_legacy_results_to_dataframe = _core._results_to_dataframe``
  (now v51's version) and ``report.py:72`` ``frame = _legacy_results_to_dataframe(results)``
* ``report.py:32/379`` the same pattern for ``export_all``

So ``report_core``'s own 326-line ``_results_to_dataframe`` is still the
innermost call on every run -- it is *extended*, not displaced.  This is
cooperative stacking, not the replacement that killed ``run_scan``.

Consequence for the follow-up
-----------------------------
Because all 24 definitions are reachable, behavioural tests here are not wasted
on dead code.  The gap is instead one of *depth*: ``validate_decision_integrity``
(912 lines, 44% of the module) is reachable from a production entry point and
has no direct test.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_CORE = ROOT / "report_core.py"
REPORT = ROOT / "report.py"
REPORT_V51 = ROOT / "report_v51.py"

#: Data and notes directories -- never importable source.
_EXCLUDED_DIRS = {"cache", "output", ".git", "__pycache__", ".workbuddy-ai", "tests"}

#: The two symbols an overlay rebinds, and what production resolves them to.
REBOUND = {
    "_results_to_dataframe": "report.py",
    "_rankable_results": "report_determinism.py",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _definitions() -> dict[str, tuple[int, int, int]]:
    """Top-level definitions in ``report_core``: name -> (start, end, size)."""
    out: dict[str, tuple[int, int, int]] = {}
    for node in _tree(REPORT_CORE).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            end = getattr(node, "end_lineno", node.lineno)
            out[node.name] = (node.lineno, end, end - node.lineno + 1)
    return out


def _call_graph(names: set[str]) -> dict[str, set[str]]:
    """Which top-level names each top-level definition calls."""
    graph: dict[str, set[str]] = {name: set() for name in names}
    for node in _tree(REPORT_CORE).body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for call in ast.walk(node):
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if isinstance(func, ast.Name) and func.id in names:
                graph[node.name].add(func.id)
            elif isinstance(func, ast.Attribute) and func.attr in names:
                graph[node.name].add(func.attr)
    return graph


def _production_sources() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*.py")
        if not (_EXCLUDED_DIRS & set(path.relative_to(ROOT).parts))
        and path.name != "report_core.py"
    ]


def _referenced_externally(name: str) -> bool:
    """Is ``name`` mentioned in production source outside ``report_core``?

    A module that *defines* the same name does not count as a reference --
    several modules carry their own ``_atomic_write_csv``, and counting those
    would make unrelated helpers look like production entry points.
    """
    pattern = re.compile(rf"\b{re.escape(name)}\b")
    own_definition = re.compile(rf"^(?:def|class)\s+{re.escape(name)}\b", re.MULTILINE)
    for path in _production_sources():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text) and not own_definition.search(text):
            return True
    return False


def _reachable(definitions: dict[str, tuple[int, int, int]]) -> set[str]:
    roots = {name for name in definitions if _referenced_externally(name)}
    graph = _call_graph(set(definitions))
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(graph.get(current, ()))
    return seen


def _probe(snippet: str) -> list[str]:
    completed = subprocess.run(
        [sys.executable, "-c", snippet], cwd=ROOT, capture_output=True, text=True
    )
    assert completed.returncode == 0, f"probe failed:\n{completed.stderr}"
    return completed.stdout.strip().splitlines()


# ---------------------------------------------------------------------------
# the module object itself
# ---------------------------------------------------------------------------


def test_all_three_names_collapse_onto_report_core() -> None:
    """``report`` and ``report_v51`` both end with ``sys.modules`` replacement.

    Probed in a subprocess because the replacement happens at import time and
    cannot be undone.  If either name stopped being ``report_core``, the
    overlays would be patching a module the implementation no longer reads its
    globals from -- and every patch below would silently stop applying.
    """
    out = _probe(
        "import sys, report, report_core;"
        "print(sys.modules['report'] is report_core);"
        "print(sys.modules.get('report_v51') is report_core)"
    )
    assert out == ["True", "True"], f"the report chain no longer collapses: {out}"


def test_each_layer_calls_through_to_the_one_below() -> None:
    """The wrappers extend rather than replace.

    This is the whole reason ``report_core`` has no dead code, and it is a
    static property of the wrappers: each saves ``_core.<name>`` before
    overwriting it, then calls the saved copy.  Drop the call and the layer
    below becomes dead -- 326 lines in ``report_core``, 155 in ``report_v51``.
    """
    for path, funcname in ((REPORT_V51, "_results_to_dataframe"), (REPORT, "_results_to_dataframe")):
        tree = _tree(path)
        wrapper = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == funcname
        )
        called = {
            call.func.id
            for call in ast.walk(wrapper)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
        }
        assert "_legacy_results_to_dataframe" in called, (
            f"{path.name}:{wrapper.lineno} no longer calls the layer below it, "
            "which would strand the implementation it just replaced"
        )
        # ``funcname`` already starts with an underscore: _results_to_dataframe
        # -> _legacy_results_to_dataframe, not _legacy__results_to_dataframe.
        assert f"_legacy{funcname}" in {
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }, f"{path.name} no longer saves the layer below before replacing it"


def test_the_original_results_to_dataframe_is_still_the_innermost_call() -> None:
    """Both halves: it is reachable, *and* production resolves it to a wrapper.

    Reachable alone would be satisfied by a dead function that something calls;
    resolved-to-a-wrapper alone would be what a *replaced* function looks like.
    Only both together establish "extended, not displaced" -- which is the
    difference between this module and ``scanner_core``'s ``run_scan``.
    """
    definitions = _definitions()
    assert "_results_to_dataframe" in _reachable(definitions)

    out = _probe(
        "import report, report_core;"
        "print(report_core._results_to_dataframe.__code__.co_filename)"
    )
    assert out[0].endswith("report.py"), (
        "the outermost wrapper no longer wins; the chain reordered: " + out[0]
    )


# ---------------------------------------------------------------------------
# the survival map
# ---------------------------------------------------------------------------


def test_every_top_level_definition_is_reachable_from_production() -> None:
    """Zero dead definitions -- the claim that makes behaviour tests worth it.

    Roots are the definitions referenced by production source outside this
    module; everything else has to be reachable from one of them.  A new
    helper nobody calls fails here rather than quietly rotting.
    """
    definitions = _definitions()
    unreachable = sorted(set(definitions) - _reachable(definitions))
    assert not unreachable, (
        "these are no longer reachable from any production entry point; the "
        f"survival map changed and behaviour tests should skip them: {unreachable}"
    )


def test_the_two_rebound_symbols_are_declared() -> None:
    """Only two symbols leave ``report_core``, and this is where they go.

    Asserted with ``__code__.co_filename`` rather than ``__module__``: an
    overlay can set the latter to whatever it likes.
    """
    out = _probe(
        "import report, report_core;"
        + "".join(
            f"print(report_core.{name}.__code__.co_filename);" for name in REBOUND
        )
    )
    resolved = dict(zip(REBOUND, out, strict=True))
    for name, expected in REBOUND.items():
        assert resolved[name].endswith(expected), (
            f"{name} now resolves to {resolved[name]}, expected {expected}; the "
            "overlay order changed"
        )
