"""v74 scan orchestration facade.

The implementation lives in :mod:`scanner_core`.  This module keeps the
historical ``scanner`` import path intact and re-publishes the core under its
own name, so every overlay that patches the module object
(``scanner_resume_v68`` swaps ``download_batch`` / ``enrich_results`` /
``clear_checkpoint``; ``scan_service`` writes
``_defer_checkpoint_clear_until_publish``) keeps mutating the single module
object the scan implementation resolves its globals from.

Keeping the alias is a behavioural requirement, not a convenience: those
overlays read the patched names through ``getattr(_core, ...)`` at call time,
so splitting the module without re-publishing it would silently drop the
resume and publication-deferral behaviour.
"""

from __future__ import annotations

import sys

import scanner_core as _core
from scanner_core import *  # noqa: F403

sys.modules[__name__] = _core
