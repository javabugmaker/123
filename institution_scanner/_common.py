"""Shared low-level helpers for the canonical package.

Every helper here previously existed as 2-4 byte-for-byte identical copies
spread across the package. Duplicated coercion, escaping and frame helpers
drift: a fix applied to one copy silently leaves the others behind. Keeping a
single copy means the semantics change everywhere at once.

This module is the bottom of the package dependency graph. It must not import
from any other module in ``institution_scanner``, otherwise the extraction
would reintroduce the coupling it exists to remove.

Deliberately NOT here: per-dataclass ``to_dict``/``as_dict`` methods. Those are
one-line ``asdict(self)`` forwarders that happen to share a body; merging them
across unrelated dataclasses would couple types that have no reason to know
about each other.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

import pandas as pd


def to_int(value: object) -> int:
    """Coerce an arbitrary value to a non-negative int, defaulting to 0.

    Non-numeric or missing values collapse to 0 rather than raising, because
    every current caller renders these into a page or a health metric where a
    missing count and a zero count are presented identically.
    """
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def escape_html(value: object) -> str:
    """Render a value as HTML-escaped text; ``None`` becomes an empty string."""
    return html.escape("" if value is None else str(value), quote=True)


def to_mapping(value: object) -> dict[str, Any]:
    """Return ``value`` when it is a dict, otherwise an empty dict.

    Typed as ``dict[str, Any]`` rather than ``dict[str, object]`` so it stays
    assignable at every original call site, some of which declared the narrower
    ``object`` value type.
    """
    return value if isinstance(value, dict) else {}


def text_series(frame: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    """Read one frame column as a stripped string Series, filling gaps."""
    source = frame.get(column, pd.Series(default, index=frame.index, dtype=object))
    if not isinstance(source, pd.Series):
        source = pd.Series(source, index=frame.index)
    return source.fillna(default).astype(str).str.strip()


def read_json_payload(path: Path) -> dict[str, Any]:
    """Load a JSON object from ``path``; unreadable or non-object yields ``{}``."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _truthy(value: object) -> bool:
    """Coerce a loosely-typed cell to a boolean.

    Exports, CSV round-trips and HTML forms all render booleans as text, and
    the accepted spellings must stay in one place: a fix to one copy of this
    predicate previously left the other three behind.
    """
    return str(value).strip().lower() in {"true", "1", "yes", "y", "是"}
