from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_VERSIONED_IMPORT = re.compile(
    r"^(?:from|import)\s+[A-Za-z0-9_.]*_v\d+(?:\s|$)", re.MULTILINE
)


def test_production_facades_do_not_import_versioned_overlays_directly() -> None:
    offenders: dict[str, list[str]] = {}
    for name in ("analytics.py", "scan_service.py"):
        text = (ROOT / name).read_text(encoding="utf-8")
        matches = _VERSIONED_IMPORT.findall(text)
        if matches:
            offenders[name] = matches
    assert not offenders, (
        "Production facades must route versioned compatibility kernels through "
        f"institution_scanner runtime modules: {offenders}"
    )


def test_runtime_inventory_is_explicitly_nonexpanding() -> None:
    from institution_scanner.runtime_inventory import runtime_inventory

    inventory = runtime_inventory()
    assert inventory["new_root_overlays_allowed"] is False
    assert inventory["migration_policy"] == "GOLDEN_EQUIVALENCE_BEFORE_REMOVAL"
    assert int(inventory["legacy_overlay_count"]) == 13
    retired = set(inventory["retired_from_production_path"])
    assert {
        "analytics_compat_v97",
        "backtest_profile_alignment_v95",
        "score_runtime_v97",
        "checkpoint_inputs_v59",
        "scanner_resume_v68",
    }.issubset(retired)
