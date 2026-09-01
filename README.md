# InstitutionScanner

A 股 / ETF 日频扫描器：使用 **TickFlow Free** 提供行情与标的池，使用
**BaoStock** 提供低频、可追溯公告日的财报数据。

## 数据架构

```text
TickFlow Free
├─ CN_Equity_A：A 股股票池
├─ CN_ETF：ETF 股票池
└─ 1d K 线：OHLCV / Amount
        ↓
    本地 Parquet
        ↓
指标 → 筛选 → 评分 → 买点 → 回测 → 排名 → GUI

BaoStock
└─ 季度利润 / 成长 / 偿债 / 现金流指标
        ↓
 fundamental_reports.csv（公告日点时明细）
        ↓
 fundamental_data.csv（扫描兼容快照）
        ↓
 QualityGate
```

行情层不使用东方财富、AkShare 行情、新浪或腾讯，也没有行情源自动回退或混源缓存。

## TickFlow Free

免费服务无需 API Key。本项目固定使用：

- `CN_Equity_A` 获取 A 股池
- `CN_ETF` 获取 ETF 池
- `klines.batch()` 批量获取日 K
- `adjust="forward"` 比例前复权，用于收益率、技术指标和历史回测

首次运行会建立约 10 年历史缓存。之后正常扫描只批量获取最近 90 根日 K 并与本地历史合并；若检测到除权导致前复权基准变化，只重建受影响标的的完整历史。

TickFlow Free 不提供实时行情，因此 GUI 的“当日收盘价”始终对应最新已完成的日 K 交易日，不会用盘中价格冒充收盘价。盘后如果 Free 服务仍在分批结算，程序会重试未更新标的；不同交易日混排会直接阻断。统一供应商延迟最多只允许 1 个交易日继续作为研究数据，且非最新完成交易日的数据不会保持 `READY/CAUTIOUS` 即时交易资格。

## BaoStock 财报

BaoStock 可匿名登录、无需 API Key，只负责低频财报，不参与 OHLCV、标的池或任何行情下载。程序按
`股票 × 年份 × 季度` 查询，并保存 `ReportPeriod` 与 `AnnouncementDate`：

- 同一天且股票池、目标报告期未变化：直接读取，不重复联网
- 新季度结束后：逐日检查是否出现新公告；未公告时保留上一份已知财报
- 法定披露窗口内标记为 `AWAITING_RELEASE`，过期仍无新报告才标记为 `STALE`
- 每只股票增量保存：中途断网后可从已有明细继续
- 刷新失败：保留已有缓存，行情扫描继续运行
- GUI 勾选“刷新财报数据”：强制刷新

财报汇总包含最新报告期、公告日、ROE、毛利率、净利润同比、资产负债率、
经营现金流/净利润及最近三份年报净利润。历史回放只使用目标日期之前已经公告的记录，
避免把后来发布的财报提前用于信号。BaoStock 不提供原“机构覆盖家数趋势”，因此该旧字段
只为读取历史结果保留，不再参与质量门槛、完整度或质量系数。超过法定披露期限仍未更新的
财报按数据未知处理，不会拿到完整财报质量系数。

BaoStock 使用原生 TCP 连接 `public-api.baostock.com:10030`，不是 HTTP 接口；防火墙需允许
该出站端口，`HTTP_PROXY` / `HTTPS_PROXY` 环境变量不会代理这条连接。

## 安装

Python 3.11+：

```bash
pip install -r requirements.txt
```

绘图组件不在扫描器与 GUI 的运行热路径中；需要绘图时再安装：

```bash
pip install -r requirements-optional.txt
```

## 运行 GUI

```bash
python gui_v85.py
```

Windows 也可以双击 `启动研究终端.bat`。v85 采用 1366×768 紧凑研究简报布局，
统一桌面端与发布页的信息层级：数据日期、研究榜、板块轮动、风险雷达、模型变化和
运行状态。扫描、排名、回测与结果字段仍复用稳定实现，不改变模型口径。

GUI 行情源固定显示为 `TickFlow Free`，财报来源为 `BaoStock（点时缓存）`。

## CLI

全市场扫描：

```bash
python main.py scan
```

仅股票 / ETF：

```bash
python main.py scan --stocks-only
python main.py scan --etfs-only
```

指定标的：

```bash
python main.py scan --tickers 600036.SH,510300.SH
```

强制重建行情缓存：

```bash
python main.py scan --force-download
```

强制刷新 BaoStock 财报：

```bash
python main.py scan --refresh-fundamentals
```

### 价值结构影子模型

`Auction Market Theory + Wyckoff/VSA + Market Structure + Trend/RS` 已作为
独立影子挑战模型接入。它遵循
`Market → RS → Trend → Value → Structure → Volume → Risk → Plan Lifecycle`，
只使用已收盘的日线 OHLCV 与免费基准行情，不推测 Footprint、Delta 或 L2。

指定标的运行影子评分和逐时点回测：

```bash
python -m institution_scanner.auction_structure_cli \
  --tickers 002961.SZ,601899.SH \
  --benchmark 沪深300
```

也可对当前 `AllResults.csv` 的全体标的运行：

```bash
python -m institution_scanner.auction_structure_cli --all-results
```

输出为 `AuctionStructureShadow.csv`、`AuctionStructureBacktestSamples.csv` 和
`AuctionStructureBacktestSummary.json`。模型只生成诊断分与独立影子排名，明确记录
`production_applied=false`；在完成样本外观察和人工晋级前，不会改写 `FinalScore`、
`RankingScore`、`TradeReady` 或候选榜。回测在计划确认后的下一可成交开盘入场，
最早 T+1 退出，并将跨训练/验证/测试边界的持仓样本标为 `purged`、排除出指标。
命令默认每只标的读取最近 900 根日K，以限制全市场影子研究的运行时间和内存；可用
`--max-bars` 调整，但不得少于 300。

## 输出

结果位于 `output/`，主要包括：

- `AllResults.csv` / `AllResults.parquet`：完整研究结果
- `Top200.parquet`：统一 RankingScore 的机器研究榜，视图标识为 `RANKED_RESEARCH`
- `Top50.csv` / `Top50Mixed.csv`：统一 RankingScore + 多样性约束的综合研究榜
- `Top50Stocks.csv` / `Top50ETF.csv`：股票、ETF 各自独立纯排名
- `Top50TradeReady.csv`：仅最终 `推荐`
- `Top50Opportunity.csv`：按 OpportunityScore 排序的非风险研究机会
- `Top50EntryCandidates.csv`：按买点优先级 → EntryScore → RankingScore 排序
- `Top50BreakoutCandidates.csv`：仅价格突破且量能、资金流同时确认的严格突破
- `Top50SustainedSignals.csv`：仍有效且非风险过滤的持续信号
- `Top50ValueTrapRisk.csv`：价值陷阱风险研究池
- 信号生命周期与回测文件

所有候选 CSV 与 `Top200.parquet` 均带 `CandidateView` / `CandidateViewRank`，
并以相同顺序保留 `ResearchPoolRank` / `ResearchDiversityPenalty`，避免跨格式读取时
把“研究榜排名”、“技术信号”和最终“推荐资格”混成同一个概念。GUI 中的
`技术信号` 只描述价格/量价结构，是否满足规则以 `交易资格` 和 `执行说明` 为准。

行情缓存位于 `cache/v3-tickflow-forward/`。旧行情源缓存不会被 TickFlow 行情层读取。

## 结果契约与安全发布

- 每次全量排名都写入 `RankingScope`、`RankingUniverseSize`、`RankingRunId`；候选子集只能展示和筛选，不能重新计算横截面百分位。
- `DecisionPolicySignature` 绑定评分、ATR、基本面门槛、交易资格和回测成本配置；程序参数改变后，GUI 会提示重新生成结果。
- 日更先在 `output/.staging/<RunId>/` 完成扫描、回测和完整性校验，再切换正式结果。运行中 GUI 固定读取 `LatestRun.json` 指向的上一份不可变快照。
- `DailyRunSummary.json` 记录阻断项统计、与上一运行的资格升降、分数大幅变化、成本模型、校准稳定性和历史股票池覆盖情况。
- 行情结果记录复权方式、复权基准日、ATR 截止日和复权重建标记，避免不同价格口径混用。
- `DecisionResults.csv` 保留执行流动性与行情时效诊断；研究排序可以保留延迟数据，但即时 `READY/CAUTIOUS` 必须使用最新完成交易日。
- 成功的正式运行会生成 v85 A 股研究简报站点；页面只读取公开字段白名单，并按报告日期截断 K 线，避免历史页面混入未来数据。详见 [WEB_REPORT.md](WEB_REPORT.md)。

## 回测交易成本

默认券商费率按产品分别计算，最低佣金均为 0 元：

- A 股：单边 `0.00008499999`（万 0.8499999）
- ETF / LOF：单边 `0.00005000001`（万 0.5000001）

股票卖出印花税单独计入，ETF 不计股票印花税。固定滑点之外，回测还按成交额参与率加入有上限的流动性冲击；停牌或一字跌停无法卖出时，退出日最多顺延 10 个交易日并记录实际退出日与延迟原因。

## 历史股票池 / ETF 池快照（可选）

为了降低仅使用当前存活标的造成的幸存者偏差，可把 CSV 或 Parquet 快照放入
`cache/historical_universe/`。至少包含：

- `Ticker`
- `AsOf`、`Date` 或 `TradeDate` 之一
- `Eligible`；或者同时提供 `Listed`、`IsST`

可选 `ExclusionReason`。正式全市场运行会持续记录股票和 ETF 的前视快照。回测会选择信号日之前最近的一份状态；没有快照时仍输出带幸存者偏差提示的诊断统计，但这类未验证样本不会再进入线上排序、同类校准或评分权重选择。

训练、验证和测试按最长 60 日结果窗口做 purge：标签跨越下一分区边界的样本标记为 `purged`，不参与拟合或评价。单标的连续信号先按持有期重叠度降权；全市场校准再对同一入场日等权，避免一次普涨/普跌被误当成数百次独立试验。

## 风险声明

本项目用于量化研究、数据分析和策略验证，不构成投资建议。历史回测不能保证未来表现。

## 性能架构

- **行情层**：TickFlow Free 批量日 K + schema 隔离 Parquet 缓存；缓存读取使用受控并行，并生成 `_manifest.json` 记录每个标的的日期、行数和文件版本。
- **财报层**：BaoStock 只做低频点时财报缓存，不参与日常行情扫描热路径；
  单会话串行访问以适配其进程级 socket，并按股票增量落盘。
- **指标层**：原始行情文件未变化且 `SCORING_VERSION` 相同时，复用持久化指标缓存；TickFlow 更新或复权重建会自动失效。
- **评分层**：单次评分内部复用 NumPy 数组；每次公开评分调用先开启新的缓存事务，因此同一 DataFrame 原地更新后不会读取上一轮的旧指标。五维上限仍为 `20/25/25/15/15`，但缺失维度按零证据处理，不再把剩余维度重新放大到 100 分；覆盖率折扣和最终 `Setup/Trigger/Execution` 权重保持不变。
- **回测层**：大股票池自动切换 `ProcessPoolExecutor` 多进程，小任务保持顺序执行以降低启动成本；每个历史候选点只评分一次，并把评分窗口限制在当前模型实际需要的 504 根已计算指标数据。分区采用 60 日标签 purge，收益率、胜率、波动与利润因子均实际使用重叠权重。
- **批量决策层**：研究准入、ETF 排除和硬门槛合并采用 pandas/NumPy 批处理；基准日期对齐、价格查找及 20/60 日收益计算采用批量数组。生产代码不再使用 `DataFrame.iterrows()`。
- **回测缓存**：按行情文件、基准、成本、时间切分、评分版本和回测参数生成哈希。参数与数据没有变化时直接复用单标的历史样本。
- **GUI**：回测每约 25 个标的输出完成数、样本数、缓存命中、耗时、速度和 ETA，进度条同步更新。

第一次全量回测仍需完成真实历史计算；从第二次开始，只要行情/评分参数没有变化，大量标的会直接命中回测缓存。若修改评分逻辑，请同步提升 `SCORING_VERSION`，派生缓存会自动重建。

### 回测模式与增量性能

- GUI 当前筛选结果 **<=100 只自动使用 Exact**：504 根历史评分窗口、20 日信号冷却、历史时点 Volume Profile，供 Top50 最终精确验证。
- **>100 只自动使用 Fast**：252 根评分窗口、40 日冷却、向量候选预筛、跳过逐历史点 Volume Profile，用于全市场粗校准。
- TickFlow 日 K 只新增交易日时，指标缓存只计算尾部窗口；回测缓存只重算最近历史尾部并与旧样本合并。前复权历史发生变化时会通过 OHLCV 指纹自动退回全量重建。
- 回测 worker 根据 CPU、任务规模和 Fast/Exact 模式自动选择，并使用 DataFrame 批次跨进程返回，减少大量 Python dict 的 IPC 开销。
