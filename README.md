# InstitutionScanner

A 股 / ETF 日频扫描器：使用 **TickFlow Free** 提供行情与标的池，**AkShare** 仅用于低频基本面补充。

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

AkShare
└─ ROE / 毛利率 / 净利润 / 机构覆盖等基本面
        ↓
 fundamental_data.csv（低频缓存）
        ↓
 QualityGate
```

行情层不再使用东方财富、AkShare 行情、新浪或腾讯，也没有行情源自动回退或混源缓存。

## TickFlow Free

免费服务无需 API Key。本项目固定使用：

- `CN_Equity_A` 获取 A 股池
- `CN_ETF` 获取 ETF 池
- `klines.batch()` 批量获取日 K
- `adjust="forward"` 比例前复权，用于收益率、技术指标和历史回测

首次运行会建立约 10 年历史缓存。之后正常扫描只批量获取最近 90 根日 K 并与本地历史合并；若检测到除权导致前复权基准变化，只重建受影响标的的完整历史。

TickFlow Free 不提供实时行情，因此 GUI 的“当日收盘价”始终对应最新已完成的日 K 交易日，不会用盘中价格冒充收盘价。

## AkShare 基本面

AkShare 只负责低频基本面，不参与任何 OHLCV 行情下载。基本面缓存默认 14 天：

- 缓存有效：直接读取，不联网
- 缓存过期：尝试刷新
- 刷新失败：保留已有缓存，行情扫描继续运行
- GUI 勾选“刷新基本面数据”：强制刷新

Windows 开启 Clash 系统代理时，AkShare 基本面请求可读取系统代理；关闭系统代理时恢复直连。代理逻辑与 TickFlow 行情层隔离。

## 安装

Python 3.10+：

```bash
pip install -r requirements.txt
```

## 运行 GUI

```bash
python gui.py
```

GUI 行情源固定显示为 `TickFlow Free`，基本面来源为 `AkShare（低频缓存）`。

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

强制刷新 AkShare 基本面：

```bash
python main.py scan --refresh-fundamentals
```

## 输出

结果位于 `output/`，主要包括：

- `AllResults.csv` / `AllResults.parquet`
- `Top50.csv`
- `Top50TradeReady.csv`
- `Top50EntryCandidates.csv`
- `Top50BreakoutCandidates.csv`
- 信号生命周期与回测文件

行情缓存位于 `cache/v3-tickflow-forward/`。旧行情源缓存不会被 TickFlow 行情层读取。

## 风险声明

本项目用于量化研究、数据分析和策略验证，不构成投资建议。历史回测不能保证未来表现。
