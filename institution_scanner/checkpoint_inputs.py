"""Canonical checkpoint input-fingerprint extension.

Resume state is valid only while the low-frequency fundamental cache and TickFlow
universe metadata are unchanged as well as ticker OHLCV state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from performance_cache import file_signature


def _existing_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        return path if path.is_file() else None
    except OSError:
        return None


def _path_signature(path: Path | None) -> str:
    if path is None:
        return "missing"
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)
    return f"{resolved}|{file_signature(path)}"


def input_fingerprints(config: Any) -> dict[str, str]:
    default = _existing_path(Path(config.CACHE_DIR) / "fundamental_data.csv")
    configured = str(getattr(config, "FUNDAMENTAL_DATA_PATH", "") or "").strip()
    fundamental = default
    if fundamental is None and configured:
        fundamental = _existing_path(Path(configured))
    universe = _existing_path(Path(config.CACHE_DIR) / "_tickflow_universe.json")
    return {
        "fundamental_data_signature": _path_signature(fundamental),
        "universe_metadata_signature": _path_signature(universe),
    }


def install(resume_module: Any, config: Any) -> None:
    base_contract_payload = resume_module._contract_payload
    if getattr(base_contract_payload, "_canonical_input_fingerprint_wrapper", False):
        return

    def contract_payload() -> dict[str, str]:
        payload = dict(base_contract_payload())
        payload.update(input_fingerprints(config))
        return payload

    contract_payload._canonical_input_fingerprint_wrapper = True  # type: ignore[attr-defined]
    resume_module._contract_payload = contract_payload
