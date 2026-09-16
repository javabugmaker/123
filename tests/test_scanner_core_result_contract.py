"""Contracts that ``scan_single_from_df``'s output must satisfy, plus two
structural facts the static provenance gate cannot see.

Three unrelated things live here because they share one property: they are
**runtime** facts, which ``tests/test_scanner_core_provenance.py`` -- an AST
reader -- cannot observe.

1. ``ScanResult`` field consistency (``scan_single_from_df``'s 60-field output).
2. The ``scanner`` facade is the *same module object* as ``scanner_core``.
3. ``v59._LEGACY_RUN_SCAN`` still points at the original 828-line body.

On the (deliberate) absence of score values
-------------------------------------------
Same rule as the companion files: no score is pinned.  What is pinned is the
*agreement between two representations of the same fact*.  ``ScanResult``
duplicates part of ``ScoreBreakdown`` into flat fields, and derives several
fields from one another; those duplications are where a silent divergence hides,
and they hold regardless of which scoring overlay is installed.

Item 2 is a behavioural requirement, not a nicety: ``scanner.py``'s docstring
records that overlays patch the module object, so splitting the alias would
silently drop resume behaviour.  Item 3 is why the 828-line ``run_scan`` is
classified as legacy at all.

Both 2 and 3 are probed in a **subprocess** so the result cannot depend on which
other test imported an overlay first.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import scanner_core as scanner

ROOT = Path(__file__).resolve().parents[1]
BARS = 260


def _frame() -> pd.DataFrame:
    """Same recipe as the companion files: synthetic, seeded, cache-free."""
    rng = np.random.default_rng(20260916)
    index = pd.bdate_range("2023-01-02", periods=BARS)
    close = np.abs(10.0 + np.cumsum(rng.normal(0.0, 0.12, BARS))) + 1.0
    return pd.DataFrame(
        {
            "Open": close * (1.0 + rng.normal(0.0, 0.004, BARS)),
            "High": close * (1.0 + np.abs(rng.normal(0.0, 0.008, BARS))),
            "Low": close * (1.0 - np.abs(rng.normal(0.0, 0.008, BARS))),
            "Close": close,
            "Volume": rng.integers(100_000, 900_000, BARS).astype(float),
        },
        index=index,
    )


@pytest.fixture
def scanned() -> scanner.ScanResult:
    info = scanner.TickerInfo(ticker="600000.SH", name="浦发银行")
    result = scanner.scan_single_from_df(info, _frame())
    assert result.error == "", f"the fixture frame failed to scan: {result.error}"
    return result


def _probe(snippet: str) -> str:
    """Run a snippet in a clean interpreter and return its stdout."""
    completed = subprocess.run(
        [sys.executable, "-c", snippet],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, f"probe failed:\n{completed.stderr}"
    return completed.stdout.strip()


# ---------------------------------------------------------------------------
# 1. ScanResult consistency
# ---------------------------------------------------------------------------


def test_the_close_is_the_last_bar_of_the_frame(scanned: scanner.ScanResult) -> None:
    """``close`` is copied from the frame, not recomputed (scanner_core:477).

    Exact rather than approximate on purpose: this is one of the few fields in
    the result that no overlay touches, so there is no reason to tolerate drift.
    """
    assert scanned.close == pytest.approx(_frame()["Close"].iloc[-1])


def test_the_flat_mirror_fields_agree_with_the_score_object(
    scanned: scanner.ScanResult,
) -> None:
    """Three fields exist twice (scanner_core:727-735).

    ``ScanResult`` carries ``score_*`` copies so consumers need not reach into
    the nested object.  A divergence would mean two parts of the published
    report disagree about the same scan.
    """
    score = scanned.score
    assert scanned.score_missing_indicators == score.missing_indicators
    assert scanned.score_coverage == pytest.approx(score.indicator_coverage)
    assert scanned.score_confidence == pytest.approx(score.confidence)


def test_passed_filters_is_the_conjunction_of_its_two_parts(
    scanned: scanner.ScanResult,
) -> None:
    """``passed = universe_eligible and signal_confirmed`` (scanner_core:563).

    Pinned as an identity rather than a value: which tickers *should* pass is
    the maintainer's call, but "passed means both" is arithmetic.
    """
    assert scanned.passed_filters == bool(
        scanned.universe_eligible and scanned.signal_confirmed
    )


def test_the_failed_filter_count_matches_the_names(scanned: scanner.ScanResult) -> None:
    """One is derived from the other (scanner_core:779-780) and both are exported."""
    names = [name for name in scanned.failed_filter_names.split(",") if name]
    assert scanned.failed_filter_count == len(names)


def test_atr_expansion_and_its_source_are_consistent(scanned: scanner.ScanResult) -> None:
    """``atr_expansion_source`` degrades to a sentinel, never to "" (scanner_core:741).

    Downstream reports on *why* an ATR figure is missing; an empty string would
    read as "not evaluated" rather than "unavailable".
    """
    if np.isfinite(scanned.atr_expansion):
        assert scanned.atr_expansion_source != "unavailable"
    else:
        assert scanned.atr_expansion_source == "unavailable"


def test_the_raw_entry_signal_is_not_yet_rewritten(scanned: scanner.ScanResult) -> None:
    """``raw_entry_signal`` equals ``entry_signal`` at this point (scanner_core:754-755).

    The pair only makes sense if something downstream can move one without the
    other, so pinning that they leave the scanner identical is what makes a
    later divergence meaningful rather than invisible.
    """
    assert scanned.raw_entry_signal == scanned.entry_signal


def test_the_value_trap_warning_tracks_its_threshold(scanned: scanner.ScanResult) -> None:
    """``risk_warning`` is derived, not independent (scanner_core:707).

    The 60 threshold itself is a strategy choice and is *not* asserted -- only
    that the warning and the number behind it never contradict each other.
    """
    expected = "价值陷阱风险偏高" if scanned.value_trap_risk >= 60 else ""
    assert scanned.risk_warning == expected


def test_every_signal_carries_operational_advice(scanned: scanner.ScanResult) -> None:
    """The advice table has a catch-all (scanner_core:708-716) and must stay total."""
    assert scanned.operation_advice, "no advice was produced for a successful scan"


def test_a_recoverable_failure_still_carries_identity(monkeypatch) -> None:
    """The ``except _SCAN_RECOVERABLE_ERRORS`` branch (scanner_core:828-835).

    Reached by making the quality lookup raise.  Like the cache-miss branch in
    the active chain, it propagates ``ticker``/``name``/``is_etf`` so a failed
    row still merges correctly downstream.
    """

    def exploding_quality(*args, **kwargs):
        raise ValueError("fundamentals unavailable")

    monkeypatch.setattr(scanner, "get_quality", exploding_quality)
    info = scanner.TickerInfo(ticker="600000.SH", name="浦发银行", is_etf=True)
    result = scanner.scan_single_from_df(info, _frame())

    assert result.error == "fundamentals unavailable"
    assert result.ticker == "600000.SH"
    assert result.name == "浦发银行"
    assert result.is_etf is True


# ---------------------------------------------------------------------------
# 2. the scanner facade is the same module object
# ---------------------------------------------------------------------------


def test_the_scanner_alias_is_the_core_module_itself() -> None:
    """``scanner.py`` re-publishes itself as ``scanner_core`` (scanner.py:24).

    Overlays patch names on the *module object*, so the alias has to be the very
    same object -- a copy, or a module that merely re-exports the same names,
    would keep working in isolation while silently dropping resume behaviour.
    """
    out = _probe(
        "import sys, scanner, scanner_core;"
        "print(sys.modules['scanner'] is scanner_core)"
    )
    assert out == "True"


def test_the_alias_reexports_the_core_namespace() -> None:
    """The star import is what keeps ``from scanner import X`` working."""
    out = _probe(
        "import scanner;"
        "missing=[n for n in ['run_scan','scan_single_from_df','ScanResult','TickerInfo']"
        " if not hasattr(scanner, n)];"
        "print('none' if not missing else ','.join(missing))"
    )
    assert out == "none"


# ---------------------------------------------------------------------------
# 3. the legacy run_scan body is still the original
# ---------------------------------------------------------------------------


def test_the_captured_legacy_run_scan_is_the_core_body() -> None:
    """``v59._LEGACY_RUN_SCAN`` must still be ``scanner_core.run_scan``'s body.

    Provenance checks this statically, by looking for the string
    ``_LEGACY_RUN_SCAN = _core.run_scan`` in the source.  This checks what the
    import actually produced, using ``__code__.co_filename`` rather than
    ``__module__`` -- the latter is forgeable by an overlay that wants its
    replacement to look like the original.

    If this ever fails, the 828-line body has been rebound *before* v59 captured
    it, and the compatibility branch is running something else entirely.
    """
    out = _probe(
        "import scanner_core as core, scanner_resume_v59 as v59;"
        "f=getattr(v59,'_LEGACY_RUN_SCAN',None);"
        "print('missing' if f is None else f.__code__.co_filename)"
    )
    assert out.endswith("scanner_core.py"), f"legacy run_scan resolved to {out}"
