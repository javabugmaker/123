"""Canonical bootstrap for scan recovery and observability compatibility kernels.

Production scan entry points import this module rather than importing versioned
root overlays directly. The underlying kernels remain until golden-equivalence
coverage permits one-at-a-time removal; this facade makes that debt explicit and
keeps install order deterministic.
"""
from __future__ import annotations

from typing import Final

import checkpoint_inputs_v59 as _checkpoint_inputs
import publication_guard_v65 as _publication_guard
import scanner_resume_v59 as _resume_v59
import scanner_resume_v68 as _resume_v68
import universe_snapshot_v82 as _universe_snapshot
import web_report_v81 as _web_report

# Stable canonical observability API. Production facades consume these names
# without importing versioned root modules directly.
enforce_cache_first_market_contract = (
    _publication_guard.enforce_cache_first_market_contract
)
record_universe_snapshot_file = _universe_snapshot.record_universe_snapshot_file
maybe_publish_canonical_report = _web_report.maybe_publish_canonical_report

SCAN_RUNTIME_FACADE_VERSION: Final = (
    "2026-09-01-v110-canonical-scan-runtime-observability-v3"
)

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _checkpoint_inputs.install()
    _resume_v59.install()
    _resume_v68.install()
    _INSTALLED = True
