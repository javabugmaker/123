"""Contract test: freeze how the ``install()`` overlays assemble the runtime.

The project has no dependency-injection container.  It assembles itself by
monkey-patching at import time: 63 modules define an ``install()`` (or a named
sibling) and 30 call sites invoke one from module scope.  That means the answer
to "what does ``score_core.entry_point`` actually point to?" is decided by
import order, not by ``score_core``'s source.

The count was 57 before Stage 3 removed the 27 *self*-installing call sites
(see PROJECT_ANALYSIS.md §16).  The remaining 30 are central assembly points —
modules that install *other* modules — which is the intended end state, not
leftover debt.  ``test_no_module_installs_itself_at_import_time`` is the gate
that keeps the self-install count at zero.

These tests freeze that answer so a future refactor cannot silently drop an
overlay or reorder two that wrap each other.  Nothing here modifies production
code — this is the "先只记录不改代码" snapshot that must exist *before* any
attempt to replace the implicit assembly with explicit wiring.

What is frozen, per production entry point:

* ``steps``  — the ordered sequence of ``install()`` calls, each with the
  symbols it rebound and what it rebound them to;
* ``final``  — where every symbol ever touched by an ``install()`` ends up
  after the import completes.

Plus one static gate that the runtime data cannot provide: the full inventory
of module-scope ``install()`` call sites, including the ones no entry point
reaches.

Recapture after a deliberate change::

    python tests/assembly_manifest.py --write
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from assembly_manifest import (
    CORE_ENTRY_POINTS,
    ENTRY_POINTS,
    GUI_ENTRY_POINTS,
    capture_subprocess,
    fixture_path,
    gui_environment_available,
    module_level_install_sites,
    self_install_sites,
    sites_fixture_path,
)

#: Module-scope ``install()`` call sites no production entry point reaches.
#: All three are declared retired in
#: ``institution_scanner.runtime_inventory.RETIRED_FROM_PRODUCTION_PATH`` with
#: migration policy ``GOLDEN_EQUIVALENCE_BEFORE_REMOVAL`` — they are *meant* to
#: be unreachable, and the list is frozen so that stays deliberate rather than
#: accidental.
UNREACHED_WITH_GUI: frozenset[str] = frozenset(
    {
        "checkpoint_inputs_v59.py:79:install",
        "scanner_resume_v68.py:128:install",
        "score_runtime_v97.py:58:install",
    }
)

#: ``gui_process_v64`` is reached only through the research terminal, so without
#: the GUI fixture in hand it counts as unreachable too.
UNREACHED_WITHOUT_GUI: frozenset[str] = UNREACHED_WITH_GUI | {"gui_process_v64.py:133:install"}


def _entry_params() -> list[Any]:
    """Core entries always run; the GUI entry is skipped without tk support."""
    params: list[Any] = [pytest.param(entry) for entry in CORE_ENTRY_POINTS]
    params += [
        pytest.param(
            entry,
            marks=pytest.mark.skipif(
                not gui_environment_available(),
                reason=f"{entry} needs {' + '.join(('tkinter', 'customtkinter'))}",
            ),
        )
        for entry in GUI_ENTRY_POINTS
    ]
    return params


def _entries_with_fixtures() -> list[str]:
    return [entry for entry in ENTRY_POINTS if fixture_path(entry).exists()]


def _load(path: Any) -> Any:  # noqa: ANN401
    return json.loads(path.read_text(encoding="utf-8"))


def _observed_sites(entry: str) -> set[str]:
    payload = _load(fixture_path(entry))
    seen: set[str] = set()
    for step in payload["steps"]:
        if not step["module_level"] or step["call_site"] == "<external>":
            continue
        location = step["call_site"].rsplit(":", 1)
        seen.add(f"{location[0]}:{location[1]}:{step['func'].rsplit(':', 1)[1]}")
    return seen


@pytest.mark.parametrize("entry", _entry_params())
def test_assembly_manifest_matches_fixture(entry: str) -> None:
    """The import-time assembly of *entry* must be byte-for-byte the frozen one."""
    expected = _load(fixture_path(entry))
    actual = capture_subprocess(entry)

    assert actual["install_calls"] == expected["install_calls"], (
        f"{entry}: install() call count changed "
        f"{expected['install_calls']} -> {actual['install_calls']}. "
        "If an overlay was added or removed on purpose, recapture with "
        "`python tests/assembly_manifest.py --write`."
    )

    problems: list[str] = []
    expected_steps = expected["steps"]
    actual_steps = actual["steps"]
    if len(expected_steps) != len(actual_steps):
        problems.append(f"step count {len(expected_steps)} -> {len(actual_steps)}")
    for old, new in zip(expected_steps, actual_steps):
        label = f"step#{old['seq']} {old['func']}"
        if old["func"] != new["func"] or old["call_site"] != new["call_site"]:
            problems.append(f"{label}: call order changed -> {new['func']} @ {new['call_site']}")
            continue
        if old["module_level"] != new["module_level"]:
            problems.append(f"{label}: module-level auto-call is now {new['module_level']}")
        lost = sorted(set(old["rebinds"]) - set(new["rebinds"]))
        added = sorted(set(new["rebinds"]) - set(old["rebinds"]))
        changed = sorted(k for k in set(old["rebinds"]) & set(new["rebinds"]) if old["rebinds"][k] != new["rebinds"][k])
        if lost:
            problems.append(f"{label}: no longer rebinds {lost}")
        if added:
            problems.append(f"{label}: now also rebinds {added}")
        if changed:
            detail = ", ".join(f"{k}: {old['rebinds'][k]} -> {new['rebinds'][k]}" for k in changed)
            problems.append(f"{label}: rebound to something else — {detail}")

    if expected["final"] != actual["final"]:
        changed = sorted(
            k for k in set(expected["final"]) & set(actual["final"]) if expected["final"][k] != actual["final"][k]
        )
        missing = sorted(set(expected["final"]) - set(actual["final"]))
        extra = sorted(set(actual["final"]) - set(expected["final"]))
        problems.append(
            "final assembly changed: "
            f"resolves differently -> {changed}; no longer touched -> {missing}; "
            f"newly touched -> {extra}"
        )

    assert not problems, f"{entry}: " + "\n  ".join(problems)


def test_module_level_install_sites_frozen() -> None:
    """Static inventory of module-scope ``install()`` calls must not drift.

    Covers what the runtime capture cannot: sites in modules no entry point
    imports.  Aliased imports (``from x import install as _install_y``, used at
    ``downloader_v51.py:154`` and ``report_v51.py:212``) are resolved, so a
    rename of the local alias does not slip past.
    """
    expected = _load(sites_fixture_path())
    actual = module_level_install_sites()

    assert actual == expected, (
        "module-scope install() call sites changed.\n"
        f"  removed: {sorted(set(expected) - set(actual))}\n"
        f"  added:   {sorted(set(actual) - set(expected))}\n"
        "Recapture with `python tests/assembly_manifest.py --write`."
    )


def test_static_inventory_covers_every_observed_module_level_call() -> None:
    """Guard the static scan itself against the aliased-import blind spot."""
    static = set(_load(sites_fixture_path()))
    observed: set[str] = set()
    for entry in _entries_with_fixtures():
        observed |= _observed_sites(entry)

    missing = sorted(observed - static)
    assert not missing, (
        "The runtime capture saw module-level install() calls the static scan "
        f"does not know about: {missing}. Almost certainly a new aliased "
        "import; teach module_level_install_sites() about it."
    )


#: Self-installs allowed to remain: the three modules registered in
#: ``institution_scanner.runtime_inventory.RETIRED_FROM_PRODUCTION_PATH``.  They
#: are queued for *deletion* (policy ``GOLDEN_EQUIVALENCE_BEFORE_REMOVAL``), not
#: for rewiring — no production entry point imports them, so their self-install
#: never runs and refactoring it would be churn.  Exempted explicitly rather
#: than silently ignored.
RETIRED_SELF_INSTALLS: frozenset[str] = frozenset(
    {
        "checkpoint_inputs_v59.py:79:install",
        "scanner_resume_v68.py:128:install",
        "score_runtime_v97.py:58:install",
    }
)


def test_no_module_installs_itself_at_import_time() -> None:
    """Stage 3 acceptance gate: every reachable self-install has been removed.

    This is the "no regression" lock.  Module-scope calls that install *another*
    module are still allowed and expected — those are the central assembly
    points.  What must stay at zero is a module wiring itself up as a side
    effect of being imported, which is what made load order implicit.
    """
    remaining = set(self_install_sites())
    unexpected = remaining - RETIRED_SELF_INSTALLS
    assert not unexpected, (
        "Self-installing modules came back. These call their own install() at "
        "module scope, so importing them mutates other modules:\n  "
        + "\n  ".join(sorted(unexpected))
        + "\nMove the call into institution_scanner.analytics_runtime (or the "
        "owning facade) and verify with "
        "`python tests/_strip_module_level_installs.py --apply`."
    )


def test_unreached_module_level_sites_are_the_known_few() -> None:
    """Frozen list of module-scope installs no production entry point executes."""
    static = set(_load(sites_fixture_path()))
    observed: set[str] = set()
    for entry in _entries_with_fixtures():
        observed |= _observed_sites(entry)

    gui_covered = fixture_path(GUI_ENTRY_POINTS[0]).exists()
    expected = set(UNREACHED_WITH_GUI if gui_covered else UNREACHED_WITHOUT_GUI)
    unreached = static - observed
    assert unreached == expected, (
        "Module-scope install() sites unreachable from every production entry "
        "point changed.\n"
        f"  newly unreachable: {sorted(unreached - expected)}\n"
        f"  now reachable:     {sorted(expected - unreached)}\n"
        "If an overlay was deliberately retired, register it in "
        "institution_scanner.runtime_inventory.RETIRED_FROM_PRODUCTION_PATH and "
        "update UNREACHED_WITH_GUI here."
    )
