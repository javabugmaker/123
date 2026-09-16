"""Survivability and assembly gates for ``daily_pipeline_core``.

Three overlays sit on top of the core and every one of them is **callback**
style: each captures the implementation below it, calls it, and only then
rebinds the name.  That is the opposite of the ``scanner_core`` shape, where an
overlay replaced the original and orphaned ~950 lines.  Here the core originals
are the innermost call of every chain, so nothing in the core is dead.

What this module locks:

1. the facade and the ``sys.modules`` entry both collapse to the core module;
2. every overlay layer really does call the one below it (verified through
   ``__code__.co_filename``, never ``__module__``);
3. the chain depths match the three-layer stack;
4. all 27 top-level definitions are reachable from a genuine root;
5. the reachability root rule rejects same-named symbols owned by other modules.

Reverse validation notes live next to each test.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest
from _daily_pipeline_probe_runner import run_probe
from recon_daily_pipeline_survivability import (
    _core_aliases,
    body_calls,
    external_roots,
    module_scope_names,
    names_in_body,
    top_level_defs,
)

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "daily_pipeline_core.py"
SKIP_DIRS = {"cache", "output", "tests", "__pycache__", ".git", ".workbuddy-ai"}

# The three-layer stack, outermost first, as observed at runtime.
EXPECTED_CHAINS: dict[str, tuple[str, ...]] = {
    "run_daily_pipeline": (
        "daily_live_freshness_v101.py",
        "daily_recovery_v74.py",
        "daily_pipeline_core.py",
    ),
    "_write_manifest": (
        "daily_live_freshness_v101.py",
        "daily_pipeline.py",
        "daily_pipeline_core.py",
    ),
    "_quality_gate_errors": (
        "daily_live_freshness_v101.py",
        "daily_pipeline.py",
        "daily_pipeline_core.py",
    ),
    "_final_output_errors": ("daily_live_freshness_v101.py", "daily_pipeline_core.py"),
    "_csv_profile": ("daily_pipeline.py", "daily_pipeline_core.py"),
    "_activate_run": ("daily_pipeline.py", "daily_pipeline_core.py"),
    "_begin_transaction": ("daily_recovery_v74.py", "daily_pipeline_core.py"),
}


@pytest.fixture(scope="module")
def probe() -> dict[str, Any]:
    return run_probe()


def test_facade_and_sys_modules_entry_collapse_to_core(probe: dict[str, Any]) -> None:
    identity = probe["identity"]
    assert identity["facade_is_core"], (
        "importing the facade no longer yields the core module; "
        "the `sys.modules[__name__] = _core` swap at daily_pipeline.py:317 is gone"
    )
    assert identity["sys_modules_entry_is_core"], (
        "sys.modules['daily_pipeline'] is a different object than the core"
    )
    assert identity["facade_file"] == "daily_pipeline_core.py"


def test_every_overlay_layer_calls_the_one_below(probe: dict[str, Any]) -> None:
    """No chain may terminate at an overlay.

    Reverse validation: the detection walks both closure cells (overlays defined
    inside ``install()``) and module globals (``_LEGACY_*`` at module scope).
    Dropping either shape truncates a chain -- v101 is closure-based,
    daily_pipeline/recovery are global-based, and both shapes appear here.
    """
    chains = probe["chains"]
    for name, expected in EXPECTED_CHAINS.items():
        assert name in chains, f"probe did not report a chain for {name}: {sorted(chains)}"
        observed = tuple(chains[name])
        assert observed == expected, (
            f"{name} chain changed: {observed} != {expected}. "
            "An overlay stopped calling the layer below it, which is what turns "
            "the lower implementation into dead code."
        )


def test_every_chain_reaches_the_core_implementation(probe: dict[str, Any]) -> None:
    chains = probe["chains"]
    for name, chain in chains.items():
        assert chain[-1] == "daily_pipeline_core.py", (
            f"{name} never reaches the core implementation: {chain}"
        )
        assert len(chain) == len(set(chain)), (
            f"{name} chain revisits a layer, suggesting a rebinding loop: {chain}"
        )


def test_all_top_level_definitions_are_reachable() -> None:
    """Reachability from genuine roots, not from same-named lookalikes."""
    tree = ast.parse(CORE.read_text(encoding="utf-8"))
    defs = top_level_defs(tree)
    assert len(defs) == 27, (
        f"daily_pipeline_core top-level definition count changed: {len(defs)} != 27"
    )

    edges = {
        name: {
            ref
            for ref in (body_calls(node) | names_in_body(node))
            if ref in defs and ref != name
        }
        for name, node in defs.items()
    }
    scope = module_scope_names(tree)
    external = external_roots(set(defs))

    roots = {name for name in defs if external.get(name) or name in scope}
    assert roots, "no roots found; the root rule has become vacuous"

    seen: set[str] = set()
    stack = sorted(roots)
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(edges.get(current, ()))

    unreachable = sorted(set(defs) - seen)
    assert not unreachable, (
        "daily_pipeline_core has definitions no production path can reach: "
        f"{unreachable}"
    )


def test_root_rule_rejects_symbols_other_modules_own() -> None:
    """The root rule must not credit another module's lookalike.

    ``institution_scanner.publication_renderer`` imports its own ``_truthy`` and
    ``_read_json`` from ``institution_scanner._common``.  A loose bare-``Name``
    rule would count those as evidence that the core's versions are production
    entry points, manufacturing roots out of unrelated code.
    """
    renderer = ROOT / "institution_scanner" / "publication_renderer.py"
    tree = ast.parse(renderer.read_text(encoding="utf-8"))

    assert not _core_aliases(tree), (
        "publication_renderer.py now imports daily_pipeline_core; "
        "this test's premise no longer holds and must be rewritten"
    )
    source = renderer.read_text(encoding="utf-8")
    assert "from ._common import _truthy" in source, (
        "publication_renderer.py no longer imports its own _truthy"
    )

    # The loose rule sees the names; the tight rule must separate the lookalike
    # from the real one.  ``_truthy`` is only ever the foreign copy, while
    # ``_read_json`` is genuinely reached through ``_core`` in
    # ``daily_recovery_v74``.  Asserting "no roots at all" would be wrong; the
    # point is that the rule discriminates.
    mentioned = {
        child.id
        for child in ast.walk(tree)
        if isinstance(child, ast.Name) and child.id in {"_truthy", "_read_json"}
    }
    assert mentioned, "publication_renderer.py no longer mentions either name"

    roots = external_roots({"_truthy", "_read_json"})
    assert roots["_truthy"] == [], (
        f"the lookalike was credited as a root: {roots['_truthy']}"
    )
    assert set(roots["_read_json"]) == {"daily_recovery_v74.py"}, (
        f"the genuine alias was not credited: {roots['_read_json']}"
    )


def test_both_integrity_versions_are_installed(probe: dict[str, Any]) -> None:
    versions = probe["versions"]
    assert versions["v101_installed"], "v101 overlay did not install"
    assert versions["recovery"], "recovery integrity version is unset"
    assert versions["live_publication"], "v101 integrity version is unset"
