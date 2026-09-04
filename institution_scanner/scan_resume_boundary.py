"""Canonical scan-resume publication boundary.

The large v59 checkpoint implementation remains a compatibility kernel. This
canonical wrapper pins enrichment to the already-validated market snapshot and
can defer final checkpoint deletion until publication commits.
"""

from __future__ import annotations

import threading
from typing import Any

import pandas as pd

_RUN_PATCH_LOCK = threading.Lock()


def install(core: Any, resume_module: Any) -> None:
    base_run_scan = resume_module.run_scan
    if getattr(base_run_scan, "_canonical_resume_boundary", False):
        core.run_scan = base_run_scan
        return

    def run_scan(
        stock_universe=None,
        etf_universe=None,
        force_download: bool = False,
        resume: bool = True,
        data_source: str = "tickflow",
        cache_first: bool = False,
        progress_callback=None,
        cancel_event: threading.Event | None = None,
    ):
        effective_resume = bool(resume and not force_download)
        downloaded_frames: dict[str, pd.DataFrame] = {}
        clear_calls = 0

        with _RUN_PATCH_LOCK:
            original_download_batch = core.download_batch
            original_enrich_results = core.enrich_results
            original_clear_checkpoint = core.clear_checkpoint

            def capturing_download_batch(*args: Any, **kwargs: Any):
                downloaded = original_download_batch(*args, **kwargs)
                downloaded_frames.clear()
                for ticker, frame in downloaded.items():
                    normalized = core._normalize_ticker(ticker)
                    if normalized and frame is not None:
                        downloaded_frames[normalized] = frame
                return downloaded

            def pinned_enrich_results(
                results: list[Any],
                source: str,
                frames: dict[str, pd.DataFrame] | None = None,
            ) -> None:
                merged: dict[str, pd.DataFrame] = dict(downloaded_frames)
                if frames:
                    merged.update(
                        {
                            core._normalize_ticker(ticker): frame
                            for ticker, frame in frames.items()
                            if frame is not None
                        }
                    )
                return original_enrich_results(results, source, frames=merged)

            def publication_aware_clear_checkpoint() -> None:
                nonlocal clear_calls
                clear_calls += 1
                defer_final = bool(
                    getattr(core, "_defer_checkpoint_clear_until_publish", False)
                )
                initial_clear = not effective_resume and clear_calls == 1
                if initial_clear or not defer_final:
                    original_clear_checkpoint()
                    return
                core.logger.info(
                    "Checkpoint retained until canonical report publication commits."
                )

            core.download_batch = capturing_download_batch
            core.enrich_results = pinned_enrich_results
            core.clear_checkpoint = publication_aware_clear_checkpoint
            try:
                return base_run_scan(
                    stock_universe=stock_universe,
                    etf_universe=etf_universe,
                    force_download=force_download,
                    resume=resume,
                    data_source=data_source,
                    cache_first=cache_first,
                    progress_callback=progress_callback,
                    cancel_event=cancel_event,
                )
            finally:
                core.download_batch = original_download_batch
                core.enrich_results = original_enrich_results
                core.clear_checkpoint = original_clear_checkpoint

    run_scan._canonical_resume_boundary = True  # type: ignore[attr-defined]
    resume_module.run_scan = run_scan
    core.run_scan = run_scan
    core.SCAN_RESUME_RUNTIME_VERSION = (
        "2026-09-04-v113-canonical-pinned-enrichment-publish-clear"
    )
