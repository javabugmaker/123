"""Recon 自检必须接进测试套件，否则它只是个"记得跑才有用"的工具。

``recon_extraction_targets.py`` 的输出是"这个函数能不能搬"。它曾经在
``gui_core`` 上给出"被 overlay 换掉的：无"的结论，而运行时实际有四个符号
被换掉——其中两个是类方法，扫描根本没往 ``ClassDef`` 里看。那个假阴性恰好
落在预算只剩 134 字节的模块上，是最不该出错的地方。

修好视力只解决一半问题：如果没人每次都跑自检，它会再次悄悄失明。所以这里
既验证自检通过，也验证**注入失明后自检会失败**——后者才证明那两个已知正例
真的在被检查，而不是摆设。
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tests" / "recon_extraction_targets.py"

#: 没有这两个，GUI 装配路径起不来，自检只能跳过 gui_core，反向验证也就无从谈起。
GUI_REQUIREMENTS = ("tkinter", "customtkinter")


def _run(extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, str(TOOL), "--selfcheck"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
        timeout=300,
    )


def _gui_available() -> bool:
    return all(importlib.util.find_spec(m) is not None for m in GUI_REQUIREMENTS)


def test_recon_selfcheck_finds_every_known_positive() -> None:
    proc = _run()
    assert proc.returncode == 0, (
        "recon 自检失败——扫描已失明，它对「可搬 / 不可搬」的结论不可信:\n" + proc.stdout + proc.stderr
    )


@pytest.mark.skipif(
    not _gui_available(),
    reason=f"需要 {GUI_REQUIREMENTS} 才能装配 GUI 路径",
)
def test_blinding_the_scan_makes_the_selfcheck_fail() -> None:
    """反向验证：故意弄瞎"扫类方法"这条通路，自检必须报红。

    不报红就说明 KNOWN_PATCHED 里那两个 gui_core 正例从来没被真正检查过。
    """
    proc = _run({"RECON_REVERSE_CHECK": "1"})
    assert proc.returncode != 0, (
        "注入失明后自检仍然通过：gui_core 的正例没有真正被检查，这个闸门是装饰品。\n" + proc.stdout
    )
    assert "ScannerGUI._cancel_process" in proc.stdout, (
        "自检失败了，但不是失败在预期的正例上——检查它到底在报什么:\n" + proc.stdout
    )
