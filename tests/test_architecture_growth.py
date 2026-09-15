"""Shrink-only byte budgets for the legacy giants at the repository root.

Two gates, and they answer different questions:

* ``test_every_large_root_module_has_a_budget`` -- "is anything growing where
  nobody is watching?"
* ``test_size_budgets_are_not_vacuous`` -- "of the budgets that do exist, can
  any of them actually fire?"

The first gate exists because this file used to be a hand-maintained list, and
the list was picked by *size* rather than by *growth*.  Measured over the last
30 commits, four of the five largest unbudgeted modules had not changed by a
single byte -- they are static debt -- while ``fundamental_quality.py``, which
no budget covered at all, grew by 6,481 bytes.  Picking targets by size would
have gated four files that are not growing and missed the one that is.

The second gate exists because of ``scanner.py``: T1 gutted it from 72,303
bytes to 941, but left its 80,000 budget behind.  That is 79 KB of phantom
headroom -- the file would have to grow eighty-fold before the gate could fire.
Same defect class as ``analytics_core``'s unrecovered 17,417 bytes, just worse.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_VERSION_RE = re.compile(r"(?:^|_)v(\d+)(?:_|\.py$)", re.IGNORECASE)
_ROOT_VERSION_CEILING = 102

#: Same value as ``LARGE_MODULE_THRESHOLD`` in
#: ``test_canonical_package_discipline``.  Keeping them equal means "large"
#: means the same thing at the root and inside the package, and 20 KB is low
#: enough to catch ``fundamental_quality.py`` -- at 22,835 bytes it is nowhere
#: near the giants, but it is the fastest-growing root module there is.
ROOT_LARGE_MODULE_THRESHOLD = 20_000

#: How far a budget may exceed the file it guards.  A budget that sits far above
#: the file is a gate that cannot fire; see the module docstring.
MAX_BUDGET_SLACK = 4_096

_SIZE_BUDGETS = {
    # 160_000 -> 145_000.  T2' extracted ``backtest_statistics`` out of this
    # module and freed 17,417 bytes, but the budget was never brought down, so
    # the freed space stayed available for re-inflation -- the extraction moved
    # the debt without retiring it.  Same rule as report_core and
    # signal_lifecycle_core below; this is the last of the three.
    "analytics_core.py": 145_000,
    # 105_000 -> 95_000 after the T3' extraction moved the 11-function
    # candidate-selection cluster into institution_scanner/report_selection.py
    # (report_core dropped 104,909 -> 91,926 bytes).  The budget has to come
    # down with it, otherwise the 13 KB that were freed stay available for
    # re-inflation and the extraction only moved the debt instead of retiring
    # it.  ~3 KB of headroom is left so ordinary fixes do not trip the gate.
    # 95_000 -> 91_752.  See the note on _normalized_size: 95_000 was frozen
    # from a CRLF checkout, so it measured 5,296 bytes of slack -- more than
    # MAX_BUDGET_SLACK allows.  Re-frozen at normalised size + 2 KiB.
    "report_core.py": 91_752,
    "gui_core.py": 105_000,
    # 100_000 -> 94_018.  Same correction as report_core: 98_000 still left
    # 6,030 bytes of slack once line endings stopped inflating the file.
    "gui.py": 94_018,
    # 80_000 -> 2_048.  T1 turned this into a 941-byte facade
    # (``from scanner_core import *`` + ``sys.modules[__name__] = _core``) but
    # left the budget at its pre-extraction value: 79 KB of phantom headroom,
    # enough for the file to grow eighty-fold before anything noticed.  This is
    # the budget-scanner.py coupling at its worst, and the reason
    # ``test_size_budgets_are_not_vacuous`` exists.
    "scanner.py": 2_048,
    # Extracted from scanner.py (T1).  The budget moved with the code: leaving
    # it untracked would turn 78 KB of debt invisible the moment the facade
    # shrank past its own gate.
    "scanner_core.py": 78_111,
    # 70_000 -> 56_000 after the T4 extraction moved the 17-function
    # per-signal attribute cluster into
    # institution_scanner/signal_attributes.py (signal_lifecycle_core dropped
    # ~67.8 KB -> 52,940 bytes on disk).  Same rule as report_core above: the
    # budget has to come down with the code, or the 15 KB that were freed stay
    # available for re-inflation and the extraction only relocated the debt.
    # ~3 KB of headroom is left so ordinary fixes do not trip the gate.
    # 56_000 -> 53_345: re-frozen at normalised size + 2 KiB, same correction
    # as report_core and gui.py.
    "signal_lifecycle_core.py": 53_345,
    # --- Brought under budget by test_every_large_root_module_has_a_budget ---
    # Each is frozen at its normalised (line-ending independent) size plus 1 KiB,
    # enough for an ordinary fix and small enough that trend growth is caught.
    # Versioned
    # overlays are included on purpose: "frozen patch" does not mean "not
    # edited" -- web_report_v84.py grew 1,588 bytes across 3 recent commits.
    "backtest_fastscore_v80.py": 34_098 + 1_024,
    "backtest_vectorization_v98.py": 42_542 + 1_024,
    "daily_pipeline_core.py": 42_270 + 1_024,
    "downloader_core.py": 30_543 + 1_024,
    # The fastest-growing root module of the last 30 commits (+6,481 bytes
    # across 12 commits) and, before this, covered by no budget at all.  It is
    # the reason the coverage gate is automatic rather than hand-maintained.
    "fundamental_quality.py": 22_835 + 1_024,
    "filters_core.py": 21_967 + 1_024,
    "indicators.py": 26_290 + 1_024,
    "main_core.py": 22_423 + 1_024,
    "model_audit.py": 26_973 + 1_024,
    "model_calibration.py": 32_222 + 1_024,
    "scanner_resume_v59.py": 27_854 + 1_024,
    "score_core.py": 44_289 + 1_024,
    "signal_lifecycle.py": 25_933 + 1_024,
    "signal_lifecycle_v51.py": 23_145 + 1_024,
}
# web_report_v84/v85/v93 budgets were removed together with the modules:
# WEB_REPORT.md documents that the production path stopped chaining
# v84/v85/v90/v93/v102/v102_1, and a runtime probe confirmed that importing
# main + daily_pipeline + publish_web_report loads only web_report_v81.  The
# six modules were 166 KB of unreachable code guarded by these three budgets.


def test_new_versioned_overlays_must_not_be_added_at_repo_root() -> None:
    offenders: list[str] = []
    for path in ROOT.glob("*.py"):
        match = _VERSION_RE.search(path.name)
        if match and int(match.group(1)) > _ROOT_VERSION_CEILING:
            offenders.append(path.name)
    assert not offenders, (
        "New version overlays belong under institution_scanner/, not repo root: "
        + ", ".join(sorted(offenders))
    )


def _normalized_size(path: Path) -> int:
    """Byte size with CRLF and LF counted identically.

    This is not cosmetic.  ``core.autocrlf`` is on for this repository, so a
    file stored with LF in the index is checked out as CRLF with one extra byte
    per line: ``analytics_core.py`` is 142,583 bytes in the index and 146,065 on
    disk after a Windows checkout.  A budget frozen from one form is wrong in
    the other -- 145,000 was green in the working copy and red in a fresh clone,
    and the two CI runners (Linux LF, Windows CRLF) disagreed with each other.

    Measuring the normalised bytes makes the number mean the same thing
    everywhere, which is the only way a byte budget can be portable.
    """
    return len(path.read_bytes().replace(b"\r\n", b"\n"))


def test_legacy_giant_modules_are_shrink_only() -> None:
    oversized = {
        name: _normalized_size(ROOT / name)
        for name, budget in _SIZE_BUDGETS.items()
        if _normalized_size(ROOT / name) > budget
    }
    assert not oversized, (
        "Legacy giant modules exceeded their shrink-only budgets; extract new "
        f"logic into institution_scanner/: {oversized}"
    )


def test_every_large_root_module_has_a_budget() -> None:
    """No root module above the threshold may grow unwatched.

    The budgets here used to be a hand-maintained list of seven names.  That is
    the wrong shape for the job: what gets noticed is whatever someone happened
    to write down, not whatever is actually moving.  Measured over the last 30
    commits, four of the five largest unbudgeted modules had not changed at all,
    while ``fundamental_quality.py`` -- unbudgeted, and not in anyone's list of
    giants -- grew by 6,481 bytes.

    So the requirement is stated once, mechanically: above
    ``ROOT_LARGE_MODULE_THRESHOLD``, you declare a budget.  A new large file
    trips this the moment it lands instead of waiting to be noticed.
    """
    missing = sorted(
        path.name
        for path in ROOT.glob("*.py")
        if _normalized_size(path) > ROOT_LARGE_MODULE_THRESHOLD
        and path.name not in _SIZE_BUDGETS
    )
    assert not missing, (
        f"Root modules over {ROOT_LARGE_MODULE_THRESHOLD} bytes with no "
        "shrink-only budget. Either extract logic out of them, or add a budget "
        "at (current normalised size + 1 KiB) with a comment saying why it is "
        "allowed to be this big: " + ", ".join(missing)
    )


def test_size_budgets_are_not_vacuous() -> None:
    """A budget must be close enough to the file that it can actually fire.

    ``scanner.py`` carried an 80,000-byte budget while the file was 941 bytes --
    79 KB of phantom headroom left behind when T1 turned it into a facade.  The
    gate looked present and was not: the file could have grown eighty-fold
    before anything failed.  ``analytics_core``'s unrecovered 17,417 bytes is
    the same defect one size down.

    Slack is allowed, because a budget with zero headroom turns every bugfix
    into a red build.  Slack large enough to hide a trend is not.
    """
    vacuous = {
        name: f"{budget} vs {size}"
        for name, budget in _SIZE_BUDGETS.items()
        if (size := _normalized_size(ROOT / name)) < budget - MAX_BUDGET_SLACK
    }
    assert not vacuous, (
        f"These budgets sit more than {MAX_BUDGET_SLACK} bytes above the file, "
        "so they cannot fire until the module has grown enormously; tighten "
        f"them or the gate is decorative: {vacuous}"
    )
