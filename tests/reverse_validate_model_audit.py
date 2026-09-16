"""Reverse validation for the ``model_audit`` gates.

A gate that has never been watched fail is not a gate. This script injects five
deliberate regressions into ``model_audit.py``, one at a time, runs the two new
test files, and reports which assertions turn red. Every case must fail at least
one test; a case that stays green means the corresponding assertion is
decorative.

Two implementation notes worth keeping:

* ``model_audit.py`` is CRLF. Anchors are built with the file's own dominant
  newline, because a ``\\n`` anchor matches nothing in a CRLF file and the case
  is then silently skipped — which is exactly the failure mode reverse
  validation exists to prevent.
* ``-q`` is not passed on the command line: ``pyproject.toml`` already sets it
  in ``addopts``, and a second ``-q`` escalates to ``-qq``, which suppresses the
  ``N passed`` summary line the report parses.

The file is restored byte-for-byte afterwards (binary read/write, so CRLF is
preserved), including on an exception, via ``finally``.

Run:  python tests/reverse_validate_model_audit.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TARGET = REPO_ROOT / "model_audit.py"
TESTS = [
    "tests/test_model_audit_behaviour.py",
    "tests/test_model_audit_provenance.py",
]


def _dominant_newline(payload: bytes) -> bytes:
    crlf = payload.count(b"\r\n")
    return b"\r\n" if crlf and crlf == payload.count(b"\n") else b"\n"


def cases(nl: bytes) -> list[tuple[str, bytes, bytes, str]]:
    return [
        (
            "A. 重建公式漏乘 recency 腿",
            nl.join([b"        * recency", b""]),
            nl,
            "test_reconstruction_reproduces_a_coherent_frame",
        ),
        (
            "B. 全宇宙规模校验放宽（不再拒绝 size 不符）",
            b"if len(sizes) == 1 and sizes[0] > 0 and sizes[0] != len(frame):",
            b"if False and len(sizes) == 1 and sizes[0] > 0 and sizes[0] != len(frame):",
            "test_universe_size_mismatch_is_rejected",
        ),
        (
            "C. recency 下限从 0.7 改成 0.0（取消 clip）",
            b'_number(frame, "SignalRecencyFactor", 1.0).clip(0.7, 1.0)',
            b'_number(frame, "SignalRecencyFactor", 1.0).clip(0.0, 1.0)',
            "test_recency_multiplier_clips_the_raw_factor_at_seventy_percent",
        ),
        (
            "D. run_audit 少写一个产物",
            nl.join(
                [
                    b"    thresholds.to_csv(",
                    b'        output_dir / "threshold_exposure.csv",',
                ]
            ),
            b"    if False: thresholds.to_csv(",
            "test_run_audit_writes_all_four_artifacts",
        ),
        (
            "E. 审计反过来改配置（破坏只读契约）",
            b"def run_audit(input_path: Path, output_dir: Path) -> dict[str, Any]:" + nl,
            b"def run_audit(input_path: Path, output_dir: Path) -> dict[str, Any]:" + nl
            + b"    import config" + nl
            + b"    config.QUALITY_MIN_COMPLETENESS_FOR_ACTIONABLE = 0.0" + nl,
            "test_run_audit_leaves_configuration_untouched",
        ),
    ]


def _pytest(extra: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", *TESTS, "--no-header", *extra],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _summary(result: subprocess.CompletedProcess[str]) -> str:
    lines = [
        line
        for line in result.stdout.splitlines()
        if any(token in line for token in ("passed", "failed", "error", "Error"))
    ]
    return lines[-1].strip() if lines else "(无摘要行)"


def _failed_names() -> list[str]:
    result = _pytest(["-rf", "--tb=no"])
    return [
        line[len("FAILED ") :].split(" ")[0]
        for line in result.stdout.splitlines()
        if line.startswith("FAILED ")
    ]


def main() -> int:
    original = TARGET.read_bytes()
    nl = _dominant_newline(original)
    nl_name = "CRLF" if nl == b"\r\n" else "LF"
    print(f"行尾: {nl_name}")

    print("基线：未改动源码跑一次（应当全绿）")
    baseline = _pytest([])
    print(f"  {_summary(baseline)}\n")
    if baseline.returncode != 0:
        print("基线不绿，终止：先修好测试再跑反向验证。")
        return 1

    missed = 0
    try:
        for label, needle, replacement, expected in cases(nl):
            hits = original.count(needle)
            if hits != 1:
                print(f"{label}\n  锚点命中 {hits} 次（期望 1）—— 跳过\n")
                missed += 1
                continue
            TARGET.write_bytes(original.replace(needle, replacement, 1))
            try:
                result = _pytest([])
                names = _failed_names() if result.returncode != 0 else []
                hit = any(expected in name for name in names)
                print(f"{label}\n  {'咬住' if hit else '未咬住'}   {_summary(result)}")
                print(f"    期望红: {expected}")
                print(f"    实际红: {[n.split('::')[-1] for n in names] or '无'}\n")
                if not hit:
                    missed += 1
            finally:
                TARGET.write_bytes(original)
    finally:
        TARGET.write_bytes(original)

    restored = TARGET.read_bytes() == original
    print(f"源码已还原: {restored}")
    print(f"未咬住的 case: {missed}")
    return 1 if missed or not restored else 0


if __name__ == "__main__":
    raise SystemExit(main())
