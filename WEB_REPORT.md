# v81 Web Report / GitHub Pages

InstitutionScanner can generate a public-safe static research brief after a successful canonical scan, standalone backtest, or completed DAILY pipeline.

## Automatic behavior

Successful canonical runs call `web_report_v81.maybe_publish_canonical_report(...)`.

The publisher:

1. Reads only published `AllResults.csv` / `DecisionResults.csv`, `Top50Mixed.csv`, `DailyRunSummary.json`, and `BacktestSummary.json`.
2. Keeps a strict public-field allowlist.
3. Writes the local static site to `output/web_report/`.
4. Uses a temporary Git working directory.
5. Pushes only `index.html`, `.nojekyll`, and `reports/` to the `gh-pages` branch.
6. Never changes the scan/backtest return code when GitHub/network authentication fails.

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

The website does not publish raw price-cache files, logs, local filesystem paths, credentials, account information, or arbitrary columns from the scanner output. Only the explicit allowlist in `web_report_v81.py` can enter the generated HTML.
