"""Reverse validation for tests/test_assembly_manifest.py.

A contract that stays green while the assembly it describes changes is worse
than no contract — it buys false confidence.  Each injection below perturbs one
structural property of the import-time assembly; the manifest suite MUST go red.

The eight injections deliberately hit *different* assertions:

===== ==================================================== =========================
Tag   Perturbation                                         Assertion that must fire
===== ==================================================== =========================
A1    an install rebinds a symbol to a different function  rebinds / final provenance
A2    an install rebinds one more symbol                   rebinds ("now also ...")
A3    a central assembly call is removed (`score.py`)      install_calls + final
A4    two central assembly calls swap order                step order
A5    an overlay is dropped from `analytics_runtime`       install_calls + final
A6    a redundant assembly call is added                   install_calls
A7    an overlay is dropped from `backtest_acceleration_v77` install_calls + final
A8    a module-level `install()` grows back                static call-site list
===== ==================================================== =========================

A5-A7 previously targeted the overlays' *own* module-level ``install()`` calls.
Stage 3 removed those, which made the anchors dead — a useful reminder that a
reverse-validation suite has to be re-aimed whenever the thing it guards is
restructured, or it quietly becomes decoration.  They now target the central
assembly points that own those overlays, and A8 is new: it puts a module-level
``install()`` back to prove the Stage 3 "no regression" gate actually bites.

!!! WARNING !!!
This script **temporarily rewrites production sources** and restores them in a
``finally`` block.  Never run it while another test run or a scan is in
progress.  If it is killed mid-run, check ``git status`` for
score_weight_cache_v79.py / score.py / backtest_acceleration_v77.py /
institution_scanner/analytics_runtime.py first.

Usage:  python tests/reverse_validate_assembly_manifest.py
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = sys.executable
TEST = "tests/test_assembly_manifest.py"

WEIGHT_CACHE = ROOT / "score_weight_cache_v79.py"
SCORE_FACADE = ROOT / "score.py"
V77 = ROOT / "backtest_acceleration_v77.py"
ANALYTICS_RUNTIME = ROOT / "institution_scanner" / "analytics_runtime.py"

INJECTIONS: list[tuple[str, pathlib.Path, str, str, str]] = [
    (
        "A1",
        WEIGHT_CACHE,
        "    _score.invalidate_model_weight_cache = invalidate_model_weight_cache",
        "    _score.invalidate_model_weight_cache = model_component_weights",
        "install(): 把一个符号改成指向另一个函数（provenance 变化）",
    ),
    (
        "A2",
        WEIGHT_CACHE,
        "    _score.invalidate_model_weight_cache = invalidate_model_weight_cache\n",
        "    _score.invalidate_model_weight_cache = invalidate_model_weight_cache\n"
        "    _score._probe_extra_rebind = invalidate_model_weight_cache\n",
        "install(): 多替换一个符号",
    ),
    (
        "A3",
        SCORE_FACADE,
        "_threshold_migration_v95.install(_config)\n",
        "# _threshold_migration_v95.install(_config)\n",
        "score facade: 去掉阈值迁移的模块级 install（config 常量不再迁移）",
    ),
    (
        "A4",
        V77,
        "_cache_acceleration.install()\n_incremental.install()\n",
        "_incremental.install()\n_cache_acceleration.install()\n",
        "backtest_acceleration_v77: 两个模块级 install 调用顺序互换",
    ),
    (
        "A5",
        ANALYTICS_RUNTIME,
        "    _runtime_v83.install()\n",
        "",
        "analytics_runtime: 集中装配点漏掉 runtime_v83",
    ),
    (
        "A6",
        ANALYTICS_RUNTIME,
        "    _postprocess_performance.install()\n",
        "    _postprocess_performance.install()\n    _scoring_consistency.install()\n",
        "analytics_runtime: 多加一次冗余装配调用",
    ),
    (
        "A7",
        V77,
        "    _tradeability_v80.install()\n",
        "",
        "backtest_acceleration_v77: 集中装配点漏掉 tradeability_v80",
    ),
    (
        "A8",
        WEIGHT_CACHE,
        "    _INSTALLED = True\n",
        "    _INSTALLED = True\n\n\ninstall()\n",
        "score_weight_cache_v79: 模块级 install() 死灰复燃（阶段 3 防回退闸门）",
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
