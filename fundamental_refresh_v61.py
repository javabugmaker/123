"""v61 fundamental refresh freshness guard.

The stable AkShare implementation intentionally preserves old factor rows when a
provider request fails.  That is correct for scan continuity, but the legacy
refresh path also rewrites ``fundamental_data_meta.json`` after combining those
old rows.  If *zero* new ticker rows were obtained, that advances the cache's
``updated`` date even though no provider evidence was refreshed.

This facade counts real rows returned by the existing batch fetch path and
restores the prior metadata file when the count is zero.  Existing cached data
remains usable, but it stays stale and the next eligible scan retries AkShare
instead of treating an outage as a successful 14-day refresh.
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


def refresh_fundamental_data(*args: Any, **kwargs: Any) -> Path:
    """Run the stable refresh without turning a zero-row outage into fresh data."""
    with _REFRESH_GUARD_LOCK:
        metadata_path = Path(_data._META_PATH)
        metadata_before = _read_bytes(metadata_path)
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

        if observed_rows == 0:
            metadata_after = _read_bytes(metadata_path)
            if metadata_after != metadata_before:
                _restore_bytes(metadata_path, metadata_before)
                _data.logger.warning(
                    "AKShare 本轮未取得任何新基本面行；保留旧缓存但不刷新时效戳，"
                    "后续扫描仍会重试。"
                )
        return result


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _data.refresh_fundamental_data = refresh_fundamental_data
    _INSTALLED = True


install()
