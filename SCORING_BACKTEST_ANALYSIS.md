# 评分与回测链路分析

> 分析时间：2026-09-15　|　基线：`a7251bc`　|　只读分析 + 实证探针，未修改生产代码
> 探针脚本：`probe_vp.py` / `probe_vp2.py`（临时目录，未入库）

---

## 0. 摘要：三条结论

1. **方法论是扎实的，好于预期。** 回测侧实现了严格的点内评分截断、`T+1` 开盘成交、净化切分（60 日结果跨边界即剔除）、重叠样本按间隔降权、完整的费用/印花税/滑点建模、以及仅用 validation 集选权重且 test 集只做审计的标定流程。这些都是教科书级的做法，不需要重构。
2. **发现一处实测确认的「回测 ≠ 实盘」评分分歧（B1）。** `volume profile / HVN` 分支在回测中被系统性剥离，而实盘保留，最多影响 structure 维度 2/15 分，23% 的样本实际触发。**且没有任何测试覆盖它。**
3. **发现一处运行时反馈闭环（B2），目前处于休眠状态。** 评分权重由回测产物 `output/ScoreCalibration.json` 在运行时决定，该文件不受版本控制。当前 `accepted=false` 所以权重等于默认值；一旦标定被接受，回测将开始影响评分、评分再影响下一轮回测样本。

另有 4 项风险（B3~B6），其中 2 项是我提出假设后**被证据推翻**的，已明确标注，避免误导后续决策。

---

## 1. 评分链路

### 1.1 结构

`score.py` 是 facade，在导入时注入 **7 个覆盖层**，最后 `sys.modules[__name__] = _core`：

```
score.py (facade)
  ├─ score_threshold_migration_v95.install(config)     ← 先发布迁移后的阈值常量
  ├─ score_core  (stable implementation)
  ├─ 覆盖：_style_adjustment → 全 1.0                  ← 避免重复奖励来源特征
  ├─ 覆盖：score_volatility → volatility_contraction_score(df, max_score=15)
  ├─ 覆盖：trigger_event_score → 新版（不复用 MA 趋势 / setup 证据）
  ├─ 覆盖：score_ticker → 缓存事务 + 替换 TriggerScore + 重算 final
  ├─ 覆盖：combine_scalar_score（来自 institution_scanner.score_kernel）
  ├─ 注入：score_acceleration_v79 / score_endpoint_acceleration_v79
  ├─ 注入：score_scale_migration_v95 / score_weight_cache_v79
  └─ 注入：institution_scanner.score_runtime.install(...)
```

### 1.2 数学

**第一层 — 五维 setup（`base_score`）**

| 维度 | 上限 | 主要输入 |
|---|---:|---|
| trend | 20 | `Close` vs `MA200`（需 ≥252 根、≥60 有效点） |
| volume | 25 | 量能 |
| accumulation | 25 | 机构痕迹 |
| volatility | 15 | `volatility_contraction_score(df, max_score=15)`（facade 覆盖，与过滤器门控同一算法） |
| structure | 15 | `DistToLow52W`(≤5) + 盘整紧密度(≤5) + `RegSlope/RegR2`(≤3) + **`Above_HVN/DistToHVN_Pct`(≤2)** |

合计上限 **100**（`SCORING_WEIGHTS` 实测值）。

两个关键设计决策：
- `_style_adjustment` 被 facade 覆盖为 `(1,1,1,1,1)`——**风格标签只做描述，不二次奖励其来源特征**，避免重复计分。
- 缺失维度**记 0，不做重归一化**。源码注释明确说明：按可用最大值重归一化会让"移除一个弱/缺失维度反而抬高总分"。这是正确的。

**第二层 — 覆盖率衰减**

```
setup_coverage     = 0.55 + 0.45 × coverage
trigger_coverage   = 0.75 + 0.25 × coverage
execution_coverage = 0.70 + 0.30 × coverage

base_score      = clamp(total      × setup_coverage,     0, 100)
trigger_score   = clamp(breakout   × trigger_coverage,   0, 100)   # core 版
               = clamp(trigger_event_score × trigger_coverage, 0, 100)  # facade 覆盖版
execution_score = clamp(execution_raw × execution_coverage, 0, 100)
```

`missing_indicators >= 4` 时直接返回 `confidence = 0.0`，跳过评分。

**第三层 — 合成**

```
final_score = clamp(base×w_setup + trigger×w_trigger + execution×w_execution, 0, 100)
final_score = min(final_score, 40 + 60 × coverage)          # 覆盖率上限
```

权重默认 **(0.60, 0.25, 0.15)**，但**运行时优先从 `OUTPUT_DIR/ScoreCalibration.json` 读取**（见 B2）。

**一致性核查（已通过）**：`score_core.py:1137-1138` 也施加了 `min(final, 40+60*coverage)`，与 `institution_scanner/score_kernel.combine_scalar_score` 一致；`analytics_core.py:124` 从 **`score`（facade）** 导入 `score_ticker`，因此回测与实盘用的是同一个评分入口。✅

---

## 2. 回测链路

### 2.1 执行路径

```
cmd_backtest
  → backtest_command_v76（整命令事务）
  → analytics_core.run_historical_backtest
      → _backtest_one_ticker_cached  ← 被 5 处重绑定
          → _backtest_one_ticker     ← 被 4 处重绑定
              → _signal_evaluations / _signal_points
              → _backtest_scoring_window   ← 严格点内截断
              → score_ticker（facade 版）
              → is_entry_tradeable / resolve_exit_index / conditional_fill_v96
```

重绑定关系（`_backtest_one_ticker`）：
`backtest_sample_acceleration_v80:154/416`、`backtest_vectorization_v98:330/1106`、
`conditional_fill_v96:338/440`（作用域内安装，可卸载）、`backtest_alignment.py:186-212`（包装）。

`backtest_alignment.install_analytics_alignment()`（由 `main.py` 调用）依次安装：
`backtest_profile_alignment_v95` → `backtest_fastscore_v80` → `scoring_consistency_v94`，
并把基准口径设为 `BACKTEST_BENCHMARK_ENTRY_BASIS = "OPEN"`。

### 2.2 抗前视机制（逐项核实）

| 机制 | 实现 | 结论 |
|---|---|---|
| 点内评分 | `_backtest_scoring_window`：`end = index+1`，`start = max(0, end - max(252, 504))`，返回 `enriched.iloc[start:end]` | ✅ 严格截断 |
| 成交时点 | `entry_index = index + 1`，`entry_price = opens[entry_index]` | ✅ 信号日 T，T+1 开盘成交 |
| 不可买入过滤 | `is_entry_tradeable(ticker, enriched, entry_index, is_etf)`（一字板 `locked_limit_up` / 停牌） | ✅ |
| PIT 资格 | `point_in_time_eligibility(ticker, signal_date)` | ✅ |
| 停牌退出 | `resolve_exit_index(..., max_delay_days=BACKTEST_MAX_EXIT_DELAY_DAYS=10)` | ✅ |
| 净化切分 | `_purged_split_label`：若 60 日结果窗口跨越 train/val/test 边界 → 标记 `"purged"` 剔除 | ✅ López de Prado 式净化 |
| 重叠降权 | `_reweight_samples`：`weight = min(1, spacing / horizon)` | ✅ 样本唯一性加权 |
| 数据完整性 | 要求整个持有期内 `High/Low` 有限且为正，`Close` 在 20/60 日退出点有限 | ✅ |

### 2.3 收益与成本口径

```
net_excess20 = net_return20 − benchmark_return20
```
- 基准按日期 `asof` 对齐，且 `backtest_alignment` 统一为**同日开盘**基准。
- 成本：`round_trip_cost_percent(...)`，含佣金（股票 `8.5e-05` / ETF `5e-05`）、印花税 `5e-04`、基础滑点，并按**成交日的成交量**计算流动性冲击（上限 `BACKTEST_MAX_LIQUIDITY_SLIPPAGE = 0.003`）。
- 回撤：`drawdown20/60 = min(lows / maximum.accumulate(prices) − 1) × 100`。

### 2.4 标定（`model_calibration.py:513`）

网格搜索 `setup ∈ [0.45, 0.70] step 0.05`、`trigger ∈ [0.15, 0.35] step 0.05`，`execution = 1 − setup − trigger` 需落在 `[0.10, 0.25]`：

- **只在 validation 集上选权重**；test 集的 IC 仅作为审计指标上报，不参与选择（源码注释：*"The test split is never used to choose weights"*）。
- 接受条件：权重不等于默认值 **且** `best_ic ≥ default_validation_ic + 0.01`。
- 样本门槛：validation ≥ 30 条且有效权重 ≥ 30。
- 平局保留默认权重。

**评价：方法论健全。** 唯一遗留问题是 B2 的产物闭环。

---

## 3. 风险清单（按严重程度 × 修复成本）

| ID | 风险 | 严重度 | 成本 | 证据与影响 |
|---|---|:-:|:-:|---|
| **B1** | **回测与实盘评分不等价（HVN 分支被回测剥离）** | **P1** | **S** | `backtest_profile_alignment_v95` 强制 `historical_volume_profile=False`，回测的评分窗口会 `drop` VP 列；实盘 `scanner_core.py:470 → 581` 传带 VP 的完整帧。**实测 60 个随机样本中 14 个（23%）触发，structure 差 +0.035 ~ +1.70（理论上限 2/15，均值 +0.94）**。无任何测试覆盖 |
| **B2** | **评分权重依赖未纳管的运行时产物** | **P2** | **S** | `score_core.py:135-155` 读取 `OUTPUT_DIR/ScoreCalibration.json`；该文件 `git ls-files` 查无记录，由 `analytics_core.py:3294` 在回测后写出 → 形成「回测产出权重 → 影响评分 → 影响下一轮样本」闭环。当前 `accepted=false`、全部 IC=0、样本=0，**闭环休眠**。已有护栏：权重区间 + 和为 1 + 仅用 validation + 需 IC+0.01 |
| **B3** | **`_backtest_one_ticker(_cached)` 多重重绑定** | **P2** | **M** | `_backtest_one_ticker` 被 4 个覆盖层重绑定，`_backtest_one_ticker_cached` 被 5 处重绑定。最终语义依赖安装顺序；`conditional_fill_v96` 还是作用域内安装/卸载 |
| **B4** | ~~不走 profile 的旧路径会重算 VP~~ | ~~P2~~ **P3（实测不可达，已推翻）** | — | 我假设 `profile=None` 时会回落到 `ENABLE_VOLUME_PROFILE=True` 重算 VP。核查后：两个无 profile 的调用点（`analytics_core.py:1466`、`backtest_incremental_v78.py:50`）都在 `len(frame) < 300` 的分支里，而 `_backtest_one_ticker` 自身在该条件下直接 `return []`；其余调用点均传 `profile=`；且所有覆盖层版本都支持 profile 契约（`_supports_profile_contract` 探测通过）。**当前不可达** |
| **B5** | **v95 文档字符串与实现冲突** | **P3** | **S** | `backtest_profile_alignment_v95.py` 称 *"Volume-profile/HVN state is observability-only"*，但 `score_core.py:394` 实际消费它并计入 structure。**这句过期注释很可能是 B1 长期未被发现的原因** |
| **B6** | **回撤序列口径略混** | **P3** | **S** | `prices20 = [entry_price] + closes[entry_index:exit20_index+1]`：首元素是 T+1 开盘价，次元素是同日收盘价，两者混在同一序列做 `maximum.accumulate`。影响很小，但口径应写明 |

### B1 实测数据（探针 `probe_vp2.py`）

```
seeds scored          : 60
HVN branch fired      : 14            (23.3%)
delta == 0            : 46
delta when fired: min=+0.0349  max=+1.7021  mean=+0.9414

sample fired rows (seed, dist, with VP, sans VP, delta):
      0  dist=  6.467   5.5827   4.8760  +0.7067
      6  dist=  3.858   6.1063   4.8778  +1.2285
      9  dist=  2.514   8.0750   6.5779  +1.4971
     16  dist=  2.581   5.0782   3.5944  +1.4838
```

判定逻辑（`score_core.py:394`）：
```python
if "Above_HVN" in df.columns and "DistToHVN_Pct" in df.columns:
    above_hvn = df["Above_HVN"].iloc[-1]
    dist_hvn = df["DistToHVN_Pct"].iloc[-1]
    if bool(above_hvn) and _is_finite(dist_hvn) and 0 < dist_hvn < 10:
        score += _clamp(1 - dist_hvn / 10, 0, 1) * 2
```
`score_acceleration_v79.py:395` 实现了完全相同的分支（同条件、同 `*2`），所以**标量与加速版之间是一致的**——问题只在回测与实盘之间。

---

## 4. 建议补的四类测试

按性价比排序，均可独立上线、纯新增、零生产改动：

1. **回测 ↔ 实盘评分等价性**（针对 B1，最高优先）
   构造一个同时满足「VP 列存在」和「回测截窗」的样本，断言 `score_structure(带VP的实盘帧) == score_structure(回测窗口帧)`。
   反向验证：临时把 v95 的 `historical_volume_profile` 改回 `True`，测试必须变红。
2. **标定产物可复现**（针对 B2）
   断言 `model_weight_signature()` 在「有标定文件」与「无标定文件」两种情况下的取值都被记录在 provenance 里；并断言 `ScoreCalibration.json` 的 hash 写入 `BacktestSummary.json`。
3. **`_backtest_one_ticker` 装配快照**（针对 B3）
   冻结当前 `__module__` / `__qualname__` 链，断言安装顺序变化会失败——防止未来新增覆盖层时静默改变语义。
4. **抗前视回归锁**（保护现有正确性）
   对 `_purged_split_label`、`_reweight_samples`、`_backtest_scoring_window` 各加一组边界用例（跨越边界 / 恰好落在边界 / 首样本 / horizon 内密集信号）。这些目前只有 golden 冻结，没有语义断言。

---

## 5. 需要你确认的问题

1. **B1 如何处置？** 三个选项：
   - (a) **回测改为保留 VP**（`historical_volume_profile=True`）——回测与实盘一致，但 EXACT 会变慢（VP 重算），且 FAST 需同步改（v95 当初就是为对齐 FAST/EXACT 才关掉的）；
   - (b) **实盘改为剔除 VP**——与回测一致，但会丢失 2 分的结构信息；
   - (c) **保持现状，但补测试把差异显式记录下来**——成本最低，承认这是一个已知且被度量过的偏差。
   我倾向 **(a)：回测应当复现实盘，而不是反过来**；若性能不可接受，再考虑 (c)。

2. **B2 的标定闭环是否是有意设计？** 如果是有意的自适应机制，建议至少把 `ScoreCalibration.json` 纳入发布物清单（**已经在** `daily_pipeline_core.py:68/83`）并把其 hash 写进 provenance；如果是无意的，建议在 `accepted` 之外再加一个显式的总开关。

---

## 6. 处置结果（2026-09-15，已实施）

采纳方案 **(a) 回测改为保留 VP**，提交 `153ff75`。

### 6.1 改动

| 文件 | 改动 |
|---|---|
| `config.py` | 新增 `BACKTEST_HISTORICAL_VOLUME_PROFILE`（默认 `True`，支持环境变量覆盖，便于无代码回滚） |
| `backtest_profile_alignment_v95.py` | 改为读取该开关，不再硬编码 `False`；版本号升至 `v97.1`；**修正过期的文档字符串**（B5） |
| `backtest_fastpath_v78.py` / `backtest_fastscore_v80.py` | 由硬编码 `include_volume_profile=False` 改为跟随 `profile.historical_volume_profile` |
| `conditional_fill_v96.py` | 新增延迟读取的开关判定 |

FAST 与 EXACT **同时**保留 VP，因此二者仍对齐，且都等于实盘。

### 6.2 向量化未受影响

- `final_score_series` 只被 `historical_backtest.py` 调用，而该处在 `_worker` 里显式设置
  `_indicators.ENABLE_VOLUME_PROFILE = False`（全帧上算 VP 会构成前视），**本次未触碰**。
- `backtest_vectorization_v98` 的向量化在**样本生成侧**（成交、退出、收益），
  评分本来就是逐信号点的 `score_ticker`；它并不使用 `final_score_series`。
- 本次没有把任何向量化代码改写成循环。VP 的 `compute_volume_profile` 内部本来就是
  numpy 向量化（`np.digitize` + 广播）。

### 6.3 性能实测（`_signal_evaluations`，1500 根合成帧）

| 模式 | VP 关 | VP 开 | 增幅 | 5000 标的墙钟（6 进程） |
|---|---:|---:|---:|---:|
| EXACT | 1727 ms/标的 | 1788 ms/标的 | **+3.5%** | +51 s |
| FAST | 545 ms/标的 | 589 ms/标的 | **+8.0%** | +36 s |

判定：可接受。若未来成为瓶颈，优化方向是让 `compute_volume_profile` 只返回末端标量
（现在它会把标量写满 504 行整列），而不是改写成滚动版本——滚动 VP 要对全部
~1500 根 bar 计算，比只在 ~34 个信号点上算慢约 25 倍。

### 6.4 验证

- 新增 `tests/test_backtest_live_scoring_equivalence.py`（4 例）：
  回测窗口重算 VP 后与实盘 structure 一致；不重算则必然偏离；并钉住 profile 配置本身。
- **反向验证**：`BACKTEST_HISTORICAL_VOLUME_PROFILE=0` 时测试必须失败。
  初版测试自己 monkeypatch 成 `True`，导致闸门空转，已修正（见下）。
- 重捕获 `signal_lifecycle_golden`：12 行变更**全部只是 `DecisionPolicySignature`**，
  分数/排名/信号零漂移 —— canary 证明了本次改动的数值影响范围。
- 重捕获 `assembly_manifest`：86 → 86 步，差异仅为版本号 bump 与 4 处行号位移。
- **229 tests / 0 failures / 0 errors**；`ruff check .` 全绿；干净克隆复现一致。

### 6.5 本轮教训

**初版测试是空转闸门。** 我在测试里先 `monkeypatch.setattr(config, ..., True)`
再断言它为真，于是无论出厂值是多少都通过。这是本项目第 9 例"审计工具被自身假设绕过"。
修正后改为断言出厂默认，反向验证（env=0）随即正确变红。
