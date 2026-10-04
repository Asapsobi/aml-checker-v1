# amlcheck — Calibration report (v1.0.0)

> P11, methodology §10, D-066 to D-069. Generated from `tests/golden/golden.json` with
> `uv run python scripts/golden.py report`, plus the live runs recorded in
> [acceptance-results.md](acceptance-results.md).

## Golden set

Golden set: 20 addresses, 20 recorded.

| Type | Predicted (truth known) | Correct | Precision | Target | Missed |
|---|---|---|---|---|---|

Verdicts: 20 of 20 as expected.
Clean addresses: 0, scored high or severe: 0.

**Measured targets met.**
**Not measured (no golden examples yet): HUB, DEPOSIT, COLLECTOR, clean band.**

### What is and isn't measured

| P11 target | Status | Why |
|---|---|---|
| Verdicts on list-backed addresses | **Measured: 20/20** | OFAC (both chains) and Tether TRON freezes, from our own synced lists |
| Trace budgets cold and warm (AT-59) | **Measured: passed** | 6 live traces, at most 105 TronGrid / 38 HyperSync cold, 71–99% fewer warm |
| No clean address high or severe | **Not measured** | Needs known-clean counterparties from the owner (Q-36) |
| HUB precision ≥ 0.9 | **Not measured** | Exchanges' proof-of-reserves pages are blocked from this machine; needs owner-observed hot wallets (Q-36) |
| DEPOSIT precision ≥ 0.9 | **Not measured** | Needs the owner's own exchange deposit addresses (Q-36) |
| COLLECTOR precision ≥ 0.8 | **Not measured** | Public-data candidates were busy services; needs owner-known collectors or decided cases (Q-36) |

The defaults stay as designed (classifier version 1, score version 1): no threshold is changed
without evidence (D-069).

## Calibration notes for v1.0.x

| Note | Seen in | Likely change, once measured |
|---|---|---|
| Money through **unattributed** services and contracts (weight 0.1) pushes `E` up: addresses with no findings scored 36–48 (medium) | P7, P10 live | Lower the inferred weights, or label the big exchanges (weight 0) — score version 2 |
| Coverage is often low (7–44%), mostly `untraced:pruned` | VS-07, AT-59 | Review `[trace] branch` and `coverage_share` against the clean set |
| A very busy deposit or collector (> 1,000 transfers in 90 days) is primary **HUB**, with its real type as a tag | P5, P11 | Consider ordering before the cap rule — classifier version 2 |
| R-HEU-02 (pass-through) fires on exchange deposits by nature | P5 | Down-weight for linked deposits |

## How to complete it (v1.0.x)

1. Send the addresses (Q-36) as `address,chain,kind,note`; kinds: `clean`, `deposit`, `hub`,
   `collector`, `block`, `review`.
2. `uv run python scripts/golden.py import owner.csv`, then `AMLCHECK_HOME=<scratch copy> uv run python
   scripts/golden.py record`.
3. `uv run python scripts/golden.py report`; tune in config, a formula change bumps its version, a
   decision per change. CI keeps the result honest (`test_at58_golden_set_offline`).
