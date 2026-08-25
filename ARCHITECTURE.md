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
- `reliability.py` — shadow challenger and leave-one-out hierarchical evidence.
- `verify_output.py` — post-run production artifact verifier.
- `postprocess_performance.py` — wide-frame consolidation at postprocess read
  boundaries; performance only, never scoring.
- `export_batch.py` — defers redundant candidate-view refreshes and materializes
  the final fully annotated candidate set once per canonical backtest command.
- `report_terminal.py` plus page policy modules — research-terminal presentation
  enhancements and page-only provenance.

Legacy root `*_vXX.py` files remain compatibility kernels for now. Do not add new
versioned monkey-patch modules for normal feature work.

## Release gates

A model-affecting change must pass:

1. Static quality (Ruff, Pyright, compile).
2. Unit / regression tests.
3. Golden reliability fixture.
4. Output-contract verification.
5. Current-session freshness verification.
6. Shadow observation before Champion promotion when scoring semantics change.

Engineering-only changes must additionally preserve canonical model signatures
and ranking columns in regression tests.

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

## Publication / performance semantics

`AllResults` is the canonical mutable post-ranking surface. Intermediate overlays
may update it, but candidate views are a derived publication surface. During a
canonical backtest command, candidate exports are deferred until calibration,
narrative, reliability and resonance diagnostics are complete, then generated
once from the final annotated frame.

Wide CSV frames are consolidated once when postprocessors read them. This avoids
pandas block fragmentation warnings without suppressing warnings globally and
without changing any values.

## Consolidation policy

Architecture consolidation is behavior-first, not deletion-first. A legacy module
can be removed only after canonical code has equivalent regression coverage and a
golden test demonstrates that the production output is unchanged.
