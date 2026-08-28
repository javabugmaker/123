# Model Health Curves

`performance_curve.py` builds point-in-time longitudinal diagnostics from `SignalHistory.csv` and exports `PerformanceCurve.csv` plus `PerformanceCurve.json`. Pages also materializes a standalone `performance.html` audit page.

`ResearchCohortNAV` and `BetaCanaryNAV` are diagnostic proxies rather than tradable portfolio NAVs. The engine aggregates each signal-date cross-section once and uses horizon-equivalent compounding, so overlapping 20-day outcome rows are not naively compounded as independent daily returns. `BenchmarkNAV` uses the same 20-day windows for the CSI 300 benchmark.

The public page exposes mature signal dates and mature sample counts before drawing conclusions. It intentionally does not publish Sharpe or CAGR until a daily position/execution ledger exists.

The public renderer is dependency-free SVG (`institution_scanner/performance_curve_web.py`). `institution_scanner/performance_curve_runtime.py` exposes hooks for DAILY artifacts, the compact report card, and the standalone detail page.

A later execution-ledger implementation can replace the proxy NAV series while keeping the JSON and Pages contract stable.
