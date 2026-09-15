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
| **`experiment/five-factor-resonance-v90` 分支处置** | v90/v91 内容**已在 main**（`b2893cb` / `bf7f1ae`），分支仅剩 5 个独有提交，无功能风险 | 可随时删除，见 §6 |

---

## 6. 需要你补充的信息（两处，都会改变优先级）

1. **GitHub Pages 站点是公开可读，还是仅自己可见？**
   - 若**公开**：`web_report_v84/v85/v93`（约 128 KB、零行为测试）直接暴露在公网，**Phase 4 应提到 Phase 1 之后立即做**，D4/D5 升为 P0。
   - 若**仅自己可见**：维持当前排序，D4 为 P1。
2. **`experiment/five-factor-resonance-v90` 分支（`af66bf8`，5 个独有提交）如何处置？**
   - v90/v91 内容已在 main，分支无功能价值。建议直接删除（我会先做 `git log` 比对确认无独有代码）。需要你确认。

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
```
