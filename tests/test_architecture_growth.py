from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_VERSION_RE = re.compile(r"(?:^|_)v(\d+)(?:_|\.py$)", re.IGNORECASE)
_ROOT_VERSION_CEILING = 102
_SIZE_BUDGETS = {
    "analytics_core.py": 160_000,
    "report_core.py": 105_000,
    "gui_core.py": 105_000,
    "gui.py": 100_000,
    "scanner.py": 80_000,
    "signal_lifecycle_core.py": 70_000,
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
