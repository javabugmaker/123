"""Guard the ``canonical_execution`` switch in ``scan_service``.

What is at stake
----------------
``scan_service_core.execute_scan`` decides whether an execution is *canonical*
with a single identity test (``scan_service.py:100``)::

    canonical_execution = run_scan_fn is _core.run_scan

When it is true, execution additionally:

* wraps the exporter with ``enforce_enrichment_contract`` and the cache-first
  market contract (``scan_service.py:104-136``);
* sets ``_defer_checkpoint_clear_until_publish`` so recovery state survives
  until the report is published (``scan_service.py:141-142``);
* after success, clears the checkpoint, records the full-market snapshot,
  refreshes the audit and publishes the canonical report
  (``scan_service.py:157-167``).

When it is false, **all of that is silently skipped**: the scan still returns
results, but no report is published and no snapshot is recorded.  There is no
error, no warning, and nothing in the output that distinguishes the two.

Why a test is needed rather than a reading of the code
-----------------------------------------------------
``run_scan_fn`` defaults to ``_core.run_scan`` *at import time* -- a Python
default argument, evaluated once when ``scan_service_core`` is imported.  The
identity test therefore compares two things captured at different moments, and
whether they agree depends on **import order**: if
``scan_resume_boundary.install()`` runs before the import, both sides are the
overlay closure; if it runs after, the default is still whatever
``_core.run_scan`` was at import time.

The subtle failure is that *both* sides can be the legacy 828-line body.  Then
``canonical_execution`` is still ``True`` -- the identity test passes -- but the
canonical extras run on top of a scan that has no resume, no v59 checkpointing
and no boundary semantics.  An identity assertion alone would not catch that, so
this file asserts the **implementation provenance** as well, and uses
``__code__.co_filename`` rather than ``__module__`` because an overlay can
rebind the latter.

Probed in a subprocess so the result cannot depend on which other test imported
an overlay first.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

PROBE = """
import inspect
import main_core
import scan_service
import scanner_core as core

default = inspect.signature(scan_service.execute_scan).parameters["run_scan_fn"].default
print("canonical:", default is core.run_scan)
print("default_impl:", default.__code__.co_filename)
print("main_is_core:", main_core.run_scan is core.run_scan)
print("core_impl:", core.run_scan.__code__.co_filename)
"""


def _probe() -> dict[str, str]:
    completed = subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"probe failed:\n{completed.stderr}"
    out: dict[str, str] = {}
    for line in completed.stdout.strip().splitlines():
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


def _is_boundary_implementation(path: str) -> bool:
    return Path(path).name == "scan_resume_boundary.py"


def test_the_default_run_scan_still_counts_as_canonical() -> None:
    """The identity half of the switch (``scan_service.py:100``).

    Fails if the default ``run_scan_fn`` and ``scanner_core.run_scan`` stop being
    the same object -- i.e. if an overlay rebinds one after the other was
    captured, which would turn the canonical extras off without any error.
    """
    assert _probe()["canonical"] == "True"


def test_the_default_run_scan_is_the_resume_boundary_not_the_legacy_body() -> None:
    """The provenance half -- the one the identity test cannot see.

    Both sides of ``is`` could be the legacy 828-line ``scanner_core.run_scan``;
    the identity test would pass and the canonical extras would silently run on
    top of a scan with no resume and no v59 checkpointing.  This asserts which
    body is actually installed.
    """
    probe = _probe()
    assert _is_boundary_implementation(probe["default_impl"]), (
        "the default run_scan_fn is not the resume boundary closure; either the "
        f"boundary overlay is no longer installed or it moved: {probe['default_impl']}"
    )
    assert _is_boundary_implementation(probe["core_impl"]), (
        f"scanner_core.run_scan is not the resume boundary closure: {probe['core_impl']}"
    )


def test_the_main_entry_passes_the_same_object() -> None:
    """``main_core:130`` forwards ``run_scan`` imported at ``main_core:57``.

    Same import-order hazard as the default argument, one module further out:
    the name is bound when ``main_core`` is imported and handed to
    ``execute_scan`` explicitly, so a late rebind would make it a *different*
    object from ``scan_service_core``'s default and quietly disable the
    canonical extras for the CLI path only.
    """
    assert _probe()["main_is_core"] == "True"


def test_no_test_injects_a_plain_set_into_the_legacy_branch() -> None:
    """The 828-line legacy body has no caller left -- keep it that way.

    ``v59`` reaches ``_LEGACY_RUN_SCAN`` only when a checkpoint is a plain set
    rather than a ``CheckpointState`` (``scanner_resume_v59:421-422``), which in
    practice means a caller that stubbed ``load_checkpoint``.  ``provenance``
    pins that the branch calls it exactly once; this pins that nothing actually
    trips it.

    It matters for the stage-2 decision: the legacy body is classified dead on
    the strength of "no production path reaches it".  If a test starts injecting
    a plain set, that classification -- and any decision to delete 828 lines --
    has to be revisited.
    """
    injections: list[str] = []
    for path in sorted(TESTS.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            # monkeypatch.setattr(<target>, "load_checkpoint", ...)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "load_checkpoint"
            ):
                injections.append(f"{path.name}:{node.lineno}")
            # <something>.load_checkpoint = ...
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == "load_checkpoint"
                    ):
                        injections.append(f"{path.name}:{node.lineno}")

    assert not injections, (
        "these stub load_checkpoint, which is how the legacy 828-line run_scan "
        f"gets reached: {injections}"
    )
