"""Shared helper: run ``daily_pipeline_core_probe.py`` once per test module.

Underscore-prefixed so pytest does not collect it.  Mirrors the retry rule in
``test_daily_pipeline_publication.py``: a transport-level ``OSError`` while
setting up the subprocess pipes is retried, a non-zero exit from the probe
itself never is -- that is a real finding and must be reported as one.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROBE = Path(__file__).resolve().with_name("daily_pipeline_core_probe.py")
_TIMEOUT_SECONDS = 300
_ATTEMPTS = 4


def run_probe() -> dict[str, Any]:
    last_error: OSError | None = None
    for _attempt in range(_ATTEMPTS):
        try:
            result = subprocess.run(
                [sys.executable, str(PROBE)],
                cwd=str(ROOT),
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
                check=False,
            )
            break
        except OSError as error:  # pragma: no cover - environment dependent
            last_error = error
    else:  # pragma: no cover - environment dependent
        raise AssertionError(
            f"could not launch the daily_pipeline_core probe: {last_error}"
        ) from last_error

    if result.returncode != 0:
        raise AssertionError(
            "daily_pipeline_core probe failed\n"
            f"returncode={result.returncode}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    try:
        return dict(json.loads(result.stdout))
    except json.JSONDecodeError as error:
        raise AssertionError(
            f"probe produced no parseable report: {error}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        ) from error
