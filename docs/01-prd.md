# amlcheck — Product Requirements Document

> **Product:** amlcheck, counterparty intelligence for USDT on TRON and BNB Smart Chain
> **Owner:** Sobi · **Status:** v1.0 draft · **Date:** 2026-10-01
> **Audience:** Claude Code (primary builder), owner (reviewer), management (via the [six-pager](00-six-pager.md))
> **This is a from-scratch build.** Nothing here depends on earlier code.

---

## 0. How to read this pack

| Doc | Read it for |
|---|---|
| [00-six-pager.md](00-six-pager.md) | Why we build this, the approach, risks, costs. Management-facing |
| **01-prd.md** (this) | What the product must do |
| [02-methodology.md](02-methodology.md) | Every rule, algorithm, threshold and formula, exactly |
| [03-architecture.md](03-architecture.md) | Stack, modules, interfaces, flows |
| [04-data-sources.md](04-data-sources.md) | Providers, known facts, what must be verified live |
| [05-data-model.md](05-data-model.md) | The full SQLite schema |
| [06-acceptance-tests.md](06-acceptance-tests.md) | The tests that define "done" |
| [07-roadmap.md](07-roadmap.md) | Phases P0–P11 and their exit criteria |
| [08-backlog.md](08-backlog.md) | Ticket-sized work for Claude Code |
| [09-decisions.md](09-decisions.md) | Decision log (ADR-style) |
| [10-open-questions.md](10-open-questions.md) | Questions for the owner, with proposed answers |
| [claude-code/playbook.md](claude-code/playbook.md) | How to build this with Claude Code |

---

## 1. Summary

amlcheck is a **local tool that screens and investigates every address the business transacts with**
in USDT on TRON (TRC20) and BNB Smart Chain (BEP20). For each counterparty it answers four questions:

| Question | Output |
|---|---|
| Can I transact with this address right now? | A verdict: `BLOCK` / `REVIEW` / `INCOMPLETE` / `NO_HITS`, with evidence |
| Where did its money come from? | A 3-hop source-of-funds trace: exposure share per risk category, plus an explicit untraced share |
| Who is it, probably? | An inferred type (exchange hub, deposit address, collector, pass-through, …) with confidence |
| How risky is it overall, and what did we decide? | A 0–100 score with a breakdown, and a recorded human decision |

It reads chain data **only from RPC providers and indexers** (no own nodes), and builds intelligence
**only about its own counterparties and the addresses their traces reach** (no whole-chain labelling).
Everything learned is kept locally, so the tool gets better with every check.

---

## 2. Context

The business runs a USDT settlement corridor (BEP20 → TRC20) and OTC flows. Every inbound payment
comes from, and every outbound payment goes to, an address the business does not control. Receiving
funds from a sanctioned wallet, from frozen or stolen funds, or from a scam operation exposes the
business to frozen balances, lost banking and exchange relationships, and legal risk.

Commercial blockchain-analytics tools (Chainalysis, TRM Labs, Elliptic, Crystal, AMLBot) solve this
with whole-chain indexing and large attribution teams. They are priced for exchanges and banks. The
business needs most of their **depth** on a **narrow scope**: one token, two chains, its own
counterparties.

---

## 3. Constraints (from the owner)

| # | Constraint | Consequence |
|---|---|---|
| C1 | No own blockchain nodes | All chain data comes from RPC providers / indexers. Every deep read has a call budget |
| C2 | Don't label the whole blockchain | Only counterparties and the addresses their traces reach are profiled and kept |
| C3 | Local-first | Runs on a laptop or one small server. SQLite. No cloud service of our own |
| C4 | Internal use | Results are not resold or shown to clients (affects data licences) |
| C5 | Free or low-cost data first | Free tiers where they are complete; paid tiers only by owner decision |
| C6 | Built with Claude Code | Docs are the spec. Every phase is reviewable in one PR |

---

## 4. Goals & non-goals

### Goals

| ID | Goal | Measure |
|---|---|---|
| G1 | Fast screening verdict | p95 < 10 s without exposure, < 60 s with 1-hop exposure |
| G2 | Every result explains itself | Each finding has a source, a timestamp and evidence (tx hash, list entry, block) |
| G3 | Never a false "clean" | A failed or stale required source can never produce `NO_HITS` |
| G4 | Tamper-evident record | Every check is in a hash-chained, append-only audit log, exportable |
| G5 | Explain source of funds | Default trace attributes ≥ 80% of inflow value to a category or a declared untraced reason |
| G6 | Recognise unknown addresses | Each traced node gets a type with confidence and the features behind it |
| G7 | Knowledge compounds | A repeat trace uses ≥ 50% fewer API queries than the first |
| G8 | Stay within free budgets | Default trace ≤ 120 BSC indexer queries and ≤ 200 TRON API requests, cold |
| G9 | Record the human decision | Every REVIEW can be closed with a decision that is exported with the audit log |
| G10 | Find counterparties automatically | A new sender to an own wallet is screened within one monitoring interval |

### Non-goals

| Item | Why |
|---|---|
| Own nodes, whole-chain index, whole-chain labels | C1, C2 |
| Other chains or tokens | Scope. The chain adapter boundary keeps it possible later |
| Cross-chain tracing through bridges and swaps | Needs more chains. A known bridge is a terminal category only |
| Machine-learning models | Too few labelled cases at start. Cases are stored so a model can be trained later |
| KYC / name screening of people and companies | Different problem |
| Automatic blocking of payments | The tool advises, a human decides |
| Multi-user accounts, hosting, resale | C3, C4 |

---

## 5. Users & use cases

| ID | User | Use case | Phase |
|---|---|---|---|
| U1 | Operator | "Before I send 50k USDT to this TRON address, can I?" → single check | P2 |
| U2 | Operator | Screen the sender of funds that just arrived | P2 |
| U3 | Operator | See who the address dealt with and how it behaves | P3 |
| U4 | Operator | Name or tag addresses ("this is our OTC partner", "this is a mixer") | P4 |
| U5 | Operator | About to pay a look-alike of a known counterparty → warned | P4 |
| U6 | Operator | "What is this unknown address?" → profile and inferred type | P5 |
| U7 | Operator | "Where did this client's 30k come from?" → trace with graph and breakdown | P6 |
| U8 | Management | One-file case report per counterparty | P7, P9 |
| U9 | Operator | Batch-screen a CSV, re-screen approved addresses on a schedule, export history | P8 |
| U10 | Operator | Close a REVIEW with a decision and note; confirm or reject inferences | P9 |
| U11 | Operator | Be told when someone new pays an own wallet, already screened | P10 |
| U12 | Corridor system | Call the checker over a local API before settling | P10 |

---

## 6. Concepts

| Term | Meaning |
|---|---|
| **Check** | One screening of one address at one time. Produces a verdict, findings, optional trace and score. Written to the audit log |
| **Source** | A data provider wrapped in an adapter (sanctions list, freeze data, chain history). Each source is `ok`, `error`, `stale` or `skipped` in a check |
| **Finding** | A rule that fired: rule ID, severity, source, plain-English summary, evidence |
| **Verdict** | `BLOCK`, `REVIEW`, `INCOMPLETE` or `NO_HITS` (§7) |
| **Counterparty** | An address that has been checked for the business, or that paid an own wallet |
| **Node** | Any address a trace reaches |
| **Profile** | Features computed from an address's cached history ([methodology §5](02-methodology.md)) |
| **Classification** | An inferred type with confidence, features, classifier version and expiry |
| **Entity** | A group of addresses believed to share one owner (an exchange hub and its deposit addresses) |
| **Label** | A category on an address or entity, with provenance: `list`, `operator`, `import` or `inferred` |
| **Exposure share** | Estimated fraction of inflow value from a category, by proportional attribution |
| **Coverage** | Share of inflow value that reached a terminal with a known category |
| **Case** | The operator's review of a counterparty, closed with a decision |

---

## 7. Verdict model & rules

### 7.1 Verdicts

| Verdict | Meaning | Operator action |
|---|---|---|
| `BLOCK` | A blocking rule fired | Do not transact. Escalate |
| `INCOMPLETE` | A required source failed or is stale | Retry, or treat as REVIEW |
| `REVIEW` | Risk findings, none blocking | Review by hand before transacting |
| `NO_HITS` | Nothing found **in the sources checked, as of the times shown** | Proceed per policy. **Not a clearance** |

**Precedence:** `BLOCK` > `INCOMPLETE` > `REVIEW` > `NO_HITS`. A sanctions hit blocks even if another
source failed.

### 7.2 Rules

All defaults live in config. Severities can be changed in config, except where noted. Exact
definitions and thresholds: [methodology §2–§4](02-methodology.md).

| Rule | Condition (short) | Default | Phase |
|---|---|---|---|
| R-SAN-01 | Address is on a sanctions list | BLOCK | P2 |
| R-FRZ-01 | An issuer freezes the address now, or seized its funds | BLOCK | P2 |
| R-FRZ-02 | Address was frozen and later released | REVIEW | P2 |
| R-SYS-01 | A required source errored, timed out or is stale | INCOMPLETE (fixed) | P2 |
| R-EXP-01 | A direct counterparty is sanctioned or frozen | REVIEW | P3 |
| R-EXP-02 | ≥ 5% of inflow (180 d) came from flagged direct counterparties | REVIEW | P3 |
| R-HEU-01 | First activity < 7 days ago | REVIEW (low) | P3 |
| R-HEU-02 | Pass-through: ≥ 90% of inflow leaves within 24 h | REVIEW | P3 |
| R-HEU-03 | Fan-in: > 50 senders of < 100 USDT within 24 h | REVIEW | P3 |
| R-HEU-04 | Fan-out: > 50 recipients within 24 h | REVIEW | P3 |
| R-HEU-05 | Dealt with an address labelled mixer, bridge or high-risk | REVIEW | P3 |
| R-HEU-06 | Looks like a known counterparty but isn't (address poisoning) | REVIEW | P4 |
| R-HEU-07 | The address itself is classified collector or distributor (≥ 0.7) | REVIEW (low), never BLOCK | P5 |
| R-TRC-01 | Trace reaches a sanctioned wallet at hop 2–3, bottleneck ≥ 1,000 USDT | REVIEW | P6 |
| R-TRC-02 | Same for a frozen or seized wallet | REVIEW | P6 |
| R-TRC-03 | Estimated exposure to high-risk categories (lists, operator labels) ≥ 5% | REVIEW | P6 |
| R-TRC-04 | Coverage < 50% on a large check | REVIEW (low) | P6 |
| R-TRC-05 | Estimated exposure to inferred suspicious patterns ≥ 10% | REVIEW (low), never BLOCK | P6 |
| R-SCR-01 | Score ≥ configured threshold | Off by default | P7 |

"(low)" marks a REVIEW as low priority. It does not change the verdict.

---

## 8. Functional requirements

### F1 · Addresses and chains (P2)

| ID | Requirement |
|---|---|
| F1.1 | Detect the chain from the format: TRON base58check `T…` (34 chars), EVM `0x` + 40 hex. `--chain` overrides for EVM |
| F1.2 | Normalise: TRON validated base58check; EVM lowercase hex. A mixed-case EVM address must pass its EIP-55 checksum, else error |
| F1.3 | Invalid input → error, no audit record, exit 1 |

### F2 · Chain data layer (P1)

| ID | Requirement |
|---|---|
| F2.1 | `HistorySource` per chain returns USDT transfers for an address within a time window, newest first, up to a limit |
| F2.2 | Transfers are stored once in a persistent cache; each address has recorded covered windows; later reads fetch only the gaps |
| F2.3 | 0-USDT transfers are dropped (address-poisoning spam) and counted |
| F2.4 | When a window holds more than the limit, the newest are read and the window is marked incomplete (never silently partial) |
| F2.5 | Every provider call goes through a process-wide rate limiter / budget pacer per provider |
| F2.6 | Contract detection per chain, cached forever |
| F2.7 | `amlcheck cache stats` and `amlcheck cache prune` |

### F3 · Sanctions (P2)

| ID | Requirement |
|---|---|
| F3.1 | Download the OFAC SDN list, parse every digital-currency address whatever its currency label, store with entry ID, entity name, program, snapshot hash and publish date |
| F3.2 | Match on the normalised address string; an EVM address listed under any EVM currency matches on BSC |
| F3.3 | Freshness = time since our last successful download; > 48 h → `stale` → INCOMPLETE |
| F3.4 | Sanity check: a new snapshot with > 20% fewer addresses than the last is rejected and logged |
| F3.5 | Optional extra lists (UK OFSI etc.) only after their format and licence are verified |

### F4 · Issuer freezes (P2)

| ID | Requirement |
|---|---|
| F4.1 | TRON: local index of Tether USDT contract events (blacklist added/removed, funds destroyed), refreshed incrementally on every TRON check and by `sync` |
| F4.2 | Live spot-check `isBlackListed` for the target on TRON |
| F4.3 | EVM: local index of Tether USDT and Circle USDC blacklist events on the EVM chains verified in VS-15, read through HyperSync and refreshed incrementally; a `0x` target listed on any of them → R-FRZ-01 on BSC (D-010, D-034). A chain that can't be refreshed and lags > 60 min → INCOMPLETE |
| F4.4 | BEP20 USDT on BSC has no freeze function: the token freeze check reports `skipped`, and every BSC result says so |
| F4.5 | No third-party AML, screening or freeze API: freeze data comes only from issuer contracts, read through RPC providers and indexers (D-033) |

### F5 · Exposure & behaviour (P3)

| ID | Requirement |
|---|---|
| F5.1 | 1-hop walk over 180 days: counterparties with amounts in and out, flags from local data only (sanctions, freeze index, labels) |
| F5.2 | Rules R-EXP-01/02, R-HEU-01–05 per methodology §3 |
| F5.3 | `labels.csv` import (`address,chain,tag,note,source`), whole-file replace, all-or-nothing |
| F5.4 | Tag `allowlist` excludes a counterparty from behaviour rules, never from sanctions/freeze findings |
| F5.5 | At most 10 R-EXP-01 and 10 R-HEU-05 findings per check, largest first |

### F6 · Audit log (P2)

| ID | Requirement |
|---|---|
| F6.1 | Every check is appended before its result is shown: inputs, sources with status and evidence, findings, verdict, tool version, config hash, client, note |
| F6.2 | `record_hash = sha256(prev_hash + canonical_json(record))`; no update or delete path in code |
| F6.3 | `audit verify` recomputes the chain and reports the first break; prints the head hash to keep elsewhere |
| F6.4 | New hashed fields are hashed only when set, so older records keep verifying |
| F6.5 | `audit list` and `audit export` (CSV, JSON with hashes, PDF) filter by date, address, verdict, client |

### F7 · Intelligence store, registry, look-alikes (P4)

| ID | Requirement |
|---|---|
| F7.1 | Labels with category, provenance, source reference, confidence, licence, author, time; retracted, never deleted |
| F7.2 | Entities: groups of addresses with a name and kind; members with role and evidence |
| F7.3 | Counterparty registry upserted after every check; `cp list/show/rebuild` (rebuild from the audit log gives the same rows) |
| F7.4 | Label packs import only with an explicit licence |
| F7.5 | R-HEU-06 compares the target with registry addresses, own wallets and trusted labels by look-alike key |

### F8 · Profiler & classifier (P5)

| ID | Requirement |
|---|---|
| F8.1 | Pure `profile()` over cached transfers; features in methodology §5 |
| F8.2 | `classify()` assigns types with confidence and the features that fired; versioned; expires after 14 days |
| F8.3 | A deposit address joins its hub's entity; unnamed hubs get an auto-named entity |
| F8.4 | Operator labels and lists always win over inferences |
| F8.5 | `amlcheck classify <address>` and a profile panel in the web UI |

### F9 · Source-of-funds trace (P6)

| ID | Requirement |
|---|---|
| F9.1 | Backward (and forward) trace up to 3 hops: time windows, proportional weights, value-weighted pruning, terminal tests, cycles (methodology §6) |
| F9.2 | Output: nodes, edges, partition of inflow value into categories and untraced reasons summing to 1, paths with bottleneck amounts |
| F9.3 | Budgets from config; a failed node read or time-out → INCOMPLETE; reaching the node budget → declared `untraced:budget` |
| F9.4 | Runs on `amlcheck trace`, `investigate`, from the web form, for checks ≥ 10,000 USDT, and through the API |
| F9.5 | Long traces run as jobs with progress, one worker, resumable from cache |
| F9.6 | Layered SVG graph: hop columns, category marks with a legend, edge width by amount, look-alikes distinguishable |

### F10 · Score & reports (P7)

| ID | Requirement |
|---|---|
| F10.1 | Score 0–100 and band per methodology §8; BLOCK ⇒ 100; INCOMPLETE ⇒ shown as a lower bound |
| F10.2 | Score never changes the verdict unless R-SCR-01 is enabled |
| F10.3 | Shown in CLI, JSON, web, batch output, API; stored with the check |
| F10.4 | Case report PDF: verdict, score, sources, findings, trace graph, exposure table, classifications, decision |

### F11 · Operator UX (P8)

| ID | Requirement |
|---|---|
| F11.1 | CLI with human output (Rich) and `--json`; exit codes: 0 NO_HITS, 3 REVIEW, 4 INCOMPLETE, 5 BLOCK, 1 could not run, 6 a watched verdict changed |
| F11.2 | `batch` from CSV: validate every row first, then screen one at a time within rate limits, stream results to CSV |
| F11.3 | Watchlist: `watch add/remove/list/run`; changed verdicts reported and recorded |
| F11.4 | Local web UI on 127.0.0.1 only: check form, history, detail with explorer links, counterparties, trace view |
| F11.5 | Web security: host allow-list (127.0.0.1, localhost), token on every POST, strict CSP, no CDN |

### F12 · Cases & decisions (P9)

| ID | Requirement |
|---|---|
| F12.1 | Open a case from a REVIEW/BLOCK check; close with `approved` / `rejected` / `escalated`, a note and the operator name |
| F12.2 | Decisions are append-only in their own hash chain, linked to the check's record hash; `audit verify` covers both chains |
| F12.3 | In a case: confirm or reject inferred types (confirm → operator label; reject → suppressed until the classifier version changes); name entities |
| F12.4 | Exports include decisions; `case export --jsonl` for future model training |

### F13 · Monitoring (P10)

| ID | Requirement |
|---|---|
| F13.1 | Register own wallets; they are labelled trusted |
| F13.2 | `monitor run` reads new inbound USDT transfers, screens new senders (not re-screened within 7 days), traces transfers ≥ 10,000 USDT |
| F13.3 | Reports like `watch run`; optional local webhook; lock file so runs never overlap |
| F13.4 | Scheduling guides for launchd, cron and systemd |

### F14 · Local API (P10)

| ID | Requirement |
|---|---|
| F14.1 | `POST /v1/check` (same JSON as `check --json`), `POST /v1/traces` + `GET /v1/traces/{id}`, `GET /v1/counterparties/{chain}/{address}`, `GET /v1/checks/{id}` |
| F14.2 | Bound to 127.0.0.1, Bearer token ≥ 32 chars from `.env`, host allow-list |
| F14.3 | `Idempotency-Key` per the IETF draft: replay returns the same result; same key, different payload → 422; in progress → 409 |
| F14.4 | Errors as `application/problem+json`; every verdict, INCOMPLETE included, is HTTP 200 |

---

## 9. Non-functional requirements

| Area | Requirement |
|---|---|
| Correctness | G3 is unit-tested for every source. Trace partition sums to 1 ± 0.001 |
| Determinism | Same cache + config ⇒ identical trace, classifications and score. Every sort has a tie-break |
| Explainability | Every number links to evidence |
| Performance | G1; trace p95 ≤ 5 min cold on BSC free tier, ≤ 90 s warm |
| Budgets | G8; hard caps in config; pacers shared process-wide |
| Storage | SQLite (WAL), one file in `~/.amlcheck/`; designed for ~1M cached transfers |
| Security | Secrets in `.env` only, never logged; servers bound to localhost; no telemetry |
| Portability | macOS and Linux; Windows best-effort |
| Observability | JSON logs, rotated, in `~/.amlcheck/logs/` |
| Quality | Python 3.12+, `mypy --strict`, ruff, ≥ 85% coverage on core logic, tests never use the network |

---

## 10. Success metrics (at v1.0)

| Metric | Target |
|---|---|
| Classifier precision: HUB, DEPOSIT | ≥ 0.9 on the golden set |
| Classifier precision: COLLECTOR | ≥ 0.8 |
| Clean golden addresses scored high/severe | 0 |
| Default trace budget | Within G8 |
| Repeat-trace saving | ≥ 50% fewer queries (G7) |
| Operator time per REVIEW | Case closed in < 10 min with report |

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| `NO_HITS` read as "clean" | Wording, disclaimer on every result, name is not "CLEAR" |
| Proportional shares read as exact | Shown as "estimated share" next to absolute bottleneck amounts; blocking-grade rules use amounts |
| Inferred types wrong | Never BLOCK, confidence shown, operator can reject, versioned classifier |
| Free tiers too slow or change | Budgets, cache, job mode; adapters swappable; paid tier is an owner decision |
| Provider shuts down or changes API | Adapter pattern, recorded fixtures catch drift, verification re-run per phase |
| Licence breach | Third-party answers never stored; label packs need a licence; internal use only (C4) |
| Scope creep toward full Chainalysis | Non-goals; anything outside becomes a question, not code |
