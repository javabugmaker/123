"""Parity gates: v83 vectorised lifecycle vs the stable engine it replaced.

``lifecycle_acceleration_v83`` is a **replacement** overlay, not a callback one.
It stores the stable implementation as ``_v83_legacy_enrich_signal_lifecycle``
and never calls it, so ``signal_lifecycle_core.enrich_signal_lifecycle``
(:1001-1268, 268 lines) is dead.  Nothing reads the stored reference.

Its docstring claims "Ranking, persistence columns and signal semantics stay
identical to the stable engine."  Two cases used to contradict that, both
confirmed by running the two implementations side by side on the same frame:

* **empty history** -- v83 raised ``KeyError`` on the two benchmark columns.
  Reachable: ``daily_pipeline_core`` seeds ``SignalHistory.csv`` into staging
  only when it already exists, so a fresh checkout or a cache miss hit it.
* **re-running a trade date already present in history** -- v83 overwrote
  ``BenchmarkReturn20D`` / ``BenchmarkReturn60D`` with NaN.  Those columns are
  written by ``analytics_core.py:787`` and read by ``performance_curve.py:151/156``,
  so the loss was consumed by the performance curve, not merely diagnostic.

**Both were fixed on 2026-09-16** by adding the two columns to v83's snapshot
and to its ``outcome_columns``, matching ``signal_lifecycle_core.py:1166/1179``
and :1239/1242.  All three probe scenarios now agree.

The gates below have therefore changed meaning: they no longer pin a known
divergence, they **pin the repair**.  Reverting the patch re-crashes the empty
history and re-wipes the benchmark columns, which turns these red again --
that is exactly what ``tests/reverse_validate_lifecycle_v83.py`` case A checks.

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
SCENARIOS = ("fresh_no_history", "prior_date_history", "same_date_rerun")
BENCHMARK_COLUMNS = ("BenchmarkReturn20D", "BenchmarkReturn60D")


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
    for name in SCENARIOS:
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


@pytest.mark.parametrize("scenario_name", SCENARIOS)
def test_implementations_agree_in_every_scenario(
    probe: dict[str, Any], scenario_name: str
) -> None:
    """The head-line gate: no scenario may diverge, and neither may raise."""
    scenario = probe[scenario_name]
    for impl in ("stable", "v83"):
        error = scenario[impl].get("error")
        assert error is None, f"{scenario_name}/{impl} raised: {error}"
    assert scenario["differs_on"] == [], (
        f"{scenario_name}: the two implementations diverge on "
        f"{scenario['differs_on']}"
    )


def test_empty_history_no_longer_crashes_v83(probe: dict[str, Any]) -> None:
    """Repaired 2026-09-16: v83 used to raise KeyError here.

    Reachable whenever ``SignalHistory.csv`` is absent -- a fresh checkout, a
    wiped ``output/``, or a ``daily-pages.yml`` cache miss.
    """
    scenario = probe["fresh_no_history"]
    assert scenario["stable"].get("error") is None
    assert scenario["v83"].get("error") is None, (
        "v83 regressed on an empty history -- the snapshot is probably missing "
        f"columns again: {scenario['v83'].get('error')}"
    )


def test_same_date_rerun_preserves_benchmark_returns(probe: dict[str, Any]) -> None:
    """Repaired 2026-09-16: v83 used to wipe both benchmark columns to NaN.

    ``Return20D`` is kept in the comparison as the built-in control: it was
    always handled identically, which is what bounded the defect to the two
    benchmark columns rather than to outcome columns in general.
    """
    scenario = probe["same_date_rerun"]
    stable = scenario["stable"]
    accelerated = scenario["v83"]
    assert stable.get("error") is None and accelerated.get("error") is None

    for column in ("Return20D", *BENCHMARK_COLUMNS):
        assert stable[f"seeded_{column}"] == accelerated[f"seeded_{column}"], (
            f"{column} diverges across a same-date re-run"
        )
        assert all(
            value is not None for value in accelerated[f"seeded_{column}"].values()
        ), f"v83 lost the seeded {column}: {accelerated[f'seeded_{column}']}"


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


def test_v83_snapshot_declares_the_benchmark_columns() -> None:
    """The mechanism, pinned statically.

    The stable engine declares both benchmark columns in its snapshot
    (``signal_lifecycle_core.py:1166/1179``) and carries them through
    ``outcome_columns`` (:1239/1242).  v83 now does the same.  If either pair
    is dropped, the crash and the wipe both come back.
    """

    def declared(tree: ast.AST, name: str) -> int:
        return sum(
            1 for node in ast.walk(tree) if isinstance(node, ast.Constant) and node.value == name
        )

    stable_tree = ast.parse(CORE.read_text(encoding="utf-8"))
    v83_tree = ast.parse(V83.read_text(encoding="utf-8"))
    for column in BENCHMARK_COLUMNS:
        assert declared(stable_tree, column) > 0, (
            f"the stable engine stopped declaring {column}"
        )
        assert declared(v83_tree, column) > 0, (
            f"v83 no longer declares {column} -- the snapshot gap reopened"
        )
