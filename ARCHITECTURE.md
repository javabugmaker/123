# InstitutionScanner canonical architecture

## Production contract

The production Champion remains locked at:

- Setup: 0.60
- Trigger: 0.25
- Execution: 0.15
- Signature order: `Setup:Trigger:Execution`
- Backtest minimum samples: 10
- v102/v106 fail-closed calibration governance: unchanged
- TradeReady hard gates: unchanged

The canonical source of truth is `config_core.py` plus
`institution_scanner/contracts.py`. Documentation, tests and reliability
verification must agree with those values; the reliability layer verifies the
production engine and never redefines it.

New model ideas must run as shadow challengers. They may create diagnostic scores,
ranks and summaries, but they must never rewrite production score/rank/eligibility
columns until an explicit promotion is made after out-of-sample validation.

## Canonical forward path

New reliability, evidence, performance and report work belongs under
`institution_scanner/`:

- `contracts.py` — immutable Champion / Challenger contracts.
- `point_in_time_backtest.py` / `pit_counts.py` — PIT metric scope and truthful
  raw/verified held-out provenance.
- `pit_maturity.py` — diagnostic archive maturity state; never auto-activates
  production evidence.
- `reliability.py` — shadow challenger and leave-one-out hierarchical evidence.
- `verify_output.py` — post-run production artifact verifier.
- `postprocess_performance.py` — wide-frame consolidation at postprocess read
  boundaries; performance only, never scoring.
- `market_cache_performance.py` — validated-frame cache-write elision and bounded
  parallel Parquet persistence while preserving atomic files and manifest order.
- `ranking_determinism.py` / `report_determinism.py` — ticker-stable exact-tie
  ordering so concurrent scanner completion order can never change ranks.
- `export_batch.py` — defers redundant candidate-view refreshes and materializes
  the final fully annotated candidate set once per canonical backtest command.
- `gui_view_model.py` — pure GUI row/view derivation outside Tk widgets.
- `performance_health.py` — comparable DAILY runtime regression diagnostics.
- `version_manifest.py` / `runtime_inventory.py` — structured provenance and
  measurable compatibility-debt inventory.
- `report_terminal.py` plus page policy modules — research-terminal presentation
  enhancements and page-only provenance.

Legacy root `*_vXX.py` files remain compatibility kernels for now. **No new root
version overlays above the current v102 compatibility ceiling are allowed.** New
forward work must live under `institution_scanner/` and be routed through stable
facades.

## Mandatory production output schema

`AllResults.csv` is not considered valid merely because it contains prices and
scores. The verifier requires a compact production-proof schema covering:

- run/model/pipeline/output/decision provenance;
- production model role and locked weight signature;
- Challenger and hierarchical non-production certification;
- calibration-governance/local-peer evidence eligibility;
- full-universe ranking scope and RunId;
- CandidateViewRank / RankingScore / execution / signal / freshness fields.

Missing a required production-proof field is a fail-closed publication error.
Candidate views have their own smaller required schemas. An empty TradeReady view
remains valid when no row passes hard gates.

## Release gates

A model-affecting change must pass:

1. Static quality (Ruff, Pyright, compile).
2. Unit / regression tests.
3. Golden reliability fixture.
4. Offline reliability-to-publication golden pipeline.
5. Output-contract verification.
6. Current-session freshness verification.
7. Shadow observation before Champion promotion when scoring semantics change.

Engineering-only changes must additionally preserve canonical model signatures
and ranking columns in regression tests. Exact score ties must be invariant to
input order; `Ticker` is the final deterministic ordering key.

The canonical `institution_scanner/` package has a stricter Ruff lane
(`B/C4/SIM/PERF/PIE`) in addition to the repository-wide compatibility lint.
Legacy kernels are not used as an excuse to weaken the forward-code quality bar.

GitHub DAILY builds with publication disabled, verifies the generated output,
checks freshness, and only then publishes the snapshot.

## Evidence semantics

Three evidence layers are intentionally separated:

- LOCAL BT — ticker-level historical evidence; production use still requires the
  minimum-sample contract.
- PEER BT — global/peer calibration; PIT, survivorship, leave-one-out, held-out
  ordering and walk-forward governance can force it to diagnostic-only.
- HIER BT — pooled asset/industry/signal evidence. It uses focal-ticker
  leave-one-out plus a Kish peer-breadth cap and remains diagnostic-only by
  contract.

This separation prevents sparse, correlated or unverified historical evidence
from being presented as a probability or silently changing the production model.

### PIT maturity

The prospective PIT archive has an explicit maturity state:

- `NO_ARCHIVE`
- `WARMUP`
- `OBSERVING`
- `SHADOW_ELIGIBLE`
- `PROMOTION_CANDIDATE`

Maturity uses snapshot-day depth, archive span, maximum snapshot gap and verified
held-out samples. It is diagnostic only: `production_activation_allowed` is always
false and any future promotion remains an explicit manual model-governance event.
With partial survivorship control the archive can become shadow-eligible but
cannot claim production-grade survivorship completeness.

## Publication / performance semantics

`AllResults` is the canonical mutable post-ranking surface. Intermediate overlays
may update it, but candidate views are a derived publication surface. During a
canonical backtest command, candidate exports are deferred until calibration,
narrative, reliability and resonance diagnostics are complete, then generated
once from the final annotated frame.

Wide CSV frames are consolidated once when postprocessors read them. This avoids
pandas block fragmentation warnings without suppressing warnings globally and
without changing any values.

Market-data frames are validated before cache persistence. The cache writer marks
canonical validated frames, avoids a redundant second full-frame validation, and
overlaps independent per-ticker Parquet writes with bounded worker threads.
Manifest materialization remains ordered after all cache writes complete.

Scanner analysis transfers market-frame ownership from the main downloaded-frame
dictionary into bounded worker futures as each ticker is submitted, reducing peak
RAM without changing indicator/scoring inputs.

`DailyRunSummary.json` exposes stage timings, scan/backtest breakdowns and a
`performance_health` comparison. A previous run is considered a valid benchmark
only when mode, universe size and cache state are materially comparable; runtime
health is diagnostic and never changes ranking/publication eligibility.

## Runtime and dependency reproducibility

Static CI and the Docker image use Python 3.11 and the same reviewed
`constraints-ci.txt` dependency constraints. `requirements.txt` remains the
compatibility range declaration; constraints define the tested runtime set.

Legacy concatenated version strings remain available for compatibility, while
`version_manifest` provides structured production/runtime provenance and includes
an explicit inventory of compatibility overlays still on the production path.
Every removed overlay must reduce that inventory after golden-equivalence tests.

## Shrink-only legacy budget

The remaining giant compatibility modules are **shrink-only**. CI guards their
size envelope so new logic cannot silently accumulate in them:

- `analytics_core.py` <= 160 KB
- `report_core.py` <= 105 KB
- `gui_core.py` <= 105 KB
- `gui.py` <= 100 KB
- `scanner.py` <= 80 KB
- `signal_lifecycle_core.py` <= 70 KB

Future extraction should move pure services/view models into the canonical package,
then reduce these ceilings. GUI and PAGE development should prefer subtraction and
view-model extraction over adding more top-level controls or diagnostic sections.

## Consolidation policy

Architecture consolidation is behavior-first, not deletion-first. A legacy module
can be removed only after canonical code has equivalent regression coverage and a
golden test demonstrates that the production output is unchanged.

Runtime monkey-patch composition is treated as visible compatibility debt, not a
preferred architecture. Production-facing facades should progressively depend on
canonical services; compatibility kernels are retired one at a time only when an
offline golden/equivalence test proves the same score, rank, eligibility and
publication surface.
