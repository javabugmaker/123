"""Source-level equivalence: the T4 extraction did not edit what it moved.

``tests/test_signal_lifecycle_golden.py`` proves the *behaviour* is unchanged by
replaying 71 frozen cases.  This file proves the *text* is unchanged, which is a
different claim and catches a different mistake: a golden fixture can only see
the inputs someone thought to feed it, while a byte comparison sees every line,
including branches no case reaches.

It is pinned to the commit *before* the extraction (``PRE_MOVE_COMMIT``) rather
than to ``HEAD``.  Once T4 is committed, ``HEAD:signal_lifecycle_core.py`` no
longer defines these functions at all, and a HEAD-relative comparison would
compare the extracted module against itself -- green forever, proving nothing.

Recapture-style maintenance does not apply here: if an intentional edit lands in
``signal_attributes``, the correct response is to update this test's expectation
deliberately, because "the move also fixed a bug" is exactly the claim that must
not be smuggled in under cover of a refactor.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: The last commit before the 17 functions left ``signal_lifecycle_core``.
PRE_MOVE_COMMIT = "cd63ffd"

#: The dependency closure ``recon_extraction_targets.py --seed`` reported as
#: self-consistent.  Kept as a literal rather than imported from
#: ``golden_signal_lifecycle`` so that changing the golden corpus cannot quietly
#: narrow what this gate covers.
MOVED = (
    "_bool",
    "_number",
    "_bool_series",
    "_text_series",
    "_append_reason",
    "_execution_risk_block",
    "_breakout_confirmation_ok",
    "_lifecycle_risk_masks",
    "_holding_status",
    "_data_freshness",
    "_backtest_confidence",
    "validate_signal_consistency",
    "_atomic_write",
    "_period_scores",
    "_opportunity_score",
    "_stage",
    "_status",
)

EXTRACTED = ROOT / "institution_scanner" / "signal_attributes.py"


def _pre_move_source() -> str:
    """Read the pre-move file straight out of the object store.

    Historical source is required because this gate proves a *mechanical* move:
    comparing the new home against the working copy of the old home would
    compare nothing at all once the move is done.  That dependence on history
    makes the gate sensitive to clone depth -- ``actions/checkout`` defaults to
    ``fetch-depth: 1``, which drops ``PRE_MOVE_COMMIT`` and used to fail here
    with a bare ``CalledProcessError: exit status 128``.  Say what is missing
    and how to fix it instead; skipping quietly is not an option, because a
    gate that passes on shallow clones protects nothing.
    """
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{PRE_MOVE_COMMIT}^{{commit}}"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        raise RuntimeError(
            f"pre-move commit {PRE_MOVE_COMMIT} is not in this clone, so the "
            "extraction cannot be compared against it. Fetch full history "
            "(git fetch --unshallow) or use fetch-depth: 0 in actions/checkout."
        )
    result = subprocess.run(
        ["git", "show", f"{PRE_MOVE_COMMIT}:signal_lifecycle_core.py"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    # Budgets in this repo are frozen from CRLF checkouts; source comparison is
    # the one place where normalising is correct, because ``git show`` always
    # emits whatever is in the object store regardless of core.autocrlf.
    return result.stdout.replace("\r\n", "\n")


def _top_level_functions(source: str) -> dict[str, str]:
    """Map function name -> raw source slice, decorators and comments included.

    Slicing the original text rather than using ``ast.get_source_segment`` keeps
    the comparison byte-exact: ``get_source_segment`` re-renders through the
    parser's position bookkeeping and silently drops interior comments.
    """
    lines = source.splitlines(keepends=True)
    tree = ast.parse(source)
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        start = node.lineno - 1
        if node.decorator_list:
            start = min(decorator.lineno for decorator in node.decorator_list) - 1
        end = node.end_lineno or node.lineno
        out[node.name] = "".join(lines[start:end])
    return out


@pytest.fixture(scope="module")
def before() -> dict[str, str]:
    return _top_level_functions(_pre_move_source())


@pytest.fixture(scope="module")
def after() -> dict[str, str]:
    return _top_level_functions(
        EXTRACTED.read_bytes().decode("utf-8").replace("\r\n", "\n")
    )


def test_every_moved_function_is_byte_identical(before: dict[str, str], after: dict[str, str]) -> None:
    changed = {
        name: (len(before[name]), len(after.get(name, "")))
        for name in MOVED
        if name in before and before[name] != after.get(name)
    }
    assert not changed, (
        "These functions were edited while being moved into "
        "institution_scanner/signal_attributes.py. A move must be a move; if "
        "the edit is intended, change PRE_MOVE-backed expectation on purpose: "
        + ", ".join(f"{k} ({a} -> {b} bytes)" for k, (a, b) in sorted(changed.items()))
    )


def test_every_moved_function_is_present_in_the_new_home(
    before: dict[str, str], after: dict[str, str]
) -> None:
    missing = [name for name in MOVED if name not in after]
    assert not missing, f"not found in {EXTRACTED.name}: {sorted(missing)}"


def test_the_new_home_defines_nothing_beyond_the_moved_set(
    before: dict[str, str], after: dict[str, str]
) -> None:
    """Catch scope creep in the extraction.

    A helper quietly added to ``signal_attributes`` during the move would not be
    covered by the byte comparison above -- there is nothing to compare it
    against -- so it would land in the canonical package with no golden case and
    no provenance record.
    """
    extra = sorted(set(after) - set(MOVED))
    assert not extra, (
        f"{EXTRACTED.name} defines functions that are not part of the reviewed "
        f"closure: {extra}. Either they belong to a different module or MOVED "
        "needs extending -- and the golden fixture needs a case for them."
    )


def test_the_old_home_no_longer_defines_them() -> None:
    """The point of the extraction: no second copy left behind.

    ``signal_lifecycle_core`` re-exports these names so overlays keep patching
    the module production actually reads, but it must not *define* them. Two
    definitions means two futures, and only one of them is tested.
    """
    still_defined = sorted(
        set(MOVED) & set(_top_level_functions((ROOT / "signal_lifecycle_core.py").read_bytes().decode("utf-8")))
    )
    assert not still_defined, (
        f"signal_lifecycle_core still defines {still_defined}; the extraction "
        "left a second copy behind and the golden gate only exercises one of them."
    )
