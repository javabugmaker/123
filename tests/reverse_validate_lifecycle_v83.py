"""Ad-hoc reverse validation for the v83 parity gates.

Run directly::

    python tests/reverse_validate_lifecycle_v83.py

Two injections, both applied at the byte level (``lifecycle_acceleration_v83.py``
is pure CRLF) and reverted afterwards:

* **A -- revert the fix.**  Strip the two benchmark columns back out of v83's
  snapshot and out of ``outcome_columns``.  This is the exact inverse of the
  2026-09-16 repair, so the gates that now pin the repair must go red: the
  empty-history crash, the same-date wipe, the static "declares the columns"
  check, and the two per-scenario agreement gates.  If they stayed green the
  gates would be describing nothing.
* **B -- make v83 a callback overlay.**  Read the stored legacy implementation
  from ``install()``.  The write-only gate must go red.

The control scenario (``prior_date_history``) is deliberately not expected to
turn red under case A: it is the assertion that the harness is not simply
always-different, and it held both before and after the fix.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
V83 = ROOT / "lifecycle_acceleration_v83.py"
PARITY_FILE = "tests/test_lifecycle_v83_parity.py"


def crlf(lines: list[str]) -> bytes:
    return "".join(line + "\r\n" for line in lines).encode("utf-8")


def block(name: str) -> bytes:
    """The snapshot entry the repair added, verbatim, so it can be removed."""
    return crlf(
        [
            f'                "{name}": core._number(',
            "                    result.get(",
            f'                        "{name}",',
            "                        pd.Series(index=result.index),",
            "                    ),",
            "                    np.nan,",
            "                ),",
        ]
    )


OUTCOME_REPAIRED = crlf(
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
OUTCOME_REVERTED = crlf(
    [
        "            outcome_columns = [",
        '                "Return20D",',
        '                "MaxDrawdown20D",',
        '                "Return60D",',
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

REVERTED_MUST_RED = {
    "test_empty_history_no_longer_crashes_v83",
    "test_same_date_rerun_preserves_benchmark_returns",
    "test_v83_snapshot_declares_the_benchmark_columns",
    "test_implementations_agree_in_every_scenario[fresh_no_history]",
    "test_implementations_agree_in_every_scenario[same_date_rerun]",
}
REVERTED_MUST_STAY_GREEN = {
    "test_implementations_agree_in_every_scenario[prior_date_history]"
}

CASES: list[tuple[str, list[tuple[bytes, bytes]], set[str], set[str]]] = [
    (
        "A: reverting the fix must break the crash, wipe, static and agreement gates",
        [
            (block("BenchmarkReturn20D"), b""),
            (block("BenchmarkReturn60D"), b""),
            (OUTCOME_REPAIRED, OUTCOME_REVERTED),
        ],
        REVERTED_MUST_RED,
        REVERTED_MUST_STAY_GREEN,
    ),
    (
        "B: reading the stored legacy implementation must break the write-only gate",
        [(CALLBACK_OLD, CALLBACK_NEW)],
        {"test_the_stored_legacy_reference_is_write_only"},
        set(),
    ),
]


def run() -> tuple[int, set[str]]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", PARITY_FILE, "--no-header", "-rf", "--tb=no"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    failed = {
        line[len("FAILED ") :].split(" ")[0].split("::")[-1]
        for line in (result.stdout or "").splitlines()
        if line.startswith("FAILED ")
    }
    summary = [line for line in result.stdout.splitlines() if "passed" in line or "failed" in line]
    print("    " + (summary[-1] if summary else "<no summary>"))
    return result.returncode, failed


def main() -> int:
    original = V83.read_bytes()
    failures = 0
    try:
        for label, patches, must_red, must_stay_green in CASES:
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
                code, failed = run()
            finally:
                V83.write_bytes(original)
            restored = V83.read_bytes() == original

            absent = must_red - failed
            leaked = must_stay_green & failed
            print(f"    expected red: {len(must_red)}, actually red: {len(failed)}")
            if absent:
                print(f"    VACUOUS — did not go red: {sorted(absent)}")
            if leaked:
                print(f"    OVER-TRIGGERED — control also went red: {sorted(leaked)}")
            print(f"    -> {'BITES' if not absent and not leaked else 'FAILED'}, restored={restored}")
            if absent or leaked or code == 0 or not restored:
                failures += 1
    finally:
        V83.write_bytes(original)
    print(f"\n{'ALL CASES BITE' if failures == 0 else f'{failures} CASE(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
