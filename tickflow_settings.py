"""Local TickFlow credential preferences for InstitutionScanner.

Secrets are intentionally stored only in the repository-local ``.env.local``
file, which is ignored by Git.  The public repository never needs to contain an
API key.  GUI-local settings take precedence over a stale Windows environment
variable so switching between authenticated and Free mode is deterministic.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_SETTINGS_PATH = PROJECT_ROOT / ".env.local"
API_KEY_NAME = "TICKFLOW_API_KEY"
API_MODE_NAME = "TICKFLOW_API_MODE"
MODE_AUTHENTICATED = "authenticated"
MODE_FREE = "free"
MODE_AUTO = "auto"


def _clean_value(value: object) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


def normalize_api_key(value: object) -> str:
    """Normalize common copy/paste wrappers without exposing the secret."""
    key = _clean_value(value)
    if len(key) >= 2 and key.startswith("<") and key.endswith(">"):
        key = key[1:-1].strip()
    return key


def load_local_settings(path: Path | None = None) -> dict[str, str]:
    settings_path = Path(path or DEFAULT_SETTINGS_PATH)
    try:
        lines = settings_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return {}

    result: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name in {API_KEY_NAME, API_MODE_NAME}:
            result[name] = _clean_value(value)
    return result


def get_tickflow_api_key(
    path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve the active API key with GUI-local mode taking precedence."""
    local = load_local_settings(path)
    mode = local.get(API_MODE_NAME, MODE_AUTO).strip().lower()
    if mode == MODE_FREE:
        return ""

    local_key = normalize_api_key(local.get(API_KEY_NAME, ""))
    if local_key:
        return local_key

    source = os.environ if environ is None else environ
    return normalize_api_key(source.get(API_KEY_NAME, ""))


def get_tickflow_setting_source(
    path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> str:
    local = load_local_settings(path)
    mode = local.get(API_MODE_NAME, MODE_AUTO).strip().lower()
    if mode == MODE_FREE:
        return "free"
    if normalize_api_key(local.get(API_KEY_NAME, "")):
        return "gui-local"
    source = os.environ if environ is None else environ
    if normalize_api_key(source.get(API_KEY_NAME, "")):
        return "environment"
    return "free"


def _rewrite_local_settings(
    updates: Mapping[str, str | None], path: Path | None = None
) -> Path:
    settings_path = Path(path or DEFAULT_SETTINGS_PATH)
    try:
        original = settings_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        original = []

    managed = set(updates)
    retained: list[str] = []
    for raw_line in original:
        stripped = raw_line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            name = stripped.split("=", 1)[0].strip()
            if name in managed:
                continue
        retained.append(raw_line)

    while retained and not retained[-1].strip():
        retained.pop()
    if retained:
        retained.append("")
    retained.append("# InstitutionScanner local TickFlow settings (gitignored)")
    for name, value in updates.items():
        if value is not None:
            retained.append(f"{name}={value}")
    retained.append("")

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = settings_path.with_name(settings_path.name + ".tmp")
    temporary.write_text("\n".join(retained), encoding="utf-8")
    os.replace(temporary, settings_path)
    try:
        settings_path.chmod(0o600)
    except OSError:
        pass
    return settings_path


def save_tickflow_api_key(value: object, path: Path | None = None) -> Path:
    key = normalize_api_key(value)
    if not key:
        raise ValueError("TickFlow API Key 不能为空")
    if any(char.isspace() for char in key):
        raise ValueError("TickFlow API Key 不能包含空格")
    settings_path = _rewrite_local_settings(
        {API_MODE_NAME: MODE_AUTHENTICATED, API_KEY_NAME: key}, path
    )
    os.environ[API_KEY_NAME] = key
    return settings_path


def use_tickflow_free(path: Path | None = None) -> Path:
    """Persist an explicit Free-mode override, even if Windows has an old key."""
    settings_path = _rewrite_local_settings(
        {API_MODE_NAME: MODE_FREE, API_KEY_NAME: None}, path
    )
    os.environ.pop(API_KEY_NAME, None)
    return settings_path


def use_tickflow_auto(path: Path | None = None) -> Path:
    """Remove the local key override and fall back to the process environment."""
    return _rewrite_local_settings(
        {API_MODE_NAME: MODE_AUTO, API_KEY_NAME: None}, path
    )


def masked_api_key(value: object) -> str:
    key = normalize_api_key(value)
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:3]}{'•' * min(12, len(key) - 7)}{key[-4:]}"
