"""v74 scan orchestration facade.

The implementation lives in :mod:`scanner_core`.  This module keeps the
historical ``scanner`` import path intact and re-publishes the core under its
own name, so every overlay that patches the module object keeps mutating the
single module object the scan implementation resolves its globals from.

Two overlays do so today: ``scanner_resume_v59`` replaces
``clear_checkpoint``, and ``main`` installs ``_guarded_report_enrich`` as
``enrich_results``.  ``scan_service`` writes
``_defer_checkpoint_clear_until_publish`` to postpone the clear until the run
has published.

(``scanner_resume_v68`` used to swap ``download_batch`` / ``enrich_results`` /
``clear_checkpoint`` together, but it is retired from the production path --
see ``RETIRED_FROM_PRODUCTION_PATH`` in
:mod:`institution_scanner.runtime_inventory`.  Naming it here as if it still
ran misdirects anyone tracing where these patches come from.)

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
