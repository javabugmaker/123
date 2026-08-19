"""v68 scan-resume publication boundary.

v59 owns the checkpoint format and scan orchestration.  This narrow wrapper
closes two remaining lifecycle gaps without copying that large implementation:

* frames accepted by the checkpoint market-state test are kept in memory and
  supplied to enrichment, preventing a second disk read from mixing a newer
  cache state into an older resumed ScanResult;
* canonical application scans may defer the final checkpoint deletion until
  report publication succeeds.  Forced/non-resume scans still perform the
  mandatory initial checkpoint clear.

The wrapper temporarily intercepts only downloader/enrichment/checkpoint calls
made by v59.  A process-wide lock avoids concurrent scans observing the
temporary hooks.
"""

from __future__ import annotations

import threading
from typing import Any

import pandas as pd

import scanner as _core
import scanner_resume_v59 as _v59

_BASE_RUN_SCAN = _v59.run_scan
_RUN_PATCH_LOCK = threading.Lock()
_INSTALLED = False


def run_scan(
    stock_universe: list[_core.TickerInfo] | None = None,
    etf_universe: list[_core.TickerInfo] | None = None,
    force_download: bool = False,
    resume: bool = True,
    data_source: str = "tickflow",
    cache_first: bool = False,
    progress_callback: _core.ScanProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> _core.ScanReport:
    """Run v59 while pinning resume enrichment to the verified market snapshot."""
    effective_resume = bool(resume and not force_download)
    downloaded_frames: dict[str, pd.DataFrame] = {}
    clear_calls = 0

    with _RUN_PATCH_LOCK:
        original_download_batch = _core.download_batch
        original_enrich_results = _core.enrich_results
        original_clear_checkpoint = _core.clear_checkpoint

        def capturing_download_batch(*args: Any, **kwargs: Any):
            downloaded = original_download_batch(*args, **kwargs)
            downloaded_frames.clear()
            for ticker, frame in downloaded.items():
                normalized = _core._normalize_ticker(ticker)
                if normalized and frame is not None:
                    downloaded_frames[normalized] = frame
            return downloaded

        def pinned_enrich_results(
            results: list[Any],
            source: str,
            frames: dict[str, pd.DataFrame] | None = None,
        ) -> None:
            # Raw frames captured from the download/settlement phase are the
            # exact market state already validated by v59. Newly analysed
            # indicator-enriched frames take precedence when available.
            merged: dict[str, pd.DataFrame] = dict(downloaded_frames)
            if frames:
                merged.update(
                    {
                        _core._normalize_ticker(ticker): frame
                        for ticker, frame in frames.items()
                        if frame is not None
                    }
                )
            return original_enrich_results(results, source, frames=merged)

        def publication_aware_clear_checkpoint() -> None:
            nonlocal clear_calls
            clear_calls += 1
            defer_final = bool(
                getattr(_core, "_defer_checkpoint_clear_until_publish", False)
            )
            # v59 calls clear once at the start of a forced/non-resume scan and
            # once after successful enrichment. Only the latter is deferrable.
            initial_clear = not effective_resume and clear_calls == 1
            if initial_clear or not defer_final:
                original_clear_checkpoint()
                return
            _core.logger.info(
                "Checkpoint retained until canonical report publication commits."
            )

        _core.download_batch = capturing_download_batch
        _core.enrich_results = pinned_enrich_results
        _core.clear_checkpoint = publication_aware_clear_checkpoint
        try:
            return _BASE_RUN_SCAN(
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
            _core.download_batch = original_download_batch
            _core.enrich_results = original_enrich_results
            _core.clear_checkpoint = original_clear_checkpoint


def install() -> None:
    """Expose v68 through both scanner and the v59 compatibility module."""
    global _INSTALLED
    if _INSTALLED:
        return
    _v59.run_scan = run_scan
    _core.run_scan = run_scan
    _core.SCAN_RESUME_RUNTIME_VERSION = "2026-08-19-v68-pinned-enrichment-publish-clear"
    _INSTALLED = True


install()
