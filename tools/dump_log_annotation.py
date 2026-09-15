"""Republish a saved step log as a check-run annotation.

Same trick as ``dump_pytest_annotation.py``, but for an arbitrary step whose
output was teed to a file::

    run: |
      set -o pipefail
      python -m institution_scanner.publish_site output/web_report 2>&1 | tee publish-out.txt
    ...
    - name: Republish failure as annotation
      if: failure()
      run: python tools/dump_log_annotation.py publish-out.txt

GitHub job logs need authentication to download, while check-run annotations are
readable anonymously, so this is what makes a red step diagnosable from outside
the browser.  Delete it together with the step that calls it once log access is
no longer a problem.
"""

from __future__ import annotations

import sys
from pathlib import Path

CHUNK = 4000
MAX_CHUNKS = 8


def _emit(text: str) -> None:
    msg = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    for start in list(range(0, len(msg), CHUNK))[:MAX_CHUNKS]:
        print("::error::" + msg[start : start + CHUNK])


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: dump_log_annotation.py <logfile>", file=sys.stderr)
        return 2
    path = Path(argv[0])
    text = path.read_text(errors="replace") if path.is_file() else "(log file missing)"
    _emit("STEP LOG: %s\n=====\n%s" % (path.name, text[-12000:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
