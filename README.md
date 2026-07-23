# InstitutionScanner

## 机构吸筹扫描器（Institutional Accumulation Scanner）

一个基于 Python 的量化选股工具，用于扫描股票和 ETF 行情数据，通过多维度指标分析市场资金行为，寻找可能处于机构布局阶段的标的。

项目目标：

> 从大量股票池中筛选价格结构、成交量行为和资金特征异常的股票，建立机构吸筹候选池。

---

# ✨ 功能特点

## 1. 股票 / ETF 扫描

支持：

- 股票扫描
- ETF 扫描
- 自定义股票列表扫描

运行：

```bash
python main.py scan
```

---

## 2. 多行情数据源

支持：

- EastMoney
- Sina
- Tencent

示例：

```bash
python main.py scan --data-source eastmoney
```

---

## 3. 缓存与断点机制

支持：

- 行情缓存
- 断点恢复
- 增量扫描

强制重新下载：

```bash
python main.py scan --force-download
```

---

## 4. 机构吸筹分析

分析维度：

- 趋势结构
- 成交量变化
- 价格位置
- 资金行为
- 风险因素

通过综合评分筛选潜在机构布局股票。

---

## 5. 报告生成

支持生成：

- CSV
- Parquet

重新生成报告：

```bash
python main.py report
```

---

## 6. 历史回测

支持：

- 20日收益回测
- 60日收益回测
- 超额收益分析
- 最大回撤分析

示例：

```bash
python main.py backtest --tickers 股票列表
```

---

# 📁 项目结构

```
InstitutionScanner/

├── main.py              # 程序入口
├── downloader.py        # 行情下载模块
├── scanner.py           # 扫描核心
├── analytics.py         # 回测分析
├── filters.py           # 股票过滤
├── config.py            # 配置
├── report.py            # 报告输出

├── cache/               # 数据缓存
├── output/              # 分析结果
└── logs/                # 日志
```

---

# 🚀 安装

Python >= 3.10

安装依赖：

```bash
pip install -r requirements.txt
```

---

# 使用方法

## 全量扫描

```bash
python main.py scan
```

## 指定股票

```bash
python main.py scan --tickers 600519,000001
```

## 只下载数据

```bash
python main.py download
```

## 清理缓存

```bash
python main.py clean
```

---

# 🧠 设计理念

传统选股：

```
寻找已经上涨的股票
```

本项目：

```
寻找资金可能正在布局的股票

价格
+
成交量
+
趋势
+
资金行为
+
风险控制

↓

综合评分

↓

候选股票池
```

---

# ⚠️ 风险声明

本项目用于：

- 量化研究
- 数据分析
- 策略验证

不构成任何投资建议。

市场存在：

- 数据误差
- 策略失效
- 投资风险

请结合自身判断。

---

# Roadmap

- [ ] 实时行情扫描
- [ ] AI评分模型
- [ ] Web Dashboard
- [ ] TradingView 联动
- [ ] 自动化交易接口

---

# License

MIT License
