# amlcheck — Decision log

> Every decision that shapes the build, ADR-style but short. Claude Code adds one with `/record-decision`
> whenever it chooses between real alternatives. Decisions are easy to reverse: say so in a new entry
> that supersedes the old one, never by editing it.

**Status:** `Accepted` (owner agreed) · `Proposed` (Claude Code's default until the owner says otherwise)
· `Superseded by D-NNN`.

## Template

```markdown
### D-NNN · <title>
- **Status:** Proposed | Accepted | Superseded by D-NNN
- **Date:** YYYY-MM-DD · **Phase:** Pn
- **Context:** why a choice was needed (1–3 lines)
- **Decision:** what was chosen
- **Alternatives:** what else was considered, and why not
- **Consequences:** what this changes (code, config, docs, risks)
```

---

## Seed decisions

| ID | Decision | Why | Status |
|---|---|---|---|
| D-001 | Build from scratch in this repo; no code reused from earlier experiments | Owner request | Accepted |
| D-002 | No own nodes; chain data only from RPC providers and indexers | Constraint C1 | Accepted |
| D-003 | Intelligence only on counterparties and the addresses their traces reach | Constraint C2 | Accepted |
| D-004 | Python 3.12+, uv, Typer, httpx, Pydantic, SQLite (WAL), FastAPI + HTMX, reportlab | Owner's stack; zero-ops local tool | Proposed |
| D-005 | TRON data from TronGrid; BSC data from Envio HyperSync free plan | Complete and free (data sources §6–§7); Etherscan BSC is paid-only | Proposed |
| D-006 | Sanctions from OFAC `SDN.XML`, matched on the address string whatever the currency label | Labels are unreliable (data sources §2) | Accepted |
| D-007 | Sanctions staleness measured from our last successful download (48 h), not OFAC's publish date | OFAC does not publish daily | Proposed |
| D-008 | Issuer freeze history from Eagle Virtual for the **target only**; never for counterparties on the Free plan; answers cached ≤ 15 min and never stored | Quota and licence | Proposed |
| D-009 | On BSC the token freeze check is `skipped` (not a gap); BSC checks can end `NO_HITS`; every BSC result says BEP20 USDT cannot freeze | Contract has no freeze function | Proposed |
| D-010 | A `0x` address frozen or seized on any EVM chain → R-FRZ-01 BLOCK on BSC | Same key controls the address on every EVM chain | Proposed |
| D-011 | Retry-After ≤ 10 s is waited out; longer → source error → INCOMPLETE. Indexer budget pacing may wait ≤ 65 s | Fast operator feedback, but traces must not fail on pacing | Proposed |
| D-012 | 0-USDT transfers are dropped from histories | Address-poisoning spam, not dealings | Proposed |
| D-013 | More transfers than `max_transfers` (5,000) in the lookback → exposure `stale` → INCOMPLETE | Never a clean result over part of a history | Proposed |
| D-014 | R-EXP-01 is REVIEW by default (configurable to BLOCK) | A counterparty's sin is not the address's own | Proposed |
| D-015 | Flags on neighbours come from local data only (sanctions snapshot, freeze index, labels) | No API quota spent on neighbours | Proposed |
| D-016 | Proportional (haircut) trace shares are shown as **estimated**, always next to absolute bottleneck amounts; blocking-grade trace rules use amounts | Money is fungible inside a wallet | Proposed |
| D-017 | Inferred classifications never produce BLOCK; R-HEU-07 and R-TRC-05 cannot be raised to BLOCK | An inference must not look like a fact | Proposed |
| D-018 | Audit log: hash-chained, append-only, no delete path; nullable fields hashed only when set; decisions in a second chain | Tamper evidence that survives schema growth | Proposed |
| D-019 | Exit codes: 0 NO_HITS, 1 could not run, 3 REVIEW, 4 INCOMPLETE, 5 BLOCK, 6 a watched or monitored verdict needs attention | Scripts can react | Proposed |
| D-020 | Data, config and logs in `~/.amlcheck/` (`AMLCHECK_HOME`); keys only from environment and `.env` files | Works the same on laptop and headless server | Proposed |
| D-021 | Web UI and API bound to 127.0.0.1, host allow-list, token on every write, strict CSP, no CDN | Any page in the browser can reach localhost | Proposed |
| D-022 | Every release tagged `vX.Y.Z`; each check records `tool_version` | Records from different code must never share a version | Proposed |
| D-023 | Results are for internal use only; no resale or client-facing reports | Constraint C4; vendor licences | Accepted |

---

## Decisions

### D-024 · Runs on the owner's laptop first (Q-01)
- **Status:** Accepted
- **Date:** 2026-10-01 · **Phase:** P0
- **Context:** Q-01 asked where amlcheck runs day to day.
- **Decision:** The owner's laptop first. A server install guide follows in P10.
- **Alternatives:** Corridor server now, or both from the start. Not needed before monitoring (P10).
- **Consequences:** CI must stay green on macOS. Defaults (`~/.amlcheck/`, macOS notifications) fit a laptop.

### D-025 · Eagle Virtual Free plan (Q-02)
- **Status:** Accepted
- **Date:** 2026-10-01 · **Phase:** P0
- **Context:** Q-02 asked which freeze-vendor plan to use.
- **Decision:** The Free plan (1,000 checks/day, credit line required) until volume needs more.
- **Alternatives:** Business plan. Costs money before we know the volume.
- **Consequences:** Confirms D-008: target-only checks, credit line shown, quota warning. VS-08 confirms the plan terms.

### D-026 · Add UK OFSI if it carries crypto addresses (Q-03)
- **Status:** Accepted
- **Date:** 2026-10-01 · **Phase:** P0
- **Context:** Q-03 asked whether to add the UK OFSI list.
- **Decision:** Yes, if VS-11 shows it carries crypto addresses in a clean format under a usable licence.
- **Alternatives:** OFAC only. Leaves UK-only designations unseen.
- **Consequences:** VS-11 runs before P2 ends. If it passes, an OFSI source is added in P2; if not, a question is raised with the finding.

### D-027 · No placeholder migration in P0
- **Status:** Proposed
- **Date:** 2026-10-01 · **Phase:** P0
- **Context:** T-0.05 asks for "an empty `0001` placeholder", but the data model gives `0001` to
  `0001_cache.sql` (P1). A DB that ran a placeholder 0001 would sit at `user_version = 1` and never run
  the real 0001.
- **Decision:** P0 ships no migration file (latest schema = 0). The runner is tested against temporary
  migration sets instead. P1 adds `0001_cache.sql` as the data model says.
- **Alternatives:** A placeholder later replaced in place (breaks append-only and any P0-era DB); a
  placeholder `0001` with the cache tables shifted to `0002` (renumbers the whole data model).
- **Consequences:** `amlcheck status` on P0 reports schema 0. No doc changes needed beyond the backlog
  wording.
