# InstitutionScanner — 整体重新分析与下一轮重构建议

> 生成时间：2026-09-15
> 基线与证据：`git log`（HEAD = `a7251bc`）、全仓源码扫描、运行时反射（`import config / config_core`）、91 个测试文件
> 本文是**只读分析 + 建议**，未修改任何生产代码。
> 所有字节数均已按换行符归一化（CRLF→LF），避免此前 `core.autocrlf` 造成的假红。

---

## 0. 本轮对既有判断的三处修正（先说结论）

在上一轮分析中我提出过三个假设，本轮用证据复核后，**两个被推翻、一个被降级**。先列出来，避免后面引用错误前提：

| 先前假设 | 复核结果 | 证据 |
|---|---|---|
| 一字板/涨停无法买入只在 `auction_structure.py` 建模，回测路径缺失 | ❌ **推翻**。`tradeability.is_entry_tradeable` 已返回 `locked_limit_up`；`tradeability_acceleration_v80.py:109-120` 有向量版（open 与 low 均在涨停阈值之上即判定封板）；`analytics_core.py:133` 直接 import 使用 | 见 §1.4 |
| `config` vs `config_core` 的并行常量漂移是"回测可复现性"的直接威胁 | ⚠️ **降级**。89 个常量不同，但 `config_core` 的**真实消费方只有 1 个**（`historical_backtest.py:43` 的 `OUTPUT_DIR`）。所有 core 模块都 `from config import`，不存在"两条链路读两套并行参数"的既成事实 | 见 §2 D6 |
| 次新股/上市年龄过滤缺失 | ✅ **证实，且比预想更彻底**。全仓唯一匹配是 `tests/golden_downloader_core.py:155` 的**测试假 ticker 名** `"N 新股"`，生产代码零实现 | 见 §2 D3 |

结论：**A 股执行现实性（涨跌停 / 一字板 / T+1 / 停牌 / 集合竞价 / 费用滑点）的建模成熟度显著高于我的先前判断**。因此"补 A 股机制"不是最高收益方向；真正的缺口在**次新股语义**和**可复现性未被证明**两处。

---

## 1. 现状评估

### 1.1 整体架构与模块划分

项目采用 **facade / core / overlay 三层 + 包内沉淀** 的结构：

- **facade 层**：`X.py` 只做「导入 `X_core` → 覆盖少量符号 → `sys.modules[__name__] = _core`」。
- **core 层**：`X_core.py` 承载真实实现，是 golden 等价性测试锚定的对象。
- **overlay 层**：`X_vNN.py` 在 import 之后**重新绑定**符号（monkey-patch），是本项目最主要的历史包袱来源。
- **包内沉淀**：`institution_scanner/` 是已抽出的规范化模块，新代码应落在这里。

根目录 122 个 `.py` 的分类与体积（归一化后）：

| 类别 | 数量 | 体积 | 说明 |
|---|---:|---:|---|
| facade | 13 | 212,858 B | `gui.py` 94,033 最大 |
| core | 13 | 658,540 B | `analytics_core.py` 142,583、`gui_core.py` 104,866、`report_core.py` 91,784、`scanner_core.py` 78,111、`signal_lifecycle_core.py` 52,565 |
| **overlay** | **69** | **730,915 B** | `web_report_v85` 50,136、`web_report_v84` 46,307、`backtest_vectorization_v98` 42,542、`backtest_fastscore_v80` 34,098、`web_report_v93` 31,638 |
| plain（无归属） | 27 | 227,213 B | 既非 facade 也非 core 也非 overlay |
| **合计** | **122** | **1,829,526 B** | |

**最刺眼的一个数字：overlay 层 730,915 B > core 层 658,540 B。** 补丁比被打的本体还大 11%。这意味着「读懂 core 不等于读懂系统行为」——任何 core 的语义都可能被 69 个 overlay 中的某一个改写。

代码行数：根 46,485 行 + 包内 13,045 行 = **约 59,530 行生产代码**；包内 60 个模块（`auction_structure.py` 72,227 B、`backtest_score_vectorized.py` 35,233 B、`fundamentals.py` 34,833 B 为最大三块）。

### 1.2 数据流向

```
启动
 main.py (facade)
   ├─ workstation_runtime_v77.configure_native_threads()   ← 硬件自适应，最先执行
   ├─ install_analytics_alignment(analytics)                ← 覆盖层注入 ①
   ├─ backtest_command_v76.install()                        ← 覆盖层注入 ②
   ├─ postprocess_performance.install()                     ← 覆盖层注入 ③
   ├─ export_batch.install(backtest_command)                ← 覆盖层注入 ④
   └─ main_core.py
        ├─ cmd_scan   → scanner_core.run_scan
        │                 ├─ downloader / downloader_v51 / downloader_v51_base  → 行情 + 涨跌停元数据
        │                 ├─ analytics (+ analytics_core)                        → 指标、机构痕迹
        │                 ├─ filters / filters_core                              → 硬过滤
        │                 ├─ score_core                                          → 五维评分
        │                 ├─ signal_lifecycle_core (+ signal_lifecycle_v51)      → 信号生命周期
        │                 └─ report_core (+ report_v51)                          → 候选输出
        ├─ cmd_report → report_core
        ├─ cmd_backtest → backtest_command_v76 → analytics_core（向量化 v98 / 快分 v80 / 增量 v78）
        └─ cmd_download / cmd_clean

日更（生产主链路）
 daily_pipeline.py (facade)
   └─ daily_pipeline_core.run_daily_pipeline (1,051 行)
        ├─ _prepare_staging            → output/.staging/<RunId>
        ├─ _begin_transaction          → 快照现有发布物
        ├─ cmd_scan / cmd_backtest     → 产出到 staging（不碰发布区）
        ├─ _publish_staging            → 原子 copy + os.replace
        ├─ _activate_run               → 推进 LatestRun.json
        └─ maybe_publish_canonical_report
   失败路径：_rollback_transaction → 字节级还原
   并发保护：daily_recovery_v74（PID 感知，检测到活 PID 直接 raise DAILY_RECOVERY_UNCERTAIN）

发布
 pages_publisher / publish_site / publication_renderer / web_report_v8x → GitHub Pages
```

### 1.3 核心策略与回测 / 实盘执行链路

**策略侧（扫描 → 评分 → 排序）**：`scanner_core.run_scan` 拉取 OHLCV 与涨跌停元数据 → `analytics_core` 计算技术/量能/机构痕迹指标 → `filters_core` 硬过滤 → `score_core` 五维评分 → `signal_lifecycle_core` 判定信号阶段 → `report_core` 输出候选。

**回测侧**：`cmd_backtest` 经 `backtest_command_v76` 走整命令事务，底层三条加速路径并存——`backtest_vectorization_v98`（向量化）、`backtest_fastscore_v80`（快分）、`backtest_incremental_v78`（增量尾部），均受 `conditional_fill_v96` 的成交约束。

**A 股机制覆盖（本轮逐项核实）**：

| 机制 | 覆盖情况 | 关键实现 |
|---|---|---|
| 涨跌停 | ✅ 完善，单一权威 | `institution_scanner/price_limit_policy.py`：北交所 `.BJ` 30%；300/301 创业板 20%（2020-08-24 前 10%）；688/689 科创板 20%；588/589 科创 ETF 20%；其余 10%。**标量与向量双实现**，provider 元数据可覆盖但需可审计来源 |
| 一字板（封板无法买入） | ✅ 已建模 | `tradeability.py:165-168` 返回 `locked_limit_up`；`tradeability_acceleration_v80.py:109-120` 向量版；`auction_structure.py:1203` 的 `HardBlockReason = "一字涨停"` |
| T+1 / 次日成交 | ✅ 已建模 | `conditional_fill_v96.py`（456 行），`CONDITIONAL_FILL_VERSION` 写入结果 provenance |
| 停牌 | ✅ 已建模 | `backtest_vectorization_v98`、`tradeability`、`tradeability_acceleration_v80` |
| 集合竞价 | ✅ 已建模 | `institution_scanner/auction_structure.py`（72,227 B） |
| 佣金 / 印花税 | ✅ 已建模 | `BACKTEST_STOCK_COMMISSION_RATE = 8.5e-05`、`BACKTEST_ETF_COMMISSION_RATE = 5.0e-05` |
| 滑点 / 流动性冲击 | ✅ 已建模 | `BACKTEST_MAX_LIQUIDITY_SLIPPAGE = 0.003`、`BACKTEST_LIQUIDITY_IMPACT_AT_ONE_PERCENT = 0.0005`、`BACKTEST_ASSUMED_TRADE_NOTIONAL = 50000` |
| 退出延迟 | ✅ 已建模 | `BACKTEST_MAX_EXIT_DELAY_DAYS = 10` |
| **次新股 / 上市年龄** | ❌ **零覆盖** | 生产代码无任何 `listing_age` / `listed_days` / 次新 实现 |
| ST / 风险警示 | ⚠️ 部分 | 14 个文件提及，未见统一策略开关 |

### 1.4 配置现状：三层，而非两层

```
config_core.py  (164 个大写常量)  ──star import──▶  config_v51.py
                                                        │
                                                        ▼
workstation_runtime_v77.runtime_profile()  ──覆盖──▶  config.py  (242 个大写常量)
```

- `config is config_core` → **False**；89 个常量不同。
- `config.py` 用 `runtime_profile()` 覆盖**硬件相关**的并行常量：`SCAN_THREADS`、`BACKTEST_MAX_PROCESSES`、`BACKTEST_CHUNK_SIZE`、`BACKTEST_FAST_CHUNK_SIZE`、`BACKTEST_INCREMENTAL_TAIL_BARS`。
- 实测两组取值（当前工作站）：`SCAN_THREADS` 12 vs 24、`BACKTEST_MAX_PROCESSES` 6 vs 12、`BACKTEST_CHUNK_SIZE` 6 vs 4、`BACKTEST_FAST_CHUNK_SIZE` 24 vs 12、`BACKTEST_INCREMENTAL_TAIL_BARS` 360 vs 900。
- `BACKTEST_PROVENANCE_VERSION`：`config` 是现行长链（含 v89/v88/v87/…），`config_core` 停留在 **`2026-08-09-v30`**（落后约 30 个版本）。
- **消费方核实**：`grep config_core` 在生产代码中只有 2 处——`config_v51.py:15`（star import）和 `historical_backtest.py:43`（只取 `OUTPUT_DIR`）。所有 core 模块（`analytics_core:20`、`scanner_core:37`、`score_core:26`、`signal_lifecycle_core:6`、`report_core:26`、`daily_pipeline_core:25`）都 `from config import`。

**所以这不是"两套并行参数同时生效"的既成故障，而是"一个过期的死分叉 + 一个未证明无害的硬件自适应"。** 真正需要回答的问题是：并行/分块参数变化，会不会改变回测的**数值结果**？目前**没有任何测试回答这个问题**。

### 1.5 依赖现状

- `requirements.txt`（面向用户，范围友好）：`pandas>=2.0,<3.0`、`numpy>=1.24,<3.0`、`scipy>=1.10,<2.0`、`ta>=0.11,<1.0`、`tqdm>=4.65,<5.0`、`pyarrow>=12,<24`、`holidays>=0.60,<1.0`、`customtkinter==5.2.2`。
- **硬钉**：`tickflow[all]==0.1.24`（唯一 OHLCV + universe 来源）、`akshare==1.18.94`（仅低频财报横截面）。
- `constraints-ci.txt`（CI 复现）：`pandas 2.3.3 / numpy 2.4.6 / scipy 1.17.1 / pyarrow 23.0.1 / holidays 0.103 / pytest 8.4.2 / ruff 0.16.4 / pyright 1.1.411`。
- 风险点：`akshare` 是**网络抓取型**依赖，上游接口变更频繁；`fundamentals.py`（34,833 B）依赖它，当前未见「抓取失败 → 降级为 unknown 而非崩溃」的契约测试。

### 1.6 测试覆盖现状

| 指标 | 数值 |
|---|---:|
| 测试文件 | 91 |
| 测试函数 | 221 |
| 测试代码行数 | 11,325 |
| 生产代码行数 | 59,530 |
| **测试 : 生产（行数）** | **1 : 5.3** |
| 当前状态 | 225 passed / 0 failed / 0 error；`ruff check .` 全绿 |

- 根模块在**任何测试文件中被点名**的数量：**67 / 122**（启发式：模块名作为单词出现在测试代码或注释中）。即 **55 个根模块在 91 个测试文件里连名字都不出现**。
- 其中体积大且影响策略语义、却零点名的：`score_acceleration_v79.py` 19,863、`web_report_v102.py` 18,742、`historical_backtest.py` 18,389、`analytics_acceleration_v77.py` 17,550、`technical_resonance_v90.py` 17,010、`lifecycle_acceleration_v83.py` 16,748、**`conditional_fill_v96.py` 16,607**（决定成交价与是否成交，直接影响收益数值）。
- 已装护栏（前三轮成果）：shrink-only 字节预算（双闸门，已归一化）→ 日更发布契约（8 测试 + 子进程探针，覆盖 7 个场景）→ CRLF 假红修复 → 3 个虚假宽松预算收紧。

---

## 2. 技术债清单（按严重程度 × 修复成本排序）

严重度：`P0` 阻断上线 / `P1` 上线前应修 / `P2` 应修但可排期 / `P3` 可选
成本：`S` 单 PR 纯新增或纯改名 / `M` 单 PR 需新增实现 / `L` 多 PR 需重构

| ID | 技术债 | 严重度 | 成本 | 证据与影响 |
|---|---|:-:|:-:|---|
| **D1** | **并行/分块参数随硬件漂移，回测可复现性「未证明」** | **P0** | **S** | `config.py` 用 `runtime_profile()` 覆盖 5 个并行常量（`SCAN_THREADS` 12/24、`BACKTEST_MAX_PROCESSES` 6/12、`BACKTEST_CHUNK_SIZE` 6/4、`BACKTEST_FAST_CHUNK_SIZE` 24/12、`INCREMENTAL_TAIL_BARS` 360/900）。**没有任何测试证明换机后数值结果不变**。它是「回测结果可复现」这条不改项能否成立的前提，也是后续所有改动能否被验证的前提 |
| **D2** | **最低历史门槛 `300` 硬编码 9 处、`21` 硬编码 1 处** | **P1** | **S** | `analytics_core.py:352(<21):1127:1464`、`backtest_cache_acceleration_v80.py:192`、`backtest_incremental_v78.py:48`、`backtest_sample_acceleration_v80.py:170`、`backtest_vectorization_v98.py:365`、`conditional_fill_v96.py:325`、`auction_structure_cli.py:294`。改门槛需同步 9 处，漏一处即造成**快路径与精确路径样本集静默不一致** |
| **D3** | **次新股 / 上市年龄零建模** | **P1** | **M** | 全仓唯一匹配是 `tests/golden_downloader_core.py:155` 的假 ticker 名 `"N 新股"`。A 股次新股（尤其上市连板）会把**不可复制的收益**计入回测统计，虚高 IC / 胜率，实盘无法兑现。这是本轮识别出的**最大 A 股实盘适配缺口** |
| **D4** | **overlay 层体积超过 core 层** | **P1** | **L** | 730,915 B vs 658,540 B（+11%），69 个 overlay。最大且零行为测试的三块：`web_report_v85` 50,136 + `web_report_v84` 46,307 + `web_report_v93` 31,638 ≈ **128 KB**。行为不可从 core 推导 |
| **D5** | **55 个根模块在测试中零点名** | **P1** | **M** | 67/122 被点名。风险最高的三个：`conditional_fill_v96` 16,607（决定成交）、`technical_resonance_v90` 17,010（已确认在 main）、`score_acceleration_v79` 19,863（加速路径数值一致性） |
| **D6** | **`config_core` 是过期死分叉** | **P2** | **S** | 164 vs 242 常量；`BACKTEST_PROVENANCE_VERSION` 停留在 `2026-08-09-v30`。**已从 P0 降级**：真实消费方只有 `historical_backtest.py:43` 的 `OUTPUT_DIR`。危害是"未来有人 import 它就会拿到 30 个版本前的默认值"，属潜伏陷阱 |
| **D7** | **`akshare` 硬钉 + 网络抓取无降级契约** | **P2** | **M** | `akshare==1.18.94`，`fundamentals.py` 34,833 B 依赖；未见「抓取失败 → quality=unknown 降级」的契约测试。实盘稳定性风险 |
| **D8** | **无 `[project]`，122 个根模块不在包内** | **P2** | **L** | `pyproject.toml` 已声明"故意不加"。与当前 GitHub Pages 交付形态一致（交付是站点，不是 wheel），**与 D4 强耦合**——不收敛 overlay 就无法安全打包 |
| **D9** | **overlay 伸手进 core 私有符号** | **P2** | **S** | `signal_lifecycle.py:90/95` 执行 `passed_filters.map(_core._bool)`。基于 `__module__` 的 recon 对此**完全盲视**，重构时容易漏 |
| **D10** | **27 个 plain 模块 227,213 B 无归属** | **P3** | **M** | 既非 facade 也非 core 也非 overlay，职责不清 |
| **D11** | **根重复函数体 28 组（含 `_bool` / `_truthy`）** | **P3** | **M** | 重复而非复用；已在先前轮次识别，未动 |

---

## 3. 重构目标与明确不改项

### 3.1 目标（按优先级）

1. **让「回测结果可复现」从口头承诺变成有测试支撑的事实**（D1）。
2. **补上 A 股次新股语义，且以零行为变更的方式先取得度量能力**（D3）。
3. **消除会造成静默偏差的硬编码门槛**（D2）。
4. **给影响策略数值但零测试的模块装语义锁**（D5）。
5. **逐步收敛 overlay 体积，最终使打包成为可能**（D4 / D8）。

### 3.2 明确不改项（Non-goals）

以下为**硬约束**，任何阶段违反即视为失败，必须回滚：

| 不改项 | 含义 | 守护方式 |
|---|---|---|
| **策略逻辑语义不变** | 评分公式、过滤条件、信号阶段判定、成交规则、排序规则的**取值与判定结果**完全不变 | golden 等价性：改动前后同一输入的产出**字节一致** |
| **回测结果可复现** | 同一数据 + 同一代码 → 相同输出；且换机器后**数值**不变（Phase 0 负责证明） | `tests/test_parallelism_determinism.py`（新增） |
| **日更发布原子性不变** | staging → 事务 → 原子替换 → 推进 LatestRun 的契约不变 | 现有 `tests/test_daily_pipeline_publication.py`（8 测试 + 探针） |
| **策略参数默认值不变** | Phase 2 把 `300`/`21` 提升为常量时，**取值必须与现在完全相同** | 既有 golden 全绿即为证明 |
| **不新增生产数据源** | 不引入新的行情/财报 provider | review 把关 |
| **不改变 Pages 交付形态** | 交付仍是 GitHub Pages 站点，本轮不做 wheel 打包 | 与 D8 一致 |

**关键设计原则：所有新能力先以 shadow（只观测、不决策）模式上线。** 新增字段只写不读，等积累足够观测数据后再决定是否让它参与过滤/评分。这条原则保证每个阶段都能独立上线且可回滚。

---

## 4. 分阶段重构路线图

每个阶段：独立 PR、独立上线、独立回滚。前一阶段不必完成即可开始后一阶段（除标注「前置」的）。

---

### Phase 0 — 并行确定性证明（先决条件，零生产代码改动）

**推荐指数：★★★★★（最高性价比，应先做）**

- **改动范围**：仅新增 `tests/test_parallelism_determinism.py`。不碰任何生产代码。
- **做什么**：在同一份冻结数据上，用 2~3 组不同的并行配置（`SCAN_THREADS` / `BACKTEST_MAX_PROCESSES` / `BACKTEST_CHUNK_SIZE` / `BACKTEST_FAST_CHUNK_SIZE`）分别跑同一段回测，断言输出**字节一致**（或至少分数 + 排名 + 收益统计一致）。
  - 实现要点：必须在**子进程**中运行（本项目 import 顺序会污染进程，`import daily_pipeline` 会顺带 import `main` 并安装覆盖层），复用 `tests/daily_publication_probe.py` 已验证的子进程 + `OSError` 重试模式。
- **预期收益**：把 D1 从「假设」变成「结论」。
  - 若一致 → D1 降级为纯性能项，**后续所有阶段都可以用 golden 安全地验证**；
  - 若不一致 → D1 立刻升级为必修 P0，且 Phase 1~3 必须暂停直到修复。
  - 无论哪种结果，都是本轮最有价值的产出。
- **验证方式**：
  1. `pytest tests/test_parallelism_determinism.py -q` 全绿；
  2. **反向验证**：人为让其中一条路径使用不同的归约顺序（如改变分块边界上的累加方式），测试**必须变红**——证明闸门会咬，而非恒真。
- **回滚方案**：`git revert` 该提交。纯新增测试文件，对生产零影响，回滚成本为零。
- **独立上线**：✅

---

### Phase 1 — 次新股上市年龄：shadow 指标（A 股实盘适配收益最高）

**推荐指数：★★★★★**

- **改动范围**（新增为主）：
  1. 新增 `institution_scanner/listing_age.py`：从首个可用 bar（或 tickflow 元数据）推断上市日 → 计算「已上市交易日数」；跨 300/301/688/689/BJ 板块正确。
  2. `config.py` 新增两个常量：`LISTING_AGE_SHADOW_ONLY: bool = True`、`MIN_LISTING_AGE_TRADING_DAYS: int = 250`（默认 True，**不参与任何决策**）。
  3. 在 scanner 结果、backtest 样本、报告各新增**一列/字段**（`ListingAgeDays` / `IsNewListing`），**只写不读**。
- **预期收益**：
  - 第一次能定量回答：「我的回测收益里有百分之多少来自次新股？这部分实盘能复制吗？」——这是**直接提升 A 股实盘适配**的度量能力。
  - 零行为变更：不参与过滤、不参与评分，**回测结果完全不变**，天然满足 §3.2 的不改项。
  - 为「未来是否启用次新硬过滤」提供决策依据，而不是凭感觉。
- **验证方式**：
  1. 单元测试：上市日推断对已知标的正确（含 2020-08-24 创业板改制、北交所开市等时间边界）；
  2. **等价性闸门**：同一输入跑 shadow 前后，**除新增列外所有输出字节一致**（复用现有 golden 机制）；
  3. **反向验证**：把 shadow 逻辑偷偷改成真的过滤，等价性测试**必须变红**——证明"只写不读"这条约束真的被闸门看着。
- **回滚方案**：`git revert`；或保持 `LISTING_AGE_SHADOW_ONLY = True` 即可让新列永不参与决策。双保险。
- **独立上线**：✅

---

### Phase 2 — 把 `300` / `21` 提升为配置常量

**推荐指数：★★★★☆**

- **改动范围**：
  1. `config.py` 新增 `MIN_HISTORY_BARS_BACKTEST = 300`、`MIN_HISTORY_BARS_INDICATOR = 21`——**取值与现在完全相同**。
  2. 替换 9 + 1 处硬编码：
     - `analytics_core.py:352`（21）、`analytics_core.py:1127`、`analytics_core.py:1464`
     - `backtest_cache_acceleration_v80.py:192`
     - `backtest_incremental_v78.py:48`
     - `backtest_sample_acceleration_v80.py:170`
     - `backtest_vectorization_v98.py:365`
     - `conditional_fill_v96.py:325`
     - `auction_structure_cli.py:294`
  3. 新增架构测试：断言根目录下不得再出现裸 `len(frame) < 300` / `< 21`（防回潮）。
- **预期收益**：消除 D2；根除「快/精路径样本集静默漂移」隐患；为 Phase 1 后续若决定启用次新硬过滤铺路（届时只改一处）。
- **验证方式**：
  1. **取值不变 → 全部既有多线程 golden 测试必须全绿**，这本身就是等价性证明；
  2. 新增架构测试通过；
  3. **反向验证**：把 `MIN_HISTORY_BARS_BACKTEST` 改成 301，除架构测试外**至少还有一个既有测试变红**——证明常量真的被所有 9 处消费（若不变红，说明有遗漏的硬编码）。
- **回滚方案**：`git revert`。纯改名式替换，取值不变，风险极低。
- **独立上线**：✅

---

### Phase 3 — 语义锁：给零测试但影响数值的模块装闸门

**推荐指数：★★★★☆**（建议在 Phase 1 之后、Phase 4 之前）

- **改动范围**：一次一个 PR，按风险从高到低：
  1. `conditional_fill_v96.py`（16,607 B）——决定成交价与是否成交，**直接影响收益数值**；
  2. `technical_resonance_v90.py`（17,010 B）——五因子共振（已确认在 main 上，`b2893cb` v90 / `bf7f1ae` v91）；
  3. `score_acceleration_v79.py`（19,863 B）——加速路径必须与精确路径数值一致。
- **做法**：golden 冻结（**子进程隔离**，因 import 顺序污染）→ 改动后断言一致 → 反向验证证明闸门会咬。
- **预期收益**：把 55 个未测模块中**风险最高的 3 个**锁住；为 Phase 4 的 overlay 收敛提供安全网。
- **验证方式**：每模块一个测试文件；**每个都必须做反向验证**（注入违规 → 测试变红 → 还原）。
- **回滚方案**：`git revert` 单个 PR。纯测试，生产零影响。
- **独立上线**：✅（三个模块可分别发）
- **注意**：`signal_lifecycle.py:90/95` 的 `_core._bool` reach-in（D9）在这一阶段应顺带处理——基于 `__module__` 的比对对此盲视。

---

### Phase 4 — overlay 收敛：web_report 三合一

**推荐指数：★★★☆☆**（门槛高，放最后）

- **前置**：必须先完成 Phase 3 同类 golden（否则无安全网）。
- **改动范围**：`web_report_v84`（46,307）+ `web_report_v85`（50,136）+ `web_report_v93`（31,638）≈ 128 KB，合并为单一实现。
- **预期收益**：根体积下降约 128 KB；Pages 渲染路径从 3 层 overlay 收敛为 1 层，行为可从单一实现推导。
- **验证方式**：golden 渲染产物（HTML / JSON）**字节一致**（子进程隔离）。
- **回滚方案**：`git revert`。
- **独立上线**：✅，但依赖前置，建议最后做。

---

### Phase 5 — 收尾：死分叉清理 + 依赖降级契约

**推荐指数：★★☆☆☆**

- `config_core` 死分叉（D6）：删除，或显式改为 `from config import *` 的再导出，并删除 `BACKTEST_PROVENANCE_VERSION` 等过期常量。消费方仅 1 处，成本 S。
- `akshare` 降级契约（D7）：新增「抓取失败 → fundamentals 不可用 → quality=unknown → 不崩溃」的契约测试（用录制 fixture 断言 schema）。

---

## 5. 关键取舍与优先级

### 5.1 取舍说明

| 取舍 | 选择 | 理由 |
|---|---|---|
| 先证明可复现性，还是先做 A 股功能？ | **先证明** | Phase 1~3 全部依赖 golden 等价性来验证。若并行参数会改变数值，所有 golden 都不可靠，后面全白做。Phase 0 只要一个测试文件，是全局解锁点 |
| 次新股过滤：直接上硬过滤，还是先 shadow？ | **先 shadow** | 硬过滤会**改变回测样本集 → 改变回测结果**，直接违反「回测结果可复现」不改项。shadow 先取得度量能力，用数据决定是否启用 |
| 魔法数字：顺手改成"更合理的值"，还是保持原值？ | **保持原值** | 改值会改变样本集。本阶段只做「提升为常量 + 替换引用」，取值不变，golden 全绿即为证明。改值是独立决策，留到 shadow 数据出来后 |
| 优先补测试，还是优先减体积？ | **优先补测试** | 55 个模块零点名、overlay 比 core 还大。没有闸门就减体积 = 蒙眼开车。Phase 3 → Phase 4 的顺序不可颠倒 |
| 打包（wheel）现在做不做？ | **不做** | 交付形态是 Pages 站点，不是 wheel；且 122 个根模块（含 69 个 overlay）不在包内，强做会产出不完整的 wheel。与 D4 强耦合，overlay 收敛后再议 |

### 5.2 优先级排序（可直接照此执行）

```
Phase 0  并行确定性证明        P0 / S   零生产改动，全局解锁    ← 先做这个
Phase 1  次新股 shadow 指标     P1 / M   A股实盘适配收益最高    ← 收益最高
Phase 2  300/21 提升为常量      P1 / S   消除静默偏差
Phase 3  三个语义锁             P1 / M   overlay 收敛的前置
Phase 4  web_report 三合一      P1 / L   需 Phase 3 前置
Phase 5  死分叉 + 依赖降级      P2 / S+M 收尾
```

Phase 0 / 1 / 2 之间无依赖，可并行推进；建议 **Phase 0 → Phase 1 → Phase 2** 串行，因为 Phase 0 的结论可能改变 Phase 1/2 的验证策略。

### 5.3 明确暂缓（本轮不做）

| 暂缓项 | 暂缓理由 | 何时重估 |
|---|---|---|
| **打包 / 加 `[project]`**（D8） | 交付形态是 Pages 站点；且 69 个 overlay 不在包内，强做产出不完整 wheel | overlay 体积降到 core 之下（约 -11%）之后 |
| **`gui_core.py` 拆分**（104,866 B） | 先前以为是「134 B 窒息点」，归一化后实际余量 2,648 B ≈ 65 行，属普通优化而非阻塞项 | 字节预算逼近时 |
| **27 个 plain 模块归属整理**（D10） | 职责梳理收益主要是可读性，不影响 A 股实盘适配 | 有明确痛点时 |
| **28 组根重复体去重**（D11，含 `_bool` / `_truthy`） | 收益是整洁度；且这些重复体被多个 overlay 依赖，改动面广 | Phase 3 语义锁装好之后 |
| **`experiment/five-factor-resonance-v90` 分支处置** | ~~待确认~~ **已于 2026-09-15 完成**：抢救测试后删除，见 §6 | — |
| **打包 / 加 `[project]`**（D8，**评级修正**） | 复评后确认**这是有意设计，不是债**：`pyproject.toml` 开头注释已说明理由（交付形态是 Pages 站点，约 120 个模块仍在根目录，wheel 会缺大半扫描器）。真正的缺口只有一个：**没有 lock 文件**，依赖用范围约束（`pandas>=2.0,<3.0`）而非精确版本，构建不可逐位复现 | 需要「换机器能装出一模一样环境」时，加 `pip-compile` / `uv lock` 即可，不动打包形态 |

---

## 6. 两处待确认信息 —— 已答复并执行（2026-09-15）

| 问题 | 答复 | 处置结果 |
|---|---|---|
| Pages 是否公开可读？ | **公开可读** | 触发 web_report 公网暴露面审计，结论见 §8.1 —— 与预期相反，真问题不是「公网出 bug」，而是那 6 个模块**根本不在执行路径上**。已删除 166 KB 死代码。 |
| `experiment/five-factor-resonance-v90` 如何处置？ | **可以删** | 已删除（本地）。删除前比对发现：分支的 v90 实现确实已被 main 的向量化 v91 取代，但分支上 103 行行为测试**从未迁移**，main 上 `technical_resonance_v90` 零专属覆盖。已抢救为 `tests/test_technical_resonance_v91.py`（4 例全过 + 反向验证有效）后再删分支。 |
| B2 标定闭环是否有意？ | **不清楚** | 按「默认它是有意的」处理。复评后确认：它是**有意设计**（见 §8.2），我此前的 P2 评级偏高。仅补了一个显式总开关。 |

> **注**：删除分支前 `git cherry` 显示 5 个提交「不在 main」，但那是因为 main 上的 v91 是**重构后的向量化重写**（patch-id 不同），不是内容丢失。判断「代码是否已迁移」不能只看 `git cherry`，要比对公开符号与行为 —— 本次比对后发现所有符号都在 main 上（`compute_five_factor_resonance` / `attach_resonance_to_samples` / `summarize_resonance_samples` / `RESONANCE_VERSION`），且分支测试在 v91 上 4/4 通过。

---

## 7. 附：本轮证据命令备查

```bash
# 规模与分类（归一化字节）
python -c "..."   # 结果见 §1.1 表格

# config 三层漂移
python -c "import config, config_core; print(config is config_core)"
# → False；89 个常量不同

# config_core 真实消费方
grep -rn "config_core" --include="*.py" . | grep -v "^./tests/"
# → config_v51.py:15 (star import)、historical_backtest.py:43 (OUTPUT_DIR)

# 涨跌停政策
sed -n '44,66p' institution_scanner/price_limit_policy.py
# → BJ 30% / 300,301 20% / 688,689 20% / 588,589 20% / 其余 10%

# 一字板
grep -rn "locked_limit_up" --include="*.py" .
# → tradeability.py:168、tradeability_acceleration_v80.py:120

# 次新股（零覆盖）
grep -rn "次新\|新股\|listing_age\|listed_days" --include="*.py" .
# → 仅 tests/golden_downloader_core.py:155 的假 ticker 名 "N 新股"

# 硬编码 300 / 21
grep -rn "len(frame) < 300\|len(market) < 300\|len(frame) < 21" --include="*.py" .
# → 9 处 300 + 1 处 21，见 §2 D2

# 运行时判定「哪些根模块真的在执行路径上」（比 grep 可靠：overlay 靠 import 副作用安装）
# 子进程 import main + daily_pipeline + publish_web_report + gui，再与根目录 *.py 求差集
python -c "import sys;import main;import daily_pipeline;import publish_web_report;\
print(sorted(m for m in sys.modules if m.startswith('web_report')))"
# → ['web_report_v81']（v84/v85/v90/v93/v102/v102_1 一个都没加载）
```

---

## 8. 本轮执行结果（2026-09-15）

### 8.1 web_report 公网暴露面审计 —— 结论与预期相反

Pages 确认公开可读后重新审计，**推翻了 §6 原先的判断**：

1. **公网产物无泄露**。发布的是 HTML，不含源码。用 8 类模式扫描（Windows 绝对路径 / 用户名 / API token / 邮箱 / Python traceback / 内网 IP / TODO / `Users\` 目录），唯一命中的是价格与成交量序列里的数字巧合（`1.31451`、成交量 `31451400`、`0.631451`），**不是用户名泄露**。
   > 教训：对数值密集产物做正则扫描时，纯数字模式必然误报，必须看上下文再下结论。

2. **真问题不是「公网出 bug」，而是那 6 个模块根本不在执行路径上。** 结构是一条单向遗赠链：
   ```
   web_report_v84 (46 KB) ← v102, v85
   web_report_v85 (50 KB) ← v90
   web_report_v90 (15 KB) ← v93
   web_report_v93 (31 KB) ← v102
   web_report_v102 (18 KB) ← v102_1
   web_report_v102_1 (4 KB) ← 无人引用
   ```
   链顶端零引用，且没有任何环节被活入口 `web_report_v81` 触达。三重证据：
   - 静态：全仓库 grep 零有效引用（唯一的「引用」是 `test_architecture_growth.py` 里的三条字节预算 —— **幽灵预算，保护着死代码**）；
   - 运行时：子进程完整 import `main + daily_pipeline + publish_web_report` 后，`sys.modules` 里**只有 `web_report_v81`**；
   - 文档：`WEB_REPORT.md:21` 明写「生产路径**不再串联** `web_report_v84/v85/v90/v93/v102/v102_1`」。

3. **处置**：删除 6 个模块共 **166,026 字节**，根目录 122 → 116 模块；同步清理三条幽灵预算和 `pyproject.toml` 里指向已删 `web_report_v102.py` 的 `UP035` 豁免。全量 238/0/0，ruff 全绿。
   **活着的 `web_report_v81` 只有 6.4 KB**，是薄编排层，实现已在 `institution_scanner/publication_renderer.py` 且已有测试。所以 **Phase 4（web_report 合并）实际已经完成，可以从路线图上划掉**，只是旧壳还留着 —— 本轮把壳清了。

### 8.2 B2 标定闭环复评 —— 我此前的评级偏高

原判 P2「潜在反馈环」。**复评后确认是有意设计，且防护比我写的更完整**：

- `config_core.py:214` 注释原文：「A validated OOS calibration file may override these defaults」—— 明确的有意设计；
- 加载器已有四重防护：`accepted` 标志 → 护栏区间（setup 0.45~0.70 / trigger 0.15~0.35 / exec 0.10~0.25）→ 和为 1 → 任何失败静默回落出厂常量；
- `model_weight_signature()` **早就**把生效权重写进了 `analytics_core.py:1497` 与 `report_core.py:209` 的产出物 —— 所以「可见性」是我此前的**误判**，它一直可见。

真正缺的只有一条：想整体关掉标定，只能删文件或改 `accepted`，**没有总开关**。已补 `MODEL_CALIBRATION_ENABLED`（默认开，行为不变），命名走 `MODEL_` 前缀从而自动进入 `decision_policy_payload` / `DecisionPolicySignature`，开关状态本身可追溯。

### 8.3 剩余优化机会（按「可独立上线 + 风险可控」排序）

| # | 机会 | 证据 | 收益 | 成本 / 风险 |
|---|---|---|---|---|
| **1** | ~~**推送 17 个未推送提交**~~ | `git rev-list --count origin/main..main` = 17 | **已完成**（本轮共推送 24 个提交）。⚠️ **我原先的理由写错了**：这里曾写「gh-pages 归档停在 09-04」，实际查 `gh-pages` 分支，其提交一直到 **2026-09-15**，公网内容是最新的一天也没落下（说明你本地 GUI 一直在正常发布）。推送本身仍然该做，但**它不是 Pages 更新的前提** | 已完成 |
| **2** | **11 个远程分支清理** | `git branch -r` 见 `codex/*`、`audit/*`、`refactor/*` 等 | 降低误合并/误 checkout 概率；仓库可读性 | 需逐个确认无独有代码（照 §6 分支处置的流程做），约半天 |
| **3** | **6 个临时诊断脚本迁出根目录** | 运行时探测 + 零引用：`_smoke_bt.py`、`debug_vec.py`、`diag_vec.py`、`diag_vec2.py`、`smoke_backtest.py`、`validate_vectorized.py`（共 12 KB） | 根目录 116 → 110；这些名字（`diag`/`debug`/`smoke`）会让人误以为是生产模块 | 低。建议移到 `tools/` 而非删除 —— 它们是你调向量化时可能还用的脚本。注意 `validate_vectorized.py` 被 `backtest_score_vectorized.py:10` 的注释引用，移动后要同步改注释 |
| **4** | **Phase 2：21 日窗口常量化**（`300` 部分见下方更正） | `21` 散落在 **6 个模块 12+ 处**：`score_core` 6 处、`score.py` 3 处、`score_endpoint_acceleration_v79` 5 处、`analytics_core` 3 处、`scanner_core` 2 处、`backtest_score_vectorized` 3 处。而 `config_core` 里**同是 21** 已有 `ROC_PERIOD` / `CMF_PERIOD` / `RSI_PERIODS` | 调窗口今天要改 12 处；更糟的是两个 overlay 会**悄悄保留旧值**（正属你最在意的隐蔽断裂）。A 股不同板块最优窗口可能不同 | 低。全被现有 golden / 向量化对齐测试锁住 |
| **5** | **Phase 3：剩下的两个语义锁** | `conditional_fill_v96` / `score_acceleration_v79`（`technical_resonance_v90` 的锁已由 `test_technical_resonance_v91.py` 补齐） | 防止 overlay 悄悄改语义 | 中 |

> **更正我自己上一轮的一处误报**：§2 D2 / 旧版 #4 写「9 处 `300` 硬编码」——
> 那是把「**沪深300**」这个指数专有名词和 `("300","301")` 创业板代码前缀也算进去了。
> 排除二者后，真正的最小样本长度判据 `len(frame) < 300` 是 **7 处**，且其中 5 处
> 分布在 `backtest_*_v80` / `v98` / `conditional_fill_v96` 等 overlay 里各写一遍。
> 所以这里的真问题不是"魔法数"，而是**同一判据跨 overlay 重复**，治法应是收敛
> 到一处判据函数，而不是简单提个常量。

> **analytics_core 的提取已经完成**（`institution_scanner/backtest_statistics.py`
> 17,969 B，16 个 helper 全在里面，`test_analytics_core_golden.py` 8 项绿）。
> 别再把它排进待办。
| **6** | **依赖 lock 文件** | `requirements.txt` 用范围约束（`pandas>=2.0,<3.0`），无 lock | 换机器可复现；`akshare` 这类高频更新库尤其需要 | 低（`pip-compile` 或 `uv lock`），但会引入新工具链 |
| **7** | **Phase 5 剩余：死分叉 + 依赖降级契约** | 见 §4 Phase 5 | — | 中 |

**明确不做的**：打包 / `[project]`（有意设计，见 §5.3 修正）、`gui_core` 拆分（余量 2,648 B 非阻塞）、27 个 plain 模块归属（纯可读性）、28 组重复体去重（改动面广，等语义锁装好）。

### 8.4 CI 红灯 —— 已定位并修复，两个彼此独立的根因

上一版这里写的是「CI 从 09-10 起就是红的，原因未知」。现在查清了，**不是一个
红灯，是两个叠在一起的**，而且第二个从来没绿过。

**定位手段（可复用）**：Actions 日志匿名访问返回 403，本机没有 `gh`、没有
Docker，`wsl.exe` 被沙箱策略拦截。但 GitHub 有两个东西是**匿名可读**的：

1. job 的 `steps[].conclusion` —— 能看出失败发生在哪一步；
2. check-run 的 **annotations** —— `GET /repos/{o}/{r}/check-runs/{id}/annotations`。

于是用 workflow 命令 `::error::<文本>` 把 pytest 的失败摘要**复写成
annotation**，就能匿名读回来。换行用 `%0A` 转义。这个办法在本轮三次迭代里
每次都拿到了完整失败信息，比二分 step 快得多。

#### 根因 1：三个 golden 闸门用位级相等比较浮点（真凶，09-10 起红）

```
FAILED tests/test_analytics_core_golden.py::test_fixture_matches_live_behaviour
FAILED tests/test_filters_core_golden.py::test_golden_output_matches_for_every_scenario
FAILED tests/test_score_core_golden.py::test_golden_output_matches_for_every_case
```

diff 全是末位噪声：

```
score_volatility[etf_like]: 3.494920656917694 -> 3.4949206569177194   (相对差 ~7e-15)
total:                     23.41711159456133  -> 23.417111594561334
```

**位级相等取决于 BLAS 内核、CPU 向量宽度和 numpy/scipy 构建，不是本仓库代码
的性质。** 同一份源码在 Windows（本地）上是绿的，在 ubuntu-24.04 + numpy
2.4.6 / scipy 1.17.1 上是红的。所以这三个「行为漂移」其实什么都没漂。

修复（`87136c5`）：

* 新增 `tests/golden_match.py`：结构化比较器，`rel_tol=1e-9` / `abs_tol=1e-12`，
  NaN 自相等，bool 按 identity 比（否则 `True == 1` 会让判定变成计数）。
  容差比实测噪声宽 6 个数量级，比这些 fixture 要抓的最小语义改动紧 5 个数量级。
* 三个闸门改走该比较器，不再各自维护 `_same`。
* 新增 `tests/test_golden_match.py`（9 项）从两头钉住容差，并钉住三个闸门必须
  真正经由 `golden_match` 比较 —— 防止以后改回 `==`，让红灯从侧门回来。

反向验证（证明松化后仍会咬）：

* fixture 注入 1e-6 相对漂移 → score 与 analytics 双双变红，还原 → 绿；
* `REL_TOL` 改成 `1e-3`（过松）或 `0`（回到位级）→ 契约测试变红，还原 → 绿。

> 教训：这是「闸门依赖机器环境」的**第四次**（前三次见 `d6d330e` / `7b22750`
> / `a7251bc`）。凡是冻结浮点输出的 fixture，都必须显式写明容差从哪来 ——
> 否则它守的不是代码，是 CPU。

#### 根因 2：浅克隆丢了历史提交（这个测试在 CI 上从来没绿过）

修掉根因 1 之后，CI 暴露出第二个：

```
ERROR at setup of test_every_moved_function_is_byte_identical
CalledProcessError: 'git show cd63ffd:signal_lifecycle_core.py' exit status 128
```

`tests/test_signal_lifecycle_extraction_equivalence.py` 要证明
`signal_lifecycle_core` → `institution_scanner.signal_attributes` 是**机械搬运**
（逐函数字节相同），只能拿搬运前的源文件比对，所以要从对象库读历史提交
`cd63ffd`。但 `actions/checkout` 默认 `fetch-depth: 1`，会丢掉这个提交。

同一个仓库的 `daily-pages.yml` **早就固定了 `fetch-depth: 0`**，只有
`static-quality.yml` 漏了 —— 所以这个闸门在本地和日更流水线里能过，在质量
门禁里从没过过。

修复（`5a4c824`）：

* `static-quality` 的 checkout 加 `fetch-depth: 0`，并在注释里写明原因，避免
  以后被当成多余配置删掉。
* 测试加预检：提交不在对象库时抛带补救办法的 `RuntimeError`，而不是裸的
  exit 128。**这里刻意不做 skip** —— 在浅克隆上安静跳过，等于这个闸门在 CI
  上什么也没守住（正是本项目反复出现的「空转闸门」缺陷类）。

#### 当前状态与遗留风险

| run | commit | 结论 |
|---|---|---|
| #597 | `ca5a36b` | failure（根因 1） |
| #602 | `87136c5` | failure（根因 1 已修，暴露根因 2） |
| #604 | `5a4c824` | failure ← **根因 2 已修，却仍然红，原因未取证** |
| #605 | `144513a` | **success** |

**不能因为 #605 绿了就宣布修好。** #604 与 #605 之间只差一个诊断脚本和一个
`if: failure()` 步骤，逻辑上不该改变测试结果 —— 这意味着要么 #604 是某个
**间歇性失败**（网络依赖 / 计时 / 并发），要么还有第三个原因没露出来。
下一步：移除诊断脚手架后再跑一次确认；若再次变红，用同一套 annotation 办法
取证，不猜。

> **方法论沉淀**：本轮两次用「运行时探测」推翻静态分析的结论（web_report 链、
> v90 分支）。这个仓库是 monkey-patch 架构，**overlay 靠 import 副作用安装，
> 静态 grep 既会漏报也会误报**。凡是「这个模块还有用吗」的问题，都应该起子进程
> import 入口、再看 `sys.modules`，而不是 grep。

### 8.5 Daily A-Share Pages：compute 成功、publish 5 秒失败（待取证）

**先更正我在 §8.3 / 上一版的错误判断。** 我曾写「gh-pages 归档停在 09-04，
所以 B1 修复和三次重构全都还没到公网」。查 `gh-pages` 分支的提交列表，它一直
到 **2026-09-15**（`report: research briefing 2026-09-15`），09-07/08/09/10/11/
14/15 都在 —— **公网内容是当天最新的，一次也没落下**。本地 `output/web_report/
reports/` 同样有 09-11 / 09-14 / 09-15，说明是你本地 GUI 在发。推送该做，但它
从来不是 Pages 更新的前提。

那么 `Daily A-Share Pages` 的红灯是什么？看 run #16（`34971404175`，commit
`ca5a36b`）：

| job | 耗时 | 结论 |
|---|---|---|
| `compute-and-verify` | 40m 47s | **成功**，产出 artifact `verified-pages-site`（33.8 KB） |
| `publish` | **5s** | **失败** |

失败步骤是 `Publish immutable verified artifact`，即
`python -m institution_scanner.publish_site output/web_report`，页面上唯一的
错误文本是 `Process completed with exit code 1`。

**5 秒说明是快速前置失败，不是克隆或推送超时。** 候选原因：

1. `_report_date()` 在 `reports/` 下找不到 `????-??-??.html` → 抛
   `WEB_REPORT_ARCHIVE_MISSING`；
2. `index.html` 缺失 → `WEB_REPORT_SITE_MISSING`；
3. `_branch_exists()` 的 `git ls-remote` 鉴权失败，返回码 128 不在
   `allow=(0, 2)` 内 → 两个候选都失败后抛 `WEB_REPORT_REMOTE_UNREACHABLE`。

已排除：`.nojekyll` 缺失（本地产出里有，且 `publication_renderer.py:461` 会写，
上传 artifact 时也带了 `include-hidden-files: true`）。

**已装取证**：publish 步骤改为 `tee publish-out.txt`，并在 `if: failure()` 下用
`tools/dump_log_annotation.py` 把日志复写成 annotation —— 明天 07:40 UTC 的定时
运行会自动把真正的错误吐出来。不想等的话，在 Actions 页面用
`Run workflow`（该 workflow 开了 `workflow_dispatch`）手动触发一次即可，约 40
分钟后就有结果。

**影响评估**：因为本地一直在发布，这个失败**不影响公网内容**，属于冗余自动化
坏了。但它每天白烧 40 分钟 Actions 时长却什么也发不出去，值得修。

> 顺带：该仓库的 workflow 列表里有十几个一次性遗留工作流
> （`apply_decision_gui_v24`、`apply_gui_clean_v25`、`apply-performance-v2`、
> `apply_research_integrity_v23` ……），外加一个 `cleanup-merged-branches`。
> 这些「apply 某次改动」型工作流是一次性脚本，留着只会让人误判当前交付链路，
> 建议归档删除（与 §8.3 #2 的分支清理一起做）。

### 8.6 本轮（09-16）：两处 `finally` 修复 + 21 日窗口常量化

#### A. 两处 `return` in `finally` —— 并更正我上一轮的严重性判断

我上一轮把 `tests/reverse_validate_downloader_core.py:156` 说成「必现空转闸门、
会撒谎」，**说高了**。最小复现的结果：

| 情形 | 行为 |
|---|---|
| 还原**成功** | 异常正常抛出 → **不会产生假绿** |
| 还原**失败** | `return 1` 吞掉异常；但退出码仍是 1（红的） |

所以真实影响是「**掩盖错误原因 + 中断后续注入**」，而不是假绿。仍然值得修
（另外 Python 3.14 会给 `SyntaxWarning: 'return' in a 'finally' block`），但它
配不上「必现」两个字。

`tests/_strip_module_level_installs.py` 那个**才是真问题**：`problems` 只在
`verify()` 里赋值，`write()` 先抛异常时 `finally` 里的 `if problems:` 变成
`UnboundLocalError`——**既掩盖原始错误，又不还原文件**（源码会留在被改动状态）。
已改为预置 `problems = []`，并在 `except` 里显式还原后重抛。

> 顺带发现：`_strip_module_level_installs.py` 现在 4 个目标**全部 SKIP**
> （`line 181 past EOF (178 lines)`）——它硬编码的行号在我前几轮删代码后已
> 失效，**当前什么也没验证**（0 保留 / 0 阻断 / 4 跳过）。又一个空转闸门，待修。

#### B. 21 日窗口常量化：20 处字面量 → 1 个常量

新增 `config_core.BREAKOUT_LOOKBACK_BARS: int = 21`。`BREAKOUT_` 已在策略签名
前缀里，所以它自动进入 `decision_policy_signature` —— 这是**正确**的：它确实
影响突破阻力位、量能基线和动量门槛。

接线（全部用带断言的补丁脚本，24 个锚点各命中恰好 1 次）：

| 模块 | 处数 | 取值方式 |
|---|---|---|
| `score_core.py` | 7 | `from config import` |
| `score.py` | 3 | `_config.` |
| `score_endpoint_acceleration_v79.py` | 5 | **`_score.`**（该 overlay 原本没有 config import，走它已导入的 `_score`，避免改动装载顺序） |
| `analytics_core.py` | 3 | `from config import` |
| `institution_scanner/backtest_score_vectorized.py` | 3 | `from config import` |

`scanner_core.py:615-616` 的 `recent_return_20d` **故意不动**：那是展示用的 20 日
涨幅指标，不是决策阈值，并进决策常量反而混淆语义。

**反向验证**：常量改成 22 → **4 个测试变红**（`score_core` golden、
`signal_lifecycle` golden、2 个向量化对齐测试）；还原 → 绿。证明 20 处真的接上
了，不是替换了个寂寞。

**golden 重捕**：`signal_lifecycle_golden.json` 12 行变化。逐列比对确认
**只有 `DecisionPolicySignature` 一列变**（`dfe59777…` → `5e456ea8…`），
其余 180+ 列、12 行完全一致。

#### C. 补上一个实测出来的盲区

反向验证时发现：`analytics_core._breakout_quality_factor` **不在 golden fixture
里**——我改的那 3 处没有闸门看着（它是 `analytics_core.py:483` 的活调用，不是
死代码）。

新增 `test_breakout_window_reads_the_shared_constant`：构造一个「收盘价高于平盘
高点、且 -22 根处有一根尖峰」的探针 frame。窗口 21 时 `iloc[-21:-1]` 取不到尖
峰、`prior_high` 是平盘值；窗口 22 时尖峰进入、`prior_high` 抬到收盘价之上，判据
翻转。

反向验证：把函数里的常量换回字面量 `21` → 测试**变红**，且报的正是那句
「helper is reading a literal instead of the shared constant」；还原 → 绿。

本地 **248 项全绿、0 失败、0 错误**，ruff 全过。

### 8.7 本轮：conditional_fill_v96 语义锁（并基于证据调整了 #5 的优先级）

原计划给 `score_acceleration_v79` 和 `conditional_fill_v96` 各装一把锁。查完现有
覆盖后发现顺序该**反过来**：

- **v79 其实已经被覆盖了。** `score_core_golden.json` 的 `function_provenance`
  显示，v79 的 `_series` / `_latest` / `_rolling_mean` / `_safe_return` /
  `_score_dimensions_available` / `classify_style` / `score_trend` **就是被测对象
  本身**，改语义会直接红；`score_endpoint_acceleration_v79` 的 `breakout_score` /
  `execution_quality_score` / `value_trap_risk` 同样在册。再装一把锁是重复劳动。
  （另注：v79 的 `score_volume` / `score_accumulation` / `score_structure` /
  `entry_point` 在该装载顺序下解析到 `score_scale_migration_v95` /
  `score_cache_guard_v80`，被盖掉了。是否在其他入口生效尚未展开，记为待查。）
- **`conditional_fill_v96` 是零覆盖。** 它把 `analytics_core._backtest_one_ticker`
  整个换成条件成交版（WAIT 单只在有效期内回踩进区间才成交），而 analytics golden
  冻结的 16 个函数全是回测**统计** helper，不含执行器 —— 改成交模型不会有任何
  闸门报错。

**新增 `tests/test_conditional_fill_v96.py`（6 项）**，锁的是**规则**而不是输出快照：

| 测试 | 锁的规则 |
|---|---|
| `test_install_publishes_the_conditional_fill_contract` | install 真的接管了执行器（用 `is` 判身份，不是 `==`）+ 三个常量已发布 |
| `test_validity_window_ends_after_the_configured_number_of_bars` | 窗口恰好 N 根：第 N 天回踩成交，第 N+1 天不成交 |
| `test_a_bar_opening_below_the_zone_rejects_the_fill` | 跳空低开是**终局**拒绝，后面再回踩也不成交 |
| `test_an_open_inside_the_zone_fills_at_the_open` | 区间内开盘 → 按开盘价成交 |
| `test_an_open_above_the_zone_fills_at_the_zone_high` | 高开回落触及区间 → 按区间上沿成交 |
| `test_no_touch_inside_the_window_means_no_fill` | 始终没回踩 → 不成交 |

**五条反向验证全部会咬**（逐个改坏 `conditional_fill_v96.py` 的对应判据）：
有效期 `+1`→`+2`、跳空 `return None`→`continue`、区间内成交价改错、高开回落改用
最低价、install 不再接管执行器 —— 每条都是「改坏变红、还原转绿」。

> **其中有一条第一次没咬**，值得单记：跳空那条原本的输入里，跳空之后没有别的
> 触碰，`return None` 和 `continue` 结果完全相同 —— 根本测不出「拒绝是终局的」。
> 补了一根后续有效回踩的 K 线才区分开。**反向验证不是走形式，它会真的告诉你
> 测试弱在哪**；这也是本项目第 N 次证明「空转闸门」只能靠注入来发现，看代码看
> 不出来。

本地 **254 项 0 失败 0 错误**，ruff 全过。

## 9. 分析与回测子系统专项审视（2026-09-16）

方法：起子进程 import `main` + `daily_pipeline` + `historical_backtest`，再看
`sys.modules`，而不是 grep。包内模块要按**带点名**（`institution_scanner.X`）判定，
否则会全部误报成"未加载"。

### 9.1 结构实测

**分析（5 个模块，全部在跑）**

| 模块 | 字节 | 备注 |
|---|---|---|
| `analytics_core.py` | 142,671 | 3483 行 / 43 个顶层定义，全仓最大 |
| `analytics_acceleration_v77.py` | 17,550 | 加速 overlay |
| `analytics.py` | 16,270 | facade |
| `analytics_runtime.py` | 4,858 | 包内 |
| `analytics_compat_v97.py` | 1,775 | 兼容层 |

`analytics_core` 里**回测四大件就占 1513 行**：`apply_backtest_ranking` 526、
`run_historical_backtest` 523、`_backtest_one_ticker` 261、`_ticker_backtest_rows` 203。

**回测（22 个模块，21 个同时装载）**

| 类别 | 模块 |
|---|---|
| 性能 overlay（8 个） | `acceleration_v77`、`alignment_acceleration_v80`、`cache_acceleration_v80`、`sample_acceleration_v80`、`fastpath_v78`、`fastscore_v80`(34 KB)、`vectorization_v98`(42 KB)、`incremental_v78` |
| 完整性 overlay（2 个） | `math_integrity_v94`、`rank_integrity_v82` |
| 生产激活（1 个） | `production_activation_v93` |
| 其它 | `alignment`、`command_v76`、`sample_guard_v80`、`worker_tuning_v80`、`profile_alignment_v95`、`historical_backtest`、包内 `backtest_statistics`/`backtest_web`/`backtest_observability`/`backtest_profile` |

> `institution_scanner/backtest_score_vectorized.py`（34 KB）在导入期**未出现在
> `sys.modules`** —— 但它是 `historical_backtest.py:117` 的**函数内懒加载**，
> **不是死代码**。判定死活必须看导入方式，不能只看导入期快照。

### 9.2 核心判断：这里的技术债不是「代码多」，是「补丁叠补丁」

最有力的证据是两个 `*_integrity_*` overlay：

- `backtest_math_integrity_v94.install()` 先存下
  `_ORIGINAL_WEIGHTED_PROFIT_FACTOR` / `_ORIGINAL_SUMMARY_TO_DICT`，再把
  `_core._weighted_profit_factor` 和 `BacktestSummary.to_dict` 换成自己的版本 ——
  这是**包一层修 bug**，不是改源头。
- `backtest_rank_integrity_v82` 装一个 "single recency ranking guard"。

即：某个加速 overlay 改坏了数值 → 不去修源头，而是**再叠一个 overlay 来纠正**。
每叠一层，真实行为就离源码更远，而一次回测的结果由 **21 个同时装载的模块**共同
决定。这才是这个子系统真正的风险，比行数多严重得多。

### 9.3 优化清单（按 价值 / 风险 排序）

| # | 事项 | 证据 | 收益 | 风险 |
|---|---|---|---|---|
| **1** | **先给 `_weighted_profit_factor` / `BacktestSummary.to_dict` 装语义锁** | v94 改的这两个函数**没有专属闸门**，analytics golden 的 16 个函数不含它们 | 做 #2 的前置；没有它，#2 就是盲改 | 低 |
| **2** | **把 v94 的修正下沉回源头，然后删掉 v94** | v94 存了 `_ORIGINAL_*`，证明是包装而非替代 | 少一层 overlay；回测数值的"唯一真相"回到源码 | 中（须先做 #1，再 golden 比对） |
| **3** | **拆 `analytics_core` 的回测四大件（1513 行）** | 两个 500+ 行函数；已有成功先例：`backtest_statistics`（16 helper / 18 KB）就是这么搬出去的，golden + provenance 闸门现成 | 单函数从 526 行降到可审规模；预算压力缓解（现仅剩 2.4 KB） | 低-中（沿用同一套闸门逐函数搬） |
| **4** | **收敛 8 个性能 overlay** | 需先做**运行时判定**：哪些仍生效。参照已发现的 `score_acceleration_v79` 有 4 个函数被 v95/v80 盖掉 | 每退役一个，少一层不确定性 | 低（只删确认不生效的） |
| **5** | **FAST / EXACT 双轨的口径显式化并锁住** | `fastscore_v80` 34 KB；输出列里有 `SmoothTriggerApproximate`，说明存在"近似"分支；现有 `test_backtest_score_vectorized_alignment` 在测对齐 | 明确列出 FAST 与 EXACT 允许在哪些列不同、容差多少，防止近似悄悄扩散 | 低 |
| **6** | **跨 overlay 重复判据收敛** | 已实证：`len(frame) < 300` 在 5 个 overlay 里各写一遍（`backtest_*_v80` ×3、`vectorization_v98`、`conditional_fill_v96`） | 一处判据，改了不会漏 | 低 |

**暂缓**：GUI 拆分（每天在用且无测试覆盖）；分支 / 遗留 workflow 清理（不可逆，
需先打 `archive/<branch>` tag）。

### 9.4 #1 已完成：backtest_math_integrity_v94 语义锁（2026-09-16）

先查清 v94 到底打了什么补丁。它一共改 5 处，**其中两处是数学、三处是接线**：

| 补丁 | 性质 |
|---|---|
| `analytics_core._weighted_profit_factor` 把 `+inf` 截断到 `PROFIT_FACTOR_SCORE_CAP` | **数学** |
| `analytics_core.BacktestSummary.to_dict` 补 `split_policy` | **数学** |
| `model_calibration._prepare_samples` 追加 `calibration_weight` | 接线 |
| `analytics.calibration_details_for_frame` 过滤 peer prior | 接线 |
| 发布 `PRODUCTION_BACKTEST_MATH_VERSION` / 两个常量 | 接线 |

原始 `_weighted_profit_factor` 在全胜样本（有盈利、无亏损）时返回 `float("inf")`，
而 `+inf` 不是合法 JSON —— 序列化后变成 `null`。这正是 v94 存在的理由。

**新增 `tests/test_backtest_math_integrity_v94.py`（8 项）**，锁规则：
全胜样本封顶且**能通过 JSON 往返**、有限盈亏比原样透传、空样本仍是 NaN、
summary 必须披露 `split_policy`、install 发布契约、`PROVISIONAL` 打 0.25 折、
**拥挤日最多一个单位影响力**（同日权重归一到 1）、peer prior 只保留 level 含
signal 且带 entry_signal 的行。

**六条反向验证全部会咬**（逐个改坏 v94）：去掉 `+inf` 截断、把 cap 从 3.0 抬到
100、summary 不披露 split_policy、PROVISIONAL 不打折、同日重叠不归一、peer prior
不要求 signal level。

> **过程中修正了一条软断言**：`assert result == installed.PROFIT_FACTOR_SCORE_CAP`
> 在常量被改成任何值时都仍然为绿 —— 光比对常量等于没锁。补了一条
> `assert installed.PROFIT_FACTOR_SCORE_CAP == 3.0`，把数字本身也钉住（理由写进
> 注释：ranking 的饱和值就是 3.0，这里不一致就会互相矛盾）。**凡是「断言等于某个
> 常量」的写法，都要自问：常量被改时这条还绿吗？**

> fixture 故意**不做拆卸**：v94 没有 `uninstall()`，而生产的两个入口
> （`backtest_command_v76:35`、`analytics_runtime:97`）也都是装上就不拆的，拆了反而
> 让测试进程不像生产。全量跑完确认无交叉污染。

本地 **262 项 0 失败 0 错误**，ruff 全过。

**下一步 #2**：把 cap 与 `split_policy` 下沉回 `analytics_core` 的
`_weighted_profit_factor` / `BacktestSummary.to_dict`，确认 golden 无数值漂移后删除
v94 —— 现在有闸门了，这一步才是安全的。

### 9.5 #4 运行时判定结果：不是「能删 4 个」，而是「5 个重绑定目标是顺序依赖的输家」

**方法**：AST 解析每个 overlay 的 `install()`，抽出 `<module>.<attr> = <值>`；再起子
进程导入生产入口，把**意图值**与**实际解析值**比对（调用比身份、常量比值）。

> 第一版判定有两处假阳性，已修正：① 字符串常量没有 `__module__`，一律被误判成
> 「被覆盖」；② `backtest_alignment._LEGACY_PRICE_ON_DATE` **本来就该指向原函数**，
> 指到原函数恰恰说明补丁生效了。所以必须比对「意图」而不是只看归属模块。

| overlay | 生效 / 被覆盖 | 判定 |
|---|---|---|
| `backtest_acceleration_v77` | 2 / 0 | 完全生效 |
| `analytics_acceleration_v77` | 4 / 0 | 完全生效 |
| `backtest_fastscore_v80` | 1 / 0 | 完全生效 |
| `backtest_alignment_acceleration_v80` | 3 / 1（该 1 条为误判） | 完全生效 |
| `backtest_vectorization_v98` | 5 / 2 | 部分生效 |
| `backtest_cache_acceleration_v80` | 0 / 1 | install 补丁被覆盖 |
| `backtest_sample_acceleration_v80` | 0 / 1 | install 补丁被覆盖 |
| `backtest_fastpath_v78` | 0 / 1 | install 补丁被覆盖 |
| `backtest_incremental_v78` | 0 / 1 | install 补丁被覆盖 |

#### 但「补丁被覆盖」≠ 模块可删 —— 逐个查直接引用后，4 个候选一个都不能删

| 候选 | 仍然被谁用 |
|---|---|
| `backtest_cache_acceleration_v80` | 被 `backtest_acceleration_v77` import |
| `backtest_sample_acceleration_v80` | 被 `backtest_acceleration_v77` / `backtest_sample_guard_v80` / **`backtest_vectorization_v98`** import（v98 改它的 `_backtest_one_ticker`，那条补丁是**生效**的） |
| `backtest_fastpath_v78` | `analytics_runtime.py:80` **显式调用 `install()`**，且登记在 `runtime_inventory.py` |
| `backtest_incremental_v78` | 被 `backtest_acceleration_v77` import |

#### 真正有价值的结论：5 个重绑定目标是「顺序依赖的输家」

| 争议目标 | 当前胜者 | 输家 |
|---|---|---|
| `analytics_core._backtest_one_ticker` | `backtest_alignment.install_analytics_alignment.<locals>.aligned_one` | `sample_acceleration_v80`、`vectorization_v98` |
| `analytics_core._backtest_one_ticker_cached` | `institution_scanner.point_in_time_backtest.install.<locals>.pit_cached` | `cache_acceleration_v80`、`incremental_v78` |
| `analytics_core._signal_evaluations` | `backtest_fastscore_v80._signal_evaluations` | `fastpath_v78` |
| `backtest_fastscore_v80._fast_score_matrix` | `scoring_consistency_v94._fast_score_matrix` | `vectorization_v98` |

这带来两个方向的真实风险：

1. **改了没用**：有人去编辑 `cache_acceleration_v80` / `incremental_v78` /
   `fastpath_v78`，会以为自己在改生产行为，实际上一个字节都没生效。
2. **顺序一变就换执行器**：`_backtest_one_ticker` 现在是 alignment 的包装，但只要
   import 顺序改变，就会悄悄换成 v98 或 v80 的实现 —— **没有任何东西会报错**，
   而回测结果会整体改变。

#### 建议的下一步（不是删，是锁）

加一条**「胜者锁」**测试：导入生产入口后，断言这 4 个争议符号解析到预期实现。
一旦有人改动装载顺序或新增 overlay，测试立刻变红，而不是让回测悄悄换一套执行器。

> 实现注意：本机 3.14 解释器加载不了 `_overlapped`，in-process `import main` 会失败，
> 所以这条锁要像 `test_assembly_manifest` 那样走**子进程 + 打桩**。CI 是 3.11，不受影响。

### 9.6 #4 落地：胜者锁 `tests/test_backtest_overlay_winners.py`（8 项）

把 §9.5 的结论固化成闸门：子进程导入 `main` + `daily_pipeline` +
`historical_backtest`，断言 4 个争议符号解析到预期实现。

| 争议符号 | 锁定胜者 | 记录中的输家 |
|---|---|---|
| `analytics_core._backtest_one_ticker` | `backtest_alignment` | `sample_acceleration_v80`、`vectorization_v98` |
| `analytics_core._backtest_one_ticker_cached` | `institution_scanner.point_in_time_backtest` | `cache_acceleration_v80`、`incremental_v78` |
| `analytics_core._signal_evaluations` | `backtest_fastscore_v80` | `fastpath_v78` |
| `backtest_fastscore_v80._fast_score_matrix` | `scoring_consistency_v94` | `vectorization_v98` |

设计要点：
- **走子进程**：导入生产入口会全进程安装 overlay，会污染套件其余部分。
- **只比 `__module__`**：胜者多是 `install()` 里的闭包（`<locals>.aligned_one`），
  比对完整限定名太脆，比对模块名既稳又有意义。
- **正反成对**：除了「胜者是谁」，还断言「记录在案的输家没赢」，并且探针取不到
  结果时**大声抛错**——否则这就是个空转闸门。

反向验证（两条，都咬）：
1. 把 `_signal_evaluations` 的期望胜者改成 `fastpath_v78` → 红；
2. 让子进程不输出探测结果 → 红（探针不可用必须炸，不能安静通过）。

> 打桩说明：子进程给 `_overlapped` 与 `tickflow` 打了桩，因为部分 Windows 构建
> （本机 3.14）加载不了 `_overlapped`，会在 `tickflow` / `curl_cffi.aio` 处中断导入
> 链。**我们测的是 import 图，不是厂商客户端**，打桩不影响判定。CI 是 3.11，无此问题。

本地 **270 项、0 真实失败**（26 项为沙箱 `WinError` 噪声），ruff 全过。

### 9.7 #2a 已完成：把 v94 的两个数学补丁下沉回源码（2026-09-16）

**结论先说**：下沉完成，`backtest_math_integrity_v94` 从「改数学 + 接线」退化为
**纯接线**。回测数值的唯一真相回到 `analytics_core` 源码，不依赖任何 overlay 的装载顺序。

#### 下沉了什么

| 规则 | 原位置 | 新位置 |
|---|---|---|
| 全胜样本 profit factor 截断到 3.0 | `v94.install()` 里的闭包 `weighted_profit_factor` | `analytics_core._weighted_profit_factor` 的 `if profit > 0.0` 分支 |
| `to_dict` 补 `split_policy` | `v94.install()` 里的闭包 `summary_to_dict` | `analytics_core.BacktestSummary.to_dict` |

两个常量 `PROFIT_FACTOR_SCORE_CAP` / `BACKTEST_SPLIT_POLICY` 移到 `analytics_core`
模块级（紧邻 `BACKTEST_TEST_START`），v94 改为**再导出** `v94.X = _core.X`，保证只有一份定义。

#### 一个必须先查清的坑：`analytics.py` 会覆盖 `to_dict`

`analytics.py:431` 把 `_core.BacktestSummary.to_dict` 换成 `_backtest_summary_to_dict`
（加 `resonance_analysis`），而且**文件末尾有 `sys.modules[__name__] = _core`**——
也就是说 `import analytics` 拿到的就是 `analytics_core` 本身。

后果有两个，都影响闸门怎么设计：

1. 下沉必须落在 `analytics_core` 源码里，让 `analytics` 的包装器把它带下去。实测链路成立：
   裸 `import analytics_core` 就能拿到 `split_policy`。
2. **进程内无法观测「未加装 overlay 的源码」**——`_LEGACY_SUMMARY_TO_DICT` 在
   `sys.modules` 替换后不可达，live 属性永远解析到门面。所以「源码层是否含 split_policy」
   这条断言只能走子进程，和 §9.6 的胜者锁同一个套路。

#### 新增闸门（4 项，全部反向验证过）

在 `tests/test_backtest_math_integrity_v94.py` 里加了两个维度：

- **行为维度**：`_weighted_profit_factor.__module__ == "analytics_core"`（若有人把规则
  重新做成 overlay 包装，解析到的模块名就变成 `backtest_math_integrity_v94`，立刻红）；
  常量对账 `v94.X == analytics_core.X`，防止 v94 长出会漂移的第二份定义。
- **源码维度**：子进程 `import analytics_core`（不装任何 overlay），直接读
  `to_dict.__module__`、`split_policy`、`pf_module`、全胜样本的返回值。
  探针不产出结果时**大声抛错**，避免变成空转闸门。

反向验证三条全咬：

| 破坏方式 | 结果 |
|---|---|
| 源码 cap 改回 `float("inf")` | 红（2 项） |
| 删掉源码的 `split_policy` 行 | 红（2 项） |
| 把 cap 重新叠回 v94 的 overlay 包装 | 红（`__module__` 那项） |
| 还原 | 绿 |

#### 顺带被闸门抓到的一次真实改动：装配清单

`tests/test_assembly_manifest.py` 4 个入口全红，报 `install_post_facade 不再重绑定`
`BACKTEST_SPLIT_POLICY` / `PROFIT_FACTOR_SCORE_CAP` / `_weighted_profit_factor`
以及 v94 的两个 `_ORIGINAL_*`。**这是闸门在正常工作**，不是回归：两个常量现在源码已有
同名定义，赋值不再构成「重绑定」；另三个确实被移除了。

按文件头文档用 `python tests/assembly_manifest.py --write` 重捕获，然后逐条核对 diff：
**4 个 JSON 只有删除与行号位移，没有任何非预期新增**，`assembly_module_level_sites.json`
（30 个模块级 install 站点）完全未变。

#### 预期行为差异

**没有**。生产路径上 v94 一直是生效的（§9.5 判定 2/0），下沉前后数值一致。
真正的差异在于：现在**不装 v94 也对**。

### 9.8 #3 第一阶段：搬 `_decision_quality_multiplier`（2026-09-16）

**结论**：搬了一个函数，`analytics_core` 从 143,359 降到 **139,796 字节**，
预算余量从 1,641 回到 **5,204 字节**。装配清单**零变化**——这本身就是「搬移没有
改变装配」的证据。

#### 为什么先做「整函数搬移」而不是切 526 行的 `apply_backtest_ranking`

先做了静态筛查：把全仓库所有 `<alias>.<attr> = ...`（alias 含 `_core` /
`analytics_core` / `analytics` / `analytics_module` …）的赋值目标抽出来，
再和装配清单的 `final` 求并集，得到「被 overlay 重绑定的符号」全集。

结论是**四大件一个都不能整搬**：

| 函数 | 行数 | 被谁重绑定 |
|---|---|---|
| `apply_backtest_ranking` | 526 | `analytics.py` |
| `run_historical_backtest` | 523 | `backtest_alignment` / `production_activation_v93` / `calibration_weight_cache_v79` / `point_in_time_backtest` |
| `_backtest_one_ticker` | 261 | 5 个模块 |
| `_ticker_backtest_rows` | 203 | `analytics.py` / `resonance_runtime_v91` |

它们都是 §9.6 胜者锁盯着的符号。整搬等于把 overlay 的目标换掉，补丁会静默失效。
所以第一阶段只搬**没有任何 overlay 重绑定**的整函数，剩下的留给后续切片。

未绑且 ≥40 行的候选共 5 个：`_decision_quality_multiplier`(114)、
`refresh_research_outcomes`(100)、`write_research_reports`(96)、
`_enrich_one_result`(93)、`_bucket_rows`(55)。
`_bucket_rows` 排除——它调 `_date_balanced_weights`，那是 golden 记录在案的
patched canary。本次搬了 `_decision_quality_multiplier`（四大件之一
`apply_backtest_ranking` 的直接下属、无模块内依赖）。

#### 验证流程（照 golden 既有工序）

1. **搬移前**先给 golden 语料加 5 个用例并捕获，冻结行为。
   语料刻意覆盖两条完备性分支：从 ROE / 毛利率 / 报告元数据**重建**，
   以及 `QualityHardDataComplete` 列**短路**。第 6 行按重建判据是「不完整」，
   按显式列是「完整」——两条分支必须返回不同值，否则语料就是空的。
   5 个用例取值两两不同（已核对）。
2. 搬移，然后**逐用例比对**：50 个用例取值全部一致，只有 `function_provenance`
   从 `analytics_core._decision_quality_multiplier` 变成
   `institution_scanner.backtest_statistics._decision_quality_multiplier`。
3. 反向验证：在**搬移后的**实现里把 `QUALITY_MULTIPLIER_UNKNOWN` 改成 `FAIL`，
   golden 立刻红；还原后绿。

#### 被两个体积闸门咬了一次——都是对的

| 闸门 | 报什么 | 处置 |
|---|---|---|
| `test_size_budgets_are_not_vacuous` | `analytics_core.py` 预算 145,000 离文件 139,796 有 5,204 字节，超过 4,096 上限 | 预算 **145,000 → 142,000**。闸门的设计意图就是「文件缩了预算必须跟着降」，否则腾出的空间还能被重新填回去 |
| `test_large_modules_are_shrink_only` | `backtest_statistics.py` 21,867 > 预算 17,969 | 预算 **17,969 → 21,867**，按该表既定机制「预算跟着代码走」，并写清理由；重新冻结后本模块恢复只减不增 |

顺带：`QUALITY_MULTIPLIER_*` 三个常量在 `analytics_core` 里再无引用，已随函数
一起移走（确认过没有 overlay 读 `_core.QUALITY_MULTIPLIER_*`，所有调用方都从
`config` 导入）。它们只在 `config_core` 定义、无人运行时打补丁，所以在提取模块里
按值导入是安全的——这点和 `compute_volume_profile` 必须晚绑定恰恰相反。

#### 剩余候选

`refresh_research_outcomes`（依赖 `_load_benchmark_frames`，得一起搬或留）、
`write_research_reports`（无依赖，最省事）、`_enrich_one_result`（连同
`_breakout_quality_factor` / `_stage_label`）。以及四大件内部的切片——那需要
逐段读 526 行 `apply_backtest_ranking`，单独一轮做。

### 9.9 #6 已完成：收敛 `len(frame) < 300` 的 7 处重复（2026-09-16）

**结论**：判据收敛到一处，但过程中撞出一个**真实的对外可见行为变化**——
`DecisionPolicySignature` 变了。这不是事故，是闸门在正常工作，下面有完整交代。

#### 是 7 处，不是 5 处

原清单写「5 个 overlay」。实际清点：

| 模块 | 变量 |
|---|---|
| `analytics_core._backtest_one_ticker` | `frame` |
| `analytics_core._signal_evaluations` | `frame` |
| `backtest_cache_acceleration_v80` | `frame` |
| `backtest_incremental_v78` | `frame` |
| `backtest_sample_acceleration_v80` | `frame` |
| `backtest_vectorization_v98` | `frame` |
| `conditional_fill_v96._load_enriched` | `market` |

5 个 overlay 之外，`analytics_core` 自己还有 2 处——那 2 处才是 overlay 的原型。

**先确认它们是不是同一条判据**：逐处读上下文后确认是。都是「缓存 K 线不足
300 根就不做富化 / 回测」，理由一致——指标在短历史下没有定义，宁可跳过也不要
在残缺历史上算出一个数。所以合并是**口径统一**，不是把不同判断硬捏在一起。

#### 落地

* `config_core.BACKTEST_MIN_HISTORY_BARS = 300`（进 BACKTEST 策略常量族）；
* `analytics_core._has_backtest_history(frame)` —— 唯一的判据实现；
* 7 处全部改调它（overlay 走 `_core._has_backtest_history`，晚绑定，不 by-value 导入）。

#### 闸门 `tests/test_backtest_history_threshold.py`（10 项）

设计要点：**光有行为测试抓不住这个缺陷**——缺陷的形态是「同一条规则有第二份拷贝」，
行为测试只测其中一份。所以装了静态 + 行为两道：

* 静态：AST 扫全仓库生产模块，找 `len(<x>) < 300` 字面量（用 AST 而非正则，
  因为这句话在两处注释里是故意保留的）。再逐个确认 6 个模块都真的引用了 helper
  ——只检查「全仓库存在一处 helper」会被「5 个 overlay 各留一份私货」骗过去。
* 行为：299 / 300 / 301 的边界、None、空帧；常量只有一份定义
  （`analytics_core.BACKTEST_MIN_HISTORY_BARS is config_core.BACKTEST_MIN_HISTORY_BARS`）。

反向验证四条全咬：

| 破坏方式 | 结果 |
|---|---|
| 在某个 overlay 里恢复字面量 | 红（3 项，静态两道 + 逐个位点那道） |
| 常量改成 400 | 红（边界 + 单一定义） |
| 边界 `>=` 改成 `>`（差一） | 红（边界） |
| overlay 不走 `_core.` 前缀（改成 by-value 导入） | 红（晚绑定那道） |
| 还原 | 绿 |

#### 预期行为差异：`DecisionPolicySignature` 会变

合并后 `test_signal_lifecycle_golden` 红了，报 `finalize_ranking/canary` 不一致。
查下来只差一列：`DecisionPolicySignature`
`5e456ea8e2ea140cdae3e737` → `5c94079806c7278264ebd331`。

机制在 `result_contract.decision_policy_payload()`：它枚举 `dir(config)`，把
`_POLICY_NAMES` 里或以 `_POLICY_PREFIXES`（含 `BACKTEST_`）开头的常量全部纳入
策略负载再取 SHA-256。而 `_POLICY_EXCLUDED_NAMES` 排除的是**不影响决策**的运维参数
（缓存开关、分块大小、进程数、进度间隔）。

`BACKTEST_MIN_HISTORY_BARS` 决定一支票是否参与回测，是决策参数，**本就应该进签名**。
所以这不是要绕过去的噪声——阈值从「散落在 7 处的硬编码 300」变成「一个已发布的
策略参数」，签名理应改变。已按文档流程重捕获 golden，逐条核对：71 个用例里
**只有 `finalize_ranking/canary` 变，且只变签名一列**，provenance 无变化。

**对外影响**：下一次跑批产出的 `DecisionPolicySignature` 会与历史报告不同，
下游按签名做「策略是否变更」比对的逻辑会触发一次。这是预期内的、一次性的。

#### 过程中的一个事故（已处理）

判定 canary 失败原因时用了 `git stash push`，它**损毁了 `.git`**：`refs/` 目录和
`objects/pack/*.pack` 都没了，git 此后报 "not a git repository"。

处置：远程 `main` 与本地 HEAD 同为 `f5abf3c`，**已提交的工作零丢失**；
重克隆恢复 `.git`，`git reset --mixed HEAD` 重建索引。替换 `.git` 后
`git status` 曾把 61 个文件报成已修改——实际是索引 stat 全失效，
`git diff` 为空、`git add` 进暂存区后 diff 也为空，确认为纯噪声，无内容差异。

教训：**这个仓库不要用 `git stash`**。要临时回退就用「先把文件内容存到内存、
`git show HEAD:<file>` 写回、测完再写回原内容」——即后面实际采用的方式。

### 9.10 #5 侦察：`SmoothTriggerApproximate` 的判据与真实截断不一致（2026-09-16，未改）

§9.3 #5 关心的是「近似悄悄扩散」。落在 `ranking_architecture_v83.py:240`：

```python
result["SmoothTriggerApproximate"] = trigger.ge(99.999)
```

而真正的截断发生在 227-231 行 `smooth_trigger = np.clip(trigger + trigger_delta, 0, 100)`，
条件是 `trigger + trigger_delta` 越出 `[0, 100]`。**两者不是一回事**，实测双向都错：

| | trigger | 未截断 delta | 真实截断 | 发布分数 | 标记 |
|---|---|---|---|---|---|
| 误报 | 100 | −12.21 | 否（87.79，离上限 12 分） | 87.79 | **True** |
| 漏报 | 95 | +10.71 | 是（105.71，被削掉 5.71） | 100.0 | **False** |
| 基线 | 92 | 0.00 | 否 | 92.0 | False（一致） |

`trigger_delta = (smooth_price − legacy_price) * (0.75 + 0.25 * coverage)`，
两个分量函数（`execution_integrity_v87.py:47/58`）的值域决定 delta 约在
`[−12.25, +10.75]`，所以两类偏差都是可达输入，不是理论构造。

**为什么漏报从输出上看不出来**：237-239 行的 `SmoothTriggerDelta` 是
`smooth_trigger − trigger`，即**截断之后**的差值，永远不可能暴露截断。
上表 `trigger=95` 那行显示 `+5.0`，实际被削掉的是 10.71。
测试因此直接调用两个生产分量函数重算未截断 delta，而不是照抄公式。

**未改，列为需你确认**，三条理由：

1. 这列**只写不读**——全仓（排除 cache/output/tests）仅 `ranking_architecture_v83.py`
   一处出现，没有代码消费它，只随 CSV 导出给人看（`test_the_column_has_no_code_consumer` 守着）。
   所以偏差目前是**诊断性**的，不影响任何决策；这也是它能活到今天的原因。
2. 正确条件取决于 `Approximate` 当初想表达什么。引入提交 `93777ca` 只写了
   "a smooth breakout shadow"，`stamp_layered_ranking` 的 docstring 只说
   "without changing production decisions"，均无规格说明。
3. 改它会改变已发布的 CSV 列。

闸门：`tests/test_ranking_architecture_smooth_trigger.py`（4 项）——三类用例各一，
外加「无代码消费者」一条。若日后有人开始消费这列，或修好判据，都会在这里红。

### 9.11 侦察：`report_core` 的存活图——与 `scanner_core` 完全相反（2026-09-16）

`report_core.py` 2090 行 / 24 个顶层定义，是仅次于 `scanner_core` 的第二大覆盖真空。
照 §10.14 的规矩先做存活图，结论与 `scanner_core` **相反**：

| | `scanner_core` | `report_core` |
|---|---|---|
| 顶层定义 | 21 | 24 |
| 死代码 | 约 950 行（53%） | **0 行（0%）** |
| overlay 形态 | **替换**（`run_scan` 828 行被闭包顶掉） | **回调**（每层都调下一层） |

#### 三层装配链

```
report_core.py   2090 行   实现
  ↑ report_v51.py  213 行  覆盖 _results_to_dataframe、装 report_determinism，然后 sys.modules 自替换
  ↑ report.py      423 行  覆盖 _results_to_dataframe 与 export_all，然后 sys.modules 自替换
```

两个包装层都以 `sys.modules[__name__] = _core` 收尾，所以 import 之后
`report`、`report_v51`、`report_core` 是**同一个模块对象**（子进程实测三者皆 True）。

#### 为什么一个死符号都没有

每层在覆盖之前**先把下一层存起来，然后调用它**：

* `report_v51.py:24` 存 `_legacy_results_to_dataframe`，`:57` 调用它
* `report.py:31` 存（此时已是 v51 版），`:72` 调用它；`:32`/`:379` 对 `export_all` 同样处理

所以 `report_core` 那份 326 行的 `_results_to_dataframe` 是**最内层的调用**，
每跑一次必到——它是被**扩展**，不是被**顶替**。这与 `run_scan` 的命运截然不同。

被 overlay 接管的只有两个符号（实测 `__code__.co_filename`）：
`_results_to_dataframe` → `report.py`，`_rankable_results` → `report_determinism.py`。

#### 生产入口（跨模块引用）8 个

| 符号 | 行数 | 引用者 |
|---|---|---|
| `refresh_candidate_exports` | 209 | 9 个模块 |
| `_results_to_dataframe` | 326 | `report_selection` |
| `export_all` | 101 | `main_core` / `scan_service` / `publication_guard_v65` |
| `print_terminal_report` | 42 | `main_core` |
| `_atomic_write_csv` / `_atomic_write_parquet` | 8 / 10 | 8 个模块 |
| `print_scan_summary` | 10 | `main_core` |
| `_rankable_results` | 23 | `report_determinism` / `report_selection` |

#### 结论与下一步

24 个定义全部可达 → 这里的补测试**不会打在死代码上**，价值高于 `scanner_core`。
缺口在**深度**而非存活：`validate_decision_integrity`（527-1439，**912 行，占模块 44%**）
在生产路径上，`_decision_projection` 调它，但**零直接测试**。
另有 19 个符号既无生产直接引用名、也无测试引用。

闸门：`tests/test_report_core_provenance.py`（5 项）——三层链塌缩、每层回调下一层、
原版仍是最内层调用、24 个定义全部可达、两个被接管符号的归属。

**待你定**：要不要给 `validate_decision_integrity` 的 912 行铺行为测试。
它是纯 DataFrame 进出、无 IO、无时间依赖，可测性远好于 `scan_single_from_df`
（381 行就要 1–2 天），但 912 行仍是相当大的一块。

#### 912 行的审查结论：没有死代码，但风险等级被低估了（2026-09-16）

先审后测，结果是**阴性**（这本身值得记）：

* **0 个不可达块**。函数是一串 `if X_columns.issubset(frame.columns)` 守卫，
  用真实 `output/runs/*/AllResults.csv` 的 **447 列表头**逐一比对，
  8 个 `_columns` 集合 + 33 处列守卫**全部可达**。
* **失败是 `raise ValueError`，不是告警**（1435-1436）。误报会**中止发布**，
  而不是留一行日志——所以这 912 行的风险等级高于同尺寸的普通校验代码。
* **对外无副作用**：543 行 `frame = frame.loc[successful].copy()` 是局部重绑定
  加拷贝，后续只读；调用方的帧不会被改。
* **21 个 `violations.append` 出口**。
* 前置的 `validate_ranking_input`（`result_contract.py:279`，535 行调用）
  同样是「列存在才检查」，所以最小夹具不会被它误伤。

**怎么在 912 行上测而不用 447 列夹具**：每个校验块都有列守卫，
只喂某一块所需的列，其余块就**确定性跳过**。于是夹具是 6 列而非 447 列，
912 行变成可独立寻址的一组检查。

闸门：`tests/test_report_core_decision_integrity.py`（14 项）——
契约 4 项（空帧、全失败行、逐行过滤、无副作用）+ 模型权重块 10 项
（签名的 5 种非法形态、终分与权重不符、**v48 容差 0.006 对默认 0.02 的双向边界**、
非有限分量不算误报、消息里点名违规行）。

**未覆盖**：其余约 20 个违规出口。这是开始，不是完成。

## 10. 评分与回测逻辑：我的重构建议（2026-09-16 意见稿）

这一节是**意见**，不是已完成的工作。所有判断都附了实测数据，取向问题单列在 §10.6。

### 10.1 一句话结论

**不要重写，先把「谁在真正算分」变成可断言的事实。** 这个项目现在最值钱的东西
不是代码，是那套护栏——golden 等价夹具、装配清单、胜者锁。任何大改都会同时废掉
它们，而它们的价值正是「让你敢改」。正确顺序是**先锁 → 再收敛 → 再下沉**，一次一小块。

### 10.2 实测：评分链在生产里的真实形态

起子进程走生产入口，看 `score_core` 上 10 个核心函数最终解析到谁：

| 符号 | 生产解析到 | 留在 score_core |
|---|---|---|
| `_score_dimensions_available` | `score_acceleration_v79` | ✗ |
| `score_trend` | `score_acceleration_v79` | ✗ |
| `score_volume` | `score_scale_migration_v95` | ✗ |
| `score_accumulation` | `score_scale_migration_v95` | ✗ |
| `score_structure` | `score_scale_migration_v95` | ✗ |
| `classify_style` | `score_acceleration_v79` | ✗ |
| `entry_point` | `score_cache_guard_v80` | ✗ |
| `value_trap_risk` | `score_endpoint_acceleration_v79` | ✗ |
| `breakout_score` | `score_endpoint_acceleration_v79` | ✗ |
| `execution_quality_score` | `score_endpoint_acceleration_v79` | ✗ |

**10 个，0 个留在 `score_core`。** 静态扫描还数出 `score_core` 上共有 **72 个符号**
被 overlay 重绑定。

也就是说：`score_core.py`（1196 行 / 43.8 KB）在生产里**是一本名字簿，不是实现**。
读它无法得知任何生产行为。这不是某个模块的错，是这套 monkey-patch 装配方式的必然结果
——但后果必须被看见：改 `score_core.score_volume` 的人，改的是一个永远不会被调用的函数。

顺带（和 §9.5 同一类问题）：`score_acceleration_v77` 的 `install()` 只绑两条
（`_score_dimensions_available`、`score_volume`），两条在生产里分别被 v79 和 v95 盖掉；
它只有 `install()` 一条可达路径，没有 by-name 调用。**是干净的退役候选。**

### 10.3 头号问题：评分公式有两套独立实现，且零共享符号

* `score_core.py` —— 标量 / EXACT，30 个顶层函数；
* `institution_scanner/backtest_score_vectorized.py` —— 向量化 / FAST，19 个私有函数
  （`_trend` / `_volume` / `_accumulation` / `_volatility` / `_structure` /
  `_value_trap` / `_breakout` / `_entry_execution` …）。

两者**同名函数 0 个**。它们在算同一件事，但没有任何共享符号能把这件事表达出来。
唯一的护栏是 `test_backtest_score_vectorized_alignment`，在 6 个字段上锁
`rtol=0, atol=1e-10`——很硬，但只覆盖那 6 个字段。

代价有三层：
1. 每次改评分语义要改两处；
2. 改漏了只有那 6 个字段会报警，其他分量的漂移是静默的；
3. 最坏的是**新增分量**：如果哪天加了一个 `_momentum`，对齐测试根本不知道它的存在。

### 10.4 建议清单（按 价值 / 代价 排序）

| # | 建议 | 价值 | 代价 |
|---|---|---|---|
| **S1** | **给评分链装「胜者锁」**（照搬 §9.6 模式，把上表 10 行冻成断言） | **高**：把「谁在算分」从考古变成事实；谁改装载顺序立刻红 | 低：半天，有现成模板 |
| **S2** | **退役 `score_acceleration_v77`** | 中：少一层、10 KB、少一个误导源 | 低：两条补丁都死、只有 install 一条路 |
| **S3** | **把 FAST/EXACT 对齐从 6 字段扩到全分量，并加显式映射表**（`_trend ↔ score_trend` …） | **高**：新增分量漏对齐会立刻红，而不是静默 | 中：要建差分台架（就是没做完的 #5） |
| **S4** | 回测的「样本集 / 权重」做成显式数据流，而不是逐层改写 | 高：现在 6 层包装各自可能改样本 | 高：要动生产路径 |
| **S5** | 评分公式抽成单一声明式描述（分量 → 权重 → 饱和/裁剪），两条路径按描述求值 | 最高：根治双实现 | **很高**：数周，且会作废全部 golden 基线 |
| **S6** | 版本常量链与 `DecisionPolicySignature` 解耦（见 §10.6） | 中：让签名重新能「识别策略变更」 | 中：一次对外变更 |
| **S7** | 明确校准的输入快照边界（哪层数据允许进校准） | 中：现在校准与回测有环 | 中 |

**如果只做一件事，做 S1。** 它不改变任何行为，但把你从「改代码前要先考古」里解放出来。

### 10.5 我不建议做的事

* **大爆炸重写。** 会同时废掉 golden、装配清单、胜者锁——你现在唯一敢改代码的理由。
* **把所有 overlay 一次性内联回源码。** 看着最干净，实际是一次性改变装配清单、
  签名、所有 golden 基线，且不可逆。要么不做，要么按 §9 的节奏一块一块来。
* **为了美观统一双实现的函数命名。** 命名不一致是**症状**不是病因。统一命名反而会
  掩盖「它们在算同一件事」这个本该被显式声明的事实——应该建**映射表**（S3），不是改名。
* **动阈值默认值。** 那是你的取向，不是缺陷。

### 10.6 需要你拍板的取向问题

1. **`DecisionPolicySignature` 该不该继续把版本串哈希进去？**

   > **2026-09-16 实测更正**：上面原本写「185 个版本常量、最长 296 字符、全都进了负载」，
   > 这条是错的。实测：`config` 上 `*_VERSION` 共 **37** 个，其中只有 **9** 个进策略负载
   > （负载共 162 个名字），长度 37~52 字符，是 `…-v80-tradeability-sample-array-v1`
   > 这种**单段**标签，不是多段历史链。真正长的账本 `PIPELINE_VERSION`（2022 字符）、
   > `OUTPUT_CONTRACT_VERSION`（1133）、`DECISION_INTEGRITY_VERSION`（609）**不在负载里**，
   > 动不了签名；`BACKTEST_PROVENANCE_VERSION`（926）本来就被 `_POLICY_EXCLUDED_NAMES` 排除。
   >
   > 所以「任何改动都会改变签名」仍成立，但元凶不是超长版本链，而是**这 9 个标签每次
   > 打补丁都会变**。已经按 S6 处理（见 §10.7）：签名保持包罗万象，另加一个只看参数的
   > `DecisionPolicyParameterDigest`。是否进一步收窄签名本身，仍要你拍板。
2. **FAST 与 EXACT 的近似边界怎么定？**
   FAST 用 252 窗口 + 40 天冷却 + 5 天候选间隔，EXACT 用 504 窗口。这是有意的设计，
   我不动。但「允许在哪些列不同、容差多少」得你来定——这正是没做完的 #5 缺的那一半。
3. **`score_core` 以后算什么？**
   它是现在唯一人类可读的公式表述（向量化那份是 numpy 向量式，很难读）。我倾向
   **保留并强化它为「规范 / 参考实现」**，让向量化那份对它负责。但如果你打算以后
   直接维护向量化版本，那 `score_core` 就该明确降级为文档——两条路都行，得选一条。

### 10.7 执行状态（2026-09-16）

| # | 建议 | 状态 | commit |
|---|---|---|---|
| **S1** | 评分链胜者锁（10 行冻结成断言） | ✅ | `25a433a` |
| **S2** | 退役 `score_acceleration_v77` | ✅ | 本轮 |
| **S3** | FAST/EXACT 对齐扩到全分量 + 显式映射表 | ✅ | `6b22872` |
| **S6** | 版本串与 `DecisionPolicySignature` 解耦 | ✅ | `1ef2f9e` |
| S4 | 回测「样本集 / 权重」做成显式数据流 | **分叉已实测并锁定**（权威待你定，见 §10.12） | 本轮 |
| S5 | 评分公式抽成单一声明式描述 | 待做 | — |
| S7 | 明确校准的输入快照边界 | **边界已实测并锁定**（取向问题待你定，见 §10.8） | 本轮 |

**S2 的做法（沿用项目既有的退役惯例，不删文件）**：`score_runtime_v97` 的先例是
「停止装载 + 登记到 `institution_scanner.runtime_inventory.RETIRED_FROM_PRODUCTION_PATH`，
文件保留」。所以这次只摘掉 `analytics_acceleration_v77.install()` 里的
`_score_acceleration.install()` 及其 import，把模块登记为退役。

**S2 的验证（改动前后各探测一次，逐一比对）**：

* 4 个核心入口（scanner / main / daily_pipeline / scan_service）下，
  `score_core` 与 `score` 上 4 个受影响的符号**胜者全部不变**
  （`_score_dimensions_available` → v79，`score_volume` → v95），且
  `score_acceleration_v77` 不再出现在 `sys.modules` 里；
* 装配清单 `install()` 调用数 124→123（daily_pipeline 126→125，scan_service 91→90，
  scanner 86→85），消失的那一步正是 `score_acceleration_v77.py:124:install`；
* 4 份清单的 `final` **0 处值变化**——只有 `score_acceleration_v77._INSTALLED`
  和 `score._score_dimensions_available` 两个键消失（后者因为再没有一步去改它，
  而 `score` 与 `score_core` 是同一个模块对象，值仍是 v79 的）。

### 10.8 S7：校准的输入边界（2026-09-16 实测）

**进入校准的入口一共四个，只有三个是「拟合」，一个只是「推理」：**

| 入口 | 位置 | 允许拟合的数据 |
|---|---|---|
| `build_global_calibration` | `analytics_core:3199` | train + validation（point-in-time 已验证） |
| `calibrate_component_weights` | `analytics_core:3206` | 内部只用 **validation**（`model_calibration:528-561`） |
| `walk_forward_stats` | `analytics_core:3202` | 每折 train（`entry_date` 与 `exit60_date` 都在折边界之前，`model_calibration:622-625`） |
| `calibration_details_for_frame` | `analytics_core:2504` | **不拟合**——把已拟合好的校准套到线上排名帧上 |

**「环」在哪（两条，都不是猜测，是逐行追出来的）**

```
run_historical_backtest → 样本帧（score = final_score）
   → calibrate_component_weights(validation) → ScoreCalibration.json（analytics_core:3208）
   → score_core._model_component_weights()（score_core:143）
   → final_score = setup*w1 + trigger*w2 + execution*w3（score_core:1143-1151）
   → 回到下一轮
```

1. **选择回路**：上一轮的权重决定哪些 setup 能变成信号，也就决定了下一轮的**样本总体**；
2. **分桶回路**：样本帧里的 `score` 就是 `final_score`（`analytics_core:1357` 取 `evaluation_map`，
   而该值来自 `analytics_core:1078` 的 `final_score`），而校准用 `score` 切 `score_bucket`
   （`model_calibration:163-169`），`score_bucket` 又是 `build_global_calibration` 的层级键之一
   （`model_calibration:214-215`）。**上一轮的权重决定了这一轮的层级划分。**

**边界的现状（as-built，已被本轮测试冻结）**

* `build_global_calibration` **自己只丢 `purged`**（`model_calibration:207-208`），
  `test` 完全靠调用方排除。它的 docstring 写「without using the held-out test set」，
  读起来像函数自己的承诺，**实际是调用方契约**——生产在 `analytics_core:3196-3198` 守住了，
  但任何研究侧调用都能把 test 喂进去且不会有任何报错。
* `calibrate_component_weights` 干净：只用 validation 选权重，test 仅用于报告 `test_ic`
  （`model_calibration:565-582`）。
* `walk_forward_stats` 也干净：折内 train 要求 `exit60_date` 早于折起点，注释里明确写了
  这是为了堵「entry-date-only 切片把 12 月底的结果漏进下一年」（`model_calibration:619-625`）。

**闸门**：`tests/test_calibration_input_boundary.py`（5 项）。反向验证 4 条全咬，且各自只红
对应的那一条：① 权重搜索改用 validation+test → 红 2 项；② 函数内部也开始丢 test → 红；
③ 不再丢 purged → 红；④ 生产调用点把 test 放进拟合帧 → 红。

**我不会替你定的三件事（都需要取向判断，不是缺陷）**

1. `build_global_calibration` 的 test 过滤要不要**下沉进函数**？下沉能堵住研究侧误用，
   但它同时是通用 research API，下沉等于改它的语义（和 §9 里 v94 的
   `calibration_details_for_frame` 是同一类取舍）。
2. **分桶回路要不要切断**？可选：用与权重无关的分量（如 `setup_score`）分桶，
   或用默认权重把历史分重算一遍再分桶。切了更干净，但会作废现有 golden 基线。
3. `calibrate_component_weights` 目前收的是**含 test 的** `verified_model_frame`，
   内部再筛 validation。要不要把筛选提到调用侧，让函数签名自己说清边界？

### 10.9 S4 侦察（2026-09-16）：样本集 / 权重的真实装配

S4 的本体是「把样本集 / 权重做成显式数据流」——那要动生产路径，**本轮没做**。
做的是动工前必须先有的那一步：**判定现在到底是谁在改样本**。

**按定义文件（`__code__.co_filename`）解析的赢家，4 个核心入口完全一致：**

| 符号 | 实际定义在 |
|---|---|
| `analytics_core.run_historical_backtest` | `backtest_production_activation_v93.py` |
| `analytics_core._backtest_one_ticker` | `backtest_alignment.py` |
| `analytics_core._verified_point_in_time_frame` | `point_in_time_backtest.py`（静止态） |
| `analytics_core._relabel_sample_splits` | `analytics_core.py` ← **唯一还活着的原生实现** |
| `analytics_core._date_balanced_weights` | `backtest_math_integrity_v94.py` |
| `analytics_core.calibration_details_for_frame` | `backtest_math_integrity_v94.py` |
| `model_calibration._prepare_samples` | `backtest_math_integrity_v94.py` |
| `backtest_sample_acceleration_v80._drawdown_percent` | `backtest_sample_guard_v80.py` |
| `score_core._model_component_weights` | `score_weight_cache_v79.py` |

**两个陷阱，都差点让我下错结论**

1. **`__module__` 在这条链上会说谎。** `backtest_production_activation_v93:272-274` 把
   `run_historical_backtest.__module__` 设成**被替换前那个函数**的模块，所以它自称来自
   `point_in_time_backtest`，实际定义在 v93 里。只有 `__code__.co_filename` 说真话。
   （`test_scoring_chain_winners` 用 `__module__` 没问题，因为评分那几个 overlay 懒得伪造。）
2. **静止态探测看不见「作用域内补丁」。** v93 **不是**永久替换 `_verified_point_in_time_frame`，
   而是**在一次 `run_historical_backtest` 调用期间**换成 `_production_point_in_time_frame`，
   跑完还原（v93:253-265）；`conditional_fill_v96` 同样在这段里 install / uninstall（258-264）。
   所以：装配清单看不到它们，任何 import 后的探测也看不到它们。
   我第一次看到「v93 的补丁不在场」差点判它死代码——**错了，它是调用期的。**

**结论**：S4 说的「6 层」实测是 **5 层 overlay + 1 个原生函数**，而且其中两层只在调用期存在。
这本身就是「该做成显式数据流」的证据：一条链上有两种完全不同的生效方式（永久重绑定 vs 调用期
作用域替换），没有任何一处把它们写在一起。

**闸门**：`tests/test_backtest_sample_pipeline_winners.py`（11 项）。反向验证 4 条全咬且各自只红
对应的一条：v80 不再接管 `_drawdown_percent` / v93 不再还原 PIT 过滤 / v93 不再卸载条件成交 /
v79 不再接管 `_model_component_weights`。作用域内补丁那部分只能用 AST 锁（跑一次回测代价太大）。

**S4 本体待你定的**：哪一层是权威？尤其是 `_verified_point_in_time_frame`，同一件事现在有两个实现
（`point_in_time_backtest.pit_verified` 静止态 + v93 调用期版本），它们什么情况下会给出不同的
样本集，我没验——那需要先定「哪个是权威」才能判断另一个是不是冗余。

### 10.10 S5 前置（2026-09-16）：补齐映射表的标量侧 + 两处死代码

S5（评分公式抽成单一声明式描述）本体没做——数周、且会作废全部 golden 基线。
这里只清掉了动工前该清的两件事。

**(1) S3 建的映射表是单向的，已补上另一半。**

`tests/test_backtest_score_vectorized_alignment.py` 原本只锁**向量化侧**：
`test_every_component_key_is_declared` 保证 FAST 不会多出一个未声明的分量键，
`test_every_vectorised_function_is_declared` 保证那个模块里不会冒出未声明的私有函数。
两个都很硬，但都是**从 FAST 往回看**。

反过来是空的：往 `score_core.score_ticker` 里接一个新分量，只要它没恰好改动测试帧上的
`final_score`，就不会有任何一条断言变红。已补：

* `SCALAR_ONLY_CALLS` —— `classify_style` / `entry_point` / `tradable_price_decimals`，
  三个被 `score_ticker` 调用但没有向量化对应物的函数，**每个都要写理由**（照搬原有的
  `VECTOR_ONLY_KEYS` 规矩：没理由就不许豁免）。其中 `entry_point` 的理由值得单说：
  它是**共用**而非**重复**——FAST 路径也调 `score_core.entry_point`
  （`backtest_fastscore_v80:608`、`backtest_fastpath_v78:356`、`conditional_fill_v96:75`），
  所以没有第二个实现需要对齐；这条豁免真正防的是「哪天有人写了个向量化的 entry_point 却不说」。
* `test_every_scalar_call_in_score_ticker_is_declared` —— 双向都查：未声明的调用要报，
  声明了却不再被调用的也要报（映射表陈旧）。
  反向验证两条都咬：接一个未声明的 `cyclical_turn_factor` → 红；把 `score_trend` 的调用
  摘掉 → 红（连带的 2 条 parity 测试也红，正确）。

**(2) 两处死代码（都不动，需你确认——它们被 golden 夹具钉住了）**

| 符号 | 位置 | 情况 |
|---|---|---|
| `cyclical_turn_factor` | `score_core:415`（约 120 行） | **全仓库零调用点**。生产没有，`score_core` 内部也没有 |
| `value_trap_risk_score` | `score_core:906` | 恒等包装器：`return value_trap_risk(df)`，生产零引用 |

两者唯一的活着的引用是 `tests/golden_score_core.py`（把它们列进捕获用例）和
`tests/reverse_validate_score_core.py`。**删它们要重捕获 golden**，所以不是我该独自决定的：
低优先的死代码，但改动面涉及基线，交给你定。

（对照：`smart_money_stage` 看着同类，其实活着——`scanner_core:694` 在调。）

> **2026-09-16 处置**：决定**不删**，改为「显式声明 + 闸门」，见 §10.13 决定 3。
> 闸门在 `tests/test_score_core_dead_symbols.py`。

### 10.11 两个新闸门：walk-forward 折内口径 + PIT 原因溯源（2026-09-16）

`test_calibration_input_boundary.py` 锁住了「哪层数据进得了拟合」，但它对
`walk_forward_stats` 只写了「每折 train」。本节补上这一层，并顺带定位了
`heldout_unverified_reason_counts` 恒空的原因。

**(1) `tests/test_walk_forward_split_boundary.py`（4 项）**

**折内 train 是按日期切的，不是按 `split` 标签切的**（`model_calibration:621-625`）：

```python
train = sample.loc[sample["entry_date"].lt(start) & sample["exit60_date"].lt(start)]
```

`exit60_date` 那半个条件是特意加的（注释写明：只按 entry 切会把 12 月底的结果漏进下一年）。
但它有个没人写下来的后果：**一行 `split == "test"` 的样本，只要 entry 与 exit60 都落在折边界之前，
就属于该折的训练集**。生产的 test split 从 2024-06-28 起，所以 2025 与 2026 折确实在用 test 标签的行拟合。

闸门的做法：构造 130 行落在 2022 折训练窗内、而全帧只有 100 行带 `split == "train"` 的夹具——
`train_samples == 130` 只有在标签被忽略时才成立。另配一条反向断言（把那 30 行改标成 train，
结果必须完全不变）和一条非空断言（该折的 test 侧确实是 40 行）。

**反向验证**：给 train 切片加上 `& split.eq("train")` → 红 3 项（130 掉到 100），
第 4 项（AST 锁调用点）正确地保持绿；把生产调用点换成 `calibration_frame` → 只红第 4 项。

**(2) `tests/test_pit_reason_provenance.py`（4 项）**

**现象**：`heldout_unverified_test_samples = 51142`，而 `heldout_unverified_reason_counts = {}`。
五万行被判未验证，却说不出原因——分不清是「按 0.25 权重降级保留」还是「整行丢弃」。

**根因（不是计数器的错）**：原因计数靠一个**临时钩子**采集——`point_in_time_backtest` 包装
`core._verified_point_in_time_frame`，在构建已验证帧时调 `_split_counts`
（`point_in_time_backtest.py:396-399`）。而 v93 不是包装它，是**替换**它
（`backtest_production_activation_v93:252-265`），且 `_production_point_in_time_frame`
**从不回调**被替换掉的那个实现。谁后装谁拿名字，所以只要 v93 在场，PIT 钩子就跑不到，
`_PIT_SPLIT_COUNTS` 恒空。

于是 `pit_counts.normalize_runtime_counts` 只能从持久的 `rolling_oos` 补回**计数**
（raw 51142 就是这么来的），**原因无从补起**——它不是填 `{}`，是干脆没有这个键。
`pit_counts.py` 的模块 docstring 已经承认过这一点（"acceleration/wrapper composition can
make that transient hook unavailable"），只是没有闸门把它钉住。

闸门的四条断言：
1. 列在的时候 `_split_counts` **确实**会归因（非空断言：证明问题在装配不在计数器）；
2. 修复层能补计数、**补不了原因**（`unverified_reasons` 键缺失，且不允许伪造）；
3. 生产的帧构建器**没有**通过任何被替换的名字回调（模块级与调用期两个名字都查）；
4. 替换是调用期的、且在 `finally` 里还原（这就是静态探测包括装配清单都看不见它的原因）。

**反向验证四条各自只红对应那一条**：计数器不再归因 → 红 1；修复层伪造原因 → 红 2；
生产实现加一行对原钩子的调用 → 红 3；删掉 `finally` 里的还原 → 红 4。

**为什么没有直接修**：修它要动 v93（生产 overlay），会新增一个此前恒空的字段的值。
按本项目节奏「先锁 → 再改」，这一轮只落闸门。要不要把原因统计接进 v93 的
`_record_run_state`（它其实已经算了 verified / provisional / known_excluded 三类计数，
只是没按 split 拆、也没记 reason），需要你定。

### 10.12 S4 前置实验：两个 PIT 帧构建器的分叉（2026-09-16 实测）

§10.9 留下一个没验的问题：`_verified_point_in_time_frame` 现在有两个实现
（原生 `analytics_core:320` 与 v93 调用期版本 `v93:78`），它们会不会给出不同的样本集。

**会，而且不是程度之差，是方向相反。** 六个输入里只有两个一致：

| 输入 | 原生 | v93 |
|---|---|---|
| status = `ELIGIBLE` | 保留 | 保留，权重 1.0 |
| UNAVAILABLE + 白名单内的缺失原因 | **丢弃** | 保留，权重 0.25 |
| UNAVAILABLE + 其他原因 | 丢弃 | 丢弃 |
| **status 列缺失** | **返回空帧** | **全保留，权重 0.25** |
| status 与 reason 均为空串 | 丢弃 | 保留，权重 0.25 |
| status = `PROVISIONAL` + 白名单原因 | 丢弃 | 保留，权重 0.25 |

第四行是最危险的一列：原生实现在没有该列时判定「没有证据」→ **整帧清空**；
v93 判定「每行都是缺失快照」→ **全部保留**。同一个输入，相反的结论。

另外记一笔 v93 的副作用：它保留下来的行不是原样保留——status 被改写成
`PROVISIONAL`，空白 reason 被填成 `no_point_in_time_snapshot`（而它本身就在白名单里），
所以**保留下来的行在第二次经过时会自动重新获得保留资格**。

**为什么这条现在要紧**：2026-09-15 那次运行里，全部 15.6 万行都是「unavailable」
（快照只覆盖 11 年窗口的最后 18 个交易日）。也就是说：

* 按**原生**读法 → 样本集为 **0 行**，校准彻底关闭；
* 按 **v93** 读法 → 样本集为 **156,547 行**，按 0.25 折价降级运行。

生产当前跑的是后者。「哪层是权威」因此不是抽象问题——它决定校准是**关**还是**降级**。

**闸门**：`tests/test_pit_frame_variant_divergence.py`（9 项）。反向验证两条各自咬对位置：
原生放宽到也保留 `PROVISIONAL` → 只红 `already_provisional` 那一格；
把 `""` 从 v93 的 reason 白名单里摘掉 → 红 4 项（两个空格类输入 + 空帧那条 + 改写权重那条）。

**仍然没定**：哪个是权威。但现在这个决定是有依据的——选原生等于选择「快照覆盖不足时校准全关」，
选 v93 等于选择「带着 0.25 折价继续跑」。两者都能自洽，代价不同，由你定。

### 10.13 三个待定决定的处置（2026-09-16）

#### 决定 1：PIT 帧构建器的权威 = v93（维持现状，不改代码）

**选 v93，不切回原生实现。** 三条理由：

1. **切过去拿不到更干净的数据。** 在快照覆盖补齐之前，"无偏样本"根本不存在——
   两种读法都拿不到它。切换只会让 15.6 万行归零、heldout 指标随之消失，
   换来的不是更可信的校准，而是没有校准。
2. **v93 的降级是已披露的。** 有 `universe_type` 状态串、有警告文案、
   有 0.25 折价且 `universe_evidence_weight` 落盘可查。切回原生只是把
   「披露的降级」换成「静默的关闭」——后者更危险，因为它看起来像"没有数据"。
3. **0.25 是否最优是另一个问题。** 它影响权重大小，不影响方向；
   而方向（已知不合格的排除、未知证据的打折保留）在 v93 里是对的。

**残余风险（接受，记账）**：0.25 是人为折扣，它稀释而不消除幸存者偏差。
等快照累积到能产生可验证样本时，这个数需要重新标定。

#### 决定 2：评分权重自适应 = 降级 + 记录（B），不重建历史成分池（A）

**不选 A。** 理由是 A 引入的是**新的、未经验证的偏差**：用上市日期 + ST/停牌状态
近似重建「当时可交易池」，并不等于真实的当时可交易池；而它的产出要数年才有意义。
用一个新的未知偏差去换一个已知的缺失，不划算。

**B 的实际工作量很小**，因为告警已经存在（PIT warm-up warning + status 字段）。
缺的只是把这条链明确标记为不激活，并**停止为它加护栏**。

**重开此决定的条件（写明，避免无限期挂着）**：
引入可信的历史成分数据源，或快照累积到能覆盖 validation 段（按当前比例约需数年）。

#### 决定 3：两处死代码 = 不删，改为显式声明 + 闸门

**这里反转了 §10.10 的原始倾向，依据是当时没查到的三点：**

1. **删它会连带删掉 reverse_validate 里两条已验证会红的用例**
   （`value_trap_risk_score：结果减半`、`cyclical_turn_factor：数据不足兜底分 50 → 51`），
   那是「golden 夹具确实会咬」的 15 条实证里的 2 条。
   用两条实证护栏换 120 行整洁——在「护栏就是这个项目的价值所在」（§10.1）的前提下是亏的。
2. **`score_core` 的定位未定**（§10.6 Q3），此时删组件是抢跑。
3. **`score_core` 是字节预算受控的 canonical 模块**，连加一行「未接入评分链」的注释
   都要提额；把声明放进测试文件则零成本，且同样消解「读者误以为它参与评分」的问题。

**闸门**：`tests/test_score_core_dead_symbols.py`（4 项）。声明即契约——
谁把它们接进 `score_ticker` 或任何 shipped 模块，测试立刻红并要求更新声明。
反向验证：接进 `score_ticker` → 红 2 项；删掉符号 → 红 1 项（声明失效）。

**真要删时的代价已降到最低**：改这一处声明 + 重捕获 golden，一步到位。

### 10.14 阶段 1 侦察：`scanner_core` 的存活图（2026-09-16）

`scanner_core.py` 是 1,784 行 / 21 个顶层函数 / **零直接行为测试**，全仓库最大的覆盖真空。
但动手补测试之前必须先做 `score_core` 那一课——**先分清哪些符号生产真的在跑**，
否则一半的测试会打在死代码上。

#### 存活图

| 区 | 符号 | 行数 | 生产归属 |
|---|---|---|---|
| **汇合点** | `scan_single_from_df` | 381 | `scanner_core`。两条生产路径都到它 |
| 活跃链 | `run_parallel_indicator_scan` / `_analyse_one_ticker` / `_analyse_one_ticker_from_df` | 78 | `scanner_core`；`main_core:188` 与 `v59:564` 都在调 |
| 活跃（薄） | `_emit_progress` | 13 | v59 在用 |
| 被接管 | `save_checkpoint` / `load_checkpoint` / `clear_checkpoint` / `_checkpoint_trade_date` | 45 | `scanner_resume_v59`（三个入口的装配清单一致） |
| 被包装、本体不走 | `run_scan` | **828** | `scan_resume_boundary` 闭包 → v59。`scanner_core` 那份只作为 `_LEGACY_RUN_SCAN` 被调一次，且兜在「checkpoint 不是 `CheckpointState`」的兼容分支里（`v59:421-422`，注释写明是给老测试用的） |
| 不可达 | `scan_single` / `_quality_hard_data_complete_from_row` / `_load_previous_tickers` | 66 | 调用点全部落在 `run_scan` 体内 |

**合计：活跃约 470 行（26%），死亡约 950 行（53%）**，其余是数据类与小工具。
不区分地"补 scanner_core 的行为测试"，会有一半打在死代码上——和 `score_core` 同一个坑。

#### 行为测试优先级（只针对活跃区）

| 级 | 目标 | 建议测什么 |
|---|---|---|
| **P0** | `scan_single_from_df`（381 行） | 同一帧两次调用结果一致（纯函数性）；`ScoreBreakdown` 分量落在 [0,100]、`total` 与分量的关系；`error` / 兜底分支；数据不足、全 NaN、ETF 与股票两条路 |
| P1 | `_analyse_one_ticker_from_df` | 缓存缺失 → `error` 分支；enriched 直通分支 |
| P2 | `run_parallel_indicator_scan` | 结果按 `score.total` 降序（1782）；单票异常不影响整批（1776-1785） |
| P3 | `_emit_progress` | 回调健壮性（None 回调、抛异常的回调） |

**不做**：`run_scan` 的 828 行、checkpoint 家族、三个不可达 helper——
除非先决定它们是要下沉还是要删。那是另一件事（阶段 2 的候选）。

#### 闸门

`tests/test_scanner_core_provenance.py`（4 项），锁住上面这张存活图。
反向验证三条各自只红对应那一条：

* v59 不再委托 `_analyse_one_ticker_from_df` → 红「汇合点」那条；
* 把 `scan_single` 接进活跃路径 → 红「不可达」那条；
* 让装配清单声称 `save_checkpoint` 未被接管 → 红「被接管」那条。

#### 待你定

P0 那 381 行要不要真的铺开测。代价是 1–2 天，且要新增一个能构造行情帧的夹具；
收益是主扫描路径第一次有直接的行为覆盖。也可以先只做 P1/P2 的便宜部分（约半天）。

#### 阶段 1 已完成（2026-09-16 晚，commit 610376a / 086b570 / ef8c480）

P0–P3 全部落地，**全量 413 项通过**（354 基线 + 59 新增），ruff 全过。
实际界定与上面的优先级表不完全一致，对应关系如下：

| 上面写的级 | 落在哪个文件 | 项 |
|---|---|---|
| P0 `scan_single_from_df` | `test_scanner_core_behaviour.py` | 10 |
| P1 `_analyse_one_ticker_from_df` | `test_scanner_core_active_chain.py`（另含 `_analyse_one_ticker`、`run_parallel_indicator_scan`、`_emit_progress`、`_raise_if_cancelled`） | 17 |
| P2 `run_parallel_indicator_scan` | 同上（降序与异常隔离另见 behaviour） | — |
| P3 `_emit_progress` | 同上 | — |

另做了两件上面标为「不做」的，理由见后：
`test_scanner_core_checkpoint.py`（20 项，`checkpoint` 家族的文件契约）与
`test_scanner_core_result_contract.py`（12 项，`ScanResult` 字段一致性 + 两个运行时事实）。

**修掉一个真缺陷**（`086b570`）：`load_checkpoint` 的 `except` 覆盖
`(OSError, UnicodeDecodeError, JSONDecodeError, TypeError, ValueError)`，漏了
`AttributeError`。checkpoint 是「合法 JSON 且顶层非对象」（`[1,2,3]`、`null`）时
`data.get("active")` 抛出并冲出 `try`，把「读不懂就重扫一遍」变成「整个扫描中止」。
修法是在取值前 `if not isinstance(data, dict): return set()`，而不是把
`AttributeError` 塞进 `except`——后者会连真实属性访问失误一起吞掉。

**发现但未改的口径分叉**：`_analyse_one_ticker` 的缓存缺失分支原样返回传入 ticker，
命中路径经 `scan_single_from_df`（454-455）会归一化并回写。不是查盘 bug
（`_load_cache → _cache_path → _safe_cache_stem` 内部已归一化，两分支找到同一文件），
差异只在发布结果的字符串，且仅在调用方传入不规范 ticker 时可见。测试按现状锁死。

**更正一处漏记**：活跃区不止 `_emit_progress`，`_raise_if_cancelled` 同样活跃——
`scanner_resume_v59` 在 388 / 476 / 583 / 655 / 675 五处调它。

**一个差点误判的探测**：`scanner` 入口的装配清单里 0 条 `scanner_core` 条目，
一度以为推翻了「`run_scan` 不在生产路径」。起子进程探完才明白 `scanner.py` 只有 24 行，
是别名门面（`sys.modules[__name__] = _core`），不是生产入口。
教训：manifest 里某入口 0 条目，先确认它是不是入口，再怀疑结论。

### 10.15 阶段 2 侦察：950 行死代码能不能删（2026-09-16）

§10.14 留下的问题是：`run_scan` 的 828 行、`checkpoint` 家族、三个不可达 helper，
合计约 950 行，是下沉还是删。侦察结论是**建议先不删，改为装闸门**，理由如下。

#### 三条硬约束

**1. 三个名字是公共 API，删名会直接炸。**
`main_core:57` 是 `from scanner import clear_checkpoint, run_parallel_indicator_scan, run_scan`，
`main_core:130` 把 `run_scan` 传给 `execute_scan`；`scan_service.py:100` 做的是
`run_scan_fn is _core.run_scan`。删掉名字是 `ImportError` / `NameError`，不是静默降级。

**2. `canonical_execution` 只比身份，不比归属——这是本轮最重要的发现。**

`scan_service.py:100` 用一次身份比较决定是否执行「canonical」的全部附加动作：
强制 `enrichment` 契约与 cache-first 市场契约（104-136）、把 checkpoint 清除推迟到
发布之后（141-142）、成功后清 checkpoint + 记录全市场快照 + 刷新审计 + 发布报告
（157-167）。为假时**这些全部静默跳过**：扫描照常返回结果，但不发报告、不记快照、
无任何报错，输出上也看不出差别。

而 `run_scan_fn` 的默认值是**导入时求值**的 Python 默认参数，比较的是两个不同时刻
捕获的对象，是否相等取决于 import 顺序。真正的隐患是：**两边可以同时是 legacy 的
828 行本体**——此时 `is` 仍然成立、`canonical_execution` 仍为真，但那些 canonical
附加动作跑在一个没有续跑、没有 v59 checkpointing 的扫描之上。
只断言身份抓不到这个，必须同时断言实现归属。

实测（`test_scan_service_canonical_execution.py`，4 项，子进程探测）：
`main_core.run_scan`、`scanner_core.run_scan`、`scan_service.execute_scan` 的默认
`run_scan_fn` 三者同一对象，且实现归属均为 `scan_resume_boundary.py`——即当前装对了。
闸门用的是 `__code__.co_filename` 而非 `__module__`，后者可被 overlay 伪造。

**3. legacy 分支当前无触发者，但删掉本体会把「走 legacy」变成「静默无结果」。**
`v59:421-422` 仅当 checkpoint 是 plain set（非 `CheckpointState`）时才调
`_LEGACY_RUN_SCAN`，即只有 stub 了 `load_checkpoint` 的老集成会踩到。
静态扫描 `tests/` 确认当前无任何注入（`test_no_test_injects_a_plain_set_into_the_legacy_branch`）。
把 828 行换成 shim 后，这类调用方不会报错，而是拿到空结果——**比报错更难查**。

#### 三个选项与代价

| 选项 | 做法 | 代价 |
|---|---|---|
| **A（推荐）** | 保持现状，已有 4 项闸门守着身份与归属 | 950 行死代码留在文件里 |
| B | 把 828 行下沉进 v59，`scanner_core` 留抛异常的 shim | 老集成从「走 legacy」变成「抛异常」，属行为变更；shim 仍需保留名字 |
| C | 直接删 | 违反约束 1，`main_core` / `scan_service` 直接 ImportError |

A 的收益已经被 §10.14 的四个测试文件拿到：活跃区有覆盖了，死区有 provenance 与
canonical 闸门锁着分类。删代码只省行数，不减风险。

#### 待你定

是否接受 A；若选 B，需要你确认「老集成可以改为抛异常」这一行为变更。
