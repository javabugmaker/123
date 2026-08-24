# InstitutionScanner canonical architecture

## Production contract

The production Champion remains locked at:

- Setup: 0.60
- Trigger: 0.15
- Execution: 0.25
- Backtest minimum samples: unchanged
- v102 fail-closed calibration governance: unchanged
- TradeReady hard gates: unchanged

New model ideas must run as shadow challengers. They may create diagnostic scores,
ranks and summaries, but they must never rewrite production score/rank/eligibility
columns until an explicit promotion is made after out-of-sample validation.

## Canonical forward path

New reliability and report work belongs under `institution_scanner/`:

- `contracts.py` — immutable Champion / Challenger contracts.
- `reliability.py` — shadow challenger and hierarchical historical evidence.
- `verify_output.py` — post-run production artifact verifier.
- `report_terminal.py` — research-terminal presentation enhancements.

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

GitHub DAILY now builds with publication disabled, verifies the generated output,
checks freshness, and only then publishes the snapshot.

## Evidence semantics

Three evidence layers are intentionally separated:

- LOCAL BT — ticker-level historical evidence; production use still requires the
  existing minimum-sample contract.
- PEER BT — global/peer calibration; v102 held-out + walk-forward governance can
  force it to diagnostic-only.
- HIER BT — pooled asset/industry/signal evidence introduced in v103. It is
  diagnostic-only by contract and is never fed into production ranking.

This separation prevents sparse or unstable historical evidence from being
presented as a probability or silently changing the production model.

## Consolidation policy

Architecture consolidation is behavior-first, not deletion-first. A legacy module
can be removed only after canonical code has equivalent regression coverage and a
golden test demonstrates that the production output is unchanged.
