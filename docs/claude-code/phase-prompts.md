# Phase prompts

> Copy-paste openers for each phase. `/start-phase N` does the mechanics; these prompts add the focus
> and the traps specific to each phase. Paste the prompt after `/start-phase N` has shown its plan,
> or use it on its own in a fresh session.

---

### P0 · Foundations & verification

```
We're starting amlcheck from an empty repo. Read CLAUDE.md, then docs/07-roadmap.md (P0) and
docs/08-backlog.md (P0 tickets). Set up the project exactly as docs/03-architecture.md §1–§2 and §6
describe. Then do T-0.09: verify VS-01…VS-06 and VS-08…VS-10 live with the source-verifier subagent,
save fixtures, and write docs/verification-log.md. Anything that differs from docs/04-data-sources.md
becomes a question. No business logic in this phase.
```

### P1 · Chain data layer

```
Phase P1. Build the chain data layer: docs/03-architecture.md §3 (chain/*, net/*), the cache rules in
docs/01-prd.md F2 and docs/02-methodology.md §3.1, and migration 0001 from docs/05-data-model.md.
Traps: TronGrid answers 403 as well as 429; HyperSync reads oldest-first only and returns hex
timestamps; BSC USDT has 18 decimals, TRON USDT 6; the cache must fetch only gaps (AT-03). Tests
first for every ticket.
```

### P2 · Screening MVP

```
Phase P2: the first real check. Follow docs/02-methodology.md §2 exactly: sources table, R-SAN-01,
R-FRZ-01/02, network failures, verdict precedence. The audit record is written before anything is
shown (CLAUDE.md #2). Traps: OFAC currency labels are unreliable (match the string); the Tether
contract's deprecated() must be checked on every sync; the freeze vendor's null verdict means
INCOMPLETE; BSC token freeze is skipped, not failed. Finish with the live checks in T-2.11.
```

### P3 · Exposure & behaviour

```
Phase P3. Implement docs/02-methodology.md §3 exactly: history reading rules (5,000 cap → INCOMPLETE),
counterparties, local-only flags, R-EXP-01/02, R-HEU-01…05, allowlist exclusion, finding caps.
Pass-through is FIFO as in §3.5. Boundary tests for every threshold. Verify VS-10 first.
```

### P4 · Intelligence store

```
Phase P4. Build intel/: categories (methodology §8), labels with provenance and retraction, entities,
the counterparty registry (rebuildable from the audit log, AT-28), licensed label packs, and the
look-alike guard R-HEU-06 (methodology §4). labels.csv keeps its whole-file replace semantics.
```

### P5 · Profiler & classifier

```
Phase P5. profile() and classify() are pure functions: implement docs/02-methodology.md §5 and §6
literally, including confidence bonuses, the primary-type order and the FRESH tag. Verify VS-12/13 for
contract detection first. R-HEU-07 can never be BLOCK (config must refuse it). Use test-author for
boundary tests on every condition (AT-36).
```

### P6 · Source-of-funds trace

```
Phase P6, the core of the product. Implement docs/02-methodology.md §7 literally: windows (§7.2),
weights (§7.3), pruning (§7.4), the terminal test order (§7.5), revisits and cycles (§7.6), failures
(§7.7), output and invariants (§7.8). Use plan mode before T-6.02 and T-6.03. AT-37 (the worked
example) must reproduce exactly; AT-38 are hypothesis property tests. Pruned ≠ failed: budget →
untraced:budget, read failure → INCOMPLETE. Run spec-reviewer on the engine before moving on. Finish
with the VS-07 live budget run.
```

### P7 · Score & reports

```
Phase P7. Implement docs/02-methodology.md §9 exactly (half-up rounding, lower bound for INCOMPLETE,
BLOCK = 100). score_json is hashed only when set (AT-44). Then the case report PDF.
```

### P8 · Operator UX

```
Phase P8. Web UI, batch, watchlist, audit export (docs/01-prd.md F11). Security first: host
allow-list, start-up token on every POST, strict CSP, HTMX vendored (AT-48). CSV export must guard
against formula injection (AT-47). Batch validates every row before screening any.
```

### P9 · Cases & decisions

```
Phase P9. Cases, the decision hash chain (separate from the check chain, linked by check record
hash), confirm/reject inferences with suppression until the classifier version changes, entity
naming, decisions in exports (docs/01-prd.md F12, docs/05-data-model.md 0008).
```

### P10 · Monitoring & local API

```
Phase P10. monitor run with a lock file, rescreen policy and alerts (F13); the local API (F14):
127.0.0.1 only, Bearer token ≥ 32 chars, Idempotency-Key per the IETF draft (replay, 409, 422),
problem+json errors, every verdict HTTP 200. Write docs/api.md and docs/server.md and the corridor
mock script.
```

### P11 · Calibration & 1.0

```
Phase P11. Build the golden set with me (Q-14), record histories for offline replay, run it, and
produce the precision and band report (docs/02-methodology.md §10). Tune config defaults only; any
formula change bumps its version and gets a decision. Then the budget report, operator guide, and
declare the JSON contract stable. Targets are in docs/07-roadmap.md P11.
```
