from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_VERSION_RE = re.compile(r"(?:^|_)v(\d+)(?:_|\.py$)", re.IGNORECASE)
_ROOT_VERSION_CEILING = 102
_SIZE_BUDGETS = {
    "analytics_core.py": 160_000,
    # 105_000 -> 95_000 after the T3' extraction moved the 11-function
    # candidate-selection cluster into institution_scanner/report_selection.py
    # (report_core dropped 104,909 -> 91,926 bytes).  The budget has to come
    # down with it, otherwise the 13 KB that were freed stay available for
    # re-inflation and the extraction only moved the debt instead of retiring
    # it.  ~3 KB of headroom is left so ordinary fixes do not trip the gate.
    "report_core.py": 95_000,
    "gui_core.py": 105_000,
    "gui.py": 100_000,
    "scanner.py": 80_000,
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
    "signal_lifecycle_core.py": 56_000,
}


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


def test_legacy_giant_modules_are_shrink_only() -> None:
    oversized = {
        name: (ROOT / name).stat().st_size
        for name, budget in _SIZE_BUDGETS.items()
        if (ROOT / name).stat().st_size > budget
    }
    assert not oversized, (
        "Legacy giant modules exceeded their shrink-only budgets; extract new "
        f"logic into institution_scanner/: {oversized}"
    )
