# InstitutionScanner 全面项目分析

> 分析时间：2026-09-14 · 分析基线：git HEAD `ca5a36b`（工作区干净，无未提交改动）
> 说明：本文所有数字均来自本地实测（ruff 0.16.7 / pytest / AST 扫描），
> 推断性结论已显式标注「推断」，实测结论给出可复现命令。

---

## 一、项目概览

| 指标 | 数值 | 来源 |
|---|---|---|
| Python 总行数 | 63,758 行 | `find . -name "*.py" \| xargs wc -l` |
| 模块总数 | 177 个（根级 121 + 规范包 56） | AST 扫描 |
| 版本覆盖层 | 73 个 `*_vXX.py` | `ls *_v*.py` |
| `install()` 补丁函数 | 63 个定义 / **57 处模块级自动调用**（别名解析后实测，见 15.3） | AST + 运行时追踪 |
| 测试 | 64 个文件 / 151 个测试函数 | pytest 收集 |
| 本地数据体积 | cache 18G + output 9.4G + logs 181M（均已 gitignore） | `du -sh` |
| 仓库本体 | .git 仅 15M | `du -sh .git` |

**一句话定位**：A 股 / ETF 日频量化扫描器——TickFlow 取行情、AKShare 取财报，
走「指标 → 筛选 → 评分 → 买点 → 回测 → 排名 → 发布」流水线，
产出 TradeReady 候选页。金融语义治理（PIT、ROE 口径、涨跌停、流动性容量）是它真正的复杂度所在。

**最独特的架构特征**：它不是一个正常的 Python 包，而是一套
**「薄门面 + 版本覆盖层猴子补丁」**的演进式架构。新代码进 `institution_scanner/`，
历史补丁冻结为根目录 `*_vXX.py`，通过 import 期 `install()` 覆盖门面模块属性来生效。

---

## 二、项目结构与模块划分

### 2.1 四层结构

```
入口层    __main__.py / cli.py → main.py（兼容门面）→ daily_pipeline.py
门面层    analytics.py  scanner.py  score.py  report.py  downloader.py  filters.py
          ↑ 薄转发，末行 sys.modules[__name__] = _core 把自己替换成 *_core
覆盖层    73 个 *_vXX.py，62 个 install()，由 analytics_runtime.py 编排
核心层    analytics_core(157KB) scanner(76KB) report_core(102KB) gui_core(102KB)
          signal_lifecycle_core(66KB) score_core(43KB)
规范包    institution_scanner/ 56 模块 —— 新代码唯一归属地
```

### 2.2 覆盖层如何装载（实测）

编排中枢 `institution_scanner/analytics_runtime.py`（117 行）显式 import 18 个内核，
在 `install_pre_facade()`（:57）与 `install_post_facade()`（:85）中按顺序调用各 `install()`，
用 `_PRE_INSTALLED` / `_POST_INSTALLED` 全局量做幂等。
调用点在门面层：`analytics.py:32` 装前置、`analytics.py:449` 装后置，形成两阶段顺序。

补丁写法是**裸属性覆盖**，例如：

- `runtime_v83.py:36` → `module.finalize_signal_ranking = layered_finalize`
- `backtest_math_integrity_v94.py:165-177` → 覆盖 `model_calibration._prepare_samples` 等
- `backtest_vectorization_v98.py:1096-1106` → 覆盖 `_core._backtest_one_ticker`

**关键风险点**：**57 处**模块级直接 `install()`（如 `runtime_v83.py:54`、
`scanner_resume_v59.py:741`），即 **import 即产生全局副作用**。其中 53 处在四个生产
入口下确实会执行，4 处执行不到（见 15.7）。
这带来三个后果：装载顺序敏感、无法在单进程内并行/隔离测试、
静态分析（Pyright）看不到补丁后的真实签名。

### 2.3 模块划分（按职责）

| 职责 | 核心模块 |
|---|---|
| 配置与契约 | `config_core.py`（权重/阈值真源）、`institution_scanner/contracts.py`、`policy_manifest.py` |
| 数据获取 | `downloader_core.py`（TickFlow）、`institution_scanner/fundamentals.py`、`akshare_fundamentals.py` |
| 技术指标 | `indicators.py` + `indicator_acceleration_v77.py` |
| 打分 | `score_core.py`（43KB）→ `institution_scanner/score_kernel.py` |
| 过滤/硬门 | `filters_core.py`、`tradeability.py`、`institution_scanner/price_limit_policy.py` |
| 质量门 | `fundamental_quality.py`、`institution_scanner/quality_policy.py` |
| 回测 | `analytics_core.py:3338 run_historical_backtest` + `institution_scanner/point_in_time_backtest.py` |
| 信号生命周期 | `signal_lifecycle_core.py:426 finalize_signal_ranking` |
| 报告/发布 | `report_core.py`、`institution_scanner/publication_renderer.py`、`pages_publisher.py` |
| GUI | `gui.py`(92KB) / `gui_core.py`(102KB) + `institution_scanner/gui_view_model.py` |
| 可观测性 | `institution_scanner/performance_health.py`、`gate_health.py`、`verify_output.py` |

---

## 三、核心功能与数据流

### 3.1 一次 DAILY 运行的真实调用链

```
daily_pipeline.py:315 → daily_pipeline_core.py:816 run_daily_pipeline()
  ① 扫描   → main_core.py:110 cmd_scan → scan_service_core.py:183 execute_scan()
              → scanner.py:868 run_scan() → scanner.py:449 scan_single_from_df()
                · 指标 indicators.py:673 compute_all_indicators
                · 硬门 filters_core.py:588 run_all_filters
                · 打分 score.py:107 score_ticker
                · 质量门 fundamental_quality.py:608 get_quality
              → analytics_core.py:701 enrich_results()
  ② 数据闸门 daily_pipeline_core.py:873 _quality_gate_errors
  ③ 回测   → analytics_core.py:3338 run_historical_backtest()
              （被 point_in_time_backtest.py:373 install() 包裹）
              → analytics_core.py:3088 finalize_signal_ranking
  ④ 发布   → daily_pipeline_core.py:682 _write_manifest → :481 _archive_run
              → :408 _publish_staging → maybe_publish_canonical_report
```

### 3.2 核心数据结构

`ScanResult`（`scanner.py:107-289`）是一个约 180 字段的 dataclass，
承载 ticker/score/final_score/ranking_score/quality_*/backtest_*/decision_state/trade_readiness。
进程内以 `list[ScanResult]` 传递，**仅在导出与排名边界转 DataFrame**
（`report_core.py:210 _results_to_dataframe()`），回测与排名阶段为列式运算。

### 3.3 打分模型

权重真源 `config_core.py:216-218`：`Setup 0.60 / Trigger 0.25 / Execution 0.15`，
签名顺序 `Setup:Trigger:Execution`，取值函数 `score_core.py:135 _model_component_weights()`。
三分量在 `score_core.py:1062 score_ticker()` 计算，合成走
`institution_scanner/score_kernel.py:46 combine_scalar_score()`（上限 `40 + 60×coverage`）。
向量化路径复用 `backtest_vectorization_v98.py:162` 与 `backtest_fastscore_v80.py:569`。

### 3.4 回测与 PIT（point-in-time）

PIT 由 `institution_scanner/point_in_time_backtest.py:373 install()` 猴子补丁注入，
核心判定 `analytics_core.py:354 _verified_point_in_time_frame()`
（只保留 `universe_snapshot_status == "ELIGIBLE"` 的行），
配合 `historical_universe.py:172 point_in_time_eligibility()`。
成熟度模型 `pit_maturity.py:37 build_pit_readiness()`：
`NO_ARCHIVE → WARMUP → OBSERVING → SHADOW_ELIGIBLE → PROMOTION_CANDIDATE`，**永不自动晋升模型**。

这是对的做法：用 PIT 治理对抗前视偏差与幸存者偏差，是本项目金融严谨性的核心。

---

## 四、依赖关系与技术栈

### 4.1 技术栈

| 类别 | 选型 |
|---|---|
| 语言 | Python 3.11（CI + Docker `python:3.11-slim-bookworm`） |
| 数据 | pandas >=2.0,<3.0 · numpy >=1.24,<3.0 · scipy · pyarrow >=12,<24 |
| 行情 | **tickflow[all]==0.1.24**（唯一 OHLCV 源，无回退/混源） |
| 财报 | **akshare==1.18.94**（东方财富整期业绩报表，带公告日） |
| 指标 | ta >=0.11,<1.0 |
| GUI | customtkinter==5.2.2（另有 Tk 路径） |
| 其他 | tqdm · holidays>=0.60 |
| 质量 | ruff >=0.12 · pyright >=1.1.400 · pytest >=8,<9 |
| CI | GitHub Actions：static-quality（ubuntu + windows-smoke）、daily-pages（cron 周一至五） |
| 约束 | `constraints-ci.txt` 锁定 13 个精确版本（pandas 2.3.3 / numpy 2.4.6 / pyarrow 23.0.1 等） |

### 4.2 耦合热点（AST 实测，372 条内部 import 边）

**扇入最高**（被依赖最多，改动影响面最大）：

```
  43  config          ← 超级枢纽，上帝配置模块
  21  analytics_core
  18  score_core
  12  downloader
  11  report
   9  indicators / trading_calendar / scanner
```

**扇出最高**（依赖最多，最难独立测试）：

```
  21  institution_scanner.analytics_runtime   ← 覆盖层编排中枢
  15  analytics_core
  13  backtest_acceleration_v77
  11  report_core
  10  score / backtest_command_v76
```

`config` 扇入 43 意味着**任何配置字段的语义变更都会波及近四分之一的内部依赖面**。

---

## 五、代码质量（全部为本地实测）

> **本节是初次审计时的快照**——当时的 CI 是红的。修复过程在第九节，
> 阶段 3 之后的最新状态在第十五、十六节。
> 具体一处容易误导的：5.3 表里 `analytics_core.py` 的 160,642 / 超预算 642
> 已在第九节降为 **159,789**（余量 211），该守卫现在是通过的。

### 5.1 静态检查：良好

| 检查 | 命令 | 结果 |
|---|---|---|
| Ruff 基础规则 | `ruff check .` | **All checks passed** |
| Ruff 严格规则（CI 同款） | `ruff check institution_scanner --select B,C4,SIM,PERF,PIE` | **1 个错误** |

唯一严格规则错误：`SIM105` @ `institution_scanner/pages_publisher.py:197`
（`try/except/pass` 应改 `contextlib.suppress(OSError)`），无自动修复。

> **一处自我修正**：我最初额外加了 `RUF` 规则跑出 118 个错误——那是我自己引入的噪声，
> 不是项目问题。CI 的确切命令不含 RUF，正确结论是 **1 个错误**。

### 5.2 测试：147 通过 / 2 失败（**CI 当前是红的**）

```
$ python -m pytest -q --ignore=tests/test_fundamental_progress.py
2 failed, 147 passed in 19.17s
```

（跳过 `test_fundamental_progress.py` 是因为本机 Python 构建无 tkinter，见 5.4）

**失败 1 —— `test_legacy_giant_modules_are_shrink_only`（真实缺陷）**

```
AssertionError: {'analytics_core.py': 160642}
```

`analytics_core.py` 实际 160,642 字节，超出 `tests/test_architecture_growth.py:10`
设定的 160,000 字节预算 **642 字节**。工作区干净、该文件无本地 diff，
说明**这是已提交到 HEAD 的状态，CI 上同样会红**。

**失败 2 —— `test_clone_retires_leftover_dir_before_fallback`（真实缺陷，非环境噪声）**

该测试已用 monkeypatch 完整 mock 掉 `_run_git` 与 `_retire_worktree`，
**不依赖网络或 git**，因此失败是真实的实现/测试不同步。
定位：`git log` 显示测试由 `a7d6b92`（#55 修复 SSH 克隆超时）引入，
而 `pages_publisher.py` 在 `b2fb271`（#57 重构）被改写；
最新提交 `ca5a36b` 同时改了实现(+55)与测试(+97)，两者未对齐。
失败信息：`WEB_REPORT_CLONE_FAILED: configured origin: timed out; HTTPS: destination ... already exists`。

### 5.3 代码体积与复杂度

**巨型模块**（bytes，对比 shrink-only 预算）：

| 模块 | 实际 | 预算 | 状态 |
|---|---|---|---|
| analytics_core.py | 160,642 | 160,000 | **超 642** |
| report_core.py | 104,909 | 105,000 | 临界（余 91 字节） |
| gui_core.py | 104,866 | 105,000 | 临界（余 134 字节） |
| gui.py | 94,033 | 100,000 | 余量小 |
| scanner.py | 78,111 | 80,000 | 余量小 |
| signal_lifecycle_core.py | 67,788 | 70,000 | 余量小 |

**超长函数**：80 个函数超过 100 行，最长的 15 个：

```
  909 行  report_core.py:665       validate_decision_integrity()
  828 行  scanner.py:868           run_scan()
  804 行  signal_lifecycle_core:426 finalize_signal_ranking()
  604 行  auction_structure.py:796 compute_auction_structure()   ← 规范包内
  526 行  analytics_core.py:2585   apply_backtest_ranking()
  523 行  analytics_core.py:3338   run_historical_backtest()
  452 行  backtest_fastscore_v80:138 _fast_score_matrix()
  381 行  scanner.py:449           scan_single_from_df()
```

**集中度**：TOP10 模块占 32.6%，TOP20 占 45.9% 的代码量。

值得警惕的一点：**规范包内部也开始长函数**——`auction_structure.py:796` 604 行、
`fundamental_schema.py:362` 263 行。说明「新代码天然更好」的假设不成立，
体积纪律没有覆盖到规范包。

### 5.4 可移植性（实测发现）

测试套件对 `tkinter` 有硬依赖：`tests/test_fundamental_progress.py:3 import gui_core`
→ `gui_core.py:13 import tkinter`。在无 GUI 的环境（Docker slim、精简 Python 构建）会
**collection error 并中断整个测试运行**（pytest 遇收集错误即 `Interrupted`）。
即：一个环境缺 tkinter，全部 151 个测试都跑不了。

### 5.5 治理机制（做得好的部分）

项目自带架构守卫测试，这是它最值得肯定的地方：

- `tests/test_architecture_growth.py:8` → 根目录版本上限 `_ROOT_VERSION_CEILING = 102`，禁止新增更高版本覆盖层
- 同文件 :9-16 → 6 个巨型模块的字节预算，强制 shrink-only
- `tests/test_runtime_facade_contract.py:12` → 禁止门面层直接 import `_vXX`；:31 断言 overlay 计数 == 13
- `institution_scanner/runtime_inventory.py` → 可量化的兼容性债务清单

**即：当前 CI 红灯恰恰证明这些守卫是真的在工作的**，不是摆设。

---

## 六、潜在改进点（按优先级）

### P0 —— 立即处理（CI 是红的）

1. **修复 2 个失败测试**。任选路径：
   - `analytics_core.py` 减 642 字节（提取纯函数到 `institution_scanner/`），或调整预算并说明理由；
   - 对齐 `pages_publisher._retire_worktree`（:182-200）与
     `test_clone_retires_leftover_dir_before_fallback` 的语义——当前 mock 下
     HTTPS 候选必然撞上 `destination already exists`，需确认是实现的清理逻辑不够，
     还是测试的 mock 假设写错了。
   顺带修掉 `pages_publisher.py:197` 的 SIM105。

2. **给测试套件加 tkinter 隔离**。在 `tests/conftest.py` 用
   `pytest.importorskip` 或 `collect_ignore` 处理 GUI 依赖，避免一个缺失模块
   中断全部 151 个测试。

### P1 —— 结构性风险

3. **消除 import 期全局副作用**。20 个模块级 `install()` 是当前最大的可维护性负债：
   装载顺序敏感、无法进程内隔离测试、静态分析失真。
   中期目标改为显式注册表 + 显式装配函数（在 `main()` 里调用一次），
   可先把「模块级自动执行」这一条作为 CI 禁止项守住。

4. **给遗留巨型核心补黄金等价测试**。这是 ARCHITECTURE.md 自己定的前置条件
   （"legacy 模块只有在等价测试保护下才能移除"），但实测覆盖严重不足：

   | 遗留模块 | 被测试文件引用数（词边界精确匹配） |
   |---|---|
   | downloader_core.py | **0** |
   | filters_core.py | **0** |
   | score_core.py / report_core.py | 1 |
   | signal_lifecycle_core.py / gui_core.py | 2 |
   | analytics_core.py / scanner.py | 3 |

   `downloader_core` 与 `filters_core` **零测试覆盖**，却处在生产主链上。
   测试投入明显偏向规范包（performance_curve 系列占了 16 个文件），
   而真正承载生产语义的遗留核心几乎裸奔。**这直接卡死了 shrink-only 的推进**。

5. **把体积与重复纪律扩展到规范包**（第二轮实测支撑，见 7.3）。
   目前只有 6 个遗留模块有字节预算，但规范包内已出现 604 行函数（:796）、
   以及 `_integer()` 4 份 / `_safe()` 4 份逐字符相同的副本。
   建议：
   - CI 检查任何函数 > 150 行即失败（先宽阈值 + 存量豁免名单）；
   - 抽出 `institution_scanner/_common.py` 收纳 `_integer` / `_safe` /
     `_read_json` / `_truthy` / `_numeric` / `_finite_float` 这 6 个高频工具，
     一次性消掉 32 组重复中的大部分；
   - 规范包是小工具函数的唯一归属地，禁止再复制粘贴。
   **这条比拆遗留模块更紧迫**——遗留模块已经停止长大（有预算守着），
   而规范包正在以相同的模式重新积累债务。

### P2 —— 工程质量

6. **拆分 `config` 上帝模块**（扇入 43）。按域拆成 `config.score` / `config.quality` /
   `config.execution`，保留聚合门面做兼容。
7. **补 `pyproject.toml` 的 `[tool.pytest.ini_options]`**。当前无 pytest 配置、
   无 conftest.py，导致 `pytest -q` 与 `python -m pytest -q` 行为不一致
   （前者不把仓库根加入 `sys.path`，会报 `No module named 'institution_scanner'`）。
   这是新人上手第一个坑。
8. **加覆盖率度量**。目前无 coverage 配置，无法回答「改这块代码有没有测试保护」——
   而这正是 P1-4 需要的决策依据。建议先只要求
   `institution_scanner/` 达到阈值，遗留模块标记为"待补"。
9. **清理开发残留脚本**：`debug_vec.py`、`diag_vec.py`、`diag_vec2.py`、
   `_smoke_bt.py`、`smoke_backtest.py`、`validate_vectorized.py` 共 6 个零引用文件
   （v98 向量化改造的遗留物）。建议删除或移入 `tools/`。
10. **处理治理不一致**：`runtime_inventory.py:31` 已将 `analytics_compat_v97`、
    `backtest_profile_alignment_v95` 标记为退役，但它们仍被
    `backtest_math_integrity_v94.py`、`backtest_alignment.py` 实际 import。
    要么更新清单、要么真正摘除引用，否则债务清单失真。
11. **补 pre-commit**。当前质量检查只在 CI 跑，本地提交无拦截。
12. **清理 49 个零引用符号**（见 7.1）。按风险从低到高：
    - 先删 9 个 `_LEGACY_*` 只写不读的保存引用（零行为风险）；
    - 再删 `historical_backtest.py` 整个文件的 3 个 checkpoint 函数
      （该文件本身已不在生产链，建议整文件评估后移入 `archive/`）；
    - `scan_single` / `get_etf_fund_flows` / `etf_research_eligibility`
      / `resolve_global_calibration` / `_purged_sample_split` **需你确认**：
      它们看起来是「做了一半的功能」而非废弃代码，删之前应确认是否计划接线。
    - **注意**：不要动 `ScanResult` 的 179 个字段——7.2 已复核，零死字段，别误删。

---

## 七、深度静态审查：死代码与重复代码（第二轮）

> 方法：AST 全量扫描 + 逐个 `grep` 人工复核。
> 候选只保留「生产代码中定义、且全库（含 tests）零引用」，
> 排除测试函数自身、排除 `__dunder__`、排除猴子补丁中被实际使用的保存引用。

### 7.1 死代码：49 个零引用符号（22 函数 + 26 常量 + 1 类）

**A. 「只写不读」的猴子补丁保存引用 —— 9 个**（最典型的一类）

覆盖层在打补丁前会先把原函数存进 `_LEGACY_*` 常量，但这 9 个存完就再没人读过：

| 常量 | 位置 |
|---|---|
| `_LEGACY_SERIES` / `_LEGACY_LATEST` / `_LEGACY_ROLLING_MEAN` / `_LEGACY_SAFE_RETURN` | `score_acceleration_v79.py:22-25` |
| `_LEGACY_SCORE_DIMENSIONS_AVAILABLE` / `_LEGACY_SCORE_TREND` / `_LEGACY_SCORE_VOLUME` / `_LEGACY_SCORE_STRUCTURE` | `score_acceleration_v79.py:26-30` |
| `_LEGACY_VOLATILITY_STATE` | `score_acceleration_v79.py:33` |
| `_LEGACY_VALUE_TRAP_RISK` / `_LEGACY_BREAKOUT_SCORE` / `_LEGACY_EXECUTION_QUALITY_SCORE` | `score_endpoint_acceleration_v79.py:19-21` |
| `_LEGACY_BACKTEST_ONE_TICKER` | `backtest_sample_acceleration_v80.py:21` |
| `_ORIGINAL_VERIFIED_POINT_IN_TIME_FRAME` | `backtest_production_activation_v93.py:40` |

对照验证：同文件里 `_LEGACY_SCORE_ACCUMULATION`(:29)、`_LEGACY_CLASSIFY_STYLE`(:31)、
`_LEGACY_ENTRY_POINT`(:32) **确实被使用**（:307、:411、:449），已被正确排除在清单外——
说明扫描不是无差别误报。

> 风险等级低（不执行、不影响行为），但语义上有欺骗性：
> 读代码的人会以为存在「回退到原实现」的路径，实际上没有。

**B. 零引用的生产函数 —— 22 个，其中值得注意的：**

| 函数 | 位置 | 备注 |
|---|---|---|
| `scan_single` | `scanner.py:832` | **在生产核心里**，与 `scan_single_from_df` 并存，零引用 |
| `get_etf_fund_flows` | `downloader_core.py:875` | ETF 资金流，疑似未接线的功能 |
| `get_data_source_label` | `downloader_core.py:139` | 数据源标签 |
| `etf_research_eligibility` | `classification.py:242` | ETF 研究准入 |
| `resolve_global_calibration` | `model_calibration.py:269` | 全局校准 |
| `_purged_sample_split` | `analytics_core.py:1633` | 回测样本清洗 |
| `_robust_mean` | `analytics_core.py:324` | 鲁棒均值 |
| `_checkpoint_path` / `_write_checkpoint` / `_load_checkpoint` | `historical_backtest.py:221/225/253` | 整文件已不在生产链 |
| `parse_report_period` / `_date_text` | `institution_scanner/fundamental_schema.py:236/247` | **规范包内** |
| `inject_backtest_into_html` | `institution_scanner/backtest_web.py:214` | **规范包内** |
| `read_backtest_summary` | `institution_scanner/pit_page_semantics.py:29` | **规范包内** |

### 7.2 已复核、判定为非缺陷（避免误导后续修改）

这两项我原本预期有问题，实测**没有**，明确记录以免有人照着错的结论去改：

1. **`ScanResult` 179 个字段，死字段为 0。**
   用两条独立路径交叉验证：属性访问（AST Load/Store 上下文区分）+ 字符串字面量
   （pandas 列访问路径）。120 个字段无字符串形式但都有属性读取；
   属性读取为 0 的字段全部有字符串引用。**两类取交集后为 0**。
   即：这个 179 字段的巨型 dataclass 虽然臃肿，但**没有堆积无用字段**，维护是克制的。

2. **恒真/恒假条件 0 处。**
   AST 扫描 `if True` / `if False` / `assert True` / 非空字面量条件，全库 0 命中。
   没有明显的不可达分支。

### 7.3 重复代码：32 组 / 79 个实例（真问题）

按完全相同的函数体（AST dump 一致，即含变量名在内逐字符相同）统计：

| 函数 | 重复处数 | 分布 |
|---|---|---|
| `_read_json()` | 5 | `resonance_reporting_v90` / `web_report_v84` / `web_report_v90` / `backtest_web` / `publication_renderer` |
| `_safe()` | 5 | `web_report_v102` / `backtest_web` / `performance_curve_web` / `pit_page_semantics` / `publication_renderer` |
| `_truthy()` | 4 | `daily_pipeline_core` / `report_core` / `signal_lifecycle_core` / `publication_renderer` |
| `_integer()` | 4 | `performance_health` / `pit_counts` / `pit_maturity` / `pit_page_semantics` |
| `_finite_float()` | 3 | `analytics_core` / `calibration_governance_v102` / `scanner` |
| `_numeric()` | 3 | `backtest_fastpath_v78` / `backtest_fastscore_v80` / `report_core` |

**最值得关注的是 `_integer()`**：4 份副本**全部在规范包 `institution_scanner/` 内部**
（`performance_health.py:31`、`pit_counts.py:27`、`pit_maturity.py:16`、`pit_page_semantics.py:22`），
实测逐字符相同：

```python
def _integer(value: object) -> int:
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0
```

`_safe()` 同样有 4 份落在规范包内。

**这直接推翻了「规范包天然比遗留代码干净」的假设。** 结合 5.3 节发现的
`auction_structure.py:796` 604 行函数，可以确认：**体积与重复的纪律没有覆盖到规范包。**
规范包目前只是"新代码的归属地"，还没有成为"质量的标杆"。
如果现在不设防，几年后 `institution_scanner/` 会变成第二个 `analytics_core.py`。

---

## 八、总体评价

**这是一个工程治理意识明显高于平均水平的项目。** PIT 语义、ROE 年报/中报口径分离、
涨跌停规则单一真源、市场流动性 vs 组合容量区分、证据分层（LOCAL/PEER/HIER）、
成熟度不自动晋升模型——这些金融语义上的严谨程度，在同类个人量化项目里是少见的。
用测试把「架构只能变好不能变坏」固化下来（版本上限、体积预算、门面隔离），
更是正确的做法，而且**它们真的在拦**（当前 CI 红灯就是证据）。

**它当前的核心矛盾是：治理规则已经到位，但历史债务的偿还速度跟不上。**
69 个版本化生产模块、63 个 `install*()` 定义、30 处模块级调用点、
6 个贴着预算上限的巨型模块、以及生产主链上零测试覆盖的 `downloader_core` /
`filters_core` ——这几件事互相锁死：因为没等价测试，所以不敢删遗留模块；
因为不敢删，所以只能继续打补丁；因为继续打补丁，遗留模块继续长大。

> **本节数字已按阶段 3 之后的状态更正，并保留更正轨迹。**
> 初次审计写的是「73 个覆盖层 / 62 个 install() / 20 个 import 即副作用」：
>
> - **20 处**是低估，实际 **57** 处（漏 3 个具名变体 + 2 处别名导入，见 15.3）；
>   阶段 3 已把它降到 **30**（见 16.4），这是本轮唯一真正减少的债务。
> - **73** 把 4 个同名测试文件（`test_backtest_score_vectorized_alignment.py`、
>   `test_gui_view_model.py`、`test_page_version.py`、`test_version_manifest.py`）
>   也算成了覆盖层，生产的版本化模块实为 **69**。
> - **62** 应为 **63**：57 个 `install` + 6 个具名变体
>   （`install_pre_facade` / `install_post_facade` / `install_analytics_alignment` /
>   `install_reliability` / `install_single_recency_ranking_guard` /
>   `install_v84_presentation`）。

**建议的破局顺序**：先修红（P0）→ 补 `downloader_core`/`filters_core` 的等价测试（P1-4）
→ 拿这两个模块做一次完整的「提取到规范包并从生产路径摘除」演练，
跑通流程后再复制到其他巨型模块。不要试图一次性重构 `analytics_core.py`
——157KB、扇入 21，那是风险最高的单点。

---

### 附：复现命令

```bash
python -m ruff check .
python -m ruff check institution_scanner --select B,C4,SIM,PERF,PIE
python -m pytest -q
stat -c%s analytics_core.py
```

---

## 九、本轮已执行的修复与验证

> 基线：HEAD `ca5a36b`。执行原则：**只修缺陷与基础设施，不动策略取向、阈值与权重。**
> 未触碰：Champion 权重签名（0.60/0.25/0.15）、任何阈值默认值、任何金融语义口径。

### 9.1 结果：CI 由红转绿

| 门禁 | 修复前 | 修复后 |
|---|---|---|
| `pytest` | 2 failed, 147 passed | **149 passed, 0 failed** |
| `ruff check .` | All passed | All passed |
| `ruff check institution_scanner --select B,C4,SIM,PERF,PIE` | 1 error | **All passed** |
| `python -m compileall -q institution_scanner` | — | OK |
| `import main, daily_pipeline, web_report_v81` | — | OK |
| 6 个巨型模块字节预算 | 1 项超标 | **全部达标** |

### 9.2 逐项变更

**① 测试基础设施（P0-2、P2-7）**

- 新增 `tests/conftest.py`：仅做 tkinter 隔离。
  `test_fundamental_progress.py` 顶层 `import gui_core` → `import tkinter`，
  缺 GUI 环境会 collection error 并**中断全部测试**。现在无 tkinter 时该文件被
  `collect_ignore` 跳过，其余 149 个测试照常跑。
  已核对：`test_architecture_growth.py` 虽匹配到 `gui_core.py`，但那只是
  字节预算字典里的字符串键，非真实 import，故**未**被误隔离。
- `pyproject.toml` 新增 `[tool.pytest.ini_options]`：`pythonpath = ["."]`、
  `testpaths = ["tests"]`、`addopts = "-q"`。
  修掉了 `pytest` 与 `python -m pytest` 行为不一致的坑（前者不把仓库根加入
  `sys.path`，会报 `No module named 'institution_scanner'`）。

**② `pages_publisher` 缺陷（P0-1）**

- `SIM105` @ `pages_publisher.py:197`：`try/except/pass` → `contextlib.suppress(OSError)`。
- 修复 `test_clone_retires_leftover_dir_before_fallback` 失败。

  该测试经核实为 **ca5a36b 新引入、从引入起即红**。判断依据：
  - 测试已用 monkeypatch 完整 mock `_run_git` 与 `_retire_worktree`，**不依赖网络/git**，
    故不是环境噪声；
  - 实现经核实**正确**：`_clone_branch` 在每个候选前调用 `_retire_worktree`（:212），
    真实实现会 `rmtree`，失败则 rename 隔离；
  - 测试的 mock 语义倒置——`fake_retire` 竟然 `mkdir` **创建**目录，
    而 retire 的语义是清退，于是 HTTPS 候选必然撞上 `destination already exists`。

  修复：让 `fake_retire` 真正清退（`shutil.rmtree(ignore_errors=True)`），
  并让 `fake_run_git` 在超时时留下目录——这才是 "leftover" 的真实来源。

  **回归能力已验证**：临时把 `_clone_branch` 里的 `_retire_worktree` 调用换成
  `pass` 后，该测试立刻转红并报出原始错误；恢复后转绿。
  证明它不是被我改成"永远通过"，而是真能捕获缺陷。

**③ 抽取规范包公共工具（P1-5）**

- 新增 `institution_scanner/_common.py`，收纳 `to_int` / `escape_html`。
- 替换规范包内 8 处逐字符相同的副本：
  - `_integer` ×4：`performance_health` / `pit_counts` / `pit_maturity` / `pit_page_semantics`
  - `_safe` ×4：`backtest_web` / `performance_curve_web` / `pit_page_semantics` / `publication_renderer`
- 同时移除 4 个文件中因此失效的 `import html`（否则触发 F401）。
- 采用「公开名 + 别名导入」（`from ._common import to_int as _integer`），
  调用点零改动，且不引入私有名跨模块导入的味道。
- **等价性已验证**：24 个用例（None / 负数 / 浮点 / 字符串 / 非数值 / 空容器 /
  布尔 / 大整数 / 含空白字符串 / HTML 特殊字符）输出与类型完全一致；
  `inf` 边界新旧均抛 `OverflowError`（行为一致，**非回归**——见 9.4）。

**④ `analytics_core` 超预算（P0-1）**

160,642 → **159,789 字节**（预算 160,000，余量 211）。删两个零引用函数：

| 函数 | 字节 | 依据 |
|---|---|---|
| `_robust_mean` | 522 | 纯内部工具，全库零引用 |
| `_purged_sample_split` | 331 | 零引用；已额外核验无 `getattr`/字符串动态调用；是 `_purged_split_label` 的**恒等转发**，被包装者仍有 8 处引用 |

> `_purged_sample_split` 的 docstring 标为 "Compatibility wrapper"，
> 本报告 7.1 节曾将其列入「需你确认」。**删除已由你确认后执行**（选项：删除 vs 上调预算）。
> 若日后需要，`git revert` 可完整恢复。

其余 5 个巨型模块预算一并复核，均达标：`report_core` 余 91、`gui_core` 余 134、
`gui.py` 余 5,967、`scanner.py` 余 1,889、`signal_lifecycle_core` 余 2,212。

### 9.3 换行符核查（针对 CRLF 保留要求）

一度出现 git 告警 `LF will be replaced by CRLF`，已核查清楚：

- `institution_scanner/backtest_web.py` 在 **HEAD 原本就是 LF**（CRLF 数 0、
  不以换行结尾），本次编辑**保持**其原有风格，未破坏。
- 其余文件工作区均为纯 CRLF，`git diff` 呈局部变更（每个文件仅 3–23 行），
  **没有整篇变红**，证明换行风格未被改动。
- 说明：`git show HEAD:<file>` 返回的是 git 规范化后的 LF，不能用于判断工作区风格；
  判断依据是 diff 是否整篇改变。

### 9.4 需要你知情的两个遗留观察

1. **`to_int` / `escape_html` 对 `inf` 会抛 `OverflowError`**（`int(float('inf'))`），
   不在 `(TypeError, ValueError)` 捕获范围内。这是**原有行为，本次原样保留**，
   不是新引入的缺陷。若希望 `inf` 也退化为 0，属于语义变更，需你确认后再改。
2. 规范包内仍有 `_mapping` 等小工具重复（未纳入本次抽取，以控制改动面积）。
   建议下一步纳入 `_common.py`。

### 9.5 未执行（需另行决策）

以下条目涉及策略取向或较大重构，**未**擅自改动：

- 消除 20 个模块级 `install()` 的 import 期全局副作用（P1-3）
- 补 `downloader_core` / `filters_core` 的黄金等价测试（P1-4）
- 拆分 `config` 上帝模块（扇入 43）（P2-6）
- 清理其余 47 个零引用符号（P2-12）
- 删除 6 个开发残留脚本（P2-9）
- 新增 pre-commit / 覆盖率配置（P2-8、P2-11）

---

## 十、下一步重构路线图

> 排序原则：**先守后攻、先易后难、按可测试性排序**。
> 每个阶段都有可验证的验收标准，且必须在上一阶段达标后才进入下一阶段。

### 10.0 先看清死锁在哪里

当前项目被一条链锁死：

```
生产语义在遗留巨型模块里 → 但几乎零测试覆盖
        ↓
不敢删（架构文档自己也要求「黄金等价测试」保护）
        ↓
只能继续打补丁 → 遗留模块继续长大 → 覆盖层继续增加
        ↓
新代码只能往规范包堆 → 规范包没有纪律约束 → 正在重蹈覆辙
```

**破局点不是"重构遗留模块"，而是"先让它可被重构"。**
所以路线图的前两阶段都不碰业务逻辑。

### 10.1 阶段 1 · 止血（约半天，零业务风险）— ✅ 已完成

> **本阶段已于 2026-09-14 执行完毕，实测结果见第十一节。**
> 其中第 3 项（零引用符号清理）属删除操作，不在本阶段验收标准内，
> 已单独出清单待确认，见 11.5。

**做什么**：给规范包 `institution_scanner/` 上自动化纪律。

1. 新增 `tests/test_canonical_package_discipline.py`：
   - **函数行数上限**：任何函数 > 150 行即失败。
     存量豁免名单先锁定 `auction_structure.compute_auction_structure`(604)
     与 `fundamental_schema.build_fundamental_summary`(263)，名单只减不增。
   - **模块字节预算**：给规范包每个模块设上限，同样 shrink-only。
   - **重复检测**：AST dump 完全相同的函数体 > 1 处即失败
     （本次已消除 8 处，剩余 `_mapping` 等先列豁免）。
2. 继续抽取剩余重复：`_mapping`（2 处）、`_read_payload`/`_read_json` 家族。
3. 分批清理 47 个零引用符号与 6 个残留脚本（每批 ≤ 10 个，批后跑全量测试）。

**为什么最先做**：遗留层已有字节预算守着、实际已停止长大；
规范包**没有**任何约束，且已出现 604 行函数和 8 处逐字符副本。
同样是半天工作量，防止未来债务的收益远大于事后清理。

**验收**：三项检查进入 CI 且为绿；存量豁免名单写入测试文件并注明理由。

### 10.2 阶段 2 · 建网（约 1–2 周，关键路径）

**做什么**：给生产主链上**零覆盖**的模块建黄金等价测试。

| 模块 | 当前覆盖 | 可测试性依据 |
|---|---|---|
| `filters_core.py` | **0** | 610 行、16 函数、**最长函数仅 68 行**、纯占比 31% |
| `downloader_core.py` | **0** | 877 行、43 函数、**纯占比 51%（最高）**、最长 116 行 |
| `score_core.py` | 1 个测试文件 | 1183 行、纯占比 21.9%、最长 177 行 |

**方法**：黄金测试（golden test）——**不需要理解或改动被测代码**，
只需构造输入、固化当前输出为 fixture。这是唯一能绕开「读不懂 157KB 遗留代码」
又能拿到回归保护的办法。

**顺序**：`filters_core` → `downloader_core` → `score_core`。
先拿最小最纯的练手，把「构造输入 + 固化输出 + 验证等价」的流程跑通。

**为什么是关键路径**：架构文档规定遗留模块移除需等价测试保护。
这两个模块不建网，阶段 4 永远启动不了。

**验收**：两个模块各 ≥ 10 个等价用例；修改被测代码后测试必须转红
（**必须做反向验证**，否则是假保护）。

### 10.3 阶段 3 · 解锁（杠杆最大，但必须先有阶段 2）

**做什么**：把 57 处模块级自动 `install()` 改为显式装配（数字经 15.3 修正：早期报告的
「20 个」是低估，漏了别名导入与跨模块调用）。

- 保留各 `install()` 函数本身，仅**移除模块级自动调用**
  （如 `runtime_v83.py:54`、`scanner_resume_v59.py:741`）。
- 由 `analytics_runtime` 在 `install_pre/post_facade` 中统一、显式调用。
- 新增「装载清单快照」测试：断言所有 install 被调用且顺序稳定。

**为什么杠杆最大**：import 期全局副作用是**三重障碍**的根源——
装载顺序不可验证、测试无法进程内隔离、Pyright 看不到补丁后的真实签名。
移除它，后面所有重构才谈得上"可验证"。

**风险与对策**：最大风险是遗漏某个 `install()` 或改变装载顺序。
对策正是阶段 2 的黄金测试 + 显式清单断言。**没有阶段 2 不要动这一阶段。**

**验收**：20 个模块级调用清零（用 grep 断言）；新增禁止项测试防止回潮；
全量测试 + 黄金基线不变。

### 10.4 阶段 4 · 提取（严格按可测试性顺序）

按纯净度从高到低推进，**一个模块跑通再进下一个**：

1. `filters_core` → `institution_scanner/filters/`（最易，作为流程样板）
2. `downloader_core` 纯逻辑（22 个纯函数可优先提取，IO 部分留后）
3. `score_core`（纯占比 21.9%）
4. `signal_lifecycle_core` 的 `finalize_signal_ranking`（804 行）**只做机械拆分**：
   提取子函数、不改任何语义。注意它纯占比仅 4.5%，**禁止**边拆边"顺手优化"。

每一步：**先补测试 → 再提取 → 保留 `*_core` 转发 → 跑等价验证 → 观察一个发布周期 → 再摘除转发。**

### 10.5 阶段 5 · 围而不攻（不建议直接重构）

`analytics_core.py`：3837 行、61 函数、**纯函数仅 8.2%**、最长 526 行。
`signal_lifecycle_core.py`：纯函数仅 **4.5%**。

纯函数占比这么低意味着它们几乎全是 IO、全局状态与副作用的混合物
——**既测不了，也拆不动**。任何直接重构都是在没有安全网的情况下跳崖。

**正确策略是蚕食**：项目既定的 shrink-only 路线本身是对的，
`point_in_time_backtest`、`backtest_score_vectorized` 等模块已经在接管它的语义。
继续往规范包搬，**让它自然萎缩**，而不是正面强攻。

### 10.6 一处我改变主意的建议

第六节 P2-6 曾建议「拆分 `config` 上帝模块（扇入 43）」。**我现在不推荐。**

实测依据：`config_core` 164 个常量 + `config` 60 个 = 224 个常量，
被 **45 个文件**引用；且常量已按前缀天然分组（BACKTEST 36、INSTITUTIONAL 10、
FUNDAMENTAL 14、TRADE 11……）。

拆分要改 45 个文件的 import，收益却只是"降低扇入"这个数字——
**改动面巨大、收益不明确、还有循环导入风险**。而配置集中本身并不算缺陷，
224 个常量集中一处反而便于查找。

**替代方案（成本 1 小时）**：给 config 加只读契约测试——
断言关键常量存在、类型正确、且 Champion 权重签名 `0.60/0.25/0.15` 不被意外修改。
这比拆分更能防住真正的风险。

### 10.7 如果只做一件事

- **只做一件容易的** → 阶段 1 的规范包纪律。
  半天，零业务风险，直接止血正在增长的债务。
- **只做一件难的** → 阶段 2 的 `filters_core` 黄金测试。
  610 行、最长函数仅 68 行，是整个遗留层最容易下手的点；
  它一旦建成，就打通了后面所有遗留重构的入口。

**不要做的事**：不要一上来重构 `analytics_core.py`。
3837 行、纯函数 8.2%、扇入 21、被 15+ 个覆盖层猴子补丁——
那是风险最高、收益最不确定的单点。

---

## 十一、阶段 1 执行结果（2026-09-14）

### 11.1 新增共享层 `institution_scanner/_common.py`

把散落在包内、逐字节相同的 5 个辅助函数收敛到一处。
**重扫确认：包内重复函数体由 8 组降到 1 组**，且剩下的 1 组
（`auction_structure.to_dict` / `policy_manifest.as_dict`）是单行 `asdict(self)`
转发器，合并只会把互不相关的 dataclass 耦在一起，属刻意保留。

| 抽取的函数 | 原副本数 | 接入模块（以别名导入，调用点零改动） |
|---|---|---|
| `to_int` | 4 | `pit_maturity`、`pit_counts`、`performance_health`、`pit_page_semantics` |
| `escape_html` | 4 | `pit_page_semantics`、`backtest_web`、`performance_curve_web`、`publication_renderer` |
| `to_mapping` | 3 | `performance_health`、`pit_counts`、`pit_maturity` |
| `text_series` | 3 | `ranking_determinism`、`reliability`、`verify_output` |
| `read_json_payload` | 2 | `backtest_web`、`publication_renderer` |

别名导入的形式（`from ._common import to_int as _integer`）是刻意的：
**调用点一行都不用改**，diff 因此只落在 import 区和被删除的函数体上。

**等价性验证**（`read_json_payload`，9 类输入全覆盖）：

```
missing file / dict / list / number / null / broken json /
empty file / directory / non-utf8 bytes      → 9/9 新旧返回值完全一致
```

**唯一行为差异**：入参为 `str` 时，旧实现抛 `AttributeError`（`str` 没有
`read_text`），新实现正常读取。三处调用点传入的全是 `Path`，
**无调用方命中**，因此实际输出不变。

### 11.2 新增 `tests/test_canonical_package_discipline.py`（5 项检查）

| 检查 | 规则 | 当前 |
|---|---|---|
| 函数行数 | > 150 行失败 | 7 个存量豁免 |
| 模块字节 | 7 个 >20KB 模块冻结为 shrink-only | 全部在预算内 |
| 大模块必须登记 | >20KB 而无预算 → 失败 | 无漏登记 |
| 重复函数体 | AST body 完全相同 → 失败 | 0 组 |
| `_common` 依赖方向 | 不得 import 包内任何模块 | 无反向依赖 |

豁免名单用 `(模块, 函数名)` 而非行号做键——否则上方任何一次编辑都会让名单失效。
名单是**只减不增**的：一旦某个条目回落到上限以内，测试会转红并提示删除，
防止豁免变成永久免死金牌。

### 11.3 反向验证（5/5 全部生效）

一条全绿的治理测试如果不做反向验证，就是假保护。逐条制造违规：

| 注入的违规 | 结果 |
|---|---|
| 从豁免名单删掉一个真实超限函数 | ✅ 转红 |
| 往豁免名单塞一个已合规函数 | ✅ 转红（stale 检查） |
| 收紧某个模块字节预算 | ✅ 转红 |
| 把 `_common.to_int` 逐字节克隆进另一个模块 | ✅ 转红 |
| 让 `_common` 反向 import 包内模块 | ✅ 转红 |

### 11.4 一处我自己的空配置，已删除

初版给重复检测加了 `EXEMPT_DUPLICATE_NAMES = {to_dict, as_dict, ...}` 永久豁免。
反向验证时发现：**把豁免清空，测试依然全绿**——因为那些转发器的 body 只有
`return asdict(self)`（20 字符），早就被 60 字符的最小长度阈值过滤掉了。

也就是说这个豁免当前不改变任何行为，是恒假配置。**已删除**，
理由写进 `MIN_DUPLICATE_CHARS` 的注释里（长度阈值已天然覆盖，无需名单）。

### 11.5 下一步：9 个零引用符号（待你确认，未动手）

修正扫描器后重新统计（**第一版 17 个里有 5 个是假阳性**）：
调用方用的是别名导入 `from ._common import to_int as _integer`，
`to_int` 出现在 AST 的 `alias.name` 而非 `ast.Name`，第一版扫描器没统计到。
**如果没复核就动手，会误删 5 个正在使用的共享函数。**

| 类型 | 位置 | 符号 |
|---|---|---|
| const | `gui_view_model.py:13` | `GUI_VIEW_MODEL_VERSION` |
| const | `performance_curve_contract.py:2` | `PERFORMANCE_CURVE_CSV_NAME` |
| const | `performance_curve_contract.py:3` | `PERFORMANCE_CURVE_JSON_NAME` |
| const | `performance_curve_contract.py:4` | `PERFORMANCE_CURVE_SECTION_ID` |
| const | `scan_runtime.py:29` | `SCAN_RUNTIME_FACADE_VERSION` |
| func | `fundamental_schema.py:236` | `parse_report_period` |
| func | `fundamental_schema.py:247` | `_date_text` |
| func | `backtest_web.py:203` | `inject_backtest_into_html` |
| func | `pit_page_semantics.py:20` | `read_backtest_summary` |

5 个常量都是 `*_VERSION` / `*_NAME` / `*_ID` 形态，**疑似对外契约标记**
（供外部脚本或页面校验），删除后可能破坏外部消费者。
这 9 项属删除操作，**等你确认后再动**。

### 11.6 门禁实测

```
ruff check .                                              All checks passed
ruff check institution_scanner --select B,C4,SIM,PERF,PIE All checks passed
python -m pytest                                          154 passed
python -m compileall -q .                                 exit 0
import main, daily_pipeline, web_report_v81               OK
```

测试数 149 → 154（新增 5 条纪律检查）。CI 的 `python -m pytest -q`
使用 `testpaths = ["tests"]`，新测试自动进 CI，无需改 workflow。

**未验证项**：`pyright` 与 `pyright -p pyrightconfig.gui.json` 两步本地跑不了
（npm registry 返回 502，装不上）。由 CI 覆盖。
（后补：已装 pyright 1.1.414 验证，改动文件非环境类错误 0。）

---

## 十二、阶段 2 首战：`filters_core` 黄金等价测试

### 12.1 交付物

| 文件 | 作用 |
|---|---|
| `tests/golden_filters_core.py` | 11 个确定性输入场景 + 固化入口（`python tests/golden_filters_core.py`） |
| `tests/fixtures/filters_core_golden.json` | 42 个用例的固化输出契约 |
| `tests/test_filters_core_golden.py` | 4 条断言 + 2 条元检查 |

输入全部由**闭式公式**生成（无随机数、无时钟、无文件系统），跨机器可复现。
覆盖：空表 / 50 根 / 252 根边界 / 牛市 / 熊市 / 横盘 / 放量新低 / 缩量新低 /
缺指标列 / 脏值（NaN·inf·0·负价）/ 收盘价恰好等于 MIN_PRICE / 成交额充裕 / 成交额不足。

**11 个 filter 全部具备双向判决**（没有任何一个在所有场景结论单一）——
这是刻意的：结论恒定的 filter，其逻辑变化无法被黄金文件察觉。
测试里有一条元检查专门守这件事。

### 12.2 反向验证 10/10 全部转红

| 注入的变更 | 结果 |
|---|---|
| `min_price` 边界 `<=` → `<` | ✅ 红 |
| `sufficient_history` `>=252` → `>252` | ✅ 红 |
| `reason` 文案小数位 `.2f` → `.3f` | ✅ 红 |
| `details` 数值精度变化 | ✅ 红 |
| `cmf_positive` 判定反转 | ✅ 红 |
| `ad_slope` 阈值 `0` → `0.5` | ✅ 红 |
| **facade** turnover 阈值 2.5e6 → 9e6 | ✅ 红 |
| **facade** `min_volume` 主路径判定反转 | ✅ 红 |
| **facade** `volatility_contraction` 文案变化 | ✅ 红 |
| config `MIN_VOLUME` 200_000 → 250_000 | ✅ 红 |

第 1 项最初**没被抓到**——我的场景里没有收盘价恰好等于下界的样本，
`<=` 与 `<` 在此无差异。补了 `close_at_min_price_boundary_300` 场景后才抓到。

### 12.3 一个真实的坑：黄金文件一度固化了错误的语义

第一版测试**单独跑是绿的，全量跑是红的**。查下来是：

`filters.py:87-88` 在**模块级**替换了 `filters_core.filter_min_volume` 和
`filter_volatility_contraction`，第 90 行再把自身换成被补丁后的 core。
所以 `filters_core` 的行为**取决于有没有别的测试先 import 了 facade**：

```
未 import filters  →  min_volume 走旧的 share-volume 规则
已 import filters  →  min_volume 走 v79 的 CNY 成交额规则（多出 liquidity_basis 字段）
                      volatility_contraction 换成带 HV 分量的新实现
```

`scanner.py:58` 用的是 `from filters import run_all_filters`，**生产走的是补丁后的版本**。
换句话说，第一版黄金文件固化的是「未被补丁的裸 core」——不是生产实际执行的语义。

**修法**：固化前显式 `import filters`，把状态钉死在生产装配后的形态；
并补了带 `Amount` 列的场景，让 v79 主路径（`turnover_cny`）和
legacy 回退路径（`shares_fallback`）都进入契约。

这条也是**阶段 3「把模块级 install() 改为显式装配」的又一实证**：
覆盖层的 import 副作用会让「看起来独立的模块」行为随 import 顺序漂移，
而且漂移是静默的——这次是靠一次偶然的全量跑才暴露出来。

### 12.4 门禁

```
ruff check .                                              All checks passed
ruff check institution_scanner --select B,C4,SIM,PERF,PIE All checks passed
pytest                                                    158 passed  (154 → 158)
pyright（新文件）                                          非环境类错误 0
生产代码零改动（filters_core / filters / config* 均未触碰）
```

### 12.5 阶段 2 剩余

按 10.2 的顺序，下一个是 `downloader_core`（877 行、纯占比 51%、最高）。
`score_core` 已有 1 个测试文件，排最后。

> `downloader_core` 已于**第十三节**完成。`score_core` 仍是最后一个。

9 个零引用符号仍在等你确认（见 11.5），未动。

---

## 十三、阶段 2 第二战：`downloader_core` 黄金等价测试

### 13.1 交付物

| 文件 | 作用 |
|---|---|
| `tests/golden_downloader_core.py` | 16 个纯函数、161 个用例的场景构造与固化脚本 |
| `tests/test_downloader_core_golden.py` | 4 项检查（见下） |
| `tests/fixtures/downloader_core_golden.json` | 161 个用例的黄金快照（71 KB） |
| `tests/reverse_validate_downloader_core.py` | 10 项反向验证，可随时复跑 |

9 个网络绑定函数（`_fetch_one` / `download_batch` 等）需要真实 provider，不在等价测试射程内，未纳入。

4 项检查：

1. **逐用例匹配黄金输出**（含抛出的异常类型）
2. **函数溯源未漂移** —— 见 13.2
3. **每个 helper 必须产生多于一种输出** —— 防「语料里恒定」的假绿
4. **输入场景可重复** —— 两次 `build_cases()` 完全一致

### 13.2 函数溯源：两个 helper 早就被覆盖层换掉了

固化时记录了每个被调函数的 `module.qualname`。结果有 2 个并不在 `downloader_core` 里：

| helper | 实际绑定位置 |
|---|---|
| `_validate_ohlcv` | `institution_scanner.market_cache_performance.install.<locals>.validated_ohlcv` |
| `_normalize_cn_share_count` | `downloader_v51_base._normalize_cn_share_count` |

这不是缺陷 —— 生产装配后就是这个状态，`downloader_core` 内部（`download_ticker`、`get_market_cap`）调用它们时解析到的也是被替换后的版本，黄金文件测的正是生产语义。

但它意味着：**如果不记录溯源，将来任何一个新覆盖层把这两个函数再换一次，黄金文件会继续绿着，而它测的已经不是当初那个函数了**。所以溯源进契约，漂移即红。

固化前顺手验了一下 `market_cache_performance.install()`：连续调用 6 次输出指纹完全一致（有 `_MARKET_CACHE_PERFORMANCE_V107_INSTALLED` 幂等守卫），所以「别人先 import 了谁」不会让固化结果摇摆。

### 13.3 反向验证 10/10 —— 以及两次「未抓到」的完整复盘

| # | 注入的变更 | 结果 |
|---|---|---|
| D1 | `normalize_ticker`：92 开头不再归北交所 | ✅ 红 |
| D2 | `is_etf_ticker`：51 前缀不再算 ETF | ✅ 红 |
| D3 | `_is_excluded_security_name`：删掉关键词匹配分支 | ✅ 红（首轮未抓到，见下） |
| D4 | `_safe_cache_stem`：兜底名 `ticker` → `TICKER` | ✅ 红 |
| D5 | `_number_or_none`：接受 0（`>0` → `>=0`） | ✅ 红 |
| D6 | `_as_mapping`：丢弃 key 为 `a` 的项 | ✅ 红（首轮 SKIP，见下） |
| D7 | `_request_chunks`：去掉 worker 系数（500 → 100） | ✅ 红（首轮未抓到，见下） |
| D8 | `_validate_ohlcv`：排序方向反转 | ✅ 红 |
| D9 | `_requires_full_rebase`：最小重叠 2 → 3 | ✅ 红 |
| D10 | `_normalize_cn_share_count`：去掉 sanity 带宽校验 | ✅ 红（首轮未抓到，见下） |

**首轮是 8 抓到 / 2 未抓到。** 两个都值得拆开说，因为性质完全不同。

**D7 —— 我的注入本身是空改动，但它顺带捅出了一个真盲点。**

原注入是 `max(1, int(BATCH) * max(1, int(WORKERS)))` 改成 `max(2, ...)`。而
`TICKFLOW_BATCH_SIZE=100`、`TICKFLOW_MAX_WORKERS=5`，`size` 恒为 500，`max(1,500)` 与
`max(2,500)` 都是 500 —— **这个改动什么都没改。是注入写错了，不是测试不行。**

换成一个真改动（去掉 worker 系数，500 → 100）之后还面对第二个问题：三个输入是
0 / 7 / 250 个 symbol，全都小于 500，在 `size=500` 下一律退化成「0 或 1 个 chunk」——
**分块行为从来没有被观测过**。补了 501 个 symbol 的用例（切成 `[500, 1]`）才真正跨过边界。

**D10 —— 测试盲点。**

`_number_or_none` 在 `number > 0` 不满足时就返回 `None`，所以 8 个输入里的
`0`、`-1.0`、`"1.2亿"`、`"3.5万"`、`"abc"`、`"8,000,000"` **全部在进门之前就被挡掉了**，
`_share_count_evidence` 走的是 `raw is None` 分支。真正走到 sanity 判断的只有 `1.0e8`，
而它落在 `[1e6, 1e13]` 带内 —— 去掉校验，返回值不变。

补了 4 个输入：`50`（归一化为 5e5，带下）、`99`（9.9e5，带下）、`100`（正好 1e6，下界）、
`1e15`（带上）。改完立刻转红。

**补完这两处，第二轮又冒出 D3 —— 同一个模式的第三次。**

把关键词匹配整段换成 `return False`，居然还是绿的。`EXCLUDED_SECURITY_KEYWORDS` 是
`债/货币/同业存单/短融/中票/REIT/浙商沪`，而原来 8 个输入**一个都不含**，这个分支从未被走过。
补了 7 个输入把这 7 个词全覆盖，才转红。

**D6 首轮是 SKIP 不是失败** —— 我的脚本用 `\n` 写多行锚点，源文件是 CRLF，锚点命中 0 次。脚本问题。

### 13.4 从这三次失败里学到的（也是给下一站 `score_core` 的提醒）

「未抓到」必须分成两类归因，混在一起会得出完全相反的结论：

1. **我的注入是空改动** —— 测试没错，是我构造的变更在给定输入下不改变行为。
   判定方法：先算一遍改动前后的取值（D7 里 500 vs 500，一眼可见）。
2. **语料没走到那个分支** —— 真盲点，必须补输入，否则那个分支一直裸奔。
   判定方法：找出该分支的守卫条件，构造一个能穿过守卫的输入。

这一轮 3 个「未抓到」里有 2 个属于第 2 类，1 个（D7）属于第 1 类但**同时**掩盖着一个第 2 类。

另一条诚实的局限：第 3 项元检查（每个 helper 必须产生多于一种输出）
**没能自动发现 D3 和 D10**。这两个 helper 整体输出确实有多种值（True/False、None/数字），
只是**特定分支**没被覆盖。这条元检查只能防「helper 整体恒定」，防不了「分支未覆盖」——
它是必要条件，不是充分条件。真正的兜底仍然只有反向验证。

### 13.5 门禁

```
ruff check .                                              All checks passed
ruff check institution_scanner --select B,C4,SIM,PERF,PIE All checks passed
pytest                                                    162 passed  (158 → 162)
python -m compileall -q .                                 rc=0
python -c "import main, daily_pipeline, web_report_v81"   OK
pyright（新文件）                                          非环境类错误 0
生产代码零改动（downloader_core / downloader_v51_base 均未触碰）
```

反向验证可随时复跑：`python tests/reverse_validate_downloader_core.py`。
该脚本会临时改写生产文件再还原（`finally` 保证），别和别的测试并行跑；
若中途被杀，第一件事是 `git status` 看那两个文件有没有还原。

### 13.6 阶段 2 剩余

只剩 `score_core`（已有 1 个测试文件）。9 个零引用符号仍在等你确认（见 11.5），未动。

> `score_core` 已于**第十四节**完成。至此阶段 2「建网」三站全部收官。

---

## 十四、阶段 2 收官：`score_core` 黄金等价测试

### 14.1 交付物

| 文件 | 作用 |
|---|---|
| `tests/golden_score_core.py` | 25 个函数、514 个用例的场景构造与固化 |
| `tests/test_score_core_golden.py` | 4 项检查（同 13.1） |
| `tests/fixtures/score_core_golden.json` | 514 个用例的黄金快照（178 KB） |
| `tests/reverse_validate_score_core.py` | 15 项反向验证，跨 6 个文件，可复跑 |

### 14.2 最扎眼的发现：25 个被测函数里，只有 9 个还在 `score_core`

provenance 记录了每个被调函数的真实归属，结果是：

| 归属 | 个数 | 函数 |
|---|---|---|
| `score_core` | 9 | `_clamp` `_is_finite` `_normalize_to_range` `tradable_price_decimals` `cyclical_turn_factor` `smart_money_stage` `value_trap_risk_score` `model_weight_signature` `_has_finite_values` |
| `score_acceleration_v79` | 7 | `_series` `_latest` `_rolling_mean` `_safe_return` `score_trend` `classify_style` `_score_dimensions_available` |
| `score_scale_migration_v95` | 3 | `score_volume` `score_accumulation` `score_structure` |
| `score_endpoint_acceleration_v79` | 3 | `breakout_score` `execution_quality_score` `value_trap_risk` |
| `score`（v113 facade） | 2 | `score_ticker` `score_volatility` |
| `score_cache_guard_v80` | 1 | `entry_point` |

一个 1183 行的「core」，**三分之二的函数体不在自己文件里**。这不是缺陷（这就是该项目的演进方式），但它意味着

> 不记录溯源的话，「改 `score_core.py`」这句话基本没有意义——你改的那行很可能根本不会被调用。

这条也是 10.3「把模块级 `install()` 改成显式装配」最有力的实证。

### 14.3 同一个坑的第二次：这次是 `entry_point`

和 12.3 一模一样的剧本，但换了个更深的层级。

我按 `filters` 的经验先写了 `import score`——**不够**。`score.py` 的 import 链只到 v79/v95/endpoint/facade，到不了 `score_cache_guard_v80`。而 v80 是这么进来的：

```
scanner → analytics → institution_scanner.analytics_runtime
        → backtest_acceleration_v77 → score_cache_guard_v80  (模块级 install())
```

而 `score_cache_guard_v80.py:115` 在模块级执行 `install()`，把 `_v79.entry_point` 和
`_score.entry_point` 一起换掉。

**单独跑测试绿，全量跑红**——因为全量跑时有别的测试先 import 了那条链。报错是 provenance 检查直接点名的：

```
{'entry_point': ('score_acceleration_v79.entry_point', 'score_cache_guard_v80.entry_point')}
```

**修法**：golden 脚本改为 `import scanner`（真正的生产入口），而不是 `import score`。
用干净解释器实测 `import scanner` 后 `entry_point` 确实解析到 `score_cache_guard_v80`，
与固化值一致。代价是固化多花约 1 秒，换来的是「将来任何新覆盖层会自动进入契约，
而不是静默漂移」。

这次和 12.3 的区别在于：**provenance 检查让它在 30 秒内就定位到了是哪个函数、从哪绑到哪**，
而不是像上次那样靠一次偶然的全量跑才发现、再回头查。这个检查算是回本了。

### 14.4 反向验证 15/15，首轮全过

跨 6 个文件，刻意铺开到每一层：

| # | 注入 | 结果 |
|---|---|---|
| S1 | `_clamp`：非有限值返回上界而非下界 | ✅ 红 |
| S2 | `_is_finite`：丢掉 `isfinite` 检查 | ✅ 红 |
| S3 | `_normalize_to_range`：退化区间 0.5 → 0.25 | ✅ 红 |
| S4 | `tradable_price_decimals`：股票/ETF 精度互换 | ✅ 红 |
| S5 | `smart_money_stage`：ACCUMULATION 门槛 35 → 36 | ✅ 红 |
| S6 | `value_trap_risk_score`：结果减半 | ✅ 红 |
| S7 | `cyclical_turn_factor`：数据不足兜底分 50 → 51 | ✅ 红 |
| S8 | `model_weight_signature`：精度 4 位 → 3 位 | ✅ 红 |
| S9 | **facade** `score_volatility`：`max_score` 15 → 20 | ✅ 红 |
| S10 | **facade** `score_ticker`：trigger 覆盖率基数 0.75 → 0.65 | ✅ 红 |
| S11 | **v79** `_latest`：去掉末尾非有限时的回退 | ✅ 红 |
| S12 | **v95** `score_volume`：原始分放大 10% | ✅ 红 |
| S13 | **v95** `score_accumulation`：原始分放大 10% | ✅ 红 |
| S14 | **endpoint v79** `breakout_score`：均线多头 15 → 16 分 | ✅ 红 |
| S15 | **v80** `entry_point`：丢弃 `price_decimals` | ✅ 红 |

**这是三站里第一次首轮全过**（filters 10/10 是补了 1 个场景后，downloader 是 8/10 补了 3 处）。
原因是 13.4 那套归因模板这次被用在了**写注入之前**而不是失败之后：先算出改动前后取值是否真不同，
再确认该分支的守卫条件有没有被语料穿过。省下了一整轮返工。

### 14.5 仍然主动补的两处分支覆盖

即便如此，固化前扫了一遍分支覆盖，补了两处：

- **`classify_style` 有 7 个返回分支，原来的语料只走到 4 个**。
  缺「趋势成长」「资金吸筹」「低波动防守」。原因是 `ROC(20)` 在上行情景下只有约 2.4%（门槛 12%）、
  `VolMA20/VolMA120` 恒等于 1（门槛 1.25）、`ATR14/Close` 恒为 4%（门槛分别是 4.5% 和 2.5%）。
  补了 `high_momentum`(drift=0.008)、`volume_surge`(尾段放量 3 倍)、
  `high_volatility`(振幅 3%)、`low_volatility`(振幅 0.8%) 四个场景，7 个分支全覆盖。
- **`score_acceleration_v79._latest` 有个「末尾非有限则回退到最后一个有限值」的分支，
  而 `score_core` 自己的 `_latest` 没有**。原语料全是干净帧，那个分支从未运行。
  补了 `tail_nan` 帧（末 5 行 Close 为 NaN），实测回退生效：22.819 而非末行的 22.932。

### 14.6 一条诚实的局限：输出等价测不到纯副作用

规划 S15 时我本想注入 `score_cache_guard_v80.entry_point` 的缓存判定
（`if previous is not None and previous != signature: 清缓存`）。想清楚后放弃了——
**这个 wrapper 不改变返回值，只管理缓存状态**。改成什么、甚至整段删掉，
单帧的输出都不会变。

最后改用了 `price_decimals=price_decimals` → `price_decimals=None`（穿透到被包装的原函数，
结果不再取整），才有区分度。

结论：黄金等价测试能守住「输出」，守不住「副作用」。缓存、日志、指标上报这三类改动，
它天然是瞎的。要覆盖得靠另一种测试，不是把 golden 语料加厚。

### 14.7 门禁

```
ruff check .                                              All checks passed
ruff check institution_scanner --select B,C4,SIM,PERF,PIE All checks passed
pytest                                                    166 passed  (162 → 166)
python -m compileall -q .                                 rc=0
python -c "import main, daily_pipeline, web_report_v81"   OK
pyright（新文件）                                          非环境类错误 0（+2 条 pandas/numpy 环境噪音）
生产代码零改动（score_core / score / v79 / v95 / endpoint / v80 六个文件均未触碰）
```

复跑：`python tests/reverse_validate_score_core.py`（会临时改写 6 个生产文件再还原，别并行跑）。

### 14.8 阶段 2 收官，以及下一步

三站全部完成：

| core | 行数 | 用例 | 反向验证 | 生产改动 |
|---|---|---|---|---|
| `filters_core` | — | 42 | 10/10 | 0 |
| `downloader_core` | 877 | 161 | 10/10 | 0 |
| `score_core` | 1183 | 514 | 15/15 | 0 |

**下一步该动阶段 3 了**——把模块级 `install()` 改成显式装配。理由不是它「不优雅」，
而是这三次建网**每一站都被 import 副作用咬了一口**：

- `filters.py:87-88`：模块级替换 → golden 一度固化了非生产语义；
- `downloader.py` / `downloader_v51_base.py`：同上，且 `_validate_ohlcv` 被 v107 换掉；
- `score.py` + `score_cache_guard_v80`：两级链式替换，第二级只有 `import scanner` 才触发。

也就是说：**在显式装配之前，任何「改这个函数」的动作都带着一个前置风险——你不确定你改的是
不是生产真正执行的那个。** 现在网建好了，改错了会红；但先把装配显式化，才能让「改哪里」
这件事本身变清楚。

9 个零引用符号仍在等你确认（见 11.5），未动。

---

## 十五、阶段 3 前置：装载清单快照（2026-09-14）

> **本节记录的是改造前的状态**。改造在第十六节，静态调用点数已从 57 降到 30。

阶段 3 是「把模块级 `install()` 改成显式装配」。在动手之前先做一件事：**把当前装配结果
冻结成契约**。原因很实在——改造本身就是要动装载顺序，如果手上没有「顺序原来长什么样」的
快照，改漏一个 `install()` 或者让两个互相包裹的覆盖层换了先后，测试全绿、线上静默变样。

本节 production code 零改动。

### 15.1 方法：调用事件追踪 + 前后快照差分

第一版设计是「属性普查」：遍历所有项目模块，凡是 `attr.__module__` 与宿主模块不同的，
就算被覆盖层替换。这个设计**作废**，它有两个硬伤：

- `from collections.abc import Callable` 与覆盖层替换长得一模一样，会把 12 个
  stdlib 名字误判成替换（根因是 `importlib.util.find_spec('collections.abc').origin`
  返回字符串 `'frozen'`，`Path('frozen').resolve()` 相对 CWD 解析后仍落在仓库内）；
- façade（`sys.modules[__name__] = _core`）会让 `score` 名下**整个 API** 都被判为
  「被替换」，`score` 与 `score_core` 实际是同一个对象。

改用 `sys.settrace` 捕获 `install()` 的调用事件，在调用前/返回后各拍一次全项目模块
`__dict__` 的 `id()` 快照，取差分。这样记录的**只是覆盖层自己写下的东西**，导入与 façade
都不会污染，同时顺带得到执行顺序。

两个实现细节：

- `Path.resolve()` 必须记忆化。第一版原型每次快照都对所有模块重新 resolve，单次
  `import scanner` 耗时 **52.88s**；加缓存后 **2.81s**。
- 每个入口必须**独立子进程**。`import main` 会顺带导入 `scan_service`，同进程内连续
  捕获会让后续入口全部命中 `sys.modules` 缓存——实测 `scan_service` 因此报出
  「0 次 install 调用」。这是观察到的，不是推测。

### 15.2 交付物

| 文件 | 作用 |
|---|---|
| `tests/assembly_manifest.py` | 捕获逻辑 + 静态清单扫描；`--write` 重新生成全部 fixture |
| `tests/test_assembly_manifest.py` | 7 个契约测试 |
| `tests/reverse_validate_assembly_manifest.py` | 7 个注入的反向验证 |
| `tests/fixtures/assembly_manifest_{scanner,main,daily_pipeline,scan_service}.json` | 四个入口的运行时快照 |
| `tests/fixtures/assembly_module_level_sites.json` | 57 处模块级调用点的静态清单 |

契约冻了三层：**调用序列**（每个 install 替换了什么、换成什么）、**最终归属**（所有被
install 触碰过的符号在 import 结束后指向谁）、**静态调用点清单**（含运行时到不了的那部分）。

### 15.3 数字（含对我自己前几节数字的修正）

模块级 `install()` 调用点，这个数字被我数错了三次：

| 来源 | 数字 | 漏了什么 |
|---|---|---|
| 第一节概览 | 20 | 只按 `def install` 的文件数粗估 |
| 阶段 3 首次 AST 审计 | 52 | 漏 3 个具名变体（`install_pre_facade` / `install_post_facade` / `install_analytics_alignment`） |
| 本节 · 补齐具名变体 | 55 | 漏 2 处**别名导入** |
| 本节 · 解析别名后 | **57** | —— |

别名那两处是 `downloader_v51.py:154` 的 `_install_market_cache_performance(_core)` 和
`report_v51.py:212` 的 `_install_report_determinism(_core)`——`from x import install as
_install_y`，按名字匹配必然漏。而 `downloader_v51.py:154` 恰恰是往 `downloader_core`
装 16 个符号的那一步，漏它等于漏掉整条下载链路。

**交叉验证**：静态清单与运行时捕获现在完全对齐——运行时独有的调用点 **0 个**。
`test_static_inventory_covers_every_observed_module_level_call` 专门守这条，防止以后
再出现新的别名写法偷偷绕过。

### 15.4 四个入口的装配规模

| 入口 | install() 调用 | 其中模块级 | 被触碰符号 | 被不同实现覆盖过 |
|---|---|---|---|---|
| `scanner` | 119 | 37 | 244 | 18 |
| `main` | 160 | 50 | 280 | 19 |
| `daily_pipeline` | 163 | 53 | 290 | 20 |
| `scan_service` | 125 | 40 | 258 | 19 |

调用次数远多于调用点数，是因为 `install()` 之间会互相调用（例如
`institution_scanner/score_runtime.py:12` 的 `install()` 会拉起 v79/v95/权重缓存三个），
且有 `_INSTALLED` 幂等守卫——重复调用实际替换 0 个符号。

### 15.5 真正顺序敏感的 18 个符号（`scanner` 入口）

这才是阶段 3 动刀时唯一需要屏住呼吸的地方。最长的链：

```
analytics_core._backtest_one_ticker_cached   写 10 次 / 5 个不同实现
  backtest_cache_acceleration_v80
  → backtest_incremental_v78
  → backtest_cache_acceleration_v80
  → backtest_alignment.aligned_cached
  → institution_scanner.point_in_time_backtest.pit_cached
```

`analytics_core._legacy_apply_backtest_ranking` 写 7 次、4 个实现（v96 → reliability →
v102 → v102_1），`_backtest_one_ticker` 写 4 次、3 个实现。`score_core.entry_point`
是干净的 v79 → v80 包裹链，与 13/14 节的 provenance 结论一致。

**注意有 3 个符号是来回翻转的**，不是干净的层层叠加：`analytics_core.market_cache_state`
与 `market_prefix_matches` 在 v77 的两个实现之间翻了 4 次；
`analytics_core.BACKTEST_PROFILE_ALIGNMENT_VERSION` 从 v113 值又翻回 v97 值。
大概率是不同导入路径各装一次，但**是否设计意图需你确认**（见 15.8）。

### 15.6 已复核、判定为非缺陷（两条我自己造的）

- **「所有重复写入都是同一个值」——假指标，已修正。** 第一版把 provenance 的解析放在
  import 结束后的第二个循环里，于是每一步记录的都是**最终值**，中间态被抹平，
  统计出「被多次替换 109 个、最终值不同 0 个」。改成在 trace 的 return 事件里就把新对象
  抓在手上（保引用防止 `id()` 复用）、provenance 事后解析，才得到真实链条。
  修完才从「18 个」里看出 5 条三跳以上的链——**原版本这份契约根本看不出谁覆盖了谁**。
- **「`score` 与 `score_core` 最终实现不同」——误读，未写入结论。** 清单里两个 key 的
  链条看起来终点不同，实际是 `dict.fromkeys` 去重显示把 `v79 → v77 → v79` 压成了
  `v79 → v77`。查 `final` 确认两者一致（本来就是同一个模块对象）。

### 15.7 覆盖不到的 4 处，以及一个差点误报

四个入口合计覆盖 53/57，剩余 4 处谁都不执行：

| 调用点 | 状态 |
|---|---|
| `score_runtime_v97.py:58` | `RETIRED_FROM_PRODUCTION_PATH` 已登记 |
| `checkpoint_inputs_v59.py:79` | 同上 |
| `scanner_resume_v68.py:128` | 同上 |
| `gui_process_v64.py:133` | 仅 GUI 路径可达 |

**差点误报**：这三个模块全项目零导入者（全库 `grep` 只命中 `ARCHITECTURE.md` 和
`institution_scanner/runtime_inventory.py` 的字符串引用），我差点把它们当「新发现的死代码」
写进来。查 `runtime_inventory.py` 才发现项目**已经显式登记**了：

```python
RETIRED_FROM_PRODUCTION_PATH: Final[tuple[str, ...]] = (
    "analytics_compat_v97", "backtest_profile_alignment_v95",
    "score_runtime_v97", "checkpoint_inputs_v59", "scanner_resume_v68",
)
# migration_policy = "GOLDEN_EQUIVALENCE_BEFORE_REMOVAL"
```

即「已退役、删除前需黄金等价验证」——是有意为之，不是漏网之鱼。**它们不该进删除清单。**
`UNREACHED_BY_ANY_ENTRY_POINT` 把这四个冻结住，以后多一个少一个都会红。

**已知盲区**：GUI 入口（`gui_v85.py`）不在契约内。它依赖 `customtkinter`，本环境
`tkinter` 与 `customtkinter` 均缺失，无法捕获。`gui_process_v64` 由
`config._install_gui_runtime_contract_if_ready()` 懒导入，同样走不到。

### 15.8 需你确认

1. 15.5 里 3 个**来回翻转**的符号（`market_cache_state` / `market_prefix_matches` /
   `BACKTEST_PROFILE_ALIGNMENT_VERSION`）：是不同导入路径各装一次的自然结果，还是
   有覆盖层被意外装了两遍？
2. `RETIRED_FROM_PRODUCTION_PATH` 里的 5 个模块：是否已有删除计划？（本节只是确认它们
   不该被当成新缺陷重复报一遍。）
3. GUI 路径要不要补一份快照？需要能装 `customtkinter` 的环境。

### 15.9 反向验证 7/7

契约测试不改代码就绿是假保护，所以逐个注入并确认变红：

| 注入 | 扰动 | 必须命中的断言 |
|---|---|---|
| A1 | install 把某符号换成另一个函数 | rebinds / final provenance |
| A2 | install 多替换一个符号 | rebinds（now also） |
| A3 | 去掉 `score.py:21` 的阈值迁移 install | install_calls + final |
| A4 | v77 里两处模块级 install 顺序互换 | step 顺序 |
| A5 | v80 去掉模块级自装载（改由 v77 触发） | step rebinds + 静态清单 |
| A6 | 新增一处模块级 install | install_calls + 静态清单 |
| A7 | 整层覆盖层不再模块级自装载 | install_calls + final |

```
反向验证: 7 抓到 / 0 未抓到 / 0 跳过
还原后复跑: rc=0
```

A5 是最有价值的一个：v80 去掉自装载后，`backtest_acceleration_v77.py:44` 仍会调用它，
**最终装配完全不变**，只有那一步的 rebinds 从 0 变成 4——也就是说普通的功能测试根本
看不出来，只有这份清单能。

### 15.10 确定性

快照测试自身不稳定就是假保护，验了两次：

- 连续 `--write` 两次，5 个 fixture 的 SHA-256 逐一相同；
- `PYTHONHASHSEED=12345` 重跑 `scanner`，与基线逐字节相同。

输出全部排序、不迭代 set、不含时间戳；对象 `repr` 只用于标量（避免嵌入内存地址）。

### 15.11 门禁

```
ruff check  (3 个新文件)                    All checks passed
ruff format --check                         3 files already formatted
pytest                                      173 passed  (166 → 173，+7)
python -m compileall -q                     rc=0
pytest tests/test_assembly_manifest.py      7 passed in 15.6s
生产代码零改动（反向验证改写的 5 个文件均已逐字节还原，git status 无残留）
```

复跑反向验证：`python tests/reverse_validate_assembly_manifest.py`
（会临时改写 `score_weight_cache_v79.py` / `score.py` / `backtest_acceleration_v77.py` /
`score_cache_guard_v80.py` / `universe_cache_acceleration_v78.py`，别并行跑）。

### 15.12 对阶段 3 的意义

网建好了，可以动刀了。阶段 3 的验收标准现在是可判定的：

1. 57 处模块级调用点归零（fixture 随每步改造同步更新，每删一处都要重新 `--write` 并说明）；
2. 四个入口的 `final` 装配结果与现在**逐字节一致**——这是硬指标，动了语义就红；
3. 加一条禁写测试，防止新的模块级 `install()` 悄悄长回来。

15.5 那 18 个符号是动刀时唯一需要盯住的：其余 226 个符号即便装载顺序变了，最终归属也不变。

---

## 十六、阶段 3 执行：移除 27 处模块级自装载（2026-09-15）

### 16.1 先补环境：GUI 路径纳入契约

第十五节留了一条盲区——GUI 入口 `gui_v85.py` 因为缺 `tkinter` / `customtkinter` 抓不到。
补完了：

- CI venv 由 managed Python 3.13.12 构建，**不带 tk 支持**；
- 系统 Python **3.14.6 是完整安装**，tkinter + customtkinter 5.2.2 + pandas + numpy 齐备；
- 补装 pytest 后，`import gui_v85` 可安全执行（`if __name__ == "__main__"` 保护，不弹窗）。

**先证明捕获与解释器无关，再采信 GUI 数据**：用两个解释器分别捕获 `scanner`，steps 与
final **完全一致**；`main` / `daily_pipeline` / `scan_service` 同样一致。所以 GUI 入口作为
第 5 个入口纳入契约是安全的，测不到时会 skip 而不是误报。

GUI 入口是一条**独立的窄装配路径**：只导入 GUI 模块，不走 scanner/analytics/score 链，
全部装配只有 3 个符号（`gui_core._popen_group_kwargs`、`gui_core.terminate_process_tree`
及其 `_INSTALLED`）。它是唯一能触达 `gui_process_v64` 的入口。

### 16.2 做法：逐个删、逐个验，不靠推理

不假设「这个 overlay 显然被集中装配点覆盖了」。写了一个拆除脚本，对每处候选：

1. 删掉模块级 `install()` 调用行（并整理留下的空行）；
2. 重新捕获四个生产入口的 `final` 装配；
3. 与冻结基线**逐项比对**——一致才保留，任何差异立即回滚文件。

判断「自装载」用运行时数据而非猜：调用点文件 == 被调用函数定义文件。剩下的都是
「跨模块调用」，也就是已经存在的集中装配点。

**27 处全部一次通过**（22 + 4 + 1），只有 `runtime_v83` 第一批被拦下。

### 16.3 唯一需要手工接管的：`runtime_v83`

它是唯一没有任何集中装配点认领的 overlay（28 个符号）。删掉自装载后，`scanner` 直接红：

```
analytics_core._v82_single_recency_guard_original
  runtime_v83._install_ranking_wrapper.<locals>.layered_finalize
  → signal_lifecycle.finalize_signal_ranking
另有 6 个符号不再被触碰
```

处理：`analytics_runtime.install_pre_facade` 里，紧跟 `_universe_cache_acceleration.install()`
之后显式调用 `_runtime_v83.install()`——这是它在原 import 序列中的相对位置（step #64，
在 universe 之后、`install_pre_facade` 之前）。它内部还会拉起 `lifecycle_acceleration_v83`。
加完四入口 final 恢复一致。

### 16.4 结果

| 指标 | 改造前 | 改造后 |
|---|---|---|
| 模块级调用点（静态） | 57 | **30** |
| 其中 **自装载** | 27 | **0**（3 个已登记退役的除外） |
| 其中 集中装配点 | 30 | 27 |
| `scanner` install 调用 / 模块级 | 119 / 37 | 86 / **15** |
| `main` | 160 / 50 | 124 / **25** |
| `daily_pipeline` | 163 / 53 | 126 / **27** |
| `scan_service` | 125 / 40 | 91 / **17** |
| 被触碰符号数 | 244 / 280 / 290 / 258 | **244 / 280 / 290 / 258（一字未变）** |

代码：28 个文件，**+5 / −81 行**（27 处 × 3 行，正好对上；+5 是 `analytics_runtime` 接管
`runtime_v83` 的 import、调用与注释）。

**符号数从头到尾一字未变**，这是「行为没变」最硬的一条证据——装配顺序变了，装配结果没变。

### 16.5 预期行为差异

**没有。** 这正是本次改造的全部意义：

- 四个入口的 `final` 装配与改造前逐项相同；
- 三个黄金等价套件（filters_core 42 / downloader_core 161 / score_core 514 用例）全绿；
- 全套测试 173 → **174 passed**（+1 是新增的自装载归零闸门）。

唯一的可观察差异是**装载时机**：这些 overlay 不再在「被 import 的瞬间」装配，而是在
集中装配点被调用时装配。对任何已经 import 生产入口的代码路径，结果完全相同。

### 16.6 闸门：防止自装载长回来

两道锁：

1. `test_no_module_installs_itself_at_import_time` —— 静态扫描，断言除 3 个已登记退役的
   模块外，没有任何模块在模块作用域调用自己的 `install()`。区分「自装载」与「跨模块装配」：
   `install()` 裸调用且名字是本文件定义的函数为自装载；`_x.install()` 与
   `from y import install as _install_z` 都是在装**别的**模块，属于集中装配，放行。
2. 反向验证新增 **A8**：往 `score_weight_cache_v79.py` 末尾塞回一个 `install()`，
   必须变红——证明第 1 道锁真的会咬。

### 16.7 反向验证重新瞄准（8/8）

改造把 A5/A6/A7 的锚点删掉了，这三个注入**失效并跳过**——反向验证套件本身变成了摆设。
这是改造的一部分成本：它也得跟着改。重新瞄准后：

| 注入 | 扰动 | 结果 |
|---|---|---|
| A1 | install 把某符号换成另一个函数 | 抓到 |
| A2 | install 多替换一个符号 | 抓到 |
| A3 | `score.py:21` 阈值迁移装配被摘 | 抓到 |
| A4 | v77 两处装配顺序互换 | 抓到 |
| A5 | `analytics_runtime` 漏掉 `_runtime_v83.install()` | 抓到 |
| A6 | `analytics_runtime` 多加一次冗余装配 | 抓到 |
| A7 | v77 漏掉 `_tradeability_v80.install()` | 抓到 |
| A8 | `score_weight_cache_v79` 模块级 install 死灰复燃 | 抓到 |

```
反向验证: 8 抓到 / 0 未抓到 / 0 跳过
```

A5/A7 现在打的是集中装配点——也就是改造后**唯一**决定装配的地方，比原来的自装载锚点更
贴近要害。

### 16.8 门禁

```
ruff check .                                        All checks passed
ruff check --select W,E1,E2,E3,F（本次 28 个文件）    0
pytest（venv 3.13.14）                              174 passed, 1 skipped
pytest（系统 3.14.6，含 GUI 入口）                    21 passed
python -m compileall -q                             rc=0
反向验证                                             8/8，还原后 rc=0
CRLF 保留                                            是（splitlines(keepends=True) 原样写回）
```

GUI 那条在 venv 下 skip（无 tkinter），在系统 Python 下通过——两种环境都验证过。

### 16.9 剩余 30 处的性质

| 类别 | 数量 | 说明 |
|---|---|---|
| 集中装配点 | 27 | `analytics_runtime`(2) / `backtest_acceleration_v77`(10) / `backtest_command_v76`(4) / `main`(4) / `score`(2) / `daily_pipeline`(2) / `analytics`(2) / `downloader_v51`(1) / `report_v51`(1) / `scan_service`(1) |
| 已登记退役 | 3 | `checkpoint_inputs_v59` / `scanner_resume_v68` / `score_runtime_v97` |

前 27 处是**目标状态，不是欠债**——它们就是「显式装配」本身，装配顺序写在明面上。
后 3 处等在删除队列里（`RETIRED_FROM_PRODUCTION_PATH`，策略
`GOLDEN_EQUIVALENCE_BEFORE_REMOVAL`），不该被当成待改造项。

### 16.10 需你确认（新增 2 条，15.8 那 3 条仍待答）

1. `runtime_v83` 我放在 `install_pre_facade` 的 `_universe_cache_acceleration.install()`
   之后。它在原序列里确实紧跟 universe，但它原本排在 `backtest_acceleration_v77` **之后**，
   现在排到了**之前**。验证结果是 final 完全一致（两者不争抢同一符号），但顺序语义变了——
   这个位置你是否认可？
2. `backtest_command_v76.py:33-36` 现在是 `main` 入口的集中装配点（装 resonance /
   production_activation / math_integrity / calibration_math），但它自己在模块作用域执行。
   要不要进一步收进 `analytics_runtime`？风险更高（会改变 main 的装配时机），我没动。

---

## 十七、收尾复核：过期数字与一个新风险（2026-09-15）

阶段 3 报完之后回头校了一遍**报告本身**，发现四处问题。都是「报告与代码不一致」，
不是代码缺陷，但会误导下一个读报告的人——尤其是第 17.3 条。

### 17.1 `analytics_core` 超预算：第九节已解决，任务单却还开着

任务单里「处理 analytics_core 超字节预算 642 字节」一直挂着 pending，实际第九节
已经做过了：`_robust_mean` 与 `_purged_sample_split` 两个零引用函数删除后，
160,642 → **159,789**，`test_legacy_giant_modules_are_shrink_only` 现在通过。
是任务单漏标完成，不是没做。已补标。

顺带复核了删除的安全性：`_robust_mean` 全库零引用（搜索命中的全是另一个仍在用的
`_weighted_robust_mean`）；`_purged_sample_split` 是 `_purged_split_label` 的恒等
转发，被包装者仍有 8 处引用。

### 17.2 但余量已经薄到不能碰（新风险）

| 模块 | 实际 | 预算 | 余量 |
|---|---|---|---|
| `analytics_core.py` | 159,789 | 160,000 | **211** |
| `report_core.py` | 104,909 | 105,000 | **91** |
| `gui_core.py` | 104,866 | 105,000 | **134** |
| `gui.py` | 94,033 | 100,000 | 5,967 |
| `scanner.py` | 78,111 | 80,000 | 1,889 |
| `signal_lifecycle_core.py` | 67,788 | 70,000 | 2,212 |

三个模块余量不足 250 字节——**一行稍长的注释就能让 CI 转红**。这本身不是缺陷
（预算的目的正是逼你别再往里加），但它意味着任何往这三个文件加代码的需求，
都必须先做等量或更多的提取。要不要设一个「预警带」（余量 <1KB 时单独告警，
而不是等超预算才失败）属治理口径，需你定，我没动。

### 17.3 第八节三个数字，两个是错的（已就地更正）

| 项 | 原写 | 实测 | 说明 |
|---|---|---|---|
| 覆盖层 | 73 | **69** | 73 把 4 个同名测试文件算成了覆盖层 |
| `install*()` 定义 | 62 | **63** | 57 个 `install` + 6 个具名变体 |
| 模块级调用点 | 20 | **30** | 阶段 3 前是 57，不是 20 |

**「20」这个错误值得单独讲**：它是按 `def install` 的文件数粗估出来的，而阶段 3
真正要动的就是这个数。用粗估数当改造目标，等于一开始就把工作量看轻了三分之二。
15.3 已经修正过一次，但第八节这个「总体评价」没跟着改——它是全篇最可能被先读的
一节。已补上更正块。

### 17.4 W292 溯源：不是阶段 3 引入的

全库 11 处 W292（文件末尾缺换行），本次改动文件里命中 2 处
（`backtest_web.py:235`、`verify_output.py:450`）。用
`git show HEAD:<file> | ruff check --stdin-filename <file> -` 比对提交版本，
确认两处在 HEAD 里就存在（当时在 246 / 456 行，被后续编辑推移了行号）。
即：**阶段 3 的 27 处删除没有引入任何新的 W292**。删掉末行的 `install()`
恰好是最容易弄丢结尾换行的操作，这条我原本最担心，所以专门验了。

### 17.5 当前状态

| 项 | 状态 |
|---|---|
| 任务单 | 12 / 12 完成 |
| 测试 | 175 passed / 1 skipped |
| `ruff check .` | All checks passed |
| 反向验证 | 8 / 8 抓到，还原后 rc=0 |
| CRLF | 保留 |
| 模块级自装载 | 27 → **0** |
| 待你确认 | 5 条（15.8 三条 + 16.10 两条）+ 11.5 的 9 个零引用符号 |

---

## 十八、9 个零引用符号：核到可执行为止（2026-09-15）

11.5 留了 9 个零引用符号等你定夺，但当时只有「grep 搜不到」这一个证据。
grep 不足以支撑删除决定——它看不到 `getattr`、看不到 `__all__` 再导出、
看不到 HTML/JSON 模板里的引用。所以补了一轮 AST 级审计，把这 9 个核到
「可以照着删」的程度。

审计覆盖四类 grep 看不到的情况：字符串字面量（动态访问）、`__all__` 导出、
非 `.py` 文件（模板/配置/文档）、定义模块本身是否被 import。

### 18.1 结论：9 个全是真零引用

| 符号 | 定义位置 | 判定 |
|---|---|---|
| `GUI_VIEW_MODEL_VERSION` | `gui_view_model.py:13` | 可删 |
| `PERFORMANCE_CURVE_CSV_NAME` | `performance_curve_contract.py:2` | 可删（但见 18.3） |
| `PERFORMANCE_CURVE_JSON_NAME` | `performance_curve_contract.py:3` | 可删（但见 18.3） |
| `PERFORMANCE_CURVE_SECTION_ID` | `performance_curve_contract.py:4` | 可删 |
| `SCAN_RUNTIME_FACADE_VERSION` | `scan_runtime.py:29` | 可删 |
| `parse_report_period` | `fundamental_schema.py:236` | 可删 |
| `_date_text` | `fundamental_schema.py:247` | 可删（见 18.2） |
| `inject_backtest_into_html` | `backtest_web.py:203` | 可删 |
| `read_backtest_summary` | `pit_page_semantics.py:20` | 可删 |

全部满足：无跨文件引用（**含测试文件**）、无 `getattr`/字符串动态访问、
不在 `__all__` 里、无 HTML/JSON/YAML 模板引用。

注意「定义模块被 import」≠「符号被引用」：`fundamental_schema.py` 被 7 处
导入、`scan_runtime.py` 被 `scan_service.py:16` 导入，但它们导入的都是**别的**
符号，这几个仍然无人使用。

### 18.2 `_date_text` 是向量化的遗留物，不是意外孤儿

`fundamental_schema.py` 里有一对：`_date_text`（标量，247 行）与
`_date_series`（向量化，252 行）。实际在用的是 `_date_series`
（287、288 两处），`_date_text` 全库只剩自己的定义行。

即：**它是向量化改造时被 `_date_series` 取代的前身**，不是「不小心忘了接」。
这类比单纯的没引用更确定——删掉不会挖掉任何人的下一步。

### 18.3 新发现：`performance_curve_contract.py` 是一个未被采用的契约

这一条改变了删除的性质，所以单独拎出来。

整个模块只有 4 行（1 行 docstring + 3 个常量），**全库 0 处 import**——
不是 3 个常量死了，是**整个模块是孤儿**。更关键的是它的值：

```
PERFORMANCE_CURVE_CSV_NAME  = "PerformanceCurve.csv"
PERFORMANCE_CURVE_JSON_NAME = "PerformanceCurve.json"
PERFORMANCE_CURVE_SECTION_ID = "performance-curves-v1"
```

前两个值在别处被**硬编码**着：
`performance_curve_runtime.py:36,37,48,54` 与 `performance_curve.py:33,34`。
第三个值 `performance-curves-v1` 全库仅此一处。

所以这不是「死代码」，是**契约建好了但没接上**——有人把产物名抽成了
常量模块，使用方却没改。两条路，取向不同，我不替你选：

- **A · 删掉**：零行为变化，但等于承认这次集中化失败。
- **B · 采纳**：把 5 处硬编码换成常量引用。行为同样不变，但把契约接上；
  代价是要动 `performance_curve_runtime.py` 和 `performance_curve.py`，
  这两个都不在当前的黄金等价网里（`score_core`/`downloader_core`/`filters_core`
  才是），所以没有测试兜底。

倾向 B（模块存在的理由就是防止产物名漂移），但**没有测试保护时改这俩文件
风险高于收益**，所以我没动。

### 18.4 审计脚本自己也有个 bug（记录一下）

第一版脚本判定 `scan_runtime.py`「0 处 import」，实际 `scan_service.py:16`
是 `from institution_scanner import scan_runtime as _scan_runtime`——脚本只看
`ImportFrom.module`，漏了「从包里 import 子模块」这种写法。`backtest_web.py`
同理被 `web_report_v81.py:14` 导入。

是靠直接 grep 交叉验证才发现的。结论没被推翻（导入模块≠引用符号），
但这正说明**审计工具本身也得被审**——和 15.6 那两条自造指标是同一类教训。

### 18.5 待你决定

1. 9 个符号：**全删 / 保留哪几个**？（我的意见：可删，其中 `_date_text`
   最没争议，`performance_curve_*` 三个建议和 18.3 一起定）
2. `performance_curve_contract.py`：**删（A）还是接上（B）**？
3. 删的话要不要我出补丁脚本（行级锚点 + 断言命中恰好 1 次 + 全文读 diff）？

### 18.6 执行结果（你授权「你决定」之后）

用 `tests/_apply_dead_symbol_patch.py` 落盘（行级锚点 + 断言命中恰好 1 次 +
预演/落盘两段式 + CRLF 原样写回）。6 个文件，**+19 / −81**。

**删掉的 4 个**

| 符号 | 位置 | 理由 |
|---|---|---|
| `_date_text` | `fundamental_schema.py:247` | 私有标量版，已被 `_date_series` 取代 |
| `inject_backtest_into_html` | `backtest_web.py:203-235` | 零引用；与 `inject_into_html` 无替代关系 |
| `read_backtest_summary` | `pit_page_semantics.py:20-26` | 零引用 |
| `PERFORMANCE_CURVE_SECTION_ID` | `performance_curve_contract.py:4` | 值全库唯一，HTML 锚点从未建立 |

**接上的 1 处契约**：`performance_curve_contract` 的两个常量替换掉 5 处硬编码
（`performance_curve_runtime.py:36,37,48,54` 与 `performance_curve.py:33,34`）。
**值完全相同，故行为不变**；顺带把「产物名各写各的」这个漂移风险消掉。

**没动的 3 个（理由）**

- `SCAN_RUNTIME_FACADE_VERSION` —— 同族 `analytics_runtime` 会把自己的
  facade 版本 publish 到 core，它没接，看起来像漏接。但 `scan_resume_boundary.py:104`
  在同一次 v113 改造里已经发布了 `SCAN_RESUME_RUNTIME_VERSION`，**scan 链路的
  可观测性并不缺**。删会丢信息，接是加一条新可观测面——两边都是取向判断，我没替你动。
- `GUI_VIEW_MODEL_VERSION` —— 该模块既无 `__all__` 也无 core，**没有接线点**。
  它不是"漏接"，是接不了。
- `parse_report_period` —— 公开工具函数，语义完整（校验季末）。删它是 API 取向
  判断，不是缺陷修复，所以留着等你点头。

### 18.7 预期行为差异

**没有。** 三类改动都是行为保持的：

- 删掉的 4 个符号在代码中引用为 **0**（已逐项复核，含测试、含动态访问、含模板）；
- 契约接上的 5 处，替换前后字符串值**完全相同**；
- 没有任何 `install()` 装配被触碰，装配清单 `final` 不受影响。

### 18.8 这轮的三个连带修复（值得一提）

删完 ruff 立刻报了 4 个错，**全是我这次删除的连带后果**，不是既有问题：

| 错误 | 成因 |
|---|---|
| `pit_page_semantics.py` F401 `json` | 删掉 `read_backtest_summary` 后无人用 |
| `pit_page_semantics.py` F401 `Path` | 同上 |
| `performance_curve_runtime.py` I001 | 我插入 import 时没按字母序放对位置 |
| `_apply_dead_symbol_patch.py` F401 `field` | 我脚本自己的多余导入 |

这类"删一个函数带出两个未使用导入"正是必须跑校验才能发现的——只删不跑等于没做完。
已全部修掉，`ruff check .` 恢复 All checks passed。

**行尾核查**：6 个文件里 5 个 CRLF 原样保留（裸 LF = 0）。唯一例外
`backtest_web.py` 是纯 LF，但**它在 HEAD 提交版本里本来就是 LF**（CRLF=0），
不是我转换的——已经用 `git show HEAD:<file>` 比对确认。
顺带：删掉该文件末尾函数时补上了结尾换行，**把一条既有的 W292 一并修掉了**。

**残留引用复核**：4 个被删标识符在 `.py`/`.html`/`.json` 中均为 **0 处**；
grep 命中的十几条全在 `PROJECT_ANALYSIS.md`（本报告）与 `signal_date_text` /
`trade_date_text` 这类**同名子串的不同标识符**里。

### 18.9 一个我留着没做的尾巴

全库有 17 个 `.py` 是纯 LF：2 个是原本就 LF（`validate_vectorized.py`、
`backtest_web.py`），**15 个是我前几轮用 Write 新建的测试与工具文件**。
项目主体是 CRLF，这 15 个是异类。

我没顺手转：它们都是未跟踪的新文件，转换本身安全，但会和这次的功能改动混在
同一个 diff 里，让你没法干净地审功能部分。建议**单独一次提交**处理，你说要我就做。

---

## 十九、第三轮深扫：恒假条件 / 不可达分支 / 只写不读字段（2026-09-15）

第七节覆盖了「死代码」与「重复代码」，但**恒假条件、不可达分支、只写不读的字段**
这三类一直没系统扫过。这轮补上，五类检查：

| 代号 | 检查 | 结果 |
|---|---|---|
| A | `if/elif` 链里出现完全相同的条件（后一支不可达） | **0** |
| B | `except` 顺序错误（宽泛在前，后处理器不可达） | **0** |
| C | `return`/`raise`/`break`/`continue` 之后的同级语句 | **0** |
| D | 只写不读的实例属性 | 初筛 23 → **复核后 6** |
| E | 字面量恒真/恒假条件 | 初筛 2 → **复核后 0** |

### 19.1 好消息：结构性不可达问题，项目是干净的

A/B/C 三类**全为 0**。这意味着：没有 `if/elif` 写了重复条件、没有 `except Exception`
挡住后面的 `except ValueError`、没有写在 `return` 后面的死语句。

考虑到这个代码库有 828 行的 `run_scan()`、909 行的 `validate_decision_integrity()`，
这个结果比"扫出多少问题"更能说明日常维护质量——**这类错误恰恰最容易在长函数里
长出来，而它们一条都没有。**

### 19.2 D 类：我扫出 23 处，撤回 17 处

这是本轮最该记的一条。第一版扫描器只认 `self.X` 形式的属性读取，
于是报出 23 个「只写不读」。交叉验证后发现**其中 17 个是我的误报**：

```python
# 我的扫描器看不见这种读取方式 —— 本项目的 GUI 状态字段大量这么用
visible = bool(getattr(self, "_detail_visible", True))          # gui.py:1366
if getattr(self, "_new_signal_only", False):                     # gui.py:1123
attached = getattr(signal_points, "evaluations", None)           # 3 处
raw_previous_token = getattr(self, "_csv_mtime", None)           # gui_core.py:2439
```

**误报率 74%。** 如果直接把这 23 条交出去，你会照着错的结论去删 17 个正在被
读取的字段。所以这一节的结论不写「发现 23 个只写不读」，只写复核后的 6 个：

| 字段 | 位置 | 出现次数全是赋值 |
|---|---|---|
| `_scan_execution_mode` | `gui_core.py:571` | 5 处 |
| `_last_scan_execution` | `gui_core.py:572` | 2 处 |
| `header_note` | `gui_v85.py:59` | 1 处 |
| `_active_nav` | `gui.py:362` / `gui_v84.py` / `gui_v85.py` | 共 4 处（3 个类各写各的） |

已确认这 4 个名字**既无 `self.X` 读取、也无 `getattr` 读取**。

**严重度判定：低优先，不配「必现」。** 它们不是会导致错误结果的缺陷，
是「记录了但没人消费」的状态。其中两个略可惜：

- `_scan_execution_mode` 会记录本次扫描是 `"inprocess"` 还是
  `"process-fallback"`，但**没有任何地方读它**——这是一条已经写好的可观测信息被丢了。
- `_last_scan_execution` 存了扫描结果对象，同样无人读。

要不要接上（比如打进日志）属你的取向，我没动。

### 19.3 E 类：2 条全是误报，撤回

扫描器报了 `gui_core.py:1239` 与 `:1248` 的 `while True`。那是**标准无限循环惯用法**
（循环体内有 `break`），不是恒假条件缺陷。撤回，是我的检查规则写得太粗。

### 19.4 这轮真正值得留下的

不是那 6 个字段，是这条：**任何静态扫描都必须先问「这个代码库用什么方式绕过我」。**

本项目用 `getattr(self, "字段名", 默认值)` 读取 GUI 状态——一种完全绕过 AST
属性节点的写法。扫描器看不见它，于是把「有读取」误判成「只写不读」，
而且**方向是危险的那一侧**（会诱导删除）。

和 15.6 的自造指标、18.4 的 `ImportFrom.module` 漏判是同一类：
**审计工具本身必须先被审。** 这已经是第三次了，所以这次把它单列成一条结论。

---

## 二十、第四轮：全局再评估与下一轮重构路线图（2026-09-15）

前三轮都是在"找问题"。这一轮换个问法：**如果只能再做一轮重构，该动哪里、按什么顺序。**

先说结论——我把问法换掉之后，最先撞出来的不是新问题，而是**前面几轮自己埋的
架构守卫已经饱和了**。这件事改变了一切：重构不再是"可选的清理"，而是"被阻塞"。

### 20.1 决定性发现：6 个巨型模块的预算只剩 256 行

`tests/test_architecture_growth.py` 里有一张 shrink-only 字节预算表。前几轮
只把它当闸门用，从没算过余量。算了：

| 模块 | 当前 | 预算 | 余量 | 折合行数 |
|---|---:|---:|---:|---:|
| `analytics_core.py` | 159,789 B | 160,000 B | **211 B** | **≈ 5 行** |
| `report_core.py` | 104,909 B | 105,000 B | **91 B** | **≈ 2 行** |
| `gui_core.py` | 104,866 B | 105,000 B | **134 B** | **≈ 3 行** |
| `scanner.py` | 78,111 B | 80,000 B | 1,889 B | ≈ 43 行 |
| `signal_lifecycle_core.py` | 67,788 B | 70,000 B | 2,212 B | ≈ 54 行 |
| `gui.py` | 94,033 B | 100,000 B | 5,967 B | ≈ 130 行 |
| **合计** | | | **10,504 B** | **≈ 256 行** |

（折合行数按各文件实测字节/行换算，非 41 B/行的粗估。）

**这意味着：给 `analytics_core.py` 打的下一个热修复，只要超过 5 行，CI 就红。**

这不是"预算设太紧"的问题——预算设成这样正是它的目的，就是在逼你提取。
但它现在从"缓慢施压"变成了"立刻窒息"：三个模块同时卡在 0.1% 以内，
已经没有"下个 sprint 再说"的余地。

顺带一提，包内也有同样的机制（`test_canonical_package_discipline.py`：
函数 150 行上限、7 个模块字节预算、7 个历史长函数豁免）。**两个闸门都活着且生效，
所以债务没有失控——它只是无处可去了。**

### 20.2 四个结构异常

预算是压力，异常才是病灶。按"异常程度 × 可动性"排序：

**异常 1：`scanner.py` 是唯一没有 `_core` 分层的巨型模块（1,784 行）**

其余 11 个域都完成了 `X.py`(门面) / `X_core.py`(实现) 的切分，
只有 scanner 没有：78 KB 全压在一个文件里，内含 `run_scan` **828 行**、
`scan_single_from_df` 381 行——这两个函数就占全文件 68%。
而它近 90 天有 **48 次提交**，是所有巨型模块里改动第二热的。
**最高热度 + 唯一没分层 + 预算只剩 43 行**，三者叠加，它是 T1。

**异常 2：`gui.py` 的门面模式失效了（2,063 行 / 62 方法）**

对比健康门面：`report.py` 423 行、`analytics.py` 451 行、`score.py` 158 行。
`gui.py` 是 2,063 行，里面 `DecisionScannerGUI` 一个类 1,683 行 / 62 方法。
它确实继承了 `_core.ScannerGUI`（1993 行 / 63 方法），我特意查过是不是复制粘贴——
**不是**：21 个同名方法里只有 2 个行数接近，其余都是真重写。
所以问题不是重复代码，是**两个 god class 叠成继承链**，加起来 125 个方法。

**异常 3：68 个 root `_vNN` 覆盖层，51 个是补丁型，版本天花板已到顶**

版本号从 v51 排到 v102，天花板 `v102` 已经有模块在用
（`web_report_v102.py`、`calibration_governance_v102.py`）。
也就是说：**新的覆盖层一个都加不进 root 了**，只能进 `institution_scanner/`。
存量这 68 个（51 个靠 `setattr`/属性赋值给别的模块打补丁）仍是 root 上的活债务。
补丁目标高度集中：`analytics_core` 被 4 个打、`_core` 别名被 4 个打。

**异常 4：`backtest` 域 16 个文件 / 186 KB 散在 root，但包里已有 5 个 backtest 模块**

域聚类结果（root，按字节）：
`gui` 225 KB(5) · `analytics` 196 KB(4) · **`backtest` 186 KB(16)** ·
`web` 172 KB(7) · `report` 128 KB(3) · `signal` 117 KB(3) · `scanner` 111 KB(3) ·
`score` 101 KB(10)。

`backtest` 是文件数最多、最碎的域（16 个），而包里已经有
`backtest_score_vectorized.py`(966) / `point_in_time_backtest.py`(445) /
`backtest_web.py` / `backtest_observability.py` / `backtest_profile.py`。
**域没归拢**：一半在包里、一半在 root，且 root 那半还在互相 import
（`backtest_acceleration_v77.py` 一家就 import 了 11 个兄弟模块）。

### 20.3 一个反直觉但有利的事实：门面热、核心冷

| 层 | 近 90 天提交 |
|---|---:|
| 门面 `analytics.py` / `report.py` / `gui.py` / `scanner.py` | 68 / 56 / 60 / 48 |
| 核心 `analytics_core.py` / `report_core.py` / `signal_lifecycle_core.py` / `score_core.py` | **5 / 6 / 9 / 4** |
| 核心 `gui_core.py`（唯一偏热的） | 17 |

**要提取的那些巨型核心模块，恰恰是全项目改动最冷的地方。**

这条决定了下一轮的风险画像：从 `analytics_core`、`report_core` 里动刀，
比从它们的门面动刀安全得多——冷代码的等价性更容易锁死，黄金测试也更容易写。
`gui_core.py`（17 次）是唯一例外，所以它排最后。

### 20.4 下一轮路线图

排序原则：**先解锁预算，再削 god，最后收覆盖层。**
每一步都复用前三轮已验证的套路：黄金等价测试 → 提取 → 反向验证。

**T1 · `scanner.py` 补 `_core` 分层**（~78 KB → 目标 ~10 KB）
建 `scanner_core.py`，把 `run_scan`(828) + `scan_single_from_df`(381) 迁过去，
`scanner.py` 退化成和其他 11 个域一致的薄门面。
理由：唯一没分层 + 热度第二 + 预算只剩 43 行，一动三得。
风险：中（有 `test_scanner_logic.py` 等既有覆盖）。

**T2 · `analytics_core.py` 抽回测**（腾 ~1,049 行 ≈ 43 KB）
`apply_backtest_ranking`(526) + `run_historical_backtest`(523) 迁进
`institution_scanner/backtest/`（正好接上异常 4，把域归拢一起做）。
做完 `analytics_core` 从 160 KB 降到 ~117 KB，余量从 5 行回到 ~1,000 行——
**这是唯一能真正解除窒息的一步**。

**T3 · `report_core.py` 拆 `validate_decision_integrity`（909 行，占文件 37%）**
单函数 909 行不能整体搬，得先按内部阶段切成若干小函数，再成组提取。
做完从 105 KB 降到 ~66 KB。
注意：它有既有的分支完整性（第十九节 A/B/C 三类全 0），说明内部结构是干净的，
拆的阶段边界会比较好找。

**T4 · `scanner.py` 之后的 GUI（放最后）**
`ScannerGUI`(63 方法) + `DecisionScannerGUI`(62 方法) 拆 mixin。
风险最高：MRO 变动、事件绑定时序、`gui_core.py` 是核心里最热的（17 次）。
且 `gui.py` 尚有 130 行余量、`gui_core.py` 只有 3 行——**先动 `gui_core`**。

**T5 · 68 个 `_vNN` 覆盖层收敛（可与 T2 并行）**
按"是否仍被 import"二分：118 处内部引用里被真正 import 的，逐个内化成显式调用；
零引用的直接删（沿用第十六节的装配清单 + 反向验证）。
版本天花板已到顶，这事的终点是明确的：**root 上不再有 `_vNN`**。

### 20.5 这一轮该怎么"做对"

前几轮留下的方法论不用重造，照抄即可，但有一条要改：

1. **先冻结、后动刀**：先跑一遍所有闸门留基线（当前 177 tests / 0 failed）。
2. **黄金等价测试先行**：T1/T2/T3 每一步先写 golden，再提取。
   `filters_core` / `downloader_core` / `score_core` 三次都是这么过的，套路成熟。
3. **逐个删、逐个验**：不要批量。第十六节的 27 处就是逐个删出来的。
4. **反向验证证明闸门会咬**：改完要故意注入一次违规，确认测试会红。
   这条最容易被跳过，也最值钱——它证明的是"闸门有效"，不是"代码正确"。
5. **新增：预算要跟着提取走。** T2 把 43 KB 从 `analytics_core` 挪进
   `institution_scanner/backtest/`，必须**同步把这部分预算也挪过去**，
   否则等于凭空多出 43 KB 额度，闸门就被自己架空了。
   前三轮的预算表都是"冻结在当时的磁盘大小"，这一轮开始它变成需要主动维护的量。

### 20.6 前四轮的一句话复盘

| 轮次 | 主产物 | 留下的教训 |
|---|---|---|
| 一 | 现状盘点 | 度量要用工具实测，不靠印象 |
| 二 | 死代码 / 重复代码 | — |
| 三 | 27 处模块级自装载移除 + 装配清单 | 删之前先冻结清单 |
| 四（本轮） | 全局再评估 + 路线图 | **守卫会饱和，预算是需要主动维护的量** |

第十九节说"审计工具本身必须先被审"。这一轮补上下半句：
**审计工具设下的预算，本身是需要被维护的对象。** 它们不是一次性装好就
自动生效的护栏，而是会随着代码增长被逐渐吃掉的资源——吃完了，
要么提取代码，要么闸门变成"每次都红、于是被 `# noqa` 掉"的摆设。

---

## 二十一、阶段 4 首战：T1 `scanner.py` 补 `_core` 分层（2026-09-15）

按 20.4 路线图执行第一步。结果：

| | 之前 | 之后 |
|---|---:|---:|
| `scanner.py` | 78,111 B / 1,784 行 | **941 B / 24 行** |
| `scanner_core.py` | — | 78,111 B / 1,784 行（实现原样迁入） |
| `scanner.py` 预算余量 | 43 行 | ≈ 1,750 行 |
| 测试 | 177 passed | **177 passed** |
| ruff 全项目 | 通过 | **通过** |

### 21.1 这一步真正难的：不是搬代码，是不能搬坏补丁

`scanner.py` 看起来只是"把实现挪走"，但有个陷阱会让重构**静默失效**：

```python
# scanner_resume_v68.py
import scanner as _core
...
original_download_batch  = _core.download_batch   # 补丁打在 scanner 模块对象上
original_enrich_results  = _core.enrich_results
original_clear_checkpoint= _core.clear_checkpoint
...
getattr(_core, "_defer_checkpoint_clear_until_publish", False)   # 运行时读标志位
```

`institution_scanner/scan_resume_boundary.py`、`scan_service.py` 也做同样的事
（后者还会**写** `_scanner._defer_checkpoint_clear_until_publish = True`）。

如果按"常规"拆法——`scanner.py` 保留 `from scanner_core import run_scan`——
那么补丁会打在 `scanner` 上，而 `run_scan` 的全局命名空间在 `scanner_core`，
**补丁彻底失效，而且不报错**：断点续扫和"发布前保留检查点"会静默退化。

项目既有范式（`analytics.py` / `report.py` / `filters.py` 末尾那一行）正是为此：

```python
sys.modules[__name__] = _core
```

`import scanner` 拿到的**就是** `scanner_core` 这个对象。所以我把 `scanner.py`
写成 24 行的纯门面，并在 docstring 里写明"这不是图方便，是行为要求"。

**验证（不靠推理，靠实测）**：

```
scanner.__name__                  = scanner_core
scanner is scanner_core           = True
sys.modules["scanner"] is scanner_core = True
scanner.run_scan is scanner_core.run_scan → True   （其余 5 个符号同样为 True）
```

### 21.2 装配清单报警了——然后证明它是虚惊

改完跑测试，`test_assembly_manifest` 红了 3 条（main / daily_pipeline / scan_service），
报 `step#87/88/90 install: now also rebinds ['scanner_core.run_scan', ...]` 共 7 个符号。

按 20.5 的纪律，不能因为"觉得是重命名"就直接重冻结。逐项查了：

| 检查项 | 结果 |
|---|---|
| 逐步 rebinds 差异 | 3 步各新增 `scanner_core.*`，**"消失"为空** |
| `final` 值变化 | **0 个** |
| `final` 新增/消失 | +7 / −0 |

"新增 7、消失 0"说明不是替换，是**多记了一份**：`sys.modules` 里 `scanner` 和
`scanner_core` 两个键指向同一对象，清单按 sys.modules 键名记录，于是同一个绑定记了两次。

那这是不是我引入的毛病？查了其他已拆门面的旧夹具：

| 模块 | 门面名键 | 核心名键 | |
|---|---:|---:|---|
| `analytics` | 5 | 60 | 双记 |
| `score` | 5 | 18 | 双记 |
| `downloader` | 3 | 11 | 双记 |
| `signal_lifecycle` | 8 | 8 | 双记 |
| `performance_cache` | 3 | 3 | 双记 |

**双记是既有行为，scanner(7+7) 与之一致。** 确认无异常后才执行 `--write` 重冻结。

值得记一笔的是：**闸门在这次是真的起作用了**。它抓到了一个我没预料到的变化，
逼我查清成因；虽然结论是虚惊，但如果我跳过调查直接重冻结，就永远不会知道
"双记"这回事。

### 21.3 预算跟着代码走（20.5 第 5 条的执行）

`scanner.py` 从 78 KB 瘦到 941 B，如果到此为止，这 78 KB 就变成了**没人盯的债务**——
预算表只查 `scanner.py`，`scanner_core.py` 完全在视野外。

所以同步把它加进闸门：

```python
_SIZE_BUDGETS = {
    ...
    "scanner.py": 80_000,
    # Extracted from scanner.py (T1).  The budget moved with the code: leaving
    # it untracked would turn 78 KB of debt invisible the moment the facade
    # shrank past its own gate.
    "scanner_core.py": 78_111,
    ...
}
```

**反向验证**（证明闸门会咬，不是摆设）：在 `scanner_core.py` 末尾追加一行注释
使其达到 78,194 B，跑闸门测试——

```
AssertionError: Legacy giant modules exceeded their shrink-only budgets;
extract new logic into institution_scanner/: {'scanner_core.py': 78194}
```

红。撤掉探针复原，177 全过。

### 21.4 顺带发现：9 个黄金夹具没进版本控制

重冻结时查到的，与本轮改动无关但影响整个防线的可信度：

```
$ git ls-files tests/fixtures/
tests/fixtures/reliability_expected.json
tests/fixtures/reliability_input.csv
```

只有 2 个被跟踪。**以下 9 个全是未跟踪（`??`）**：

- `assembly_manifest_{scanner,main,daily_pipeline,scan_service,gui_v85}.json`
- `assembly_module_level_sites.json`
- `{filters,downloader,score}_core_golden.json`

它们是"冻结快照"，是反向验证的基准。没进 git 意味着：
**换台机器 clone 下来，基准不存在**——闸门要么失败要么自动重新生成，
而自动生成的基准等于"用当前代码校验当前代码"，闸门失效。

这不是本轮要修的事，但它是"闸门看着结实、实际只在本地结实"的典型。
建议单独一次提交把它们纳入版本控制（体积：合计约 820 KB，其中
`score_core_golden.json` 182 KB 最大，可接受）。

### 21.5 T1 之后的状态与下一步

本轮改动文件：
- 新增 `scanner_core.py`（实现原样迁入，零行为改动）
- 重写 `scanner.py`（24 行门面）
- `tests/test_architecture_growth.py`（+1 条预算）
- `tests/test_scanner_memory_contract.py`（改指 `scanner_core.py`——内存契约在实现里）
- `tests/fixtures/assembly_manifest_*.json`（重冻结）

**未做的事，说清楚**：T1 只是**分层**，不是**瘦身**。`run_scan`(828 行) 和
`scan_single_from_df`(381 行) 是整体搬过去的，代码一行没少、一个函数没拆。
它买到的是：一致性（和另外 11 个域同构）、预算解锁、以及后续拆解的前置条件。
真正的体积下降要靠 T2/T3。

下一步按 20.4：**T2——`analytics_core.py` 抽回测**（`apply_backtest_ranking` 526 行 +
`run_historical_backtest` 523 行 → `institution_scanner/backtest/`），
那是唯一能解除 5 行窒息的一步。

---

## 二十二、T2 受阻：回测函数被外部重新赋值，动不了（2026-09-15）

按 20.4 进入 T2（`analytics_core.py` 抽 `apply_backtest_ranking` 526 行 +
`run_historical_backtest` 523 行）。依赖分析做完，**结论是两个都动不了**。
这是本轮最重要的发现，因为它否掉了我自己上一节写下的路线。

### 22.1 约束图：37 个符号被外部重新赋值

扫全仓库对 `analytics_core` 顶层符号的外部赋值（`X_core.<name> = ...`），得到：

| | 数量 | 行数 |
|---|---:|---:|
| 被外部重新赋值（**不可移动**） | 37 | 2,318 |
| 可自由移动 | 46 | 1,288 |

**我选的两个目标，恰好都在不可移动名单里**：

```
apply_backtest_ranking   def  526 行   被 1 个模块改: analytics.py
run_historical_backtest  def  523 行   被 1 个模块改: calibration_weight_cache_v79.py
```

点名这两个的分别是 `analytics.py:434`（门面用 77 行包装版覆盖核心版）
和 `calibration_weight_cache_v79.py`。

一旦搬走，覆盖会打在 `analytics_core` 上，而实现在新模块解析全局——
**和 21.1 那个陷阱同源：补丁静默失效，不报错。**

### 22.2 更糟的一层：`_legacy_apply_backtest_ranking` 是公共补丁点

`analytics.py` 把核心原版另存为 `_core._legacy_apply_backtest_ranking`，
而它被 **4 个覆盖层各自替换**：

- `calibration_governance_v102.py:350`
- `calibration_math_v96.py:337`
- `calibration_semantics_v102_1.py:105`
- `institution_scanner/reliability.py:658`

调用点在 `analytics.py:401`（77 行包装版内部）。而 `main_core.py:34` 从
`analytics` 导入 `apply_backtest_ranking`——**生产链路走的是门面包装版**。

所以那 526 行的核心版本是"legacy 实现"，只能通过 `_legacy_` 别名到达，
而这个别名上有 4 层包装。它不是"可以随便搬的死代码"，是**四层装饰的活代码**。

另有 21 个模块绕过门面直接 `import analytics_core`，全部在这张网里。

### 22.3 修正后的 T2′：搬自由的，不搬被盯上的

不能搬两个大函数，但可以搬它们周围**没有被盯上**的东西。46 个自由符号按主题分：

| 组 | 符号数 | 行数 | 内部依赖 | 可搬性 |
|---|---:|---:|---|---|
| [A] 回测统计工具 | 19 | 487 | 多数 0–1 个 | **高** |
| [B] 研究/富化 | 27 | 801 | 0–4 个 | 中（需逐一核） |

[A] 组里有一批**零内部依赖的纯函数**，是理想目标：

```
_candidate_endpoint_matrix        43 行  依赖 0
_backtest_evidence                38 行  依赖 0
_spearman                         36 行  依赖 0
_entry_date_equal_weight_stats    30 行  依赖 0
_expand_legacy_backtest_metrics   24 行  依赖 0
_weighted_observations            17 行  依赖 0
_weighted_arrays                  15 行  依赖 0
```

搬走 [A] 组（487 行 ≈ 20 KB）：`analytics_core` 从 159,789 B 降到约 139,800 B，
余量从 **5 行回到约 490 行**。加上 [B] 组里依赖为 0 的
`_decision_quality_multiplier`(114) / `write_research_reports`(96)，
可到约 135,000 B、余量约 600 行。

**不搬的**：`BacktestSummary`(98 行)。虽然名字没被重新赋值，但 `analytics.py:431`
做 `_core.BacktestSummary.to_dict = ...`——搬走后这条会变成给导入引用打属性，
语义上可行但风险不值得。

### 22.4 为什么我不在这里直接动手

T2′ 比 T1 高一个风险等级，理由具体：

1. **没有黄金测试。** 前三次（`filters_core` / `downloader_core` / `score_core`）
   都是先有 golden 再提取。`analytics_core` **没有** `golden_analytics_core.json`。
   那 487 行里含 `_spearman`、`_weighted_quantile` 这类数值函数，
   改错一位小数不会让任何断言失败。
2. **被 4 层包装包围。** 改动会穿过 `calibration_*` 三层 + `reliability` 一层，
   装配清单会红，但"红"只告诉我变了，不告诉我**变得对不对**。
3. **收益/风险比不如先补测试。** 补一个 `analytics_core` 的黄金夹具是可复用资产，
   之后每次提取都能白用；直接提取则是一次性赌博。

所以建议顺序改为：

> **T2′-0** 先建 `tests/golden_analytics_core.py` + `tests/fixtures/analytics_core_golden.json`
> （对 46 个自由符号 + 2 个大函数做行为快照）
> **T2′-1** 再搬 [A] 组 487 行
> **T2′-2** 反向验证

这仍然能解除窒息，只是多一步。

### 22.5 教训：路线图里的"行数最大"不等于"最好搬"

20.4 选 T2 的依据是"`apply_backtest_ranking` 526 行 + `run_historical_backtest`
523 行 = 文件 27%，搬走能腾 43 KB"。这个推理有个隐含假设：**大的就是可搬的**。

实际约束是反过来的——**被越多覆盖层盯上的代码，越是系统的承重结构**，
它大恰恰是因为它是补丁汇聚点，而不是因为它没人管。
`run_historical_backtest` 523 行、被 1 个模块改；`_backtest_one_ticker` 261 行、
被 4 个模块改；`_backtest_one_ticker_cached` 131 行、被 4 个模块改。

**下一轮选目标，第一指标应该是"被重新赋值次数"，不是行数。**

---

## 二十三、T2′ 执行：16 个统计函数搬出 `analytics_core`（2026-09-15）

### 23.1 结果

| 项 | 数值 |
|---|---|
| 搬出函数 | 16 个（`_finite_float` … `_entry_date_equal_weight_stats`） |
| 新模块 | `institution_scanner/backtest_statistics.py`，17,523 字节 |
| `analytics_core.py` | 156,782 → 142,583 字节（**释放 14,199 字节 ≈ 355 行**） |
| 预算余量 | 3,218 B（≈80 行）→ **17,417 B（≈435 行）** |
| 测试 | 177 → **187 passed** |
| ruff | All checks passed |

§20 判定的"6 个巨型模块合计只剩 256 行余量"里最窒息的一个，被解开了。

### 23.2 两条互相独立的等价证据链

搬移这类操作只有一种失败方式是静默的：**代码看着一样，行为悄悄变了**。
所以用了两条不会同时失真的证据。

**证据一（源码级，覆盖全部 16 个）**：新模块的函数文本与 `git show HEAD:analytics_core.py`
逐字节比对，16/16 一致，共 14,659 字节。这是一个**传递性证明**——新模块的内容取自搬移
那一刻的工作区版本，它若等于 HEAD，说明搬移前我从未改动过这 16 个函数，搬移是纯拷贝。

**证据二（运行时，覆盖其中 14 个）**：`tests/fixtures/analytics_core_golden.json`
在搬移**前**冻结了 45 个用例的输出，搬移后重放：

```
顶层字段差异: ['function_provenance']      ← 只有出处变了
用例总数: 45 -> 45
值发生变化的用例: 无（0 个）
provenance 变化数: 14                      ← 恰好是 14 个搬移候选
未变的 provenance: ['_date_balanced_weights', '_bucket_rows']  ← 金丝雀仍在原处
```

`_finite_float` / `_safe_return` 不在黄金夹具的 14 个候选内，由证据一覆盖。

### 23.3 抓到一个真实回归：`compute_volume_profile` 丢了加速版

这是本次最有价值的发现，而且**它是黄金夹具抓不到的**。

`indicator_acceleration_v77.install()` 在导入之后做两件事：

```python
_ind.compute_volume_profile = compute_volume_profile              # 打在 indicators 上
setattr(analytics_core, "compute_volume_profile", compute_volume_profile)  # 打在 analytics_core 上
```

搬移前，`_backtest_scoring_window` 在 `analytics_core` 里读模块全局名，
拿到的是**加速版**。搬移后新模块用 `from indicators import compute_volume_profile`
按值绑定，导入那一刻就冻结了**原始版**。实测：

```
装配后 backtest_statistics.compute_volume_profile: indicators          ← 原始版
装配后 analytics_core.compute_volume_profile:      indicator_acceleration_v77  ← 加速版
bs 与 ac 是否同一对象: False
```

**为什么值夹具抓不到**：加速版和原始版返回**完全相同的数**。
45 个用例全绿，因为它只能验证"算得对不对"，验证不了"用的是哪个实现"。
这类回归只有身份断言能抓。

**修法**：新模块改为 `import indicators as _indicators`，调用点走
`_indicators.compute_volume_profile(...)` 在**调用时**解析。
由于 overlay 同时也打在 `indicators` 上，调用时解析拿到的就是同一个加速函数对象，
与搬移前的解析规则完全一致。改回延迟绑定后，45 个值仍与搬移前夹具逐字节相同，
双向证明等价。

### 23.4 固化：一个前向闭包闸门

`tests/test_extracted_module_closure.py` 持续扫描：
"新模块从模块对象上解析的名字" ∩ "overlay 在 `analytics_core` 上重绑定的名字"，
交集必须都在 `golden.LATE_BOUND_NAMES` 里（即已被显式处理）。

它带一个**已知正例自检**：如果扫描连 `compute_volume_profile` 都找不到了，
测试直接报"扫描瞎了，别信它的'无危险'结论"。这个自检不是多余的——
见下节。

### 23.5 审计工具第 3/4/5 次被绕过（同一类错误又来了三次）

| # | 工具 | 怎么被绕过的 | 后果 |
|---|---|---|---|
| 3 | 我的"自由符号"扫描 | `backtest_math_integrity_v94:166` 通过**函数参数** `analytics_module._date_balanced_weights = ...` 打补丁 | §22 的自由/被改表是错的，差点搬走被打补丁的函数 |
| 4 | ruff F401 | `BACKTEST_NORMAL_WEIGHT` 在本文件没用，但 `calibration_math_v96:228` 用 `_core.BACKTEST_NORMAL_WEIGHT` **从模块属性读** | 照 ruff 删会直接让 overlay AttributeError |
| 5 | 我第一版闭包扫描 | 它只认 `import analytics_core as X` 形式的别名，而 `indicator_acceleration_v77` 是 `sys.modules.get("analytics_core")` | 扫描输出"无危险交集"，而危险就在那里 |

第 5 次最危险，因为它输出的是**"一切正常"**。这条已经上升为规矩：

> **一个能给出"无异常"结论的审计工具，必须先证明它能在已知正例上失败，
> 否则它的"无异常"一文不值。**

`test_scan_still_sees_the_known_positive` 就是把这条规矩写成了代码。

同时，被 ruff 判死的 5 个 import 没有一刀切删掉，逐个核到可执行：

- `BACKTEST_FULL_WEIGHT_SAMPLES`、`BACKTEST_LOW_CONFIDENCE_MAX_SAMPLES` → 真死，**删**
- `BACKTEST_NORMAL_WEIGHT` → `calibration_math_v96` 从属性读，**留** + noqa 注明坐标
- `compute_volume_profile` → `indicator_acceleration_v77:258` 的 setattr 目标，**留** + noqa
- `_weighted_observations` → 反向导入里没人调用，但黄金闸门通过 `analytics_core` 解析它，**留** + noqa

### 23.6 反向验证：5 个闸门逐个证明会咬

"测试通过"只说明此刻没违规，不说明闸门有用。逐个注入违规：

| 闸门 | 注入的违规 | 结果 |
|---|---|---|
| 值等价 | `_weighted_mean` 结果 `+ 1e-9` | 咬住：`2.066666666666667` vs `2.066666667666667` |
| 绑定等价 | 改回 `from indicators import compute_volume_profile` | 咬住：`extracted=indicators vs core=indicator_acceleration_v77` |
| 搬移候选 | 在 `analytics.py` 末尾 `_core._weighted_mean = 包装函数` | 咬住：`_weighted_mean -> analytics._rv_patched_mean` |
| 金丝雀（实时） | `_core._bucket_rows = _core._weighted_arrays` | 咬住：`_bucket_rows -> ...backtest_statistics._weighted_arrays` |
| 预算 | 预算 17,523 → 17,000 | 咬住：`'17523 > 17000'` |
| 闭包（前向） | `_core.BACKTEST_SCORE_WINDOW_BARS = 999` | 咬住：点名常量与来源文件 |

**最后一栏是最说明问题的**：注入后 `test_canaries_never_enter_the_extracted_module`
（实时）失败了，而 `test_patched_canaries_stay_outside_analytics_core`（读冻结夹具）
**依然绿着**。搬移前写那个冻结断言是对的；搬移后它已经变成"永远为真"的摆设。
所以补了实时版本——**冻结夹具记录的是"当时在哪儿"，实时断言记录的是"现在在哪儿"**。

所有注入均已还原：`git diff analytics.py` 为空，`analytics.py` 与 HEAD 逐字节一致。

### 23.7 预算跟着代码走

新模块 17,523 字节，低于 `LARGE_MODULE_THRESHOLD`（20,000），
`test_every_large_module_has_a_budget` 不会强制它登记。但它是从一个已经窒息的
巨型模块里搬出来的债务，所以显式登记了：

```python
"backtest_statistics.py": 17_523,
```

否则 `analytics_core` 刚腾出的 14 KB 可以在新家里悄悄长回去，没人会发现。

### 23.8 本轮遗留

1. ~~**9 个黄金夹具仍未纳入 git**~~ —— **已在提交 `25b4f46` 解决**：
    `tests/fixtures/` 下 13 个夹具全部受版本控制（含本轮的 `report_core_golden.json`）。
2. **T3 未动**：`report_core.py` 的 `validate_decision_integrity`（909 行）拆分。
   按 §22.5 的新指标，动手前必须先查它的**被重新赋值次数**，而不是看行数。
3. 本次只搬了 16 个函数 / 14 KB。`analytics_core` 仍有 3,445 行，
   §20 的"46 个自由符号（1288 行）"里还有 30 个没动——它们的性质已经验证过，
   下一轮可以继续用同一套夹具白用。

### 23.9 可复用的做法

这一轮真正沉淀下来的不是那 14 KB，是这套顺序：

1. **先冻结，再搬** —— 夹具在搬移前采集，搬移后重放，0 值变化才算过关。
2. **源码级 + 运行时两条证据** —— 覆盖互补，任一单独都不够。
3. **值相等 ≠ 实现相同** —— 必须补身份断言，否则性能回归隐身。
4. **审计工具先自证会失败** —— 否则"无异常"没有意义。
5. **ruff 说"没用"不等于能删** —— overlay 通过模块属性读写，静态分析看不见。
6. **反向验证 5 个闸门** —— 证明会咬，而不是证明没咬。
7. **预算随代码移动** —— 否则腾出的空间会悄悄长回去。

---

## 24. T3′：把 report_core 的候选筛选簇搬进 institution_scanner.report_selection

提交 `af5dcfd`。`report_core.py` 从 104,909 字节降到 91,784（2,422 → 2,080 行），
预算从 105,000 收到 95,000。199 passed，干净树复验同样 199 passed。

### 24.1 选簇：以依赖闭包为单位，而不是以行数

上一轮的教训是"看着大就动手"：目标从 909 行缩到 579 行又缩到 300 行，每次都是
搬进去之后才发现依赖不干净。所以这一轮先把侦察固化成工具
`tests/recon_extraction_targets.py`，它以**传递依赖闭包**为单位评估：

```
python tests/recon_extraction_targets.py report_core \
    --seed _apply_research_policy,_ensure_diversity_columns,_diversify_ranked_candidates,_institutional_tier

闭包成员 (11): _apply_research_policy, _clean_group_key, _diversify_ranked_candidates,
              _ensure_diversity_columns, _etf_theme_key, _institutional_tier, _policy_column,
              _policy_text, _policy_truthy, _truthy, _vectorized_etf_research_policy
合计 323 行
依赖的被改符号（会脱离补丁）: 无
闭包外还依赖的函数（会成环）: 无
=> 该簇自洽，可以整体搬出
```

被排除的两个目标：`print_terminal_report`（依赖被 `report_determinism` 改写的
`_rankable_results`）和 `refresh_candidate_exports`（闭包 20 个函数 / 1,542 行，
含 909 行的 `validate_decision_integrity`）。

工具带 `--selfcheck`：先把 5 个已知被改符号喂进去，扫不到就报错"扫描已失明"。
这是第 6 次同类扫描被绕过后定下的规矩。

### 24.2 两条独立的等价性证据

| 证据 | 结果 |
|---|---|
| 源码级：与 `git show HEAD:report_core.py` 逐字比对 | 11 个函数中 **10 个完全相同**（13,008 字节） |
| 运行时：63 个黄金用例重放 | **取值变化 0** |
| provenance | 11 个迁到 `report_selection`，canary `_rankable_results` 未动 |

唯一的偏差是 `_institutional_tier` 里 3 处阈值改为 `_config.X`，见 24.3。

### 24.3 核心风险：阈值迁移依赖 import 顺序

这是本轮最值钱的发现，而且它**不是**"某个 overlay 改了 report_core 的符号"那种
已知模式，是一种新的：

```
config（未装配）      INSTITUTIONAL_TIER_A_SCORE = 35.0
config（装配后）      INSTITUTIONAL_TIER_A_SCORE = 36.0825
config_core（始终）   INSTITUTIONAL_TIER_A_SCORE = 35.0     <- config 与 config_core 不是同一个对象
report_core 实际持有  36.0825
```

`score.py:21` 在导入时调用 `score_threshold_migration_v95.install(config)`，把
35/30/25 改写成 36.0825/30.9278/25.7732。`report_core` 之所以持有迁移后的值，
只是因为它的第 25 行 `from analytics import ...` 先于第 32 行 `from config import ...`
执行——**一个 import 顺序的偶然**，注释里没人写过，代码里也看不出来。

后果：新模块若按值 `from config import INSTITUTIONAL_TIER_A_SCORE`，就会固化成
它自己被导入那一刻的快照。偏差 1.08 分，而 63 个用例里**只有 3 个**会察觉。

对应措施三条：

1. 新模块 `import config as _config`，在调用时读 `_config.X`（与 T2′ 的
   `compute_volume_profile` 同一条规矩）。
2. 语料里放三个落在迁移缝隙内的探针：`MIGRATION_PROBES = (35.5→B/A, 30.5→C/B,
   25.4→D/C)`，每个在两套常量下返回不同档位。
3. 两个闸门分别盯取值（`test_migration_probes_pin_the_migrated_thresholds`）和
   绑定（`test_extracted_module_shares_the_migrated_thresholds`，拿 `report_core`
   仍持有的旧快照与新模块解析到的值对比）。

另外还有一个 corpus 守卫 `test_migration_probes_are_still_differential`：
万一以后迁移系数改到某个探针不再跨边界，它会先红，避免留下一个"看着绿但什么都没守住"的探针。

### 24.4 顺带抓出的一处既有重复

`test_canonical_package_discipline::test_no_duplicated_function_bodies` 报：

```
publication_renderer.py:_truthy:181 == report_selection.py:_truthy:213
```

这个重复**早就存在**，但 `_truthy` 原先在仓库根目录的 `report_core.py` 里，而该闸门
只扫 `institution_scanner/` 包内——跨过包边界的那一刻它才变得可见。按闸门要求沉到
`institution_scanner/_common.py`（那模块的说明写的就是"每个助手此前都以 2-4 份逐字
副本散落在包里"），`publication_renderer` 改为导入。

**结论：包边界是闸门的一个盲区。** 每往包里搬一个符号，都可能暴露出根目录时代
看不见的重复。这本身是把代码往包里搬的额外收益。

### 24.5 反向验证：7 个闸门全部被证明会咬住

注入违规 → 观察是否转红 → 还原（已确认无残留）。

| 闸门 | 注入方式 | 结果 |
|---|---|---|
| 取值等价 | 把 `cluster_step` 乘 2 | 红 |
| 字节预算 | 给 `report_selection.py` 追加 60 行 | 红 |
| overlay 重绑定 | 插件把 `report_core._ensure_diversity_columns` 换成假函数 | 红 |
| 阈值绑定 | 插件把 `config` 三个阈值回滚到 35/30/25 | 红 |
| 迁移探针 | 同上 | 红 |
| 共享归属 | 插件替换 `report_core._truthy` | 红 |
| 语料守卫 | 把夹具里全部「D级等待确认」改成「B级观察」 | 红 |

**过程中有 3 次注入本身是无效的**，值得记下来：

1. 改罚分下限 `0.70 → 0.60`：语料里罚分最低只到 0.95，下限根本没被触到，
   改动是个空操作。**注入前要先确认被改的代码路径真的会被语料走到。**
2. 插件里直接 `import config` 后改阈值：`score.py` 的 `install()` 在其之后又跑了一次，
   把值覆写回去。必须**先 `import report` 钉住装配**再改。
3. 只改 `golden_report_core.py` 的语料而不重新采集：档位守卫读的是**夹具**，不是语料源码。
   改语料不重采，守卫当然不红。

也就是说，"闸门没咬住"有两种可能：闸门是假的，**或者注入是假的**。三种里有两种是后者。

### 24.6 预算现状

| 模块 | 当前 | 预算 | 余量 |
|---|---|---|---|
| `analytics_core.py` | 142,583 | 160,000 | 17,417 B ≈ 435 行 |
| `report_core.py` | 91,784 | 95,000 | 3,216 B ≈ 80 行 |
| `gui_core.py` | 104,866 | 105,000 | **134 B ≈ 3 行** |
| `gui.py` | 94,033 | 100,000 | 5,967 B ≈ 149 行 |
| `scanner_core.py` | 78,111 | 78,111 | **0** |
| `signal_lifecycle_core.py` | 67,788 | 70,000 | 2,212 B ≈ 55 行 |
| `institution_scanner/report_selection.py` | 16,963 | 16,963 | 0 |
| `institution_scanner/backtest_statistics.py` | 17,969 | 17,969 | 0 |

`report_core` 让出 13 KB，`gui_core` 现在是下一个窒息点（3 行）。

### 24.7 本轮遗留

1. **`gui_core` 只剩 3 行预算**，但 recon 工具还没扫过它。
2. **T3′-2 未动**：`report_core` 还有约 1,219 行，其中
   `validate_decision_integrity`（909 行，无分段注释）会撞
   `FUNCTION_LINE_LIMIT`（150）。要先分段才能搬。
3. **`analytics_core` 的 17,417 B 余量没有锁**：T2′ 之后预算停在 160,000 没往下收，
   这笔腾出的空间理论上还能悄悄长回去。与"只减不增"的纪律不一致，
   下一轮动 `analytics_core` 时应一并处理。
4. **`_truthy` 在仓库里还有 4 份逐字副本**（`daily_pipeline_core`、
   `web_report_v84`、`universe_snapshot_v82` 等）。它们在包外，闸门看不见；
   搬进包里时会被自动拦下。

### 24.8 新增的可复用做法

8. **以依赖闭包为单位评估，不以行数** —— 否则目标会在动工后反复缩水。
9. **侦察要固化成带自测的工具** —— 一次性脚本的结论无法复核，而且同类扫描
   已经被绕过 6 次。
10. **常量也可能被 overlay 改写** —— 已知模式是"函数被换"，这次是"`config` 的
    常量被 `install()` 改写"。判断标准应该是"装配前后取值/字节码是否变化"，
    而不是"它看起来像不像常量"。
11. **探针用例要自带"仍然有效"的守卫** —— 否则迁移系数一改，探针就退化成普通用例。
12. **反向验证失败时，先怀疑注入** —— 本轮 3 次无效注入里，2 次是被注入的代码
    路径没被走到或随后被覆写。
13. **包边界会暴露既有重复** —— 每搬一个符号进包，都可能被
    `test_no_duplicated_function_bodies` 拦下，这是收益不是麻烦。

---

## 25. T4：把 signal_lifecycle_core 的属性簇搬进 institution_scanner.signal_attributes

提交 `8732a1f`。`signal_lifecycle_core.py` 从 67,788 字节降到 52,565（腾出 15,223），
预算从 70,000 收到 56,000；新模块 17,837 字节，纳入 `MODULE_BYTE_BUDGETS`。
215 passed，干净克隆复验同样 215 passed（字节数与行尾在克隆里逐字节复现）。

### 25.1 选簇：沿用 T3′ 的闭包口径

```
python tests/recon_extraction_targets.py signal_lifecycle_core --seed <17 个函数>
闭包成员 (17)，合计 407 行 / 16,337 字节
依赖的被改符号（会脱离补丁）: 无
闭包外依赖: 无
```

三个符号故意留在原处，并作为 canary 一起冻结进夹具：

| 符号 | 运行时解析到 | 留在原处的原因 |
|---|---|---|
| `_is_active` | `signal_lifecycle` | `signal_lifecycle.py` 直接给它赋值 |
| `finalize_signal_ranking` | `runtime_v83._install_ranking_wrapper.<locals>.layered_finalize` | 装在闭包里，搬走等于复制 |
| `strict_filter_override_mask` | `signal_lifecycle` | 同上 |

### 25.2 本轮真正的发现：overlay 会"伸手进模块"取符号

第一次尝试把 `_bool` 并进 `_common._truthy`（两者函数体逐字节相同，只有 docstring
不同）。recon 全绿、源码比对全绿，但两个 canary 的值从真实值变成 `null`。原因：

```python
# signal_lifecycle.py:90
passed_filters.map(_core._bool)
```

overlay 是**从模块对象上按属性取符号**，不是 `from ... import`。recon 通过比较
`__module__` 判断"谁定义了它"，对这种耦合完全失明——`_bool` 从来没被 rebind，
所以它不在任何"被改写的符号"清单里。

结论：**重命名一个会被 overlay 按属性取用的符号，不是一次"提取"可以顺手做的事。**
合并被撤回，17 个函数恢复成纯搬运（源码级 17/17 逐字节相同）。`_bool` / `_truthy`
的重复仍然挂着，归入去重那一轮，届时连同 `signal_lifecycle.py` 的调用点一起改。

顺带发现重复闸门的另一个盲区：`test_no_duplicated_function_bodies` 把 docstring
也算进哈希，所以"逻辑相同、文档串不同"的两个函数会算出不同摘要。T3′ 抓到它那份
是因为两边恰好都没写 docstring。

### 25.3 两重等价性证明，都做成了常驻闸门

| 证明 | 文件 | 锚点 |
|---|---|---|
| 源码级逐字节 | `tests/test_signal_lifecycle_extraction_equivalence.py` | 固定 SHA `cd63ffd` |
| 运行时取值 | `tests/fixtures/signal_lifecycle_golden.json` + `tests/test_signal_lifecycle_golden.py` | 71 个用例，移动前后 0 变化 |

**为什么锚定固定 SHA 而不是 HEAD**：提交之后，`HEAD:signal_lifecycle_core.py`
里已经没有这 17 个函数了。比对 HEAD 等于拿新模块跟自己比，永远绿、证明不了任何事。
这是"锚点选错会让闸门永远通过"的又一种形态——和"闸门没咬先怀疑注入"是同一类错误。

### 25.4 反向验证（4 次注入，全部确认会咬）

| 注入 | 咬住的闸门 |
|---|---|
| 改掉 `_bool` 接受的拼写（`是` 不再为真） | `test_fixture_matches_live_behaviour`：`bool/chinese` True→False |
| 删掉 `signal_lifecycle_core` 对 `_bool` 的反向导出 | 同时咬住 4 个：取值、来源、canary 归属、reach-in 专用闸门 |
| 给搬走的函数加一行注释 | 源码比对：`_number (202 -> 214 bytes)` |
| 在新模块里夹带一个 `_stray_helper` | `test_the_new_home_defines_nothing_beyond_the_moved_set` |

第一次和第二次注入都因为 **CRLF** 而没生效（`replace("...\n")` 匹配不到 `\r\n`）——
"注入没生效"和"闸门没咬"表现一样，所以注入后必须断言替换次数。

### 25.5 死导入：ruff 说没用，不等于能删

搬走 17 个函数后，`signal_lifecycle_core` 有 9 个 `config` 常量和 `os`/`Path` 变成
F401。删除前先查了三类外部取用：

* `_core.<常量>` —— 只有 `analytics_core.py:41` 的注释和 `calibration_math_v96.py:228`
  命中，但那个 `_core` 是 `analytics_core`，与本模块无关；
* `signal_lifecycle.<常量>` —— 无（`signal_lifecycle.py` 结尾 `sys.modules[__name__] = _core`，
  所以两者命名空间等价）；
* `signal_lifecycle_v51.py:19` 的 `from signal_lifecycle_core import *` —— 本模块没有
  `__all__`，星号导入会吃掉全部公有名；但该 overlay 用的是 `_config.X`，不依赖这里。

确认无消费者后删除。常量闸门随之从"对照 `signal_lifecycle_core`"改成"对照 `config`"：
原模块和提取模块的绑定方式完全相同，两者一致证明不了谁没过期；`config` 是活的值，
才是能发现"提前快照"的参照。

### 25.6 预算

| 模块 | 现在 | 预算 | 余量 |
|---|---|---|---|
| `signal_lifecycle_core.py` | 52,565 | 56,000 | 3,435 B ≈ 86 行 |
| `institution_scanner/signal_attributes.py` | 17,837 | 17,837 | 0 |
| `analytics_core.py` | 142,583 | 160,000 | 17,417 B ≈ 435 行（仍未锁） |
| `gui_core.py` | 104,866 | 105,000 | **134 B ≈ 3 行** |

### 25.7 本轮遗留

1. **`_bool` / `_truthy` 重复仍在**（且 `_truthy` 全仓库还有多份副本在包外）——
   去重时要连同 `signal_lifecycle.py:90/95` 的调用点一起改，否则会再断一次。
2. **`analytics_core` 的 17,417 B 余量仍未锁**（24.7 第 3 条的遗留，本轮未处理）。
3. **5 个超阈模块没有预算**：`score_core` 44,289、`daily_pipeline_core` 42,270、
   `downloader_core` 30,543、`main_core` 22,423、`filters_core` 21,967，全部超过项目
   自己定的 20,000 B 门槛却没有进入任何预算表。
4. **recon 看不见属性取用**：建议给它加一条"按 `mod.X` 形态扫描 overlay 源码"的能力，
   否则下一轮提取还会靠 canary 撞运气。

### 25.8 新增的可复用做法

14. **提取就是提取，不要顺手去重/重命名** —— 重命名一个会被 overlay 按属性取用的
    符号，会让所有基于 `__module__` 的侦察集体失明。
15. **等价性证明的锚点必须是不可变 SHA** —— 比 HEAD 会在提交后退化成自我比较。
16. **给"看不见的耦合"写专用闸门** —— canary 只能告诉你"坏了"，
    `test_module_level_attribute_reachin_is_still_satisfied` 能告诉你"坏在哪"。
17. **注入脚本要断言替换次数** —— CRLF 会让 `replace` 静默不匹配，
    "注入失败"与"闸门没咬"的输出完全一样。
18. **提取后清理死导入前，先查三类属性取用** —— 直接删除 vs `import *` 消费者
    vs overlay 的 `_core.X`；本轮只有第三类真的存在过（在别的模块上）。
