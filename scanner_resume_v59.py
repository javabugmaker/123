"""v59 crash-safe scanner resume contract.

The legacy scanner remains the stable analysis implementation.  This module
replaces only the orchestration/checkpoint boundary used by canonical
``scan_service`` execution:

* completed analysis rows are persisted from the *current interrupted run*,
  never reconstructed from the previously published AllResults.parquet;
* checkpoint metadata is bound to the latest completed A-share trading day and
  to every material runtime/output contract version;
* market-cache state is stored per row and revalidated after the normal TickFlow
  refresh, so a provider settlement advance or forward-adjustment rebase forces
  that ticker to be analysed again;
* checkpoint parts are append-only atomic JSON files, avoiding repeated writes
  of a multi-thousand-row monolithic checkpoint;
* legacy callers that monkey-patch ``load_checkpoint`` to return a plain set
  retain the old run_scan path for compatibility, while real on-disk legacy
  ticker-only checkpoints fail closed and trigger a clean recomputation.

No scoring, filter, ranking, ATR or backtest semantics are changed here.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import config as _config
import scanner as _core
from performance_cache import market_cache_state
from trading_calendar import latest_completed_trading_day

_LEGACY_RUN_SCAN = _core.run_scan
_CHECKPOINT_SCHEMA_VERSION = 2
_CHECKPOINT_PART_SIZE = 250
_CHECKPOINT_LOCK = threading.Lock()
_CHECKPOINT_PERSISTED: set[str] = set()
_CHECKPOINT_PARTS: list[str] = []
_CHECKPOINT_SEQUENCE = 0
_INSTALLED = False

_SCAN_RESULT_FIELDS = {item.name for item in fields(_core.ScanResult)}
_SCORE_FIELDS = {item.name for item in fields(_core.ScoreBreakdown)}


class CheckpointState(set[str]):
    """Set-compatible checkpoint state carrying current-run result snapshots."""

    def __init__(
        self,
        values: set[str] | list[str] | tuple[str, ...] = (),
        *,
        snapshots: dict[str, _core.ScanResult] | None = None,
        market_states: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(values)
        self.snapshots = snapshots or {}
        self.market_states = market_states or {}


def _checkpoint_parts_dir() -> Path:
    path = Path(_core._CHECKPOINT_PATH)
    return path.with_name(f"{path.stem}_parts")


def _checkpoint_trade_date(now: datetime | None = None) -> str:
    """Bind resume state to the market session, not the wall-clock date."""
    return latest_completed_trading_day(now).isoformat()


def _contract_payload() -> dict[str, str]:
    return {
        "scoring_version": str(getattr(_config, "SCORING_VERSION", "")),
        "pipeline_version": str(getattr(_config, "PIPELINE_VERSION", "")),
        "market_data_version": str(getattr(_config, "MARKET_DATA_VERSION", "")),
        "output_contract_version": str(
            getattr(_config, "OUTPUT_CONTRACT_VERSION", "")
        ),
        "decision_integrity_version": str(
            getattr(_config, "DECISION_INTEGRITY_VERSION", "")
        ),
    }


def _json_value(value: Any) -> Any:
    if value is pd.NA:
        return None
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    return value


def _serialize_result(result: _core.ScanResult) -> dict[str, Any]:
    return _json_value(asdict(result))


def _restore_result(payload: Any) -> _core.ScanResult:
    if not isinstance(payload, dict):
        raise ValueError("checkpoint result is not an object")
    raw = dict(payload)
    score_payload = raw.pop("score", {})
    if not isinstance(score_payload, dict):
        score_payload = {}
    score_kwargs = {
        key: value for key, value in score_payload.items() if key in _SCORE_FIELDS
    }
    score = _core.ScoreBreakdown(**score_kwargs)
    kwargs = {
        key: value
        for key, value in raw.items()
        if key in _SCAN_RESULT_FIELDS and key != "score"
    }
    kwargs["score"] = score
    result = _core.ScanResult(**kwargs)
    result.ticker = _core._normalize_ticker(result.ticker)
    if not result.ticker or result.error:
        raise ValueError("checkpoint result is not a successful scan row")
    return result


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        suffix=".json",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(
            payload,
            handle,
            ensure_ascii=False,
            allow_nan=True,
            separators=(",", ":"),
        )
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _reset_session() -> None:
    global _CHECKPOINT_PERSISTED, _CHECKPOINT_PARTS, _CHECKPOINT_SEQUENCE
    _CHECKPOINT_PERSISTED = set()
    _CHECKPOINT_PARTS = []
    _CHECKPOINT_SEQUENCE = 0


def _empty_state() -> CheckpointState:
    _reset_session()
    return CheckpointState()


def _load_manifest() -> dict[str, Any]:
    path = Path(_core._CHECKPOINT_PATH)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _manifest_is_current(payload: dict[str, Any], data_source: str) -> bool:
    if not payload.get("active"):
        return False
    if int(payload.get("schema_version", 0) or 0) != _CHECKPOINT_SCHEMA_VERSION:
        return False
    if str(payload.get("trade_date", "")) != _core._checkpoint_trade_date():
        return False
    expected_source = _core.normalize_data_source(data_source) if data_source else ""
    if expected_source and str(payload.get("data_source", "")) != expected_source:
        return False
    contract = payload.get("contract")
    return isinstance(contract, dict) and contract == _contract_payload()


def load_checkpoint(data_source: str = "") -> CheckpointState:
    """Load only a complete v59 snapshot checkpoint; legacy files are ignored."""
    global _CHECKPOINT_PERSISTED, _CHECKPOINT_PARTS, _CHECKPOINT_SEQUENCE
    with _CHECKPOINT_LOCK:
        payload = _load_manifest()
        if not payload or not _manifest_is_current(payload, data_source):
            return _empty_state()

        parts = payload.get("parts")
        processed = {
            _core._normalize_ticker(value)
            for value in payload.get("processed", [])
            if str(value).strip()
        }
        if not isinstance(parts, list) or not parts or not processed:
            return _empty_state()

        snapshots: dict[str, _core.ScanResult] = {}
        market_states: dict[str, dict[str, Any]] = {}
        parts_dir = _checkpoint_parts_dir()
        try:
            for part_name in parts:
                name = str(part_name or "").strip()
                if not name or Path(name).name != name:
                    raise ValueError("invalid checkpoint part name")
                part_path = parts_dir / name
                rows = json.loads(part_path.read_text(encoding="utf-8"))
                if not isinstance(rows, list):
                    raise ValueError("invalid checkpoint part")
                for row in rows:
                    if not isinstance(row, dict):
                        raise ValueError("invalid checkpoint row")
                    ticker = _core._normalize_ticker(str(row.get("ticker", "")))
                    state = row.get("market_state")
                    restored = _restore_result(row.get("result"))
                    if (
                        not ticker
                        or restored.ticker != ticker
                        or not isinstance(state, dict)
                        or not state
                    ):
                        raise ValueError("incomplete checkpoint row")
                    snapshots[ticker] = restored
                    market_states[ticker] = dict(state)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
            _core.logger.warning(
                "Checkpoint snapshot is incomplete/corrupt; recomputing instead of mixing runs."
            )
            return _empty_state()

        if set(snapshots) != processed or set(market_states) != processed:
            _core.logger.warning(
                "Checkpoint manifest/result mismatch; recomputing instead of mixing runs."
            )
            return _empty_state()

        _CHECKPOINT_PERSISTED = set(processed)
        _CHECKPOINT_PARTS = [str(value) for value in parts]
        _CHECKPOINT_SEQUENCE = len(_CHECKPOINT_PARTS)
        return CheckpointState(
            processed,
            snapshots=snapshots,
            market_states=market_states,
        )


def save_checkpoint(
    processed: set[str],
    data_source: str = "",
    *,
    results: list[_core.ScanResult] | None = None,
    market_frames: dict[str, pd.DataFrame] | None = None,
) -> None:
    """Persist only new successful current-run rows as atomic checkpoint parts."""
    global _CHECKPOINT_PERSISTED, _CHECKPOINT_PARTS, _CHECKPOINT_SEQUENCE
    if not _core.ENABLE_CHECKPOINT or not results or not market_frames:
        return

    normalized_processed = {
        _core._normalize_ticker(ticker) for ticker in processed if str(ticker).strip()
    }
    by_ticker = {
        _core._normalize_ticker(result.ticker): result
        for result in results
        if result is not None and not result.error and str(result.ticker).strip()
    }
    available = normalized_processed.intersection(by_ticker).intersection(market_frames)

    with _CHECKPOINT_LOCK:
        new_tickers = sorted(available.difference(_CHECKPOINT_PERSISTED))
        if not new_tickers:
            return

        new_parts: list[str] = []
        staged_tickers: set[str] = set()
        parts_dir = _checkpoint_parts_dir()
        parts_dir.mkdir(parents=True, exist_ok=True)
        try:
            for offset in range(0, len(new_tickers), _CHECKPOINT_PART_SIZE):
                chunk = new_tickers[offset : offset + _CHECKPOINT_PART_SIZE]
                rows: list[dict[str, Any]] = []
                for ticker in chunk:
                    frame = market_frames.get(ticker)
                    if frame is None or frame.empty:
                        continue
                    state = market_cache_state(frame)
                    if not state.get("last") or not state.get("tail_fingerprint"):
                        continue
                    rows.append(
                        {
                            "ticker": ticker,
                            "market_state": _json_value(state),
                            "result": _serialize_result(by_ticker[ticker]),
                        }
                    )
                if not rows:
                    continue
                part_index = _CHECKPOINT_SEQUENCE + len(new_parts) + 1
                part_name = f"part_{part_index:05d}.json"
                _atomic_write_json(parts_dir / part_name, {"rows": rows})
                # Keep the on-disk part format intentionally explicit; unwrap it
                # after the atomic write so the loader has a stable list schema.
                wrapper = json.loads((parts_dir / part_name).read_text(encoding="utf-8"))
                _atomic_write_json(parts_dir / part_name, {"rows": wrapper["rows"]})
                new_parts.append(part_name)
                staged_tickers.update(str(row["ticker"]) for row in rows)

            if not new_parts:
                return

            # Parts are committed first; the manifest is the atomic visibility
            # boundary.  Orphan parts from a crash are harmless and ignored.
            manifest_parts = [*_CHECKPOINT_PARTS, *new_parts]
            manifest_processed = sorted(_CHECKPOINT_PERSISTED | staged_tickers)
            manifest = {
                "active": True,
                "schema_version": _CHECKPOINT_SCHEMA_VERSION,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "trade_date": _core._checkpoint_trade_date(),
                "data_source": (
                    _core.normalize_data_source(data_source) if data_source else ""
                ),
                "contract": _contract_payload(),
                "processed": manifest_processed,
                "parts": manifest_parts,
            }
            _atomic_write_json(Path(_core._CHECKPOINT_PATH), manifest)
            _CHECKPOINT_PERSISTED.update(staged_tickers)
            _CHECKPOINT_PARTS = manifest_parts
            _CHECKPOINT_SEQUENCE = len(manifest_parts)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            _core.logger.warning("Failed to save v59 checkpoint snapshot: %s", exc)


def clear_checkpoint() -> None:
    with _CHECKPOINT_LOCK:
        try:
            Path(_core._CHECKPOINT_PATH).unlink(missing_ok=True)
            shutil.rmtree(_checkpoint_parts_dir(), ignore_errors=True)
        except OSError as exc:
            _core.logger.warning("Failed to remove checkpoint: %s", exc)
        finally:
            _reset_session()


def _market_state_matches(frame: pd.DataFrame | None, expected: dict[str, Any] | None) -> bool:
    if frame is None or frame.empty or not expected:
        return False
    try:
        current = market_cache_state(frame)
    except (TypeError, ValueError):
        return False
    return current == expected


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
    """Run the canonical scan with snapshot-verified interruption recovery."""
    start_time = time.perf_counter()
    _core._raise_if_cancelled(cancel_event)
    _core._emit_progress(progress_callback, "prepare", 0, 0, "准备扫描")
    data_source = _core.normalize_data_source(data_source)
    if force_download:
        resume = False

    if stock_universe is None and etf_universe is None:
        stock_universe, etf_universe = _core.build_ticker_universe(
            include_stocks=True,
            include_etfs=True,
        )

    all_tickers: list[_core.TickerInfo] = []
    if stock_universe:
        all_tickers.extend(stock_universe)
    if etf_universe:
        all_tickers.extend(etf_universe)

    seen: set[str] = set()
    unique: list[_core.TickerInfo] = []
    for item in all_tickers:
        item.ticker = _core._normalize_ticker(item.ticker)
        if item.ticker not in seen:
            seen.add(item.ticker)
            unique.append(item)
    all_tickers = unique
    universe_symbols = {_core._normalize_ticker(item.ticker) for item in all_tickers}

    if resume:
        checkpoint = _core.load_checkpoint(data_source)
        # Compatibility boundary for older tests/integrations that inject a
        # plain set.  Real v59 disk loads always return CheckpointState, and
        # legacy ticker-only disk files return an empty CheckpointState.
        if checkpoint and not isinstance(checkpoint, CheckpointState):
            return _LEGACY_RUN_SCAN(
                stock_universe=stock_universe,
                etf_universe=etf_universe,
                force_download=force_download,
                resume=resume,
                data_source=data_source,
                cache_first=cache_first,
                progress_callback=progress_callback,
                cancel_event=cancel_event,
            )
    else:
        _core.clear_checkpoint()
        checkpoint = CheckpointState()

    checkpoint.intersection_update(universe_symbols)

    _core.logger.info(
        "Phase 1/2: preparing TickFlow data for %d tickers (batch workers=%d)...",
        len(all_tickers),
        _core.TICKFLOW_MAX_WORKERS,
    )
    _core._emit_progress(
        progress_callback,
        "download",
        0,
        len(all_tickers),
        f"准备 TickFlow 行情：{len(all_tickers)} 个标的",
    )

    def on_download_progress(
        completed: int, total: int, available: int, unavailable: int
    ) -> None:
        _core._emit_progress(
            progress_callback,
            "download",
            completed,
            total,
            f"TickFlow 行情 {completed}/{total} · 可用 {available} · 无数据/失败 {unavailable}",
        )

    # Never skip the market refresh on resume.  We must verify that the raw
    # frame is still byte-semantically the same market state as the snapshot.
    download_started = time.perf_counter()
    downloaded = _core.download_batch(
        all_tickers,
        desc="Downloading",
        force=force_download,
        source=data_source,
        cache_first=cache_first and not force_download,
        skip_tickers=set() if resume else None,
        progress_callback=on_download_progress,
    )
    download_elapsed = time.perf_counter() - download_started
    _core.logger.info("Download phase complete in %.1f seconds.", download_elapsed)
    _core._raise_if_cancelled(cancel_event)
    _core._emit_progress(
        progress_callback,
        "download",
        len(all_tickers),
        len(all_tickers),
        f"行情准备完成，用时 {download_elapsed:.1f}s",
    )

    downloaded_frames = {
        _core._normalize_ticker(ticker): frame for ticker, frame in downloaded.items()
    }
    downloaded_symbols = set(downloaded_frames)

    resumed: dict[str, _core.ScanResult] = {}
    if isinstance(checkpoint, CheckpointState):
        for ticker in checkpoint:
            snapshot = checkpoint.snapshots.get(ticker)
            expected = checkpoint.market_states.get(ticker)
            if snapshot is None:
                continue
            if _market_state_matches(downloaded_frames.get(ticker), expected):
                resumed[ticker] = snapshot

    invalidated = len(checkpoint) - len(resumed)
    if resumed:
        _core.logger.info(
            "Resuming interrupted scan from verified current-run snapshots: %d tickers.",
            len(resumed),
        )
    if invalidated > 0:
        _core.logger.warning(
            "Checkpoint market state changed for %d tickers; they will be analysed again.",
            invalidated,
        )

    processed_set = set(resumed)
    analyse_queue: list[_core.TickerInfo] = []
    skipped_no_cache = 0
    for item in all_tickers:
        ticker = _core._normalize_ticker(item.ticker)
        if ticker in processed_set:
            continue
        frame = downloaded_frames.get(ticker)
        if ticker in downloaded_symbols and frame is not None and not frame.empty:
            analyse_queue.append(item)
        else:
            skipped_no_cache += 1

    _core.logger.info(
        "Phase 2/2: analysing %d tickers (%d threads) — %d verified checkpoint rows, %d without valid cache. Universe=%d, downloaded=%d.",
        len(analyse_queue),
        _core.SCAN_THREADS,
        len(processed_set),
        skipped_no_cache,
        len(all_tickers),
        len(downloaded_symbols),
    )

    results: list[_core.ScanResult] = [resumed[ticker] for ticker in sorted(resumed)]
    analysed_frames: dict[str, pd.DataFrame] = {}
    analysed_this_run: set[str] = set()
    successful = len(results)
    failed = 0
    passed = sum(1 for result in results if result.passed_filters)

    _core._emit_progress(
        progress_callback,
        "analyse",
        0,
        len(analyse_queue),
        f"开始指标分析：{len(analyse_queue)} 个标的",
    )
    analysis_started = time.perf_counter()
    with _core.ThreadPoolExecutor(max_workers=_core.SCAN_THREADS) as executor:
        max_pending = max(_core.SCAN_THREADS * 4, _core.SCAN_THREADS)
        ticker_iter = iter(analyse_queue)
        futures: dict[Any, _core.TickerInfo] = {}

        def submit_next() -> bool:
            if cancel_event is not None and cancel_event.is_set():
                return False
            try:
                item = next(ticker_iter)
            except StopIteration:
                return False
            ticker = _core._normalize_ticker(item.ticker)
            futures[
                executor.submit(
                    _core._analyse_one_ticker_from_df,
                    item,
                    downloaded_frames[ticker],
                    data_source,
                )
            ] = item
            return True

        for _ in range(min(max_pending, len(analyse_queue))):
            submit_next()

        completed = 0
        with _core.tqdm(
            total=len(analyse_queue),
            desc="Analysing",
            unit="ticker",
            disable=not _core.sys.stderr.isatty(),
        ) as progress:
            while futures:
                _core._raise_if_cancelled(cancel_event)
                future = next(_core.as_completed(futures))
                item = futures.pop(future)
                completed += 1
                try:
                    result, frame = future.result()
                except Exception as exc:  # preserve legacy per-ticker isolation
                    _core.logger.exception("Analysis error for %s", item.ticker)
                    result, frame = (
                        _core.ScanResult(ticker=item.ticker, error=str(exc)),
                        None,
                    )

                results.append(result)
                if frame is not None:
                    analysed_frames[result.ticker] = frame

                if result.error:
                    failed += 1
                    _core.logger.warning(
                        "Analysis failed for %s: %s", item.ticker, result.error
                    )
                else:
                    successful += 1
                    processed_set.add(_core._normalize_ticker(item.ticker))
                    if result.passed_filters:
                        passed += 1

                analysed_this_run.add(_core._normalize_ticker(item.ticker))
                progress.update(1)

                if completed % 100 == 0 or completed == len(analyse_queue):
                    _core.logger.info(
                        "ANALYSE progress: %d/%d (%d successful, %d failed).",
                        completed,
                        len(analyse_queue),
                        successful,
                        failed,
                    )
                if completed % 25 == 0 or completed == len(analyse_queue):
                    _core._emit_progress(
                        progress_callback,
                        "analyse",
                        completed,
                        len(analyse_queue),
                        f"指标分析 {completed}/{len(analyse_queue)} · 成功 {successful} · 失败 {failed}",
                    )

                if (
                    _core.ENABLE_CHECKPOINT
                    and completed % _core.CHECKPOINT_INTERVAL == 0
                ):
                    _core.save_checkpoint(
                        processed_set,
                        data_source,
                        results=results,
                        market_frames=downloaded_frames,
                    )
                submit_next()

    analysis_elapsed = time.perf_counter() - analysis_started
    _core.logger.info("Analysis phase complete in %.1f seconds.", analysis_elapsed)

    # Persist the tail below CHECKPOINT_INTERVAL as well, so a crash during
    # enrichment never forces more recomputation than necessary.
    if _core.ENABLE_CHECKPOINT and processed_set:
        _core.save_checkpoint(
            processed_set,
            data_source,
            results=results,
            market_frames=downloaded_frames,
        )

    _core._raise_if_cancelled(cancel_event)
    _core.logger.info("Enriching %d scan results...", len(results))
    _core._emit_progress(
        progress_callback, "enrich", 0, len(results), "正在增强评分与排序"
    )
    enrichment_started = time.perf_counter()
    enrichment_succeeded = True
    try:
        _core.enrich_results(results, data_source, frames=analysed_frames)
    except _core._SCAN_RECOVERABLE_ERRORS:
        enrichment_succeeded = False
        _core.logger.exception(
            "Failed to enrich scan results; checkpoint retained for safe retry"
        )
    enrichment_elapsed = time.perf_counter() - enrichment_started
    _core.logger.info(
        "Enrichment complete: %d scan results in %.1f seconds.",
        len(results),
        enrichment_elapsed,
    )
    _core._raise_if_cancelled(cancel_event)
    _core._emit_progress(
        progress_callback,
        "enrich",
        len(results),
        len(results),
        f"评分增强完成，用时 {enrichment_elapsed:.1f}s",
    )

    if enrichment_succeeded:
        _core.clear_checkpoint()

    results.sort(
        key=lambda result: (
            _core._parse_float(result.ranking_score, np.nan)
            if np.isfinite(_core._parse_float(result.ranking_score, np.nan))
            else _core._parse_float(result.institutional_score, np.nan)
            if np.isfinite(_core._parse_float(result.institutional_score, np.nan))
            else _core._parse_float(result.final_score, result.score.total)
        ),
        reverse=True,
    )

    elapsed = time.perf_counter() - start_time
    report = _core.ScanReport(
        results=results,
        total_tickers=len(all_tickers),
        successful=successful,
        failed=failed,
        passed_filters=passed,
        elapsed_seconds=elapsed,
        download_seconds=download_elapsed,
        analysis_seconds=analysis_elapsed,
        enrichment_seconds=enrichment_elapsed,
    )
    _core.logger.info(
        "Scan complete: %d successful, %d failed, %d passed filters, %.1f seconds.",
        successful,
        failed,
        passed,
        elapsed,
    )
    _core._emit_progress(
        progress_callback,
        "complete",
        len(all_tickers),
        len(all_tickers),
        f"扫描完成：成功 {successful} · 失败 {failed} · 用时 {elapsed:.1f}s",
    )
    return report


def install() -> None:
    """Install the v59 contract into the stable scanner module exactly once."""
    global _INSTALLED
    if _INSTALLED:
        return
    _core.CheckpointState = CheckpointState
    _core._checkpoint_trade_date = _checkpoint_trade_date
    _core.load_checkpoint = load_checkpoint
    _core.save_checkpoint = save_checkpoint
    _core.clear_checkpoint = clear_checkpoint
    _core.run_scan = run_scan
    _INSTALLED = True


install()
