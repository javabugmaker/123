"""Forward-looking closure gate for code extracted out of ``analytics_core``.

``institution_scanner/backtest_statistics.py`` used to live inside
``analytics_core``.  While it did, its helpers resolved their globals from
``analytics_core``'s namespace -- so an overlay that rebinds, say,
``analytics_core.compute_volume_profile`` reached them automatically.  After the
move they resolve from their own module, and any such rebinding silently stops
reaching them.

That is not hypothetical: ``indicator_acceleration_v77`` rebinds
``compute_volume_profile`` on ``analytics_core``, and the first version of the
extraction lost the accelerated implementation without a single test failing --
because the accelerated and the original implementation return *identical
numbers*, so no value fixture can see the difference.  It was found by hand.

This test keeps looking for the next one.  It has two halves:

* a **known-positive self-test**.  A scan whose verdict can be "all clear" must
  be shown to fail on a case that is known to be present, or its "all clear"
  means nothing.  Two earlier drafts of this scan reported "no danger" purely
  because their alias detection could not see how
  ``indicator_acceleration_v77`` obtains the module (``sys.modules.get``, not an
  import alias).
* the **gate**: any name the extracted module resolves from a module object that
  some overlay also rebinds on ``analytics_core`` must be listed in
  ``golden.LATE_BOUND_NAMES``, i.e. deliberately handled.
"""

from __future__ import annotations

import ast
import builtins
import re
from pathlib import Path
from types import ModuleType

import golden_analytics_core as golden

ROOT = Path(__file__).resolve().parents[1]
EXTRACTED = ROOT / "institution_scanner" / "backtest_statistics.py"

#: The one case this scan was written to catch.  If it ever stops being found,
#: the scan has gone blind and "no new dangerous names" is worthless.
KNOWN_POSITIVE = "compute_volume_profile"

_ALIAS_IMPORT = re.compile(r"^import\s+analytics_core\s+as\s+(\w+)", re.M)
_ASSIGN = re.compile(r"\b(\w+)\.(\w+)\s*=(?!=)")
_SETATTR = re.compile(r"""setattr\(\s*(\w+)\s*,\s*["'](\w+)["']""")


def _aliases_for(text: str) -> set[str]:
    """Names that provably mean analytics_core in this file.

    ``analytics_core`` itself counts regardless of how it was obtained -- that
    is what the first two drafts got wrong.
    """
    return {"analytics_core", *_ALIAS_IMPORT.findall(text)}


def _rebound_on_analytics_core() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in sorted(ROOT.glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        aliases = _aliases_for(text)
        hits = {attr for obj, attr in _ASSIGN.findall(text) if obj in aliases}
        hits |= {attr for obj, attr in _SETATTR.findall(text) if obj in aliases}
        if hits:
            found[path.name] = hits
    return found


def _local_names(func: ast.AST) -> set[str]:
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


def _resolved_names() -> set[str]:
    """Names the extracted helpers read from a module object.

    Covers both shapes: a plain global (``logger``, ``np``) and an attribute on
    an imported module (``_indicators.compute_volume_profile``), because only
    the second survives a rebinding and the first is exactly what breaks.
    """
    tree = ast.parse(EXTRACTED.read_text(encoding="utf-8"))
    namespace = vars(__import__(golden.MOVE_HOME, fromlist=["x"]))

    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local = _local_names(node)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                if sub.id not in local and sub.id not in dir(builtins):
                    names.add(sub.id)
            elif isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                holder = namespace.get(sub.value.id)
                if isinstance(holder, ModuleType):
                    names.add(sub.attr)
    return names


def test_scan_still_sees_the_known_positive() -> None:
    """Self-test: prove the scan is not blind before trusting its verdict."""
    rebound: set[str] = set()
    for attrs in _rebound_on_analytics_core().values():
        rebound |= attrs
    assert KNOWN_POSITIVE in rebound, (
        f"The scan no longer finds {KNOWN_POSITIVE}, which "
        "indicator_acceleration_v77 does rebind on analytics_core.  The scan "
        "has gone blind -- fix it before trusting any 'no danger' verdict."
    )


def test_no_unhandled_overlay_rebound_name_reaches_extracted_module() -> None:
    rebound: dict[str, set[str]] = {}
    for filename, attrs in _rebound_on_analytics_core().items():
        for attr in attrs:
            rebound.setdefault(attr, set()).add(filename)

    dangerous = sorted(_resolved_names() & set(rebound))
    unhandled = [name for name in dangerous if name not in golden.LATE_BOUND_NAMES]

    assert not unhandled, (
        "These names are read by institution_scanner/backtest_statistics and "
        "rebound on analytics_core by an overlay.  Unless they are resolved "
        "late (through the owning module's attribute) the extracted helpers "
        "silently keep the un-patched original.  Handle them and add them to "
        "golden.LATE_BOUND_NAMES: "
        + ", ".join(f"{n} (rebound by {', '.join(sorted(rebound[n]))})" for n in unhandled)
    )
