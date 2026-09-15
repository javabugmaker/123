"""Automated discipline gates for the canonical ``institution_scanner`` package.

The legacy root modules are already governed by ``test_architecture_growth.py``
(shrink-only byte budgets, a version ceiling).  That is why the debt stopped
growing there and started migrating into this package instead: until now the
canonical package had no gate at all, and it already hosts a 604-line function
plus byte-identical copies of the same helper in several modules.

These checks are deliberately *structural* — line counts, byte counts, AST body
hashes, import direction.  They do not encode any business rule, threshold or
strategy preference, so they can pass or fail without ever arguing about
whether a number should be 0.60 or 0.65.

Exemption lists here are **shrink-only**: if a grandfathered item falls back
inside the limit, the test fails and asks you to delete the entry.  An
exemption that never expires is just a second copy of the debt.
"""

from __future__ import annotations

import ast
import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Final

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "institution_scanner"
COMMON = PACKAGE / "_common.py"

FUNCTION_LINE_LIMIT: Final = 150
LARGE_MODULE_THRESHOLD: Final = 20_000
# Bodies shorter than this are dominated by their signature; a duplicate that
# small is noise, not extraction debt.  This threshold is also what keeps
# per-dataclass forwarders (``def to_dict(self): return asdict(self)``, 20
# characters) out of the report — they are one-liners that merely happen to
# share a body, and merging them would couple unrelated types.  No name-based
# exemption list is needed for them.
MIN_DUPLICATE_CHARS: Final = 60

# --------------------------------------------------------------------------
# Grandfathered violations.  Every entry is a pre-existing offender recorded at
# the moment the gate went live; new offenders are not accepted.  Keyed by
# (module, function) rather than line number so unrelated edits above a
# function do not invalidate the list.
# --------------------------------------------------------------------------
KNOWN_LONG_FUNCTIONS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("auction_structure.py", "compute_auction_structure"),  # 604
        ("auction_structure.py", "_apply_lifecycle"),  # 171
        ("auction_structure.py", "backtest_auction_structure"),  # 158
        ("fundamental_schema.py", "build_fundamental_summary"),  # 263
        ("fundamentals.py", "refresh_fundamental_data"),  # 228
        ("backtest_score_vectorized.py", "final_score_series"),  # 201
        ("verify_output.py", "_verify_all_results"),  # 184
    }
)

# Frozen at the current on-disk size: only shrinking is allowed.
MODULE_BYTE_BUDGETS: Final[dict[str, int]] = {
    "auction_structure.py": 72_227,
    "backtest_score_vectorized.py": 35_233,
    # Extracted from analytics_core (T2').  It is below LARGE_MODULE_THRESHOLD,
    # so the "every large module has a budget" gate would not have caught it --
    # but these are 17.5 KB of statistics helpers that used to sit inside a
    # saturated giant.  The budget moves with the code: without an entry here,
    # analytics_core's freed 14 KB could quietly re-inflate in its new home.
    #
    # Measured on a *checkout*, i.e. with CRLF line endings (core.autocrlf).
    # An earlier 17_523 was frozen from an LF working copy and turned the gate
    # red on a clean clone by 446 bytes.  Budgets are byte counts of what is on
    # disk, so they must be frozen from the same line endings a clone produces.
    "backtest_statistics.py": 17_969,
    "fundamentals.py": 34_833,
    "fundamental_schema.py": 24_559,
    "performance_curve_web.py": 24_557,
    "publication_renderer.py": 22_510,
    "reliability.py": 20_541,
}

def _iter_package_modules() -> Iterator[Path]:
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _iter_functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def test_functions_stay_within_line_limit() -> None:
    over_limit: dict[tuple[str, str], int] = {}
    for path in _iter_package_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in _iter_functions(tree):
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            if length <= FUNCTION_LINE_LIMIT:
                continue
            key = (path.name, node.name)
            over_limit[key] = max(over_limit.get(key, 0), length)

    keys_over_limit = set(over_limit)
    unexpected = sorted(keys_over_limit - KNOWN_LONG_FUNCTIONS)
    assert not unexpected, (
        f"Functions longer than {FUNCTION_LINE_LIMIT} lines must be split or "
        "registered in KNOWN_LONG_FUNCTIONS with a measured reason: "
        + ", ".join(f"{m}:{n} ({over_limit[(m, n)]}L)" for m, n in unexpected)
    )

    stale = sorted(KNOWN_LONG_FUNCTIONS - keys_over_limit)
    assert not stale, (
        "These entries are back inside the limit and must be removed from "
        "KNOWN_LONG_FUNCTIONS — the exemption list is shrink-only: "
        + ", ".join(f"{m}:{n}" for m, n in stale)
    )


def test_large_modules_are_shrink_only() -> None:
    oversized = {
        name: f"{size} > {budget}"
        for name, budget in sorted(MODULE_BYTE_BUDGETS.items())
        if (size := (PACKAGE / name).stat().st_size) > budget
    }
    assert not oversized, (
        "Canonical modules exceeded their shrink-only byte budgets; extract "
        f"new logic into a sibling module instead of growing these: {oversized}"
    )


def test_every_large_module_has_a_budget() -> None:
    missing = sorted(
        path.name
        for path in _iter_package_modules()
        if path.stat().st_size > LARGE_MODULE_THRESHOLD
        and path.name not in MODULE_BYTE_BUDGETS
    )
    assert not missing, (
        f"Modules above {LARGE_MODULE_THRESHOLD} bytes need an entry in "
        f"MODULE_BYTE_BUDGETS so growth stays visible: {missing}"
    )


def test_no_duplicated_function_bodies() -> None:
    groups: dict[str, list[str]] = {}
    for path in _iter_package_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in _iter_functions(tree):
            body = "\n".join(ast.unparse(stmt) for stmt in node.body)
            if len(body) < MIN_DUPLICATE_CHARS:
                continue
            digest = hashlib.sha1(body.encode("utf-8")).hexdigest()
            groups.setdefault(digest, []).append(f"{path.name}:{node.name}:{node.lineno}")

    duplicates = sorted(groups[k] for k, v in groups.items() if len(v) > 1)
    assert not duplicates, (
        "Byte-identical function bodies — extract the shared copy into "
        "institution_scanner/_common.py instead of editing them one at a time: "
        + "; ".join(" == ".join(group) for group in duplicates)
    )


def test_common_module_has_no_intra_package_imports() -> None:
    """``_common`` is the bottom of the package graph; keep it that way.

    It exists to hold code that several modules share.  If it starts importing
    back into the package, every extraction adds a cycle risk instead of
    removing coupling.
    """
    if not COMMON.exists():
        return
    tree = ast.parse(COMMON.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] == "institution_scanner":
                offenders.append("." * node.level + (node.module or ""))
        elif isinstance(node, ast.Import):
            offenders.extend(a.name for a in node.names if a.name.split(".")[0] == "institution_scanner")
    assert not offenders, (
        f"_common.py must not import from the package it serves: {sorted(set(offenders))}"
    )
