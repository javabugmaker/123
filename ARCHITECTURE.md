# InstitutionScanner canonical architecture

## Production contract

The production Champion remains locked at:

- Setup: 0.60
- Trigger: 0.25
- Execution: 0.15
- Signature order: `Setup:Trigger:Execution`
- Backtest minimum samples: 10
- v102/v106 fail-closed calibration governance: unchanged
- TradeReady hard gates: preserved except for explicit data-semantic integrity repairs

The canonical source of truth is `config_core.py` plus
`institution_scanner/contracts.py`. New alpha/model ideas remain shadow
challengers until explicit out-of-sample promotion. Engineering/data-semantic
repairs must not silently change the Champion weight signature.

## Canonical forward path

New work belongs under `institution_scanner/`. Root `*_vXX.py` files are
compatibility kernels only and the production overlay count is shrink-only.

Core canonical services include:

- `contracts.py` — immutable Champion / Challenger contracts.
- `policy_manifest.py` — typed model, quality and execution policies plus stable
  `DP-xxxxxxxxxxxx` policy hash.
- `quality_policy.py` — PASS / FAIL / UNKNOWN / NOT_APPLICABLE evidence semantics
  and annual-vs-interim ROE ownership.
- `execution_capacity.py` — market capacity vs configured portfolio/order capacity.
- `price_limit_policy.py` — one A-share/ETF exchange-limit rule used by scalar and
  vectorized tradeability paths.
- `gate_health.py` — diagnostic-only run-level gate distribution drift detection.
- `quality_output.py` — additive publication provenance without expanding the
  legacy `ScanResult` object.
- `backtest_profile.py` — canonical FAST/EXACT historical scoring profile.
- `score_runtime.py` — canonical score-runtime composition.
- `checkpoint_inputs.py` / `scan_resume_boundary.py` — canonical scan wrapper
  semantics around the remaining v59 checkpoint kernel.
- `point_in_time_backtest.py` / `pit_counts.py` / `pit_maturity.py` — PIT metric
  scope, provenance and archive maturity.
- `reliability.py` — shadow challenger and leave-one-out hierarchical evidence.
- `verify_output.py` — post-run production artifact verifier.
- `postprocess_performance.py` — wide-frame consolidation at read boundaries.
- `market_cache_performance.py` — validated cache persistence performance.
- `ranking_determinism.py` / `report_determinism.py` — deterministic exact-tie
  ordering.
- `export_batch.py` — final candidate-view materialization after annotations.
- `gui_view_model.py` — pure GUI view derivation outside Tk widgets.
- `performance_health.py` — comparable DAILY runtime regression diagnostics.
- `version_manifest.py` / `runtime_inventory.py` — structured provenance and
  measurable compatibility-debt inventory.
- `publication_contract.py` — narrow, stable candidate projection plus one
  run-level provenance manifest.
- `publication_renderer.py` — single-pass Pages renderer with shared static
  assets; no chained HTML mutation or embedded wide research frames.

No new root version overlay above the v102 compatibility ceiling is allowed.

## Financial quality semantics

AKShare/Eastmoney exposes ROE for the report period. An interim Q1/H1/Q3 ROE is
therefore not a full-year ROE and must not be compared directly with the fixed
annual quality thresholds.

The production semantic contract is:

1. keep the raw latest report-period `ROE` for provenance;
2. expose it as `InterimROE` when the latest report is not annual;
3. derive `LatestAnnualROE` only from an annual report that was already announced
   by the point-in-time cutoff;
4. use `ROEHardGateValue=LatestAnnualROE` for full-year quality thresholds;
5. if an AKShare interim report exists but no usable annual ROE exists, ROE
   evidence is `UNKNOWN`, not `FAIL`;
6. never multiply Q1/H1/Q3 ROE by a guessed annualization factor;
7. preserve `LatestReportPeriod` and `LatestAnnouncementDate` PIT provenance.

`QualityGateEvidenceCompleteness` means completeness of evidence required by the
quality gate. `FinancialFieldCoverage` is a separate measure of how many raw
financial fields are present; an unavailable `DebtToAssets` value must not be
misrepresented as a failed quality condition.

## Execution-capacity semantics

Liquidity has two different meanings and they are kept separate:

- `MarketExecutionEligible`: the security itself clears the minimum 60-day
  turnover floor.
- `PortfolioExecutionEligible`: the configured order also fits within the maximum
  turnover participation rate.

The output exposes `TradeLiquidityMaxOrderCNY` and headroom. The default assumed
order remains 50,000 CNY for backward compatibility, while
`INSTITUTION_SCANNER_ORDER_NOTIONAL_CNY` may explicitly set the live research
notional. A smaller account can therefore pass portfolio capacity on a security
that would fail a 50,000 CNY order without pretending the market itself is
illiquid.

## Price-limit semantics

`institution_scanner.price_limit_policy` is the single source for:

- standard A-share 10% rules;
- ChiNext 10% before 2020-08-24 and 20% thereafter;
- STAR 20%;
- Beijing Stock Exchange 30%;
- relevant ETF fallback rules;
- validated provider ratio overrides.

Scalar tradeability and the vectorized historical matrix consume this same
policy so historical rule changes cannot drift between live and backtest paths.

## Mandatory production output schema

`AllResults.csv` is not valid merely because it contains prices and scores. The
verifier requires production/model/pipeline provenance, full-universe ranking
scope, run ID, execution/signal/freshness fields and candidate-view provenance.

Additive v113 audit fields include:

- interim/annual/hard-gate ROE provenance;
- quality evidence status strings;
- gate-evidence completeness and raw financial-field coverage;
- market and portfolio execution eligibility;
- maximum order capacity and liquidity headroom;
- structured decision-policy hash via the version manifest.

Missing required production-proof fields remains a fail-closed publication
error. An empty TradeReady view is valid when no row passes hard gates.

## Gate-health observability

`DailyRunSummary.json` records diagnostic gate health. A near-zero quality pass
rate, especially with high hard-data completeness or a sharp collapse relative
to the previous comparable run, is surfaced as a warning/critical flag. This is
observability only: gate-health diagnostics never alter scores or eligibility.

## Release gates

A model-affecting or decision-semantic change must pass:

1. Static quality (Ruff, strict canonical Ruff, Pyright, compile).
2. Unit / regression tests.
3. Golden reliability fixture.
4. Offline reliability-to-publication golden pipeline where applicable.
5. Output-contract verification.
6. Current-session freshness verification.
7. Shadow observation before Champion promotion for genuine alpha/model changes.

Engineering-only changes must preserve the Champion weight signature and ranking
semantics. Exact ties remain ticker-stable.

## Evidence semantics

Three evidence layers remain intentionally separated:

- LOCAL BT — ticker-level historical evidence; production use requires the
  minimum-sample contract.
- PEER BT — global/peer calibration; PIT/survivorship/held-out governance can
  force it to diagnostic-only.
- HIER BT — pooled asset/industry/signal evidence with focal-ticker leave-one-out
  and peer-breadth caps; diagnostic-only by contract.

Prospective PIT archive maturity remains:

- `NO_ARCHIVE`
- `WARMUP`
- `OBSERVING`
- `SHADOW_ELIGIBLE`
- `PROMOTION_CANDIDATE`

Maturity never auto-promotes a model.

## Publication and performance semantics

`AllResults` is the canonical mutable post-ranking surface. Candidate views are
derived publication surfaces and must not recompute cross-sectional ranks.
Candidate exports are deferred until calibration, narrative, reliability and
other diagnostics are complete, then materialized once.

The publication boundary has three deliberately different artifacts:

1. `AllResults.parquet` is the complete research/audit surface.
2. `DecisionResults.csv` is the lightweight operational/GUI surface.
3. `PublicCandidates.csv` plus `PublicationManifest.json` is the stable public
   page contract. Long legacy version strings occur once in the manifest, not
   once per candidate row.

The stable `web_report_v81` entry point now calls the canonical renderer
directly. Historical `web_report_vXX` modules are compatibility archives and
are not composed on the production Pages path. Current and dated reports share
`assets/report-v114.css` and `assets/report-v114.js`; market-cache/K-line data is
not embedded into every archive page.

Wide CSV frames are consolidated at postprocessor read boundaries. Market-data
frames are validated before cache persistence; independent ticker writes may be
bounded-parallel while manifest materialization remains ordered.

`DailyRunSummary.json` exposes stage timings, scan/backtest breakdowns,
performance health and gate health. These diagnostics never change ranking.

## Runtime and dependency reproducibility

Static CI and the Docker image use Python 3.11 and reviewed constraints from
`constraints-ci.txt`. `requirements.txt` remains the compatibility range; the
constraints file defines the tested runtime set.

Legacy concatenated version strings remain readable for compatibility. New
consumers should use `version_manifest`, including its typed decision-policy
manifest/hash and explicit runtime overlay inventory.

The supported CLI entry point is `python -m institution_scanner`; `main.py`
remains a compatibility facade. Docker and the Windows CI smoke job exercise
the package entry point/import surface.

DAILY compute/verification and Pages publication are separate CI jobs. The
compute job has read-only repository permission and uploads a verified static
artifact; only the dependent publication job receives write permission. The
generated `gh-pages` worktree is no longer tracked on `main`.

## Shrink-only legacy budget

The remaining giant compatibility modules are shrink-only. CI guards their size
ceiling so new logic cannot accumulate in them:

- `analytics_core.py` <= 160 KB
- `report_core.py` <= 105 KB
- `gui_core.py` <= 105 KB
- `gui.py` <= 100 KB
- `scanner.py` <= 80 KB
- `signal_lifecycle_core.py` <= 70 KB

Future extraction moves pure services/view models into the canonical package and
then lowers these ceilings. GUI and Pages development should prefer subtraction
and shared view-model extraction over new top-level controls.

## Compatibility-debt policy

Architecture consolidation is behavior-first, not deletion-first. A legacy
module may be removed from the production path only after canonical code owns the
same semantics and regression/golden tests prove equivalence.

v113 retires five thin production wrappers from runtime composition:

- `analytics_compat_v97`
- `backtest_profile_alignment_v95`
- `score_runtime_v97`
- `checkpoint_inputs_v59`
- `scanner_resume_v68`

The remaining overlay count is reported by `runtime_inventory()` and must only
move downward. Large kernels such as `scanner_resume_v59` are retained until a
full equivalence test can protect their checkpoint/crash semantics.
