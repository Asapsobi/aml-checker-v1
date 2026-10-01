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
