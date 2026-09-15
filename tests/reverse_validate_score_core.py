"""Reverse validation for tests/test_score_core_golden.py.

Each injection mutates one helper's logic; the golden suite MUST go red.  If it
stays green, the golden file is not actually protecting that code path and the
number it "passes" is worthless.

Injections deliberately span four different files, because provenance shows only
9 of the 25 helpers under test still live in ``score_core`` — the rest are owned
by ``score_acceleration_v79``, ``score_scale_migration_v95``,
``score_endpoint_acceleration_v79``, ``score_cache_guard_v80`` and the ``score``
facade.  A net that only needs ``score_core`` to change would miss most of what
actually runs.

!!! WARNING !!!
This script **temporarily rewrites production sources** and restores them in a
``finally`` block.  Never run it while another test run or a scan is in progress.
If it is killed mid-run, check ``git status`` for score_core.py / score.py /
score_acceleration_v79.py / score_scale_migration_v95.py before anything else.

Usage:  python tests/reverse_validate_score_core.py
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable
TEST = "tests/test_score_core_golden.py"

CORE = ROOT / "score_core.py"
FACADE = ROOT / "score.py"
V79 = ROOT / "score_acceleration_v79.py"
V95 = ROOT / "score_scale_migration_v95.py"
ENDPOINT = ROOT / "score_endpoint_acceleration_v79.py"
V80 = ROOT / "score_cache_guard_v80.py"

INJECTIONS: list[tuple[str, pathlib.Path, str, str, str]] = [
    (
        "S1",
        CORE,
        "    if not _is_finite(value):\n        return low",
        "    if not _is_finite(value):\n        return high",
        "_clamp: 非有限值返回上界而非下界",
    ),
    (
        "S2",
        CORE,
        "return bool(pd.notna(value) and np.isfinite(float(value)))",
        "return bool(pd.notna(value))",
        "_is_finite: 丢掉 isfinite 检查（inf 变有限）",
    ),
    (
        "S3",
        CORE,
        "    if max_val == min_val:\n        return 0.5",
        "    if max_val == min_val:\n        return 0.25",
        "_normalize_to_range: 退化区间返回值 0.5 -> 0.25",
    ),
    (
        "S4",
        CORE,
        "    return 3 if is_etf else 2",
        "    return 2 if is_etf else 3",
        "tradable_price_decimals: 股票/ETF 精度互换",
    ),
    (
        "S5",
        CORE,
        "        and breakout >= 35.0",
        "        and breakout >= 36.0",
        "smart_money_stage: ACCUMULATION 门槛 35 -> 36",
    ),
    (
        "S6",
        CORE,
        "    return value_trap_risk(df)",
        "    return value_trap_risk(df) * 0.5",
        "value_trap_risk_score: 结果减半",
    ),
    (
        "S7",
        CORE,
        '            "score": 50.0,\n            "confidence": 0.0,',
        '            "score": 51.0,\n            "confidence": 0.0,',
        "cyclical_turn_factor: 数据不足兜底分 50 -> 51",
    ),
    (
        "S8",
        CORE,
        'f"{setup:.4f}:{trigger:.4f}:{execution:.4f}"',
        'f"{setup:.3f}:{trigger:.3f}:{execution:.3f}"',
        "model_weight_signature: 精度 4 位 -> 3 位",
    ),
    (
        "S9",
        FACADE,
        "volatility_contraction_score(df, max_score=15.0)",
        "volatility_contraction_score(df, max_score=20.0)",
        "facade score_volatility: max_score 15 -> 20",
    ),
    (
        "S10",
        FACADE,
        "trigger_coverage = 0.75 + 0.25 * float(result.indicator_coverage)",
        "trigger_coverage = 0.65 + 0.25 * float(result.indicator_coverage)",
        "facade score_ticker: trigger 覆盖率基数 0.75 -> 0.65",
    ),
    (
        "S11",
        V79,
        "    finite = np.flatnonzero(np.isfinite(values))\n    return float(values[int(finite[-1])]) if finite.size else np.nan",
        "    return last",
        "v79 _latest: 去掉末尾非有限时的回退",
    ),
    (
        "S12",
        V95,
        "    return _scale_dimension(raw, VOLUME_RAW_MAX, VOLUME_NOMINAL_MAX)",
        "    return _scale_dimension(raw * 1.1, VOLUME_RAW_MAX, VOLUME_NOMINAL_MAX)",
        "v95 score_volume: 原始分放大 10%",
    ),
    (
        "S13",
        V95,
        "    return _scale_dimension(raw, ACCUMULATION_RAW_MAX, ACCUMULATION_NOMINAL_MAX)",
        "    return _scale_dimension(raw * 1.1, ACCUMULATION_RAW_MAX, ACCUMULATION_NOMINAL_MAX)",
        "v95 score_accumulation: 原始分放大 10%",
    ),
    (
        "S14",
        ENDPOINT,
        "        points += 15.0 if price > ma20 > ma50 else 8.0 if price > ma20 else 0.0",
        "        points += 16.0 if price > ma20 > ma50 else 8.0 if price > ma20 else 0.0",
        "endpoint v79 breakout_score: 均线多头排列 15 -> 16 分",
    ),
    (
        "S15",
        V80,
        "        price_decimals=price_decimals,\n    )",
        "        price_decimals=None,\n    )",
        "v80 entry_point: 丢弃 price_decimals（价格不再取整）",
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
        try:
            write(path, original.replace(old_e, new_e, 1))
            rc = run_test()
        finally:
            write(path, original)
            if read(path) != original:
                print(f"[FATAL] {tag} 还原失败！文件仍被改动：{path}")
                return 1
        if rc == 0:
            print(f"[BAD]  {tag} {label}: *** NOT CAUGHT ***")
            missed.append(tag)
        else:
            print(f"[GOOD] {tag} {label}: 抓到 (rc={rc})")
            caught.append(tag)

    print(f"\n反向验证: {len(caught)} 抓到 / {len(missed)} 未抓到 / {len(skipped)} 跳过")
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
