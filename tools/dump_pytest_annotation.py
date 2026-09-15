"""TEMPORARY diagnostic: republish a pytest failure as a check-run annotation.

GitHub Actions job logs are not readable without authentication, but check-run
*annotations* are.  This script runs the suite and re-emits the failure summary
as ``::error::`` workflow commands, which makes the failing test names and
tracebacks readable through the public API.

It is wired into ``static-quality.yml`` behind ``if: failure()`` so it costs
nothing on a green run.  Delete it, and the step that calls it, once the gate
is consistently green -- it is scaffolding, not part of the product.
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
    _emit("PYTEST rc=%d\nFAILED:\n%s\n=====TAIL=====\n%s" % (proc.returncode, summary, out[-12000:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
