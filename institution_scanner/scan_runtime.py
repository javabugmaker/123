"""Canonical bootstrap for scan recovery and observability compatibility kernels.

The large v59 checkpoint implementation remains a compatibility kernel until it
can be extracted under golden equivalence. Small wrapper layers now live in the
canonical package so production composition has fewer root monkey-patch modules.
"""

from __future__ import annotations

from typing import Final

import config as _config
import publication_guard_v65 as _publication_guard
import scanner as _scanner_core
import scanner_resume_v59 as _resume_v59
import universe_snapshot_v82 as _universe_snapshot
import web_report_v81 as _web_report
from institution_scanner import checkpoint_inputs as _checkpoint_inputs
from institution_scanner import scan_resume_boundary as _resume_boundary

# Stable canonical observability API. Production facades consume these names
# without importing versioned root modules directly.
enforce_cache_first_market_contract = (
    _publication_guard.enforce_cache_first_market_contract
)
record_universe_snapshot_file = _universe_snapshot.record_universe_snapshot_file
maybe_publish_canonical_report = _web_report.maybe_publish_canonical_report

SCAN_RUNTIME_FACADE_VERSION: Final = (
    "2026-09-04-v113-canonical-scan-runtime-observability-v4"
)

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _resume_v59.install()
    _checkpoint_inputs.install(_resume_v59, _config)
    _resume_boundary.install(_scanner_core, _resume_v59)
    _INSTALLED = True
