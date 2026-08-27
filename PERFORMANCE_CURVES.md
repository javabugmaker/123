# Model Health Curves

`performance_curve.py` builds point-in-time longitudinal diagnostics from `SignalHistory.csv` and exports `PerformanceCurve.csv` plus `PerformanceCurve.json`.

The first version deliberately labels `ResearchCohortNAV` and `BetaCanaryNAV` as diagnostic proxies rather than tradable portfolio NAVs. It aggregates each signal-date cross-section once and uses horizon-equivalent compounding, so overlapping 20-day outcome rows are not naively compounded as independent daily returns.

The public renderer is dependency-free SVG (`institution_scanner/performance_curve_web.py`). `institution_scanner/performance_curve_runtime.py` exposes two integration hooks: `after_history_refresh(history)` for DAILY and `after_page_build(page_path, output_dir)` for Pages.

A later execution-ledger implementation can replace the proxy NAV series while keeping the JSON and Pages contract stable.
