"""Ad-hoc reverse validation for the v83 parity gates.

Run directly::

    python tests/reverse_validate_lifecycle_v83.py

Two injections, both applied at the byte level (the file is pure CRLF) and
reverted afterwards:

* **A -- apply the fix.**  Give v83's snapshot the two benchmark columns and
  add them to ``outcome_columns``.  Three gates must go red: the empty-history
  crash, the same-date wipe, and the static "v83 omits the columns" check.
  If they stayed green, the gates would be describing nothing.
* **B -- make v83 a callback overlay.**  Read the stored legacy implementation
  from ``install()``.  The write-only gate must go red.

The control scenario (``prior_date_history``) is deliberately not injected
against: it is the assertion that the harness is not simply always-different.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
V83 = ROOT / "lifecycle_acceleration_v83.py"

def crlf(lines: list[str]) -> bytes:
    return "".join(line + "\r\n" for line in lines).encode("utf-8")


SNAPSHOT_ANCHOR = crlf(['                "MaxDrawdown20D": core._number('])
SNAPSHOT_PATCH = crlf(
    [
        '                "BenchmarkReturn20D": core._number(',
        "                    result.get(",
        '                        "BenchmarkReturn20D", pd.Series(index=result.index)',
        "                    ),",
        "                    np.nan",
        "                ),",
        '                "BenchmarkReturn60D": core._number(',
        "                    result.get(",
        '                        "BenchmarkReturn60D", pd.Series(index=result.index)',
        "                    ),",
        "                    np.nan",
        "                ),",
    ]
) + SNAPSHOT_ANCHOR

OUTCOME_OLD = crlf(
    [
        "            outcome_columns = [",
        '                "Return20D",',
        '                "MaxDrawdown20D",',
        '                "Return60D",',
        '                "MaxDrawdown60D",',
        "            ]",
    ]
)
OUTCOME_NEW = crlf(
    [
        "            outcome_columns = [",
        '                "Return20D",',
        '                "BenchmarkReturn20D",',
        '                "MaxDrawdown20D",',
        '                "Return60D",',
        '                "BenchmarkReturn60D",',
        '                "MaxDrawdown60D",',
        "            ]",
    ]
)

CALLBACK_OLD = crlf(["    core.enrich_signal_lifecycle = _build_enricher(core)"])
CALLBACK_NEW = crlf(
    [
        "    core.enrich_signal_lifecycle = _build_enricher(core)",
        "    _ = core._v83_legacy_enrich_signal_lifecycle",
    ]
)

CASES: list[tuple[str, list[tuple[bytes, bytes]], list[str]]] = [
    (
        "A: applying the fix must break the crash, wipe and static gates",
        [(SNAPSHOT_ANCHOR, SNAPSHOT_PATCH), (OUTCOME_OLD, OUTCOME_NEW)],
        [
            "tests/test_lifecycle_v83_parity.py::test_empty_history_crashes_v83_but_not_the_stable_engine",
            "tests/test_lifecycle_v83_parity.py::"
            "test_same_date_rerun_drops_benchmark_returns_in_v83_only",
            "tests/test_lifecycle_v83_parity.py::test_v83_snapshot_omits_the_benchmark_columns",
        ],
    ),
    (
        "B: reading the stored legacy implementation must break the write-only gate",
        [(CALLBACK_OLD, CALLBACK_NEW)],
        ["tests/test_lifecycle_v83_parity.py::test_the_stored_legacy_reference_is_write_only"],
    ),
]


def run(nodeids: list[str]) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", *nodeids, "-q", "--no-header"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    tail = (result.stdout or "").strip().splitlines()
    print("    " + ("\n    ".join(tail[-2:]) if tail else "<no output>"))
    return result.returncode


def main() -> int:
    original = V83.read_bytes()
    failures = 0
    try:
        for label, patches, nodeids in CASES:
            print(f"\n=== {label}")
            current = original
            missing = [old for old, _ in patches if old not in current]
            if missing:
                print(f"    SKIP: anchor not found ({len(missing)} of {len(patches)})")
                failures += 1
                continue
            for old, new in patches:
                current = current.replace(old, new, 1)
            V83.write_bytes(current)
            try:
                code = run(nodeids)
            finally:
                V83.write_bytes(original)
            restored = V83.read_bytes() == original
            verdict = "RED as expected" if code != 0 else "STILL GREEN (vacuous!)"
            print(f"    -> {verdict}, restored={restored}")
            if code == 0 or not restored:
                failures += 1
    finally:
        V83.write_bytes(original)
    print(f"\n{'ALL CASES BITE' if failures == 0 else f'{failures} CASE(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
