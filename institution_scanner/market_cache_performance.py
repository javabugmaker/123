"""Safe market-cache persistence acceleration.

TickFlow batch responses and merged cache frames have already passed the canonical
OHLCV validator before persistence. Re-validating every multi-year DataFrame while
writing it to Parquet is redundant. During a full batch, independent ticker files
can also be persisted concurrently because each ticker owns a distinct cache path.

This layer changes persistence scheduling only. Market frames, dates, adjustment
semantics and scanner scores are untouched. Atomic per-file replacement and the
existing manifest contract are preserved.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import pandas as pd

MARKET_CACHE_PERFORMANCE_VERSION: Final = (
    "2026-08-25-v107-validated-parallel-market-cache-writes-v1"
)
VALIDATED_FRAME_ATTR: Final = "_institution_scanner_ohlcv_validated_v107"
_DEFAULT_WRITE_WORKERS: Final = 4


def _write_workers() -> int:
    raw = str(os.environ.get("INSTITUTION_SCANNER_CACHE_WRITE_THREADS", "") or "").strip()
    try:
        requested = int(raw) if raw else _DEFAULT_WRITE_WORKERS
    except ValueError:
        requested = _DEFAULT_WRITE_WORKERS
    logical = max(1, int(os.cpu_count() or _DEFAULT_WRITE_WORKERS))
    return min(max(1, requested), logical, 8)


def _is_validated(frame: pd.DataFrame | None) -> bool:
    return bool(
        frame is not None
        and not frame.empty
        and frame.attrs.get(VALIDATED_FRAME_ATTR, False)
    )


def _mark_validated(frame: pd.DataFrame | None) -> pd.DataFrame | None:
    if frame is not None and not frame.empty:
        frame.attrs[VALIDATED_FRAME_ATTR] = True
    return frame


def _write_validated_frame(
    core: Any,
    ticker: str,
    frame: pd.DataFrame,
    source: str | None,
) -> None:
    """Atomically persist a frame that already passed canonical OHLCV validation."""
    path = Path(core._cache_path(ticker, source))
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        suffix=".parquet",
        dir=path.parent,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        frame.to_parquet(temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@dataclass
class _BatchState:
    executor: ThreadPoolExecutor
    workers: int
    pending: list[tuple[Future[None], str]] = field(default_factory=list)
    manifests: dict[str, tuple[str, pd.DataFrame]] = field(default_factory=dict)
    flush_requested: bool = False


def install(core: Any) -> None:
    """Install validated-write elision and batch-parallel persistence once."""
    if getattr(core, "_MARKET_CACHE_PERFORMANCE_V107_INSTALLED", False):
        return

    original_validate = core._validate_ohlcv
    original_save = core._save_cache
    original_record = core._record_market_manifest
    original_flush = core._flush_market_manifest
    original_batch = core.download_batch
    local = threading.local()

    def validated_ohlcv(frame: pd.DataFrame | None) -> pd.DataFrame | None:
        return _mark_validated(original_validate(frame))

    def current_state() -> _BatchState | None:
        value = getattr(local, "batch_state", None)
        return value if isinstance(value, _BatchState) else None

    def save_cache(
        ticker: str,
        frame: pd.DataFrame,
        source: str | None = None,
    ) -> None:
        ready = frame if _is_validated(frame) else validated_ohlcv(frame)
        if ready is None or ready.empty:
            return
        state = current_state()
        if state is None:
            if _is_validated(ready):
                _write_validated_frame(core, ticker, ready, source)
            else:
                original_save(ticker, ready, source)
            return
        future = state.executor.submit(
            _write_validated_frame,
            core,
            ticker,
            ready,
            source,
        )
        state.pending.append((future, str(ticker)))

    def record_manifest(ticker: str, frame: pd.DataFrame) -> None:
        state = current_state()
        if state is None:
            original_record(ticker, frame)
            return
        key = str(core.normalize_ticker(ticker))
        state.manifests[key] = (str(ticker), frame)

    def flush_manifest() -> None:
        state = current_state()
        if state is None:
            original_flush()
            return
        state.flush_requested = True

    def finish_batch(state: _BatchState, *, publish_manifest: bool) -> None:
        started = time.perf_counter()
        failures: list[tuple[str, BaseException]] = []
        for future, ticker in state.pending:
            try:
                future.result()
            except BaseException as exc:
                failures.append((ticker, exc))
        state.executor.shutdown(wait=True)
        local.batch_state = None

        if failures:
            ticker, exc = failures[0]
            raise RuntimeError(
                f"MARKET_CACHE_WRITE_FAILED: {ticker}: {exc}"
            ) from exc

        if publish_manifest:
            for ticker, frame in state.manifests.values():
                original_record(ticker, frame)
            if state.flush_requested or state.manifests:
                original_flush()

        if state.pending:
            core.logger.info(
                "Market cache persistence flushed: %d frames, workers=%d, wait=%.2fs.",
                len(state.pending),
                state.workers,
                time.perf_counter() - started,
            )

    def download_batch(*args: Any, **kwargs: Any) -> dict[str, pd.DataFrame]:
        if current_state() is not None:
            return original_batch(*args, **kwargs)
        workers = _write_workers()
        state = _BatchState(
            executor=ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="market-cache",
            ),
            workers=workers,
        )
        local.batch_state = state
        try:
            result = original_batch(*args, **kwargs)
        except BaseException:
            try:
                finish_batch(state, publish_manifest=False)
            except BaseException:
                core.logger.exception(
                    "Market cache writer failed while unwinding a download error."
                )
            raise
        finish_batch(state, publish_manifest=True)
        return result

    core._validate_ohlcv = validated_ohlcv
    core._save_cache = save_cache
    core._record_market_manifest = record_manifest
    core._flush_market_manifest = flush_manifest
    core.download_batch = download_batch
    core.MARKET_CACHE_PERFORMANCE_VERSION = MARKET_CACHE_PERFORMANCE_VERSION
    core.CACHE_WRITE_THREADS = _write_workers()
    core._MARKET_CACHE_PERFORMANCE_V107_INSTALLED = True
