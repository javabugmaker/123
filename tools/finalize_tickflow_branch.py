from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_required(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"missing target: {label}")
    return text.replace(old, new, 1)


# Fundamentals: AkShare networking lives outside the TickFlow market downloader.
fund = read("fundamental_data.py")
fund = replace_required(
    fund,
    "from downloader import configure_akshare_proxy_from_system, normalize_ticker\n",
    "from downloader import normalize_ticker\n"
    "from network_proxy import configure_akshare_proxy_from_system\n",
    "fundamental proxy import",
)
write("fundamental_data.py", fund)

# Scanner messaging must reflect TickFlow's own batch worker pool, not the old
# per-ticker downloader thread count.
scanner = read("scanner.py")
scanner = scanner.replace("    DOWNLOAD_THREADS,\n", "")
scanner = replace_required(
    scanner,
    "    SCORING_VERSION,\n    setup_logging,\n",
    "    SCORING_VERSION,\n    TICKFLOW_MAX_WORKERS,\n    setup_logging,\n",
    "scanner tickflow worker config",
)
scanner = replace_required(
    scanner,
    '        "Phase 1/2: downloading data for %d tickers (%d threads)...",\n'
    '        len(all_tickers),\n'
    '        DOWNLOAD_THREADS,\n',
    '        "Phase 1/2: preparing TickFlow data for %d tickers (batch workers=%d)...",\n'
    '        len(all_tickers),\n'
    '        TICKFLOW_MAX_WORKERS,\n',
    "scanner phase-one log",
)
write("scanner.py", scanner)

# CLI examples and labels should no longer imply multiple market providers.
main = read("main.py")
main = main.replace(
    "python main.py scan --tickers AAPL,TLT # Scan specific tickers only",
    "python main.py scan --tickers 600036.SH,510300.SH # Scan specific A-share/ETF tickers",
)
write("main.py", main)

# Add migration/incremental invariants to the dedicated provider test module.
test_path = "test_tickflow_provider.py"
tests = read(test_path)
if "test_incremental_cache_update_uses_short_batch" not in tests:
    tests += '''\n\n    def test_incremental_cache_update_uses_short_batch(self):\n        stale = pd.DataFrame(\n            {\n                "Open": [10.0, 10.1],\n                "High": [10.2, 10.3],\n                "Low": [9.9, 10.0],\n                "Close": [10.1, 10.2],\n                "Volume": [1000, 1100],\n            },\n            index=pd.to_datetime(["2026-08-06", "2026-08-07"]),\n        )\n        recent = pd.DataFrame(\n            {\n                "Open": [10.1, 10.2],\n                "High": [10.3, 10.4],\n                "Low": [10.0, 10.1],\n                "Close": [10.2, 10.3],\n                "Volume": [1100, 1200],\n            },\n            index=pd.to_datetime(["2026-08-07", "2026-08-10"]),\n        )\n        ticker = downloader.TickerInfo("600000.SH")\n        with (\n            patch.object(downloader, "_load_cache", return_value=stale),\n            patch.object(downloader, "_cache_has_completed_daily_bar", return_value=False),\n            patch.object(\n                downloader, "_batch_fetch", return_value={"600000.SH": recent}\n            ) as batch,\n            patch.object(downloader, "_save_cache"),\n        ):\n            result = downloader.download_batch([ticker])\n\n        batch.assert_called_once_with(["600000.SH"], downloader._INCREMENTAL_BARS)\n        self.assertEqual(str(result["600000.SH"].index[-1].date()), "2026-08-10")\n\n    def test_forward_adjustment_change_forces_full_rebuild(self):\n        stale = pd.DataFrame(\n            {\n                "Open": [10.0, 10.1],\n                "High": [10.2, 10.3],\n                "Low": [9.9, 10.0],\n                "Close": [10.1, 10.2],\n                "Volume": [1000, 1100],\n            },\n            index=pd.to_datetime(["2026-08-06", "2026-08-07"]),\n        )\n        rebased = stale.copy()\n        rebased[["Open", "High", "Low", "Close"]] *= 0.9\n        full = pd.DataFrame(\n            {\n                "Open": [9.0, 9.1, 9.2],\n                "High": [9.2, 9.3, 9.4],\n                "Low": [8.9, 9.0, 9.1],\n                "Close": [9.1, 9.2, 9.3],\n                "Volume": [1000, 1100, 1200],\n            },\n            index=pd.to_datetime(["2026-08-06", "2026-08-07", "2026-08-10"]),\n        )\n        with (\n            patch.object(downloader, "_load_cache", return_value=stale),\n            patch.object(downloader, "_cache_has_completed_daily_bar", return_value=False),\n            patch.object(\n                downloader,\n                "_batch_fetch",\n                side_effect=[{"600000.SH": rebased}, {"600000.SH": full}],\n            ) as batch,\n            patch.object(downloader, "_save_cache"),\n        ):\n            result = downloader.download_batch([downloader.TickerInfo("600000.SH")])\n\n        self.assertEqual(batch.call_count, 2)\n        self.assertEqual(batch.call_args_list[0].args, (["600000.SH"], downloader._INCREMENTAL_BARS))\n        self.assertEqual(batch.call_args_list[1].args, (["600000.SH"],))\n        self.assertEqual(str(result["600000.SH"].index[-1].date()), "2026-08-10")\n\n    def test_universe_refresh_always_fetches_stock_and_etf_sets(self):\n        client = Mock()\n        client.universes.get.side_effect = [\n            {"symbols": ["600000.SH"]},\n            {"symbols": ["510300.SH"]},\n        ]\n        with (\n            patch.object(downloader, "_tickflow", return_value=client),\n            patch.object(downloader, "_instrument_batches", return_value=[]),\n            patch.object(downloader, "_save_universe_cache"),\n        ):\n            payload = downloader._fetch_complete_universe()\n\n        self.assertEqual(payload["stocks"], ["600000.SH"])\n        self.assertEqual(payload["etfs"], ["510300.SH"])\n        self.assertEqual(\n            [call.args[0] for call in client.universes.get.call_args_list],\n            ["CN_Equity_A", "CN_ETF"],\n        )\n'''
write(test_path, tests)

README = r'''# InstitutionScanner

A 股 / ETF 日频扫描器：使用 TickFlow Free 提供行情与标的池，AkShare 仅用于低频基本面补充。

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

首次运行会建立约 10 年历史缓存。之后正常扫描仅批量获取最近 90 根日 K 并与本地历史合并；若检测到除权导致前复权基准变化，只重建受影响标的的完整历史。

TickFlow Free 不提供实时行情，因此 GUI 的“当日收盘价”始终对应最新已完成的日 K 交易日，不会用盘中价格冒充收盘价。

## AkShare 基本面

AkShare 只负责低频基本面，不参与任何 OHLCV 行情下载。基本面缓存默认 14 天：

- 缓存有效：直接读取，不联网
- 缓存过期：尝试刷新
- 刷新失败：保留已有缓存，行情扫描继续运行
- GUI 勾选“刷新基本面数据”：强制刷新

Windows 开启 Clash 系统代理时，AkShare 基本面请求可读取系统代理；关闭系统代理时恢复直连。该代理逻辑与 TickFlow 行情层隔离。

## 安装

Python 3.10+：

```bash
pip install -r requirements.txt
```

## 运行 GUI

```bash
python gui.py
```

GUI 行情源固定显示为 `TickFlow Free`，基本面来源显示为 `AkShare（低频缓存）`。

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
'''
write("README.md", README)

# Remove one-shot migration machinery from the final tree.
for relative in (
    "tools/migrate_tickflow_free.py",
    ".github/workflows/apply-tickflow-free-migration.yml",
    "tools/finalize_tickflow_branch.py",
    ".github/workflows/finalize-tickflow-branch.yml",
):
    (ROOT / relative).unlink(missing_ok=True)
