"""Ad-hoc reverse validation: prove the new gates actually bite.

Run directly::

    python tests/reverse_validate_daily_pipeline.py

Each case mutates a CRLF source file at the byte level, runs the affected test,
and restores the original bytes.  A gate that stays green under its own failure
injection is decoration, not a gate.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CORE = ROOT / "daily_pipeline_core.py"
FACADE = ROOT / "daily_pipeline.py"

CASES: list[tuple[str, pathlib.Path, bytes, bytes, str]] = [
    (
        "A: an unreachable top-level def must be reported",
        CORE,
        b"def build_parser() -> argparse.ArgumentParser:",
        b"def _brand_new_unreferenced_helper() -> None:\r\n"
        b'    """Nothing calls this."""\r\n'
        b"    return None\r\n"
        b"\r\n"
        b"\r\n"
        b"def build_parser() -> argparse.ArgumentParser:",
        "tests/test_daily_pipeline_core_assembly.py::test_all_top_level_definitions_are_reachable",
    ),
    (
        "B: flipping the QualityApplicable default must break the divergence gate",
        CORE,
        b'row.get("QualityApplicable", True)',
        b'row.get("QualityApplicable", False)',
        "tests/test_daily_pipeline_core_contract.py::"
        "test_quality_applicable_defaults_diverge_when_the_column_is_absent",
    ),
    (
        "C: an overlay that stops calling the layer below must break the chain gate",
        FACADE,
        b"payload = _LEGACY_WRITE_MANIFEST(*args, **kwargs)",
        b"payload = {}  # reverse validation: legacy call removed",
        "tests/test_daily_pipeline_core_assembly.py::test_every_overlay_layer_calls_the_one_below",
    ),
]


def run_test(nodeid: str) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", nodeid, "-q", "--no-header"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    tail = (result.stdout or "").strip().splitlines()
    print("    " + ("\n    ".join(tail[-3:]) if tail else "<no output>"))
    return result.returncode


def main() -> int:
    failures = 0
    for label, path, old, new, nodeid in CASES:
        print(f"\n=== {label}")
        original = path.read_bytes()
        if old not in original:
            print(f"    SKIP: anchor not found in {path.name}")
            failures += 1
            continue
        try:
            path.write_bytes(original.replace(old, new, 1))
            code = run_test(nodeid)
        finally:
            path.write_bytes(original)
        restored = path.read_bytes() == original
        ok = code != 0 and restored
        print(f"    -> {'RED as expected' if code != 0 else 'STILL GREEN (vacuous!)'}"
              f", restored={restored}")
        if not ok:
            failures += 1
    print(f"\n{'ALL CASES BITE' if failures == 0 else f'{failures} CASE(S) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
