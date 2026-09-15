"""Republish a pytest failure as a check-run annotation.

Why this exists
---------------
GitHub Actions job logs require authentication to download, but check-run
*annotations* are readable anonymously through the public API::

    GET /repos/{owner}/{repo}/check-runs/{id}/annotations

This script runs the suite and re-emits the failure summary as ``::error::``
workflow commands, which puts the failing test names and tracebacks where they
can be read without a token.  It is wired into ``static-quality.yml`` behind
``if: failure()``, so on a green run it never executes.

It earned its keep immediately: the gate was red for days and this was the only
way to see that the three golden fixtures were failing on last-digit BLAS noise
rather than on a real behaviour change.  It is also how an intermittent failure
will be caught next time -- the gate has already flipped red and green on
identical source once (runs #604 / #605).

Delete it, and the step that calls it, whenever CI log access stops being a
problem.  It is scaffolding, not part of the product.
"""

from __future__ import annotations

import subprocess
import sys

CHUNK = 4000
MAX_CHUNKS = 8


def _emit(text: str) -> None:
    msg = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    for start in list(range(0, len(msg), CHUNK))[:MAX_CHUNKS]:
        print("::error::" + msg[start : start + CHUNK])


def main() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=short", "-rf"],
        capture_output=True,
        text=True,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    failed = [line for line in out.splitlines() if line.startswith("FAILED")]
    summary = "\n".join(failed) if failed else "(no FAILED lines captured)"
    _emit(
        "PYTEST rc=%d\nFAILED:\n%s\n=====TAIL=====\n%s"
        % (proc.returncode, summary, out[-12000:])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
