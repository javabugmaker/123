# tools/

One-off diagnostic and smoke scripts. **Nothing here is imported by the
production path** — verified by importing `main`, `daily_pipeline` and
`publish_web_report` in a child process and diffing `sys.modules` against the
repository root; none of these modules appear.

They live in `tools/` rather than at the root so that `*.py` at the root means
"production module" without exceptions, and so a `diag_`/`debug_`/`smoke_`
prefix never gets mistaken for part of the scanner.

Run them from anywhere; each derives the project root from `__file__` and puts
it on `sys.path` itself:

```powershell
python tools/validate_vectorized.py
```

| script | what it does |
|---|---|
| `validate_vectorized.py` | Asserts the vectorised scorer matches the scalar `score_core`. Run after changing either (referenced from `backtest_score_vectorized.py`). |
| `diag_vec.py` / `diag_vec2.py` / `debug_vec.py` | Compare vectorised vs scalar scoring on real cached frames. |
| `smoke_backtest.py` | Timing smoke test over cached frames. |
| `_smoke_bt.py` | Exercises `historical_backtest`'s private worker directly. |
| `dump_pytest_annotation.py` | CI-only: reruns the suite and republishes the failure as a check-run annotation, which is readable without authenticated log access. Wired behind `if: failure()` in `static-quality.yml`. |

Most read `cache/v4-tickflow-forward-volume-shares/*.parquet`, which is
**not** in git — they are no-ops on a fresh clone until a scan has populated
the cache.
