"""Parity gates: v83 vectorised lifecycle vs the stable engine it replaced.

``lifecycle_acceleration_v83`` is a **replacement** overlay, not a callback one.
It stores the stable implementation as ``_v83_legacy_enrich_signal_lifecycle``
and never calls it, so ``signal_lifecycle_core.enrich_signal_lifecycle``
(:1001-1268, 268 lines) is dead.  Nothing reads the stored reference.

Its docstring claims "Ranking, persistence columns and signal semantics stay
identical to the stable engine."  Two cases contradict that, both confirmed by
running the two implementations side by side on the same frame:

* **empty history** -- v83 raises ``KeyError`` on the two benchmark columns,
  the stable engine completes.  Reachable: ``daily_pipeline_core`` seeds
  ``SignalHistory.csv`` into staging only when it already exists.
* **re-running a trade date already present in history** -- v83 overwrites
  ``BenchmarkReturn20D`` / ``BenchmarkReturn60D`` with NaN, the stable engine
  preserves them.  Those columns are written by ``analytics_core.py:787`` and
  read by ``performance_curve.py:151/156``, so this is not an unread
  diagnostic: it is data the performance curve consumes.

Neither case is fixed here.  These gates pin current behaviour so the
divergence cannot shift unnoticed, and so that fixing it later is a deliberate
act that turns these tests red first.

All observations come from ``lifecycle_v83_parity_probe.py`` in a subprocess:
v83's ``install()`` mutates whatever module it is handed, and the stable
original is only reachable before that happens.
"""

from __future__ import annotations

import ast
import json
import pathlib
import subprocess
import sys
from typing import Any

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PROBE = pathlib.Path(__file__).resolve().with_name("lifecycle_v83_parity_probe.py")
CORE = ROOT / "signal_lifecycle_core.py"
V83 = ROOT / "lifecycle_acceleration_v83.py"
SKIP_DIRS = {"cache", "output", "tests", "__pycache__", ".git", ".workbuddy-ai"}


@pytest.fixture(scope="module")
def probe() -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, str(PROBE)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert result.returncode == 0, (
        f"parity probe crashed\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return dict(json.loads(result.stdout))


def test_probe_reached_every_scenario(probe: dict[str, Any]) -> None:
    assert "probe_error" not in probe, probe["probe_error"]
    for name in ("fresh_no_history", "prior_date_history", "same_date_rerun"):
        assert name in probe, f"probe is missing {name}: {sorted(probe)}"


def test_v83_replaces_rather_than_wraps_the_stable_engine(probe: dict[str, Any]) -> None:
    """Ownership by ``co_filename``, never ``__module__``.

    v83:400 sets ``enrich_signal_lifecycle.__module__ = core.__name__``, so the
    assembly manifest records it as ``signal_lifecycle_core._build_enricher``.
    The real definition lives in ``lifecycle_acceleration_v83.py``.
    """
    owners = probe["owners"]
    assert owners["original"] == "signal_lifecycle_core.py"
    assert owners["vectorised"] == "lifecycle_acceleration_v83.py", (
        "the vectorised enricher no longer comes from lifecycle_acceleration_v83; "
        "a different overlay has taken over the name"
    )


def test_the_stored_legacy_reference_is_write_only() -> None:
    """Nothing consumes the implementation v83 displaced.

    If a future overlay started delegating to it, the stable engine would be
    back on the production path and this test would correctly go red.
    """
    # Classified by AST context rather than by text: the ``hasattr`` guard
    # mentions the name as a plain string, and a substring scan would count it
    # as a reader.
    writes: list[str] = []
    reads: list[str] = []
    for path in ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "_v83_legacy_enrich_signal_lifecycle"
            ):
                where = f"{path.relative_to(ROOT)}:{node.lineno}"
                (reads if isinstance(node.ctx, ast.Load) else writes).append(where)

    assert writes, "the legacy fallback attribute is no longer stored anywhere"
    assert not reads, (
        "the stored legacy implementation is now read -- v83 has become a "
        f"callback overlay and the stable engine is back on the path: {reads}"
    )


def test_empty_history_crashes_v83_but_not_the_stable_engine(
    probe: dict[str, Any],
) -> None:
    scenario = probe["fresh_no_history"]
    assert scenario["stable"].get("error") is None, (
        f"the stable engine also failed on an empty history: {scenario['stable']}"
    )
    error = scenario["v83"].get("error", "")
    assert "KeyError" in error, (
        "v83 no longer raises on an empty history -- either it was fixed (update "
        f"this gate deliberately) or the symptom changed: {error}"
    )
    for column in ("BenchmarkReturn20D", "BenchmarkReturn60D"):
        assert column in error, (
            f"the KeyError no longer names {column}; the missing-column set "
            f"changed: {error}"
        )


def test_same_date_rerun_drops_benchmark_returns_in_v83_only(
    probe: dict[str, Any],
) -> None:
    """The data-loss case, with ``Return20D`` as the built-in control.

    ``Return20D`` is handled identically by both implementations, which is what
    makes this a bound to the two benchmark columns rather than a general
    outcome-column disagreement.
    """
    scenario = probe["same_date_rerun"]
    stable = scenario["stable"]
    accelerated = scenario["v83"]
    assert stable.get("error") is None and accelerated.get("error") is None

    assert stable["seeded_Return20D"] == accelerated["seeded_Return20D"], (
        "Return20D now diverges too; the divergence is no longer specific to the "
        "benchmark columns"
    )
    assert all(value is not None for value in stable["seeded_BenchmarkReturn20D"].values()), (
        f"the stable engine lost the seeded benchmark returns: {stable['seeded_BenchmarkReturn20D']}"
    )
    assert all(value is None for value in accelerated["seeded_BenchmarkReturn20D"].values()), (
        "v83 now preserves BenchmarkReturn20D across a same-date re-run -- it was "
        f"fixed, so update this gate: {accelerated['seeded_BenchmarkReturn20D']}"
    )
    assert all(value is None for value in accelerated["seeded_BenchmarkReturn60D"].values()), (
        f"{accelerated['seeded_BenchmarkReturn60D']}"
    )


def test_prior_date_history_is_identical(probe: dict[str, Any]) -> None:
    """Control: the harness is not simply always different.

    With history on an earlier trade date both implementations agree on every
    compared column, including the seeded benchmark returns.
    """
    scenario = probe["prior_date_history"]
    assert scenario["differs_on"] == [], (
        f"the two implementations diverge even in the control scenario: "
        f"{scenario['differs_on']}"
    )
    stable = scenario["stable"]
    accelerated = scenario["v83"]
    assert stable["seeded_BenchmarkReturn20D"] == accelerated["seeded_BenchmarkReturn20D"]
    assert all(value is not None for value in stable["seeded_BenchmarkReturn20D"].values())


def test_v83_snapshot_omits_the_benchmark_columns() -> None:
    """The mechanism, pinned statically.

    The stable engine declares both benchmark columns in its snapshot
    (``signal_lifecycle_core.py:1166/1179``) and carries them through
    ``outcome_columns`` (:1239/1242).  v83's snapshot and its
    ``outcome_columns`` list (:368-373) name neither.  That is why both the
    crash and the wipe happen.
    """
    stable_tree = ast.parse(CORE.read_text(encoding="utf-8"))
    v83_tree = ast.parse(V83.read_text(encoding="utf-8"))

    def declared(tree: ast.AST, name: str) -> int:
        return sum(
            1
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and node.value == name
        )

    for column in ("BenchmarkReturn20D", "BenchmarkReturn60D"):
        assert declared(stable_tree, column) > 0, (
            f"the stable engine stopped declaring {column}"
        )
        assert declared(v83_tree, column) == 0, (
            f"v83 now mentions {column} -- the snapshot gap was closed, so "
            "update the parity gates deliberately"
        )
