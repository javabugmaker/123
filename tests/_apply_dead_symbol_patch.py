"""一次性补丁脚本：清理 3 个死函数 + 接上 performance_curve 契约。

用法:  python tests/_apply_dead_symbol_patch.py            # 预演，只报告
       python tests/_apply_dead_symbol_patch.py --apply    # 落盘

设计要点（与 tests/_strip_module_level_installs.py 一致）：
  * 行级锚点：用「整行内容」定位，不是子串；
  * 断言命中恰好 1 次——命中 0 次说明代码已变，命中多次说明锚点不够独特，
    两种都必须停下来看，绝不猜；
  * 保留 CRLF：以 newline="" 读入、splitlines(keepends=True) 切分、原样写回；
  * 预演优先：默认只打印将要发生的改动，确认无误才 --apply。

删除清单（依据 PROJECT_ANALYSIS.md 第十八节的 AST 级审计）：
  _date_text              fundamental_schema.py   被向量化的 _date_series 取代
  inject_backtest_into_html  backtest_web.py      零引用，与 inject_into_html 无替代关系
  read_backtest_summary   pit_page_semantics.py  零引用
  PERFORMANCE_CURVE_SECTION_ID                   值全库唯一，HTML 锚点从未建立

接上契约（消除 5 处产物名硬编码，值完全相同，故行为不变）：
  performance_curve_runtime.py:36,37,48,54
  performance_curve.py:33,34
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class DeleteBlock:
    """从 start_anchor 所在行起删除 count 行。"""

    rel: str
    start_anchor: str
    count: int
    note: str


@dataclass
class ReplaceLine:
    """整行替换：old 必须命中恰好 1 次。"""

    rel: str
    old: str
    new: str
    note: str


@dataclass
class InsertAfter:
    """在 anchor 行之后插入 lines。"""

    rel: str
    anchor: str
    lines: list[str]
    note: str


DELETES = [
    DeleteBlock(
        rel="institution_scanner/fundamental_schema.py",
        start_anchor="def _date_text(value: Any) -> str:",
        count=5,  # 函数 3 行 + 其后 2 个空行
        note="_date_text：私有标量版，已被 _date_series（向量化）取代",
    ),
    DeleteBlock(
        rel="institution_scanner/backtest_web.py",
        start_anchor="def inject_backtest_into_html(path: Path, json_path: Path) -> bool:",
        count=33,
        note="inject_backtest_into_html：零引用",
    ),
    DeleteBlock(
        rel="institution_scanner/pit_page_semantics.py",
        start_anchor="def read_backtest_summary(output_dir: Path) -> dict[str, Any]:",
        count=9,  # 函数 7 行 + 其后 2 个空行
        note="read_backtest_summary：零引用",
    ),
    DeleteBlock(
        rel="institution_scanner/performance_curve_contract.py",
        start_anchor='PERFORMANCE_CURVE_SECTION_ID = "performance-curves-v1"',
        count=1,
        note="PERFORMANCE_CURVE_SECTION_ID：值全库唯一，锚点从未建立",
    ),
]

REPLACES = [
    ReplaceLine(
        rel="institution_scanner/performance_curve_runtime.py",
        old='            csv_path=root / "PerformanceCurve.csv",',
        new="            csv_path=root / PERFORMANCE_CURVE_CSV_NAME,",
        note="采纳契约：CSV 产物名",
    ),
    ReplaceLine(
        rel="institution_scanner/performance_curve_runtime.py",
        old='            json_path=root / "PerformanceCurve.json",',
        new="            json_path=root / PERFORMANCE_CURVE_JSON_NAME,",
        note="采纳契约：JSON 产物名",
    ),
    ReplaceLine(
        rel="institution_scanner/performance_curve_runtime.py",
        old='    return inject_into_html(Path(page_path), Path(output_dir) / "PerformanceCurve.json")',
        new="    return inject_into_html(Path(page_path), Path(output_dir) / PERFORMANCE_CURVE_JSON_NAME)",
        note="采纳契约：after_page_build",
    ),
    ReplaceLine(
        rel="institution_scanner/performance_curve_runtime.py",
        old='        Path(output_dir) / "PerformanceCurve.json",',
        new="        Path(output_dir) / PERFORMANCE_CURVE_JSON_NAME,",
        note="采纳契约：build_detail_page",
    ),
    ReplaceLine(
        rel="performance_curve.py",
        old='PERFORMANCE_CURVE_CSV = OUTPUT_DIR / "PerformanceCurve.csv"',
        new="PERFORMANCE_CURVE_CSV = OUTPUT_DIR / PERFORMANCE_CURVE_CSV_NAME",
        note="采纳契约：根模块 CSV",
    ),
    ReplaceLine(
        rel="performance_curve.py",
        old='PERFORMANCE_CURVE_JSON = OUTPUT_DIR / "PerformanceCurve.json"',
        new="PERFORMANCE_CURVE_JSON = OUTPUT_DIR / PERFORMANCE_CURVE_JSON_NAME",
        note="采纳契约：根模块 JSON",
    ),
]

INSERTS = [
    InsertAfter(
        rel="institution_scanner/performance_curve_runtime.py",
        anchor="from performance_curve import curve_summary, write_performance_curve",
        lines=[
            "from institution_scanner.performance_curve_contract import (",
            "    PERFORMANCE_CURVE_CSV_NAME,",
            "    PERFORMANCE_CURVE_JSON_NAME,",
            ")",
        ],
        note="导入契约常量",
    ),
    InsertAfter(
        rel="performance_curve.py",
        anchor="from config import OUTPUT_DIR",
        lines=[
            "from institution_scanner.performance_curve_contract import (",
            "    PERFORMANCE_CURVE_CSV_NAME,",
            "    PERFORMANCE_CURVE_JSON_NAME,",
            ")",
        ],
        note="导入契约常量",
    ),
]

# 删完函数后需要收尾的文件：去掉尾部空行，保证以单个换行结束。
# backtest_web.py 原本没有结尾换行（既有 W292），删除末尾函数后顺手补上。
TIDY_TAILS = ["institution_scanner/backtest_web.py"]


def read_lines(rel: str) -> list[str]:
    return (ROOT / rel).read_text(encoding="utf-8", newline="").splitlines(keepends=True)


def write_lines(rel: str, lines: list[str]) -> None:
    (ROOT / rel).write_text("".join(lines), encoding="utf-8", newline="")


def find_exactly_once(lines: list[str], anchor: str, rel: str) -> int:
    """行级锚点：命中必须恰好 1 次。"""
    hits = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == anchor]
    if len(hits) != 1:
        raise SystemExit(
            f"[FATAL] {rel}: 锚点命中 {len(hits)} 次（应为 1）：{anchor!r}\n"
            "        代码已变或锚点不够独特，请人工确认后再跑。"
        )
    return hits[0]


def tidy_tail(lines: list[str]) -> list[str]:
    """去掉尾部空行，确保最后一行以单个换行结束。"""
    while lines and lines[-1].strip() == "":
        lines.pop()
    if not lines:
        return lines
    eol = "\r\n" if lines[-1].endswith("\r\n") else "\n"
    lines[-1] = lines[-1].rstrip("\r\n") + eol
    return lines


def main() -> int:
    apply = "--apply" in sys.argv
    mode = "落盘" if apply else "预演"
    print(f"=== 死符号清理补丁（{mode}）===\n")

    touched: dict[str, list[str]] = {}

    def note(rel: str, msg: str) -> None:
        touched.setdefault(rel, []).append(msg)

    # 1) 删除块
    for item in DELETES:
        lines = read_lines(item.rel)
        idx = find_exactly_once(lines, item.start_anchor, item.rel)
        end = idx + item.count
        # 边界校验：确认删除范围没有越过下一个 def/顶层语句
        block = lines[idx:end]
        if apply:
            del lines[idx:end]
            if item.rel in TIDY_TAILS:
                lines = tidy_tail(lines)
            write_lines(item.rel, lines)
        note(item.rel, f"删除 {item.count} 行 @L{idx+1}  {item.note}")
        print(f"  [删除] {item.rel}:L{idx+1}  -{item.count} 行  ({item.note})")
        for b in block[:3]:
            print(f"          - {b.rstrip()}")

    # 2) 整行替换
    for item in REPLACES:
        lines = read_lines(item.rel)
        idx = find_exactly_once(lines, item.old, item.rel)
        if apply:
            eol = "\r\n" if lines[idx].endswith("\r\n") else "\n"
            lines[idx] = item.new + eol
            write_lines(item.rel, lines)
        note(item.rel, f"替换 L{idx+1}  {item.note}")
        print(f"  [替换] {item.rel}:L{idx+1}  ({item.note})")
        print(f"          - {item.old}")
        print(f"          + {item.new}")

    # 3) 插入 import（放在替换之后，避免打乱锚点行号）
    for item in INSERTS:
        lines = read_lines(item.rel)
        idx = find_exactly_once(lines, item.anchor, item.rel)
        eol = "\r\n" if lines[idx].endswith("\r\n") else "\n"
        block = [line + eol for line in item.lines]
        if apply:
            lines[idx + 1 : idx + 1] = block
            write_lines(item.rel, lines)
        note(item.rel, f"插入 {len(block)} 行 @L{idx+2}  {item.note}")
        print(f"  [插入] {item.rel}:L{idx+2}  +{len(block)} 行  ({item.note})")

    print(f"\n共涉及 {len(touched)} 个文件：")
    for rel in sorted(touched):
        print(f"  {rel}  ({len(touched[rel])} 处改动)")
    print("\n" + ("已落盘。请跑 ruff + pytest，并全文读一遍 diff。" if apply else "预演结束，未修改任何文件。加 --apply 落盘。"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
