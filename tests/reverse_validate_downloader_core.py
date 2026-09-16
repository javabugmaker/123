"""Reverse validation for tests/test_downloader_core_golden.py.

Each injection mutates one helper's logic; the golden suite MUST go red.  If it
stays green, the golden file is not actually protecting that code path and the
number it "passes" is worthless.

!!! WARNING !!!
This script **temporarily rewrites production sources** (downloader_core.py and
downloader_v51_base.py) and restores them in a ``finally`` block.  Never run it
while another test run or a scan is in progress.  If it is killed mid-run, check
``git status`` for downloader_core.py / downloader_v51_base.py before anything else.

Usage:  python tests/reverse_validate_downloader_core.py
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

# Derived, not hardcoded: this repository is public, so a literal
# ``C:\Users\<name>\...`` path would publish a developer's account name, and a
# literal ROOT would break the script on every other checkout.  The sibling
# reverse-validation scripts already do it this way.
ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable
TEST = "tests/test_downloader_core_golden.py"

CORE = ROOT / "downloader_core.py"
V51 = ROOT / "downloader_v51_base.py"

INJECTIONS: list[tuple[str, pathlib.Path, str, str, str]] = [
    (
        "D1",
        CORE,
        'normalized.startswith(("4", "8", "92"))',
        'normalized.startswith(("4", "8"))',
        "normalize_ticker: 92 开头不再归为北交所 (BJ)",
    ),
    (
        "D2",
        CORE,
        'code.startswith(("15", "16", "50", "51", "56", "58"))',
        'code.startswith(("15", "16", "50", "56", "58"))',
        "is_etf_ticker: 51 前缀不再算 ETF",
    ),
    (
        "D3",
        CORE,
        "return any(keyword.upper() in normalized for keyword in EXCLUDED_SECURITY_KEYWORDS)",
        "return False",
        "_is_excluded_security_name: 丢掉关键词匹配（只留 ST/退）",
    ),
    (
        "D4",
        CORE,
        'return safe or "ticker"',
        'return safe or "TICKER"',
        "_safe_cache_stem: 兜底名改大小写",
    ),
    (
        "D5",
        CORE,
        "return number if np.isfinite(number) and number > 0 else None",
        "return number if np.isfinite(number) and number >= 0 else None",
        "_number_or_none: 接受 0",
    ),
    (
        "D6",
        CORE,
        "    if isinstance(value, Mapping):\n        return dict(value)",
        '    if isinstance(value, Mapping):\n        return {str(k): v for k, v in value.items() if str(k) != "a"}',
        "_as_mapping: 丢弃 key 为 'a' 的项",
    ),
    (
        "D7",
        CORE,
        "size = max(1, int(TICKFLOW_BATCH_SIZE) * max(1, int(TICKFLOW_MAX_WORKERS)))",
        "size = max(1, int(TICKFLOW_BATCH_SIZE))",
        "_request_chunks: 去掉 worker 系数 (500 -> 100)",
    ),
    (
        "D8",
        CORE,
        "    cleaned = cleaned.sort_index()",
        "    cleaned = cleaned.sort_index(ascending=False)",
        "_validate_ohlcv: 排序方向反转",
    ),
    (
        "D9",
        CORE,
        "    if len(common) < 2:",
        "    if len(common) < 3:",
        "_requires_full_rebase: 最小重叠 2 -> 3",
    ),
    (
        "D10",
        V51,
        'return float(normalized) if evidence["sanity_passed"] and normalized is not None else None',
        "return float(normalized) if normalized is not None else None",
        "_normalize_cn_share_count: 去掉 sanity 带宽校验",
    ),
]


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", newline="")


def write(path: pathlib.Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="")


def run_test() -> int:
    result = subprocess.run(
        [PY, "-m", "pytest", TEST, "-q", "-p", "no:cacheprovider"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode


def main() -> int:
    caught: list[str] = []
    missed: list[str] = []
    skipped: list[str] = []

    baseline = run_test()
    if baseline != 0:
        print(f"[FATAL] 基线测试就不是绿的 (rc={baseline})，先修测试再谈反向验证")
        return 1
    print("[OK] 基线绿\n")

    for tag, path, old, new, label in INJECTIONS:
        original = read(path)
        # Sources are CRLF on this checkout; normalise the anchor's line endings
        # before matching or every multi-line anchor silently misses.
        eol = "\r\n" if "\r\n" in original else "\n"
        old_e = old.replace("\n", eol)
        new_e = new.replace("\n", eol)
        count = original.count(old_e)
        if count != 1:
            print(f"[SKIP] {tag} {label}: 锚点命中 {count} 次（应为 1），无法注入")
            skipped.append(tag)
            continue
        restore_ok = True
        try:
            write(path, original.replace(old_e, new_e, 1))
            rc = run_test()
        finally:
            # Deliberately no ``return`` in this block.  A ``return`` inside
            # ``finally`` discards any in-flight exception, and since ``rc``
            # would never have been assigned the outer ``if rc == 0`` branch is
            # unreachable -- a crashed validation run used to be reported as
            # "抓到 (GOOD)".  A gate whose self-check cannot fail is worse than
            # no gate, so let the exception propagate and decide afterwards.
            write(path, original)
            if read(path) != original:
                print(f"[FATAL] {tag} 还原失败！文件仍被改动：{path}")
                restore_ok = False
        if not restore_ok:
            return 1
        if rc == 0:
            print(f"[BAD]  {tag} {label}: *** NOT CAUGHT ***")
            missed.append(tag)
        else:
            print(f"[GOOD] {tag} {label}: 抓到 (rc={rc})")
            caught.append(tag)

    print(
        f"\n反向验证: {len(caught)} 抓到 / {len(missed)} 未抓到 / {len(skipped)} 跳过"
    )
    if missed:
        print(f"未抓到: {', '.join(missed)}")
    if skipped:
        print(f"跳过: {', '.join(skipped)}")
    final = run_test()
    print(f"还原后复跑: rc={final}")
    print("结论:", "全部抓到" if not missed and not skipped and final == 0 else "仍有未抓到")
    return 0 if not missed and not skipped and final == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
