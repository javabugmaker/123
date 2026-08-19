"""v67 fundamental refresh freshness guard.

The stable AkShare implementation preserves old factor rows when a provider
request fails or is incomplete. That is useful for scan continuity, but the
legacy refresh path rewrites ``fundamental_data_meta.json`` after combining old
and new rows. Without a refresh-coverage guard, a tiny partial provider response
can therefore make a mostly old cache look fresh for another 14 days.

This facade counts real ticker rows returned by the current batch request. The
combined data file is still kept, but its freshness metadata advances only when
at least the same 80% coverage used by the fundamental completeness contract is
obtained. Zero-row and materially partial outages remain stale and retry later.
"""

from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path
from typing import Any

import fundamental_data as _data

_LEGACY_REFRESH = _data.refresh_fundamental_data
_LEGACY_FETCH_ROW = _data._fetch_fundamental_row
_REFRESH_GUARD_LOCK = threading.Lock()
_INSTALLED = False


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def _restore_bytes(path: Path, payload: bytes | None) -> None:
    if payload is None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _requested_stock_count(args: tuple[Any, ...], kwargs: dict[str, Any]) -> int:
    values = kwargs.get("tickers")
    if values is None and args:
        values = args[0]
    if values is None:
        return 0
    try:
        normalized = {
            _data.normalize_ticker(value)
            for value in values
            if str(value).strip()
        }
    except TypeError:
        return 0
    return sum(
        1
        for ticker in normalized
        if ticker
        and not ticker.split(".", 1)[0].startswith(
            ("15", "16", "50", "51", "56", "58")
        )
    )


def refresh_fundamental_data(*args: Any, **kwargs: Any) -> Path:
    """Keep metadata stale unless the current provider refresh is broadly usable."""
    with _REFRESH_GUARD_LOCK:
        metadata_path = Path(_data._META_PATH)
        metadata_before = _read_bytes(metadata_path)
        expected_rows = _requested_stock_count(args, kwargs)
        observed_rows = 0

        def counting_fetch(*fetch_args: Any, **fetch_kwargs: Any):
            nonlocal observed_rows
            row = _LEGACY_FETCH_ROW(*fetch_args, **fetch_kwargs)
            if row is not None:
                observed_rows += 1
            return row

        _data._fetch_fundamental_row = counting_fetch
        try:
            result = _LEGACY_REFRESH(*args, **kwargs)
        finally:
            _data._fetch_fundamental_row = _LEGACY_FETCH_ROW

        minimum_coverage = float(
            getattr(_data, "_CACHE_COMPLETENESS_THRESHOLD", 0.80)
        )
        refresh_coverage = (
            observed_rows / expected_rows if expected_rows > 0 else 1.0
        )
        insufficient_refresh = (
            expected_rows > 0 and refresh_coverage + 1e-12 < minimum_coverage
        )
        if insufficient_refresh:
            metadata_after = _read_bytes(metadata_path)
            if metadata_after != metadata_before:
                _restore_bytes(metadata_path, metadata_before)
                _data.logger.warning(
                    "AKShare 本轮新基本面覆盖率仅 %.1f%%（%d/%d，要求至少 %.0f%%）；"
                    "保留已合并数据但不刷新时效戳，后续扫描仍会重试。",
                    refresh_coverage * 100.0,
                    observed_rows,
                    expected_rows,
                    minimum_coverage * 100.0,
                )
        return result


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _data.refresh_fundamental_data = refresh_fundamental_data
    _INSTALLED = True


install()
