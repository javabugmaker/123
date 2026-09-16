# 完成度评估与待办清单

> 评估基准：`main` @ `6ce9c99`（工作区仅有一个未跟踪目录 `.workbuddy-ai/`，无未提交改动）
> 本文为**只读评估产物**，未修改任何源码。所有结论都附了可复核的判据（文件:行 / 命令输出）。

> ⚠️ **本文已过期，勿据此排期。** 基准是 `6ce9c99`，2026-09-16 复查时 HEAD 已到
> `9e957f8`（相隔 15 个提交）。已实测失效的条目：F1（v90 分支早已处置，本地分支已删）、
> B3（`retention-days: 7` 已在 `daily-pages.yml:89`）、T3/D1（预算数字已与
> `_SIZE_BUDGETS` 对齐）、T6（根因记错且原修法不可行，见 `REFACTOR_PLAN.md` §9.14）。
> **当前未闭合项以 `REFACTOR_PLAN.md` 的 §9.12 / §9.13 / §9.14 / §10.7 为准。**

## 更新：部署目标已确认为 GitHub Pages

交付物是 <https://javabugmaker.github.io/123/> 静态站点，源码在
<https://github.com/javabugmaker/123/>。据此已执行下列项，正文中以 ✅ 标注：

| 项 | 处置 |
| --- | --- |
| T1 日更链路回归保护 | ✅ 新增 `tests/test_daily_pipeline_publication.py`（8 用例）+ 子进程探针 |
| T2 `.staging` 原子切换 | ✅ 同上，覆盖隔离 / 回滚 / 原子发布 |
| T3 + D1 预算文档与代码一致 | ✅ `ARCHITECTURE.md` 六个上限已同步，并写明"代码才是权威来源" |
| F3 pyproject 死配置 | ✅ 删除两个不存在的 `tools/*.py` 排除项 |
| B1 打包形态 | ✅ 结论为**不发包**，已在 `pyproject.toml` 写明理由 |
| B2 GUI 入口保护 | ✅ `gui_v85` 已加入 Windows smoke 导入检查 |
| D2 `启动研究终端.bat` | ❌ **撤回**，是误报，见下文 |
| （新发现）字节预算与换行符不兼容 | ✅ 已修，见下文「新发现」——**这一条比上面任何一项都严重** |

### 新发现（干净树验证抓到的，比待办清单里任何一项都严重）

`ARCHITECTURE.md` 的预算数字对齐之后，我做了一次干净树验证
（`git clone` 到临时目录跑全量），结果 `analytics_core.py` **超预算 1,065 字节**。
根因不是它变大了，而是 **`core.autocrlf` 让索引里的 LF 文件（142,583 B）
在检出后变成 CRLF（146,065 B，每行多 1 字节）**，而 145,000 这个预算是
`4f927be` 从 LF 工作副本冻结的。后果：

- **任何新克隆都是红的**（工作区绿、克隆红）—— 也就是说 `4f927be` 那次
  "217 passed" 只在作者自己的机器上成立；
- Linux runner（LF）与 Windows runner（CRLF）对同一份代码**判定互相矛盾**。

已修：两处预算闸门改为测量**归一化字节数**（CRLF 与 LF 计为相同）。
口径一变，又连带暴露三个真实的虚假宽松预算并已收紧
（`report_core` 95,000→91,752、`gui.py` 98,000→94,018、
`signal_lifecycle_core` 56,000→53,345）。

**连带推翻一个旧结论**：多轮记录都把 `gui_core.py` 当"只剩 134 字节"的下一个
窒息点 —— 那也是 CRLF 造成的假象。归一化后余量是 **2,648 字节（约 65 行）**，
所以 F6 的 gui_core 拆分从"被预算倒逼"降回普通优化。

---

**一次误报（值得记）**：D2 原判"README 指向不存在的 bat"。复核发现
`git ls-files | grep '\.bat$'` 匹配不到，是因为 **git 默认把非 ASCII 路径转义成
八进制**（`core.quotePath`），实际输出是 `"\345\220\257...bat"` 带引号结尾。
用 `git -c core.quotePath=false ls-files` 确认该文件**已在库中**。
这是本项目第 7 次"审计工具被自己的假设绕过"。

---

## 0. 总体结论

**这是一个"已经在真实运行、且工程质量明显高于平均水平"的项目，但正处在重构收尾期：主干功能齐备，历史欠账尚未结清。**

| 维度 | 完成度 | 一句话判断 |
| --- | --- | --- |
| 功能完整性 | ~85% | CLI / GUI / 日更流水线 / Pages 发布 / 影子模型研究通道五条主线都通；欠的是 overlay 债收敛与一个悬空实验分支 |
| 测试与质量控制 | ~70% | 217 用例全绿、ruff/pyright/compileall/双 CI 齐全；但约 400 KB 大模块零行为测试，其中 `daily_pipeline_core.py` 每个交易日都在 CI 上跑 |
| 构建与部署 | ~75% | Dockerfile / compose / 2 个 workflow / 权限分离都做了；缺 `[project]` 打包元数据，README 教了一个 CI 不保护的入口 |
| 文档说明 | ~75% | 5 份文档、README 14 节；但 `ARCHITECTURE.md` 的预算数字已与门禁代码矛盾，README 还指向一个不存在的文件 |

**最需要警惕的三件事**（其余都是可排期的改进）：

1. `ARCHITECTURE.md` 的预算上限是**宽松的旧值**，和 `tests/test_architecture_growth.py` 里收紧后的真实预算不一致 —— 文档会让人误判还有余量。（T3/D1）
2. `output/.staging` 原子切换是 README 承诺的"安全发布"核心机制，但**零测试覆盖**。（T2）
3. `daily_pipeline.py` 每天在 CI 上真实运行，本地 217 个测试**完全覆盖不到它**。（T1）

---

## 一、功能完整性

### P0（发布前必须）

#### F1. 处置悬空分支 `experiment/five-factor-resonance-v90`
- **做什么**：三选一并写进记录 —— 合入 / 丢弃 / 继续孵化。若丢弃，删掉本地分支防误合；若合入，需 rebase 到 `main` 后补 golden 等价测试 + 在 `_SIZE_BUDGETS` 里给新文件加预算。
- **涉及文件**：`technical_resonance_v90.py`（新增 367 行）、`analytics.py`（+148/-5）、`tests/test_v90_technical_resonance.py`（新增 103 行）、`tests/test_architecture_growth.py::_SIZE_BUDGETS`
- **判断依据**：`git merge-base --is-ancestor experiment/five-factor-resonance-v90 main` 返回 false；`git diff --stat main...` 显示 3 文件 +613 行；该分支**落后 `main` 179 个提交**。它新增的 `technical_resonance_v90.py` 会落在根级，按现有门禁规则必须登记预算，否则 `test_every_large_root_module_has_a_budget` 会红。

#### F2. 书面确认 AuctionStructure 影子模型"不转正"这一发布口径
- **做什么**：本版本以 `production_applied=False` 发布，需要确认 (a) README §129 的描述与 CLI 输出一致；(b) 下游/网页若消费 `AuctionStructureProductionApplied` 列，`False` 不会被误读为"运行失败"。
- **涉及文件**：`institution_scanner/auction_structure.py:30, 1398, 1435, 1480, 1642`、`institution_scanner/auction_structure_cli.py:45, 236`、`tests/test_auction_structure_shadow.py:72, 97, 298`、README §129
- **判断依据**：`auction_structure.py:30` 为 `MODEL_PRODUCTION_APPLIED: Final = False`，且测试里有**三处断言它必须为 False** —— 说明这是刻意为之，不是缺陷。但 `ARCHITECTURE.md` §Release gates 要求"真正的模型变更必须先 shadow observation 再 champion promotion"，所以"长期停在 shadow"是否是终态，需要你确认。

#### F3. ✅ 已完成：删掉 `pyproject.toml` 里两个指向不存在文件的 ruff 排除项
- **做什么**：删除 `extend-exclude` 中的 `tools/apply_project_hardening.py` 和 `tools/v34_migrate.py`；或者把这两个脚本找回并入库。
- **涉及文件**：`pyproject.toml`
- **判断依据**：`git ls-files | grep tools/` 无输出，仓库里没有 `tools/` 目录。这是"死配置"，会误导后来者以为该工具仍在生效。

### P1 / P2（可选优化）

#### F4. 继续收敛 v113 遗留 overlay 债
- **做什么**：下一个候选是 `scanner_resume_v59`（先把 checkpoint/crash 语义做成 golden fixture），其余小 wrapper 沿用 T3′/T4 的同一套流程：冻结不可变 SHA → 运行时 golden 回放 → 反向验证证明闸门会咬。
- **涉及文件**：`scanner_resume_v59.py` 等根级 `*_vNN.py`、`tests/golden_*.py`、`tests/test_architecture_growth.py`
- **判断依据**：`ARCHITECTURE.md` §Compatibility-debt policy 明确"大内核如 `scanner_resume_v59` 在有完整等价测试保护其 checkpoint/crash 语义之前保留"；根级仍有 122 个 `.py`、约 68 个 overlay。

#### F5. 清理根级 28 组重复函数体（含 `_bool` / `_truthy`）
- **做什么**：先区分"真重复"与"刻意保留的兼容副本"；对后者加显式白名单注释，而不是删。
- **涉及文件**：重复体门禁测试 + 各根级 `*_vNN.py`
- **判断依据**：`MIN_DUPLICATE_CHARS = 60` 且哈希**包含 docstring**，所以"相同逻辑 + 不同 docstring"不会被判为重复 —— 这意味着实际重复量可能比 28 组更多，需要先做一次带 docstring 归一化的统计再决定。

#### F6. 拆分 `gui_core.py` —— 优先级已下调（原判据是换行符假象）
- **做什么**：按 T4 同款流程拆；先跑已修好（类方法不再失明）的 recon。
- **涉及文件**：`gui_core.py`、`tests/recon_extraction_targets.py`
- **判断依据（已修正）**：原判"剩余余量仅 134 字节"用的是 CRLF 原始尺寸
  104,866 对预算 105,000。**按归一化口径余量是 2,648 字节 ≈ 65 行**，
  不再是被预算倒逼的紧急项。30 次提交内增长 +2,869 字节仍然属实，
  所以它是"确实在长、值得排期"，但**应排在 T4（大模块补测试）之后**。

---

## 二、测试与质量控制

### P0（发布前必须）

#### T1. ✅ 已完成：给 `daily_pipeline_core.py` 补契约测试
- **落地**：`tests/test_daily_pipeline_publication.py`（8 用例）+ `tests/daily_publication_probe.py`
  （子进程探针）。8 个注入故障全部反向验证咬住，225 passed。
- **做什么**：把一次真实运行的产物（`LatestRun.json`、`DailyRunSummary.json`、`PublicCandidates.csv` 的关键列）做成 fixture，断言"相同输入 → 相同汇总"。最低限度也要先做"可导入 + 关键函数 smoke + 产出契约字段齐全"三层。
- **涉及文件**：`daily_pipeline_core.py`、`daily_pipeline.py`、新建 `tests/test_daily_pipeline_contract.py`
- **判断依据**：`.github/workflows/daily-pages.yml:61` 每个交易日执行 `python daily_pipeline.py --backtest-mode fast`；但在 `tests/` 里搜 `daily_pipeline` 只命中 `assembly_manifest.py`、两个 `assembly_manifest_*.json` fixture、`test_architecture_growth.py`、`_strip_module_level_installs.py` —— **全是结构与预算，没有一条行为断言**。文件 42,270 字节。这意味着这条唯一的真实执行路径，回归只能靠"线上跑挂了才发现"。

#### T2. ✅ 已完成：补 `output/.staging` 原子切换的测试
- **落地**：同上测试文件，覆盖 staging 目录位置、种子文件不被污染、发布无残留临时文件、
  空 staging 拒绝发布、run id 复用被拒、写入路径重定向与还原。
- **做什么**：断言三件事 —— (a) 运行期间正式输出目录不被写入；(b) 完整性校验失败时不发生切换；(c) `LatestRun.json` 只在切换成功后更新。
- **涉及文件**：负责 staging 的模块（在 `daily_pipeline_core.py` 链路内）、`tests/test_publication_contract.py`（已含 `LatestRun`，可扩展）
- **判断依据**：README §183 承诺"日更先在 `output/.staging/<RunId>/` 完成扫描、回测和完整性校验，再切换正式结果；运行中 GUI 固定读取 `LatestRun.json` 指向的上一份不可变快照"。但 `grep -rl "\.staging" tests/*.py` **返回空**。这是 README 主打的安全发布机制，却没有任何回归保护；一旦坏掉，GUI 会读到半写状态。

#### T3. ✅ 已完成：让预算文档与门禁代码不再互相矛盾
- **落地**：`ARCHITECTURE.md` 六个上限已改为与 `_SIZE_BUDGETS` 一致，并补上
  「权威表在代码里、这里是摘要」的说明 + `scanner.py` 幽灵余量的教训。
- **做什么**：二选一。最低成本是更新 `ARCHITECTURE.md` 的六个数字；更好的做法是加一个测试，从 `ARCHITECTURE.md` 解析上限并与 `_SIZE_BUDGETS` 比对，让文档或代码成为唯一事实来源。
- **涉及文件**：`ARCHITECTURE.md` §Shrink-only legacy budget、`tests/test_architecture_growth.py::_SIZE_BUDGETS`
- **判断依据**：

  | 模块 | 文档写的上限 | 门禁实际预算（4f927be 后） |
  | --- | --- | --- |
  | `analytics_core.py` | 160 KB | **145,000** |
  | `report_core.py` | 105 KB | **95,000** |
  | `gui_core.py` | 105 KB | 105,000（一致） |
  | `gui.py` | 100 KB | **98,000** |
  | `scanner.py` | 80 KB | **2,048** |
  | `signal_lifecycle_core.py` | 70 KB | **56,000** |

  文档是**更宽松**的那一侧，且 `scanner.py` 差了两个数量级（幽灵余量）。

### P1

#### T4. 给其余 8 个零行为测试的大模块补测试（按是否被 CI 执行排序）
- **做什么**：先做被 `publish_site` 真实调用的 `web_report_*`，再做回测链路，最后 `gui.py`。
- **涉及文件**：`gui.py` 94,033；`web_report_v85.py` 50,136；`web_report_v84.py` 46,307；`backtest_vectorization_v98.py` 42,542；`backtest_fastscore_v80.py` 34,098；`model_calibration.py` 32,222；`web_report_v93.py` 31,638；`model_audit.py` 26,973（合计约 358 KB）
- **判断依据**：这些模块在 `tests/` 里只出现在预算表和名字清单中。`tests/test_gui_view_model.py` 只覆盖 `gui_view_model`，**不覆盖** `gui.py` / `gui_core.py`；`tests/test_e2e_contract_pipeline.py` 只覆盖 `reliability` / `verify_output`。

#### T5. 用"变更热度"而不是"文件体积"来决定测试投入顺序
- **做什么**：正式排 T4 的工期前，先跑一次近 30 次提交的 `--stat` 热度统计，按"谁在长"而不是"谁最大"排序。
- **涉及文件**：一次性统计脚本（可放 `tests/` 下作为数据依据）
- **判断依据**：`4f927be` 已用数据**否证**了按体积选目标的思路 —— 体积最大的 5 个未预算模块里有 4 个零变更，反而是体积不起眼的 `fundamental_quality.py` 悄悄涨了 +6,481 字节。测试优先级大概率存在同样的误判。

#### T6. 清掉 pandas timedelta 弃用告警
- **做什么**：在相关 resample/时间分组处显式指定 `unit`。
- **涉及文件**：`tests/test_auction_structure_shadow.py`（13 条告警来源）及其调用的分组代码
- **判断依据**：`pandas/core/resample.py:2359` 的 `DeprecationWarning: The 'generic' unit for NumPy timedelta is deprecated, and will raise an error in the future`。现在是警告，下一次依赖升级就会变成硬错误。

#### T7. 让本地测试可稳定复现
- **做什么**：把 `--basetemp` 固定到仓库外的稳定路径，或改用 CI-only 复现；并把偶发 ERROR 的根因（`_safe_shutil_rmtree` / `[WinError 6] 句柄无效`）记录下来。
- **涉及文件**：本地运行约定（可写入 `ARCHITECTURE.md` 或 `CONTRIBUTING`）
- **判断依据**：本次评估中连续跑 5 次完整套件，第 2、3 次分别出现 `test_universe_snapshot_io.py` 与 `test_signal_lifecycle_extraction_equivalence.py` 的 `ERROR`，报错模块**每次都不一样**，单独重跑必过，第 4、5 次又全绿。属环境型抖动，不是仓库缺陷，但会持续消耗判断成本。

### P2

#### T8. pyright 双配置逐步收敛
- **做什么**：评估是否把 `pyrightconfig.gui.json` 的严格项逐步推广到全项目。
- **涉及文件**：`pyrightconfig.json`（9 类 `report*` 全设为 `none`）、`pyrightconfig.gui.json`（仅 4 个 gui 文件开 `reportAttributeAccessIssue=error`）
- **判断依据**：主配置把 `reportAttributeAccessIssue`、`reportCallIssue`、`reportOptional*` 等 9 类全部关成 `none`，实际只跑最基础的 `basic`。收紧收益大但噪声也大，建议作为长期项。

---

## 三、构建与部署

### P0（发布前必须）

#### B1. 决定打包形态 —— ✅ 已定为「不发包」，理由已写进 `pyproject.toml`
- **结论**：交付物是 GitHub Pages 静态站点，不是可分发的 Python 包。
- **决定性依据（比"没有 `[project]`"更硬）**：271 个入库 `.py` 里有 **122 个在仓库根级**，
  只有 `institution_scanner/`（以及根级 `main.py` → `scanner` → `analytics` → `report`
  这条 facade 链）之外的部分在包内。任何从包目录构建的 wheel 都会**缺掉扫描器的大部分实现**，
  `pip install` 后必然 ImportError。所以加 `[project]` 不是"补个元数据"，而是先要做完
  T5（overlay 收敛）——那是个大工程，且收益与"发布静态站点"这个目标无关。
- **已做**：在 `pyproject.toml` 顶部写明"没有 `[project]` 是刻意的"及源码运行方式，
  避免后来者把它当成遗漏补上。
- **代价（接受）**：无版本号、无 console_scripts、`constraints-ci.txt` 只约束 CI 安装。
  若将来需要版本可观测，走 `institution_scanner.version_manifest`（已存在）而非打包。

#### B2. ✅ 已完成：让 README 教的 GUI 入口进入 CI 保护范围
- **做什么**：二选一 —— (a) 把 GUI 入口的导入加入 `static-quality.yml` 的 Windows smoke；(b) README 改为推荐受 CI 保护的入口。
- **涉及文件**：`.github/workflows/static-quality.yml`、`README.md` §84-95
- **判断依据**：README §87 让用户执行 `python gui_v85.py`；而 Windows smoke job 的导入检查只覆盖 `main, daily_pipeline, web_report_v81`（`static-quality.yml:54`），**不含任何 `gui*`**。也就是说，README 主推的 GUI 路径完全在 CI 视野之外。

### P1

#### B3. 确认 Pages 发布链路的产物保留期与凭据
- **做什么**：给 `upload-artifact@v4` 显式设置 `retention-days`（默认 90 天），并确认 `gh-pages` 分支推送凭据与 `workflow` 权限配置。
- **涉及文件**：`.github/workflows/daily-pages.yml`
- **判断依据**：该 workflow 已正确拆成 compute / publish 两个 job（权限分离到位），通过 artifact 传递产物；但未显式设置保留期，`publish` 失败后回溯窗口取决于默认值。

### 已确认没问题、无需处理
- `Dockerfile` 的 `HEALTHCHECK` 是**轻量导入探针**（`python -c "import requests,pandas,numpy; print('OK')"`），不会触发真实扫描，无副作用；镜像也已用非 root 用户 `scanner`。
- CI 覆盖面是完整的：ruff、canonical 包严格 ruff（`B,C4,SIM,PERF,PIE`）、pyright ×2、pytest、compileall、Windows smoke、每日 Pages（compute/publish 权限分离）。

---

## 四、文档说明

### P0（发布前必须）

#### D1. 修正 `ARCHITECTURE.md` 过期的预算数字
- 与 T3 是同一件事的文档侧，必须一起做。见上文表格。

#### ~~D2. 修掉 README 指向不存在文件的启动说明~~ —— 已撤回（误报）
- **撤回原因**：`git ls-files` 默认启用 `core.quotePath`，非 ASCII 路径会被转义为
  `"\345\220\257\345\212\250\347\240\224\347\251\266\347\273\210\347\253\257.bat"`，
  末尾带引号，所以 `grep '\.bat$'` 匹配不到。
  `git -c core.quotePath=false ls-files` 确认 **`启动研究终端.bat` 已在库中**，
  是全库唯一的非 ASCII 文件名。README 无误，无需改动。
- **教训**：任何用 `git` 输出做文件名断言的脚本，都必须先关掉 `core.quotePath`。

#### D3. 补"如何判断一次运行是成功的"
- **做什么**：在 README 增一节排查清单，至少包含：首次运行会建约 10 年历史缓存（现在只在 §39 一笔带过）、日志位置、以及"可以自己跑 `python -m institution_scanner.verify_output output` 复核"（这个能力 CI 天天在用，但 README 没告诉用户）。
- **涉及文件**：`README.md`（当前 14 节，无 Troubleshooting / FAQ）
- **判断依据**：README 有 §安装 / §运行 GUI / §CLI / §输出 / §结果契约与安全发布，但没有"失败了怎么办"。`verify_output` 已存在于 `institution_scanner` 包且被 CI 调用，属"已有能力没写进文档"。

### P1 / P2

#### D4. 抽离 `PROJECT_ANALYSIS.md` 的「可复用做法」
- **做什么**：把已积累的 20 条可复用做法抽成独立文档（或并入 skill），`PROJECT_ANALYSIS.md` 只留逐次记录。
- **判断依据**：`PROJECT_ANALYSIS.md` 已到 §26，既是历史记录又承担方法论手册，检索成本高。

#### D5. 补 `CONTRIBUTING` / 分支与发布流程说明（可选）
- **判断依据**：仓库有 11 个远端分支、1 个未合入的本地实验分支、无 `CONTRIBUTING.md`，也没有任何 issue/需求记录在树内。

---

## 五、需要你补充的信息（这几条会实质改变优先级）

1. ~~**部署目标是什么？**~~ **已答**：GitHub Pages（`javabugmaker.github.io/123`），
   源码在 `github.com/javabugmaker/123`。据此 B1 定为不发包、B2 已执行。
2. ~~**"上线"指什么？**~~ **部分已答**：交付物是日更静态站点。
   仍需确认的是——**这个站点是已经对外公开、还是仅供自己看**？
   这决定 T6（pandas 弃用告警）和 T4（web_report_* 测试）的紧迫程度：
   若只是自用，站点挂一天无所谓；若已有读者，则 `web_report_v85/v84/v93`（约 128 KB、
   零行为测试）应提到 P0。
3. **`experiment/five-factor-resonance-v90` 怎么处置？** 合入 / 丢弃 / 继续孵化。
4. **AuctionStructure 影子模型本版本是否计划转正？** 若不转，是否有下游在消费 `AuctionStructureProductionApplied` 这一列？
5. **是否有仓库外的 issue tracker 或需求清单？** 仓库内 **0 条** `TODO` / `FIXME` / `XXX` / `HACK` / `NotImplemented` 注释，也没有 `docs/issues` 之类的目录 —— 我无法判断"计划中但尚未做"的功能有哪些。如果有，请给我，我会据此重排整个清单。
6. **`main.py` 兼容门面是否仍需保留？** 它导入 `workstation_runtime_v77` 等一批根级模块，是 overlay 架构的入口之一，也是 Windows smoke 的检查对象。
7. **`启动研究终端.bat` 还需要维护吗？** 如果它是仓库外分发的，README 里应注明获取方式。

---

## 附：本次评估的可复核基线

| 项 | 数值 |
| --- | --- |
| 提交 | `main` @ `6ce9c99`，工作区干净（仅 `.workbuddy-ai/` 未跟踪） |
| 测试 | 75 个 `test_*.py`，217 个用例，本次评估中 5 次运行 3 次全绿（2 次为环境型偶发 ERROR，见 T7） |
| ruff | `ruff check .` 全过 |
| 代码规模 | 271 个入库 `.py`，其中根级 122 个 |
| CI | 2 个 workflow，Python 3.11 |
| 代码内遗留标记 | `TODO` / `FIXME` / `XXX` / `HACK` / `NotImplemented` **全部为 0** |
