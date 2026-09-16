"""Declared-dead ``score_core`` symbols, and why they are not deleted.

§10.10 recorded two symbols with zero call sites anywhere.  The obvious move is
to delete them; this file is the argument for not doing that yet, frozen as
assertions.

Why not delete
--------------
1. Both are pinned by ``tests/fixtures/score_core_golden.json`` (514 cases)
   *and* by ``tests/reverse_validate_score_core.py``, where the cases
   "value_trap_risk_score: 结果减半" and "cyclical_turn_factor: 数据不足兜底分
   50 → 51" are two of the fifteen live proofs that the golden harness bites.
   Deleting the symbols deletes those proofs too -- trading real guardrail
   coverage for tidiness, in a codebase whose entire value proposition is the
   guardrails (§10.1).
2. ``score_core``'s future role is still open (§10.6 Q3: canonical reference
   implementation, or demoted to documentation).  Deleting components
   pre-empts that choice.
3. ``score_core`` is a byte-budgeted canonical module, so even annotating the
   functions as unused would need a budget raise.  The declaration therefore
   lives here, where nothing else has to move.

What this file does instead
---------------------------
Makes "dead" an explicit, gated state.  Anyone who wires one of these into
``score_ticker`` gets a red test demanding the declaration be updated -- the
same "no reason, no exemption" rule the vectorised map already uses.  The
misreading this is meant to prevent (a reader assuming an unused component
participates in scoring) is answered by the declaration itself.

Deleting them later is a one-line change here plus a golden recapture.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCORE_CORE = ROOT / "score_core.py"
_SKIP_DIRS = frozenset({"tests", "__pycache__", ".workbuddy-ai", ".git", "cache", "output"})

#: ``score_core`` symbols that are implemented but wired into nothing.
#: The reason is mandatory -- an unexplained entry is a hole in the gate.
DEAD_SYMBOLS: dict[str, str] = {
    "cyclical_turn_factor": (
        "约 120 行，全仓库零调用点：生产没有，score_core 内部也没有。"
        "留下它是因为 golden 与 reverse_validate 里有它两条已验证会红的用例；"
        "删它等于用两条护栏换 120 行整洁，且 score_core 的定位尚未定（§10.6 Q3）。"
    ),
    "value_trap_risk_score": (
        "恒等包装器 return value_trap_risk(df)，生产零引用。同上："
        "reverse_validate 的「结果减半」用例是 golden 敏感性的一条实证。"
    ),
}


def _top_level_public_functions() -> set[str]:
    tree = ast.parse(SCORE_CORE.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    }


def _production_call_sites() -> set[str]:
    """Every name called anywhere in shipped code, tests and caches excluded."""
    found: set[str] = set()
    for path in sorted(ROOT.rglob("*.py")):
        if _SKIP_DIRS & set(path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                found.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                found.add(node.func.attr)
    return found


@pytest.fixture(scope="module")
def public_functions() -> set[str]:
    return _top_level_public_functions()


@pytest.fixture(scope="module")
def call_sites() -> set[str]:
    return _production_call_sites()


def test_every_declared_dead_symbol_still_exists(public_functions: set[str]) -> None:
    """Guard against a stale declaration.

    If one of these is actually removed, this file must be updated in the same
    change -- otherwise the gate quietly stops describing anything.
    """
    missing = sorted(set(DEAD_SYMBOLS) - public_functions)
    assert not missing, (
        f"declared dead but no longer a public score_core function: {missing}; "
        "remove the entry from DEAD_SYMBOLS"
    )


def test_declared_dead_symbols_are_called_nowhere(call_sites: set[str]) -> None:
    """The deadness itself, scanned repo-wide rather than assumed.

    Covers both bare calls and ``module.symbol(...)`` forms, so a call added in
    any shipped module -- not just ``score_ticker`` -- turns this red and forces
    the declaration to be revisited.
    """
    revived = sorted(set(DEAD_SYMBOLS) & call_sites)
    assert not revived, (
        f"{revived} now have call sites in shipped code; they are no longer "
        "dead -- update DEAD_SYMBOLS and the §10.10 note"
    )


def test_declared_dead_symbols_are_outside_the_scoring_chain() -> None:
    """The specific misreading this file exists to prevent.

    Not a duplicate of the scan above: that one is repo-wide and catches any
    caller, this one pins that the scoring entry point itself does not reach
    them, which is what a reader of ``score_core`` would otherwise assume.
    """
    tree = ast.parse(SCORE_CORE.read_text(encoding="utf-8"))
    score_ticker = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "score_ticker"
    )
    called = {
        node.func.id
        for node in ast.walk(score_ticker)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    reached = sorted(set(DEAD_SYMBOLS) & called)
    assert not reached, (
        f"score_ticker now calls {reached}; they are part of the score and the "
        "declaration above is wrong"
    )


def test_every_declared_dead_symbol_carries_a_reason() -> None:
    """Same rule as ``VECTOR_ONLY_KEYS`` and ``SCALAR_ONLY_CALLS``.

    An unexplained exemption is how a gate rots: the next reader cannot tell
    whether it is deliberate or merely out of date.
    """
    for name, reason in DEAD_SYMBOLS.items():
        assert reason.strip(), f"{name} is declared dead with no reason"
        assert len(reason) > 40, (
            f"{name}'s reason is too short to be a reason: {reason!r}"
        )
