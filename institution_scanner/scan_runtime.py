"""Canonical bootstrap for the legacy scan recovery compatibility kernels.

Production scan entry points import this module rather than importing versioned
root overlays directly. The underlying kernels remain until golden-equivalence
coverage permits one-at-a-time removal; this facade makes that debt explicit and
keeps install order deterministic.
"""
from __future__ import annotations

from typing import Final

import checkpoint_inputs_v59 as _checkpoint_inputs
import fundamental_refresh_v61 as _fundamental_refresh
import scanner_resume_v59 as _resume_v59
import scanner_resume_v68 as _resume_v68

SCAN_RUNTIME_FACADE_VERSION: Final = (
    "2026-08-25-v109.4-canonical-scan-runtime-facade-v1"
)

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _checkpoint_inputs.install()
    _fundamental_refresh.install()
    _resume_v59.install()
    _resume_v68.install()
    _INSTALLED = True
