"""v59 checkpoint input-fingerprint extension.

A ScanResult already contains low-frequency fundamental fields and universe
metadata-derived fields.  Resuming an interrupted scan is therefore safe only
when those input files are unchanged as well as the per-ticker OHLCV frame.

This small extension wraps ``scanner_resume_v59._contract_payload`` instead of
copying the large scanner orchestration facade.  The checkpoint manifest then
fails closed when either the AKShare fundamental cache or TickFlow universe
snapshot changes between interruption and resume.
"""

from __future__ import annotations

from pathlib import Path

import config as _config
import scanner_resume_v59 as _resume
from performance_cache import file_signature

_BASE_CONTRACT_PAYLOAD = _resume._contract_payload
_INSTALLED = False


def _existing_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        return path if path.is_file() else None
    except OSError:
        return None


def _fundamental_input_path() -> Path | None:
    """Mirror fundamental_data.fundamental_data_path without importing AKShare."""
    default = _existing_path(Path(_config.CACHE_DIR) / "fundamental_data.csv")
    if default is not None:
        return default
    configured = str(getattr(_config, "FUNDAMENTAL_DATA_PATH", "") or "").strip()
    return _existing_path(Path(configured)) if configured else None


def _universe_input_path() -> Path | None:
    return _existing_path(Path(_config.CACHE_DIR) / "_tickflow_universe.json")


def _path_signature(path: Path | None) -> str:
    if path is None:
        return "missing"
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)
    return f"{resolved}|{file_signature(path)}"


def input_fingerprints() -> dict[str, str]:
    """Return cheap stat-based identities for non-OHLCV scan inputs."""
    return {
        "fundamental_data_signature": _path_signature(_fundamental_input_path()),
        "universe_metadata_signature": _path_signature(_universe_input_path()),
    }


def _contract_payload() -> dict[str, str]:
    payload = dict(_BASE_CONTRACT_PAYLOAD())
    payload.update(input_fingerprints())
    return payload


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _resume._contract_payload = _contract_payload
    _INSTALLED = True


install()
