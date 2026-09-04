# v115 A 股研究简报 / GitHub Pages

InstitutionScanner 在正式扫描与 DAILY 校验完成后生成公开安全的静态研究简报。页面只负责解释已经落地的决策结果，不重新评分、排名或判断交易资格。

## 发布数据边界

同一运行保留三个用途明确的结果面：

1. `AllResults.parquet`：完整 400+ 列研究与审计数据；不进入网页。
2. `DecisionResults.csv`：GUI 与执行工作流使用的轻量决策数据。
3. `PublicCandidates.csv` + `PublicationManifest.json`：网页使用的 58 列稳定候选契约，以及只记录一次的模型、策略、市场状态与 Run ID。

`PublicCandidates.csv` 不重复保存超长的历史版本字符串。网页优先读取该文件；旧运行没有新契约时，才兼容读取 `Top50Mixed.csv`。

## 单一渲染路径

稳定入口仍是 `web_report_v81.py`，但它现在直接调用 `institution_scanner/publication_renderer.py`。生产路径不再串联 `web_report_v84/v85/v90/v93/v102/v102_1`，也不再依赖正则和字符串替换逐层改写 HTML。

生成的站点位于 `output/web_report/`：

- `index.html`：最新研究简报；
- `reports/YYYY-MM-DD.html`：不可变的日期归档；
- `reports/index.html`：历史索引；
- `assets/report-v115.css`、`assets/report-v115.js`：当前页和历史页共享的静态资源；
- `performance.html`、`backtest.html`：有相应数据时生成的独立审计页。

页面首屏固定回答六个问题：市场处于什么状态、有多少 READY / CAUTIOUS / OBSERVE、数据截至哪天，以及执行容量按多大订单估算。模型版本、运行状态与证据覆盖默认收在诊断区。

研究候选表恢复 `TREND` 列：它读取本地 TickFlow 行情缓存，截取不晚于报告日的最近 30 个交易日收盘价，并渲染为紧凑 SVG 走势线。A 股颜色约定为红涨绿跌；缓存缺失时显示 `—`，不会阻断报告生成，也不会影响评分和排名。

页面不嵌入 `AllResults` 宽表或整套 TickFlow 行情缓存；每个候选只写入一条轻量 SVG 走势线，因此历史归档仍保持紧凑。

## 自动发布与权限

`.github/workflows/daily-pages.yml` 分为两个作业：

1. `compute-and-verify` 只有仓库只读权限，完成扫描、回测、输出契约与时效校验，然后上传静态站点 artifact；
2. `publish` 仅在前一个作业成功后获得 `contents: write`，下载同一个已验证 artifact 并推送 `gh-pages`。

因此校验失败的运行没有发布权限，也不会更新 Pages。`main` 分支不再跟踪本地 `.gh-pages/` 工作树；历史站点只存在于部署分支。

发布器读取 `gh-pages` 时优先使用公开 HTTPS，推送时依次尝试带 GitHub Actions 凭据的 HTTPS 和已配置 origin。网络或认证失败不会改变扫描、回测的返回码。

站点地址：<https://javabugmaker.github.io/123/>

历史页地址：`https://javabugmaker.github.io/123/reports/YYYY-MM-DD.html`

## 手工构建与重试

用最新正式输出重新构建并发布：

```powershell
python publish_web_report.py
```

只生成本地页面、不推送：

```powershell
$env:INSTITUTION_SCANNER_WEB_PUBLISH="0"
python publish_web_report.py
```

只发布已经验证并打包好的站点目录：

```powershell
python -m institution_scanner.publish_site output/web_report
```

网络较慢时可以把单条 Git 命令超时调整到 15–600 秒（默认 90）：

```powershell
$env:INSTITUTION_SCANNER_WEB_GIT_TIMEOUT="180"
python publish_web_report.py
```

## GitHub Pages 一次性设置

仓库 Settings → Pages 选择 **Deploy from a branch**，分支为 **gh-pages**，目录为 **/(root)**。之后成功推送部署分支即可更新站点。

## 安全边界

网页不发布行情缓存、日志、本地路径、凭据、账户信息或任意扫描列。所有动态 HTML 值都会转义；评分、排名、TradeReady、PIT 与校准治理在页面构建前已经由生产输出契约锁定。
