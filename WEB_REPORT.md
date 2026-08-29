# v85 A 股研究简报 / GitHub Pages

InstitutionScanner can generate a public-safe static research briefing after a successful canonical scan, standalone backtest, or completed DAILY pipeline. The v85 presentation uses the same compact editorial system as the desktop GUI without changing scores, decisions, or source data.

## Automatic behavior

Successful canonical runs continue to call the stable compatibility entry point `web_report_v81.maybe_publish_canonical_report(...)`, which delegates rendering to the canonical research terminal and transport to `institution_scanner/pages_publisher.py`.

The publisher:

1. Reads only already-published candidate views, run summaries, and the historical price cache required for the selected report date.
2. Keeps a strict public-field allowlist.
3. Writes the local static site to `output/web_report/`.
4. Uses public HTTPS first when reading `gh-pages`, so an SSH port-22 timeout cannot block the clone.
5. Tries HTTPS credentials and then the configured origin when pushing.
6. Pushes only `index.html`, `.nojekyll`, `reports/`, and the optional performance page to the `gh-pages` branch.
7. Never changes the scan/backtest return code when GitHub/network authentication fails.

The report contains these primary sections:

- 市场状态与核心指标
- 重点机会和可切换研究视图
- 板块轮动与风险雷达
- 模型变化与运行状态
- 严格按报告日截断的交互 K 线详情

The expected site for this repository is:

`https://javabugmaker.github.io/123/`

Historical pages are stored under:

`https://javabugmaker.github.io/123/reports/YYYY-MM-DD.html`

## One-time GitHub Pages setting

GitHub Pages must have a publishing source enabled once for the repository. In GitHub repository settings, open **Pages** and select **Deploy from a branch**, branch **gh-pages**, folder **/(root)**.

After that, every successful local publish to `gh-pages` updates the Pages site automatically.

## Manual retry

If a scan succeeded but a network/Git authentication error prevented the site push, retry without rerunning the scanner:

```powershell
python publish_web_report.py
```

The command uses the latest already-published local output.

If the network is unusually slow, the per-command Git timeout can be adjusted
without changing source code (15-600 seconds, default 90):

```powershell
$env:INSTITUTION_SCANNER_WEB_GIT_TIMEOUT="180"
python publish_web_report.py
```

## Controls

Disable GitHub publishing while still generating local HTML:

```powershell
$env:INSTITUTION_SCANNER_WEB_PUBLISH="0"
```

Change the maximum number of displayed candidate rows (25-1000, default 250):

```powershell
$env:INSTITUTION_SCANNER_WEB_REPORT_ROWS="250"
```

## Safety boundary

The website does not publish raw price-cache files, logs, local filesystem paths, credentials, account information, or arbitrary columns from the scanner output. v85 reuses the explicit public allowlist from the stable report layer; every dynamic HTML value is escaped and historical charts are cut off at the report date.
