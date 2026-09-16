"""Stage 3 workhorse: try removing one module-level ``install()`` at a time.

For each candidate this deletes the module-scope ``install()`` call, re-captures
the assembly manifest for every core production entry point, and compares the
``final`` map against the frozen fixture.  Identical -> the removal is kept.
Any difference -> the file is restored byte-for-byte and the blocker is
recorded.

Nothing is deleted on faith.  A module that self-installs might be the *only*
thing that installs it under some entry point, and the central assembly points
(``analytics_runtime``, ``backtest_acceleration_v77``) run in a different order
than import-time self-installation, so "obviously redundant" is not a proof.

!!! WARNING !!!
Rewrites production sources.  Restores on failure, but if it is killed mid-run
check ``git status``.  Do not run concurrently with anything else.

Usage:  python tests/_strip_module_level_installs.py [--apply]
        (without --apply it is a dry run)
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from assembly_manifest import (  # noqa: E402
    CORE_ENTRY_POINTS,
    capture_subprocess,
    fixture_path,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: (file, 1-based line number of the module-level ``install()`` call)
#:
#: Batch 1 (done, 22/22 kept) covered the overlays reached through
#: ``import scanner``.  Batch 2 is the rest of the self-installing modules;
#: each has a named caller that can own it instead:
#:   backtest_command_v76  <- main.py:32 `_backtest_command.install()`
#:   daily_recovery_v74    <- daily_pipeline.py:29
#:   resonance_runtime_v91 <- backtest_command_v76.py:33
#:   scanner_resume_v59    <- (no known caller — likely BLOCKED)
CANDIDATES: list[tuple[str, int]] = [
    ("backtest_command_v76.py", 181),
    ("daily_recovery_v74.py", 206),
    ("resonance_runtime_v91.py", 172),
    ("scanner_resume_v59.py", 741),
]


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", newline="")


def write(path: pathlib.Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="")


def expected_finals() -> dict[str, dict[str, str]]:
    return {
        entry: json.loads(fixture_path(entry).read_text(encoding="utf-8"))["final"]
        for entry in CORE_ENTRY_POINTS
    }


def verify(baseline: dict[str, dict[str, str]]) -> list[str]:
    """Return a human-readable list of assembly differences (empty == clean)."""
    problems: list[str] = []
    for entry, expected in baseline.items():
        try:
            actual = capture_subprocess(entry)["final"]
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{entry}: import failed ({type(exc).__name__}: {exc})")
            continue
        if actual == expected:
            continue
        changed = sorted(
            k for k in set(expected) & set(actual) if expected[k] != actual[k]
        )
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        detail = []
        if changed:
            detail.append(
                "resolves differently: "
                + "; ".join(f"{k}: {expected[k]} -> {actual[k]}" for k in changed[:4])
            )
        if missing:
            detail.append(f"no longer touched: {missing[:6]}")
        if extra:
            detail.append(f"newly touched: {extra[:6]}")
        problems.append(f"{entry}: " + " | ".join(detail))
    return problems


def strip_call(text: str, lineno: int) -> tuple[str, str]:
    """Remove line *lineno*, then tidy the whitespace it leaves behind.

    Works whether or not the call is the last line: consecutive blank lines are
    collapsed to the PEP8 two (``indicator_acceleration_v77.py`` keeps
    ``acceleration_status()`` after the call), and trailing blanks at EOF are
    dropped.
    """
    lines = text.splitlines(keepends=True)
    if lineno > len(lines):
        raise ValueError(f"line {lineno} past EOF ({len(lines)} lines)")
    target = lines[lineno - 1]
    if not re.match(r"^install\w*\(", target.strip()):
        raise ValueError(f"not an install call: {target.strip()!r}")
    del lines[lineno - 1]

    collapsed: list[str] = []
    blank_run = 0
    for line in lines:
        if line.strip() == "":
            blank_run += 1
            if blank_run <= 2:
                collapsed.append(line)
        else:
            blank_run = 0
            collapsed.append(line)
    lines = collapsed

    while len(lines) > 1 and lines[-1].strip() == "":
        lines.pop()
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += "\r\n" if "\r\n" in text else "\n"
    return target.strip(), "".join(lines)


def main(argv: list[str]) -> int:
    apply_changes = "--apply" in argv
    baseline = expected_finals()
    kept: list[str] = []
    blocked: list[tuple[str, str]] = []
    skipped: list[tuple[str, str]] = []

    for name, lineno in CANDIDATES:
        path = ROOT / name
        original = read(path)
        try:
            target, edited = strip_call(original, lineno)
        except (ValueError, IndexError) as exc:
            skipped.append((f"{name}:{lineno}", str(exc)))
            print(f"[SKIP] {name}:{lineno} — {exc}")
            continue
        if edited == original:
            skipped.append((f"{name}:{lineno}", "no change"))
            continue

        if not apply_changes:
            print(f"[DRY]  {name}:{lineno} would remove {target}")
            continue

        # Same defect class as reverse_validate_downloader_core.py: a ``return``
        # inside ``finally`` discards the exception it is cleaning up, and
        # reading ``problems`` before it is assigned would mask that exception
        # with a NameError while leaving the file in its edited state.  Restore
        # explicitly instead -- on a raised error first, then on a dirty verify.
        problems: list[str] = []
        restore_ok = True
        try:
            write(path, edited)
            problems = verify(baseline)
        except BaseException:
            write(path, original)
            raise
        if problems:
            write(path, original)
            if read(path) != original:
                print(f"[FATAL] {name} 还原失败！")
                restore_ok = False
        if not restore_ok:
            return 1

        if problems:
            blocked.append((f"{name}:{lineno}", problems[0]))
            print(f"[BLOCK] {name}:{lineno} — {problems[0]}")
        else:
            kept.append(f"{name}:{lineno}")
            print(f"[OK]   {name}:{lineno} — 四入口 final 完全一致，删除保留")

    print()
    print(f"保留删除: {len(kept)} / 阻断: {len(blocked)} / 跳过: {len(skipped)}")
    for tag, why in blocked:
        print(f"  阻断 {tag}: {why}")
    for tag, why in skipped:
        print(f"  跳过 {tag}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
