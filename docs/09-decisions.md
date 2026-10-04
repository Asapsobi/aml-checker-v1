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
| D-008 | Issuer freeze history from Eagle Virtual for the **target only**; never for counterparties on the Free plan; answers cached ≤ 15 min and never stored | Quota and licence | Superseded by D-033 |
| D-009 | On BSC the token freeze check is `skipped` (not a gap); BSC checks can end `NO_HITS`; every BSC result says BEP20 USDT cannot freeze | Contract has no freeze function | Proposed |
| D-010 | A `0x` address frozen or seized on any EVM chain → R-FRZ-01 BLOCK on BSC | Same key controls the address on every EVM chain | Deferred by D-039 (no other-chain data in v1) |
| D-011 | Retry-After ≤ 10 s is waited out; longer → source error → INCOMPLETE. Indexer budget pacing may wait ≤ 65 s | Fast operator feedback, but traces must not fail on pacing | Proposed |
| D-012 | 0-USDT transfers are dropped from histories | Address-poisoning spam, not dealings | Proposed |
| D-013 | More transfers than `max_transfers` (5,000) in the lookback → exposure `stale` → INCOMPLETE | Never a clean result over part of a history | Proposed |
| D-014 | R-EXP-01 is REVIEW by default (configurable to BLOCK) | A counterparty's sin is not the address's own | Accepted (Q-04, 2026-10-03) |
| D-015 | Flags on neighbours come from local data only (sanctions snapshot, freeze index, labels) | No API quota spent on neighbours | Proposed |
| D-016 | Proportional (haircut) trace shares are shown as **estimated**, always next to absolute bottleneck amounts; blocking-grade trace rules use amounts | Money is fungible inside a wallet | Accepted (Q-07, 2026-10-03) |
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
- **Status:** Superseded by D-033
- **Date:** 2026-10-01 · **Phase:** P0
- **Context:** Q-02 asked which freeze-vendor plan to use.
- **Decision:** The Free plan (1,000 checks/day, credit line required) until volume needs more.
- **Alternatives:** Business plan. Costs money before we know the volume.
- **Consequences:** Confirms D-008: target-only checks, credit line shown, quota warning. VS-08 confirms the plan terms.

### D-026 · Add UK OFSI if it carries crypto addresses (Q-03)
- **Status:** Deferred by D-041
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

### D-028 · Target history TTL 60 s (Q-15)
- **Status:** Accepted
- **Date:** 2026-10-01 · **Phase:** P0
- **Context:** `[cache] target_ttl_seconds` had no default in the docs.
- **Decision:** 60 s. A check re-reads the target's newest transfers when the cached window ends more
  than a minute ago.
- **Alternatives:** 0 (always re-read: more provider calls on repeated checks); minutes (a check could
  miss a payment that just arrived).
- **Consequences:** Default in `config.py` and `config.example.toml`. Used from P1 (`chain/cache.py`).

### D-029 · Network and quota defaults (Q-16)
- **Status:** Accepted
- **Date:** 2026-10-01 · **Phase:** P0
- **Context:** The docs give no value for `[network] timeout_seconds`, `[tron] requests_per_second` or
  `[eagle_virtual] quota_warn_share`.
- **Decision:** 20 s; 10 requests/s until VS-05 measures the owner's TronGrid key; warn at 80% of the
  freeze vendor's daily quota.
- **Alternatives:** Shorter timeouts (false `error`s on slow pages); no quota warning (the operator
  finds out at the limit).
- **Consequences:** Defaults in config. `[tron] requests_per_second` is revisited after VS-05.

### D-030 · TRON transfer identity without an event index (Q-17)
- **Status:** Accepted
- **Date:** 2026-10-01 · **Phase:** P0
- **Context:** TronGrid transfer rows have no event index, and one transaction can hold several USDT
  transfers that appear differently in the sender's and the recipient's history (VS-04). A per-history
  ordinal would store one transfer twice or merge two.
- **Decision:** On TRON, `idx` = the number of earlier rows of the same transaction with the identical
  (`sender`, `recipient`, `amount`). The `transfers` key becomes (`chain`, `tx_hash`, `sender`,
  `recipient`, `amount`, `idx`). On BSC `idx` stays the log index.
- **Alternatives:** Fetch `/v1/transactions/{tx}/events` for exact indexes (one more call per
  transaction: too expensive for histories); ordinal within the tx (not stable, see context).
- **Consequences:** Data model `0001_cache.sql` updated before it is written (P1). Exact event indexes
  only if a later phase needs them.

### D-031 · Rate-limit answer without Retry-After (Q-18)
- **Status:** Superseded by D-036
- **Date:** 2026-10-01 · **Phase:** P0
- **Context:** Keyless TronGrid answers 429 with no `Retry-After`; the wait (5 s) is only in the message
  (VS-05). D-011 assumed the header.
- **Decision:** A 429 (or TronGrid 403) without `Retry-After`, where no budget pacer applies, waits 5 s
  and retries once; a second refusal is a source `error`. The indexer budget pacer (HyperSync) is
  unchanged.
- **Alternatives:** Treat as an immediate error (more INCOMPLETE checks for a 5 s pause); parse the wait
  from the message (fragile wording).
- **Consequences:** Methodology §2.4 updated. `net/http.py` (T-1.01). Re-checked when VS-05 runs with a key.

### D-032 · Eagle Virtual CLEAR beside chains that are behind (Q-19)
- **Status:** Superseded by D-033
- **Date:** 2026-10-01 · **Phase:** P0
- **Context:** Since API 1.3.0 (2026-09-28) Eagle Virtual answers `CLEAR` for the chains that are
  current and lists the ones behind in `coverage.not_vouched_for` (VS-08). Reading only `verdict` would
  give a clean result over a gap.
- **Decision:** The source is `stale` (→ INCOMPLETE) when the verdict is `null` or the target's own
  chain (TRON; BNB Chain for BSC) is in `not_vouched_for`. Other chains behind are noted in the
  evidence and are not a gap.
- **Alternatives:** Any chain behind is a gap (frequent INCOMPLETE for chains we don't transact on);
  ignore `not_vouched_for` (breaks non-negotiable 1).
- **Consequences:** Methodology §2.1 and PRD F4.3 updated. Freeze-vendor adapter (T-2.07) reads
  `not_vouched_for` on every answer; VS-08 must confirm the `chain_id` values for TRON and BNB Chain.

### D-033 · No third-party AML, screening or freeze API
- **Status:** Accepted
- **Date:** 2026-10-01 · **Phase:** P0
- **Context:** The owner gives keys only for RPC providers and blockchain indexers, not for another
  AML checker. Eagle Virtual was the freeze source for both chains (D-008, D-025, D-032).
- **Decision:** amlcheck uses only public lists (OFAC, later OFSI) and RPC providers / indexers
  (TronGrid, HyperSync, public BSC RPC). No third-party AML, screening or freeze API.
- **Alternatives:** Keep Eagle Virtual on the Free plan (owner declined).
- **Consequences:** Supersedes D-008, D-025 and D-032; the quota part of D-029 lapses. `[eagle_virtual]`
  config and `AMLCHECK_EAGLE_VIRTUAL_KEYS` removed; VS-08 and VS-09 dropped. TRON keeps its own Tether
  index (F4.1, F4.2). BSC freezes come from D-034. PRD F4.3/F4.5, methodology §2.1/§2.3, data sources
  §1/§5, acceptance AT-11–AT-13 and AT-16, backlog T-2.07, CLAUDE.md #7 updated.

### D-034 · Own EVM freeze index (Tether, Circle) through HyperSync
- **Status:** Superseded by D-039
- **Date:** 2026-10-01 · **Phase:** P0
- **Context:** Without a third-party API (D-033), a `0x` address frozen by Tether or Circle on another
  EVM chain would pass a BSC check, since BEP20 USDT can't freeze.
- **Decision:** Index Tether USDT and Circle USDC blacklist events on the EVM chains where they can
  freeze, through HyperSync with the same token as BSC data, like the TRON index. Any add without a
  later removal → R-FRZ-01 on BSC (D-010); added then removed → R-FRZ-02. A chain lagging > 60 min that
  can't be refreshed → `stale` → INCOMPLETE. Contracts, events and chains are confirmed by VS-15
  before code.
- **Alternatives:** TRON only, with BSC reporting cross-chain freezes as not checked (misses a `0x`
  address Tether froze on Ethereum).
- **Consequences:** New `screening/evm_freeze.py` (T-2.07) and VS-15 in P2. `issuer_events` holds both
  indexes. Being local data, the index also flags BSC counterparties (methodology §3.3) and trace
  terminals (§7.5 test 3), which the Free-plan API could not. Other issuers (AUSD, XUSD, USD0, …) are
  not covered.

### D-035 · Refuse databases this amlcheck did not create
- **Status:** Proposed
- **Date:** 2026-10-01 · **Phase:** P0
- **Context:** An older tool also called amlcheck keeps its DB at `~/.amlcheck/amlcheck.db` (our
  default home, D-020) with `user_version` 4. From P1 on, our runner would read it as our schema and
  run later migrations on it.
- **Decision:** A new DB is marked with `PRAGMA application_id = 0x616D6C63` ("amlc"). The runner
  refuses any DB without that mark that has tables or a non-zero `user_version`, and changes nothing.
- **Alternatives:** A different default home (diverges from D-020 and the docs); trusting
  `user_version` alone (the clash above).
- **Consequences:** `amlcheck status` exits 1 with a clear message while the old DB sits in
  `~/.amlcheck/`. The owner moves it or sets `AMLCHECK_HOME`.

### D-036 · TronGrid key suspension (Q-20)
- **Status:** Accepted
- **Date:** 2026-10-01 · **Phase:** P0
- **Context:** The owner's TronGrid key allows 15 requests/s; going over answers 429 without
  `Retry-After` and suspends the key for 30 s (VS-05). D-031's single 5 s wait would always fail.
- **Decision:** Prevent it: one process-wide TronGrid limiter at `[tron] requests_per_second` = 10. If
  a 429/403 without `Retry-After` still comes, a **check** treats the source as `error` at once (a 30 s
  wait breaks the 10 s rule of D-011). **Traces and syncs** wait it out through the pacer, for the seconds
  in "suspended for N s" or 30 s if absent, within the 65 s pacer limit.
- **Alternatives:** D-031 (too short a wait); always wait 30 s (slow checks for the operator).
- **Consequences:** Supersedes D-031. Methodology §2.4 updated. `net/http.py` and `net/limits.py` (T-1.01,
  T-1.02).

### D-037 · No `http_cache` table
- **Status:** Accepted
- **Date:** 2026-10-01 · **Phase:** P1
- **Context:** `http_cache` in `0001_cache.sql` held third-party freeze answers for ≤ 15 min. D-033
  removed the only source that needed it.
- **Decision:** `0001_cache.sql` creates `transfers`, `history_windows` and `contracts` only.
- **Alternatives:** Keep the table unused (dead schema that migrations must carry forever).
- **Consequences:** Data model updated. A later short-lived cache, if ever needed, comes as a new migration.

### D-038 · Address validation moves to P1
- **Status:** Accepted
- **Date:** 2026-10-01 · **Phase:** P1
- **Context:** `amlcheck history <addr>` (P1) must detect and validate the address, but `core/address.py`
  was ticket T-2.01 in P2.
- **Decision:** Build `core/address.py` in P1 as T-1.00 with its unit tests; P2 wires it into `check`
  (T-2.01) and covers the audit-record parts of AT-07 and AT-08.
- **Alternatives:** A throwaway check inside `history` (two validators to keep in sync).
- **Consequences:** Backlog updated. P1 adds `base58` and `eth-hash` (architecture §1).

### D-039 · v1 covers TRC20 and BEP20 only
- **Status:** Accepted
- **Date:** 2026-10-01 · **Phase:** P2
- **Context:** VS-15 started verifying Tether and Circle freeze contracts on six other EVM chains for the
  EVM freeze index (D-034). The owner: too wide for this version; stay on TRC20 and BEP20.
- **Decision:** v1 indexes issuer freezes on TRON only (Tether's contract). BEP20 USDT can't freeze, so
  the BSC freeze source is always `skipped`, and its reason says freezes of the same `0x` address on
  other chains are not checked in v1. No EVM freeze index; D-010 waits for a later version.
- **Alternatives:** D-034's index on six chains (more coverage, more scope, more verification).
- **Consequences:** Supersedes D-034; defers D-010. T-2.07 and VS-15 dropped (findings logged for later);
  AT-11/AT-12 dropped, AT-13 and AT-16 now about the TRON index, AT-19 names the gap. PRD F4.3,
  methodology §2.1/§2.3/§3.3/§7.5/§8, data sources §1/§4/§5/§9, `[freshness] evm_index_max_lag_minutes`
  removed. A BSC check can end `NO_HITS` without any freeze source; the result says what wasn't checked.

### D-040 · Download the zipped OFAC list and resume after stalls
- **Status:** Proposed
- **Date:** 2026-10-03 · **Phase:** P2
- **Context:** Live, `amlcheck sync` failed: the 29 MB `SDN.XML` took 35 min on a 12 KB/s line,
  stalled past the 20 s read timeout, and every retry started from zero. A stale list makes every check
  INCOMPLETE after 48 h.
- **Decision:** `[ofac] sdn_url` defaults to `SDN_XML.ZIP` (the same file, 2.6 MB; VS-01 addendum);
  `sync` unpacks it (a plain XML URL still works) and keeps the hash of the XML. Large downloads resume
  with `Range` after a stall and restart only if the server's copy changed; they give up after five
  attempts in a row without new bytes.
- **Alternatives:** a longer timeout alone (still restarts 29 MB); OFAC's daily delta feed (another
  format to verify and maintain).
- **Consequences:** `net/http.download`; `screening/sanctions.unpack`; config default changed (config
  hash changes). Data sources §2 updated.

### D-041 · UK OFSI list left for after v1
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P2
- **Context:** D-026 would add OFSI if VS-11 found crypto addresses under a clean licence. The owner wants a
  tight v1 (see D-039).
- **Decision:** No OFSI in v1; VS-11 is not run now. v1 screens against OFAC SDN only.
- **Alternatives:** Run VS-11 and add OFSI in P2 (more coverage, more scope).
- **Consequences:** Defers D-026. PRD F3.5 stays optional. Revisit after v1.0.

### D-042 · "Flagged" counterparties for R-EXP-02
- **Status:** Proposed
- **Date:** 2026-10-03 · **Phase:** P3
- **Context:** R-EXP-02 is "≥ 5% of USDT received came from flagged counterparties", and methodology §3.3
  lists the flags as `sanctioned`, `frozen` and `label:<tag>`. Read literally, an `allowlist` label
  would count as a flag.
- **Decision:** For R-EXP-02 a counterparty is flagged when it is sanctioned, frozen (TRON index), or
  carries a tag in `[heuristics] risky_tags` (mixer, bridge, high_risk). `allowlist` and free-text tags
  never flag.
- **Alternatives:** any label (would count trusted wallets as risk); sanctioned/frozen only (ignores the
  operator's own risk labels).
- **Consequences:** `screening/exposure.py`. A risky-tagged counterparty can raise both R-EXP-02 (share)
  and R-HEU-05 (dealt with it).

### D-043 · Cache retention for addresses nobody needs (Q-05)
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P4
- **Context:** Q-05: how long to keep cached histories of addresses that are not counterparties,
  labelled or own wallets.
- **Decision:** 30 days unused (`[cache] history_keep_days`). Registry counterparties, labelled addresses
  (labels.csv and active intel labels) and entity members are kept regardless; own wallets join in P10.
- **Alternatives:** Keep everything (unbounded growth); a shorter period (more provider reads).
- **Consequences:** `cache prune`'s keep rule (T-4.07).

### D-044 · R-TRC-01 is REVIEW (Q-08)
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P6
- **Context:** Q-08: a sanctioned wallet 2–3 hops upstream with a bottleneck ≥ 1,000 USDT.
- **Decision:** REVIEW by default; configurable to BLOCK (`[rules.severity]`).
- **Alternatives:** BLOCK (indirect money would block payments the address may not control).
- **Consequences:** PRD §7.2 default unchanged.

### D-045 · In a trace, DEPOSIT uses only stored facts about the top recipient
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P6
- **Context:** Methodology §7.5 test 9 classifies read nodes; DEPOSIT needs to know whether the node's
  top recipient is a hub. Reading every top recipient would add up to one read per node and break the
  trace budget (PRD G8: ≤ 120 BSC queries, ≤ 200 TRON requests).
- **Decision:** In a trace the top recipient counts as a hub only from what is stored: an unexpired
  classification (HUB) or a label/entity of an exchange or service kind. No extra read.
- **Alternatives:** Read top recipients (budget); skip DEPOSIT in traces (loses the commonest terminal).
- **Consequences:** `trace/engine.py`. `amlcheck classify` and checks still read the top recipient (P5).

### D-046 · Senders already on the path go to `untraced:cycle`
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P6
- **Context:** Methodology §7.2 says inflows from addresses on the current path are excluded from a
  node's window; §7.6 and AT-41 say such a sender's weight goes to `untraced:cycle`. If excluded, its
  weight could never reach that bucket.
- **Decision:** A sender on the item's own path stays in the node's inflow and takes its share like any
  sender (pruning included); terminal test 1 then puts that weight in `untraced:cycle` and the trace does
  not follow it. A sender seen on a different path is not a cycle (§7.6).
- **Alternatives:** Exclude path senders (weight silently spread over the others; AT-41 unreachable).
- **Consequences:** Partition keeps summing to 1; the cycle is visible in the result.

### D-047 · §7.11: A resolves from its named entity without a read
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P6
- **Context:** The worked example reads A (a hub) and then resolves it to `exchange_regulated` through the
  entity the operator named, counting 4 reads (AT-37). Under §7.5's order, test 4 (labels and entity
  kinds, local data) comes before any read, so A resolves without being read.
- **Decision:** Follow §7.5's order (local data first saves the budget, principle P4). AT-37 expects 3
  addresses read (T, B, E); the partition, coverage and findings are unchanged.
- **Alternatives:** Read hubs before checking their entity (wastes the budget on the biggest nodes).
- **Consequences:** AT-37 and the §7.11 table updated; no formula changes, no version bump.

### D-048 · Peel-chain freshness from what the trace read
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P6
- **Context:** Methodology §6.1: a peel-chain node must be first seen within 30 days before its edge.
  A node's true first activity costs extra reads (TRON 2 requests, BSC up to 2 indexer queries plus
  ~11 s), up to 31 times per trace, against the G8 budget.
- **Decision:** A node is fresh when the earliest activity known for it, its earliest transfer in the
  window the trace read or a first activity already stored in the cache, is no earlier than the window
  start (30 days before its edge). No extra reads (principle P4: only data already read).
- **Alternatives:** Look up first activity for every expanded node (budget); drop the condition.
- **Consequences:** An old wallet whose earlier activity is outside the window and not in the cache
  can count as fresh, so `layering` may slightly over-count; it is an annotation, never in the
  partition or a blocking rule.

### D-049 · `investigate` is a check with the trace on; `trace` records no verdict
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P6
- **Context:** PRD F9.4 names `amlcheck trace` and `investigate` but defines neither. U7 asks "where
  did this client's 30k come from?" with a graph and breakdown.
- **Decision:** `investigate <addr>` runs a full check with the trace forced on: one audit record
  linked to the trace (`trace_id`), the check's verdict and exit codes (D-019), then the trace
  breakdown and an optional `--svg` graph. `trace <addr> [--direction in|out]` is an investigation
  only: the trace is stored as a job, R-TRC findings are shown for information, no check record is
  written. Exit 0 complete, 4 partial (a read failed or time ran out), 1 could not start.
- **Alternatives:** `trace` also writing a check record (two commands doing one job; a forward
  trace has no verdict to record); `investigate` as an alias of `trace` (no recorded verdict for U7).
- **Consequences:** A verdict that considered the trace always comes from `check --trace` or
  `investigate`, so it is in the audit log; `trace` is safe to run as often as needed.

### D-050 · No paid HyperSync tier (Q-09)
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P6
- **Context:** Q-09 asked whether to pay for HyperSync if the free plan could not meet the trace budget.
  VS-07 measured 9–35 HyperSync queries per cold BSC trace (G8 allows 120) and 2 per repeat.
- **Decision:** Stay on the free plan.
- **Alternatives:** A paid tier now (cost without a measured need).
- **Consequences:** Revisit if P10 monitoring (auto-screening senders, traces at ≥ 10,000 USDT) needs
  more than the free budget.

### D-051 · R-SCR-01 off by default (Q-10)
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P7
- **Context:** PRD F10.2: the score never changes the verdict unless R-SCR-01 is enabled.
- **Decision:** `[score] review_at` = 0 keeps it off. When the owner sets it above 0, a score at or above
  it adds R-SCR-01 (REVIEW, never BLOCK; refused at config load).
- **Alternatives:** On with a default threshold (the score is uncalibrated until P11).
- **Consequences:** Verdicts depend on rules only until the owner opts in.

### D-052 · Score details: inferred confidence, rounding, uncertainty (Q-21, Q-22, Q-23)
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P7
- **Context:** Methodology §9 leaves three details open.
- **Decision:** (1) An inferred terminal adds `w × share × confidence` (its classification's
  confidence; 1 when the terminal has none): a share-weighted mean. (2) `E`, `D`, `B`, `U` are stored to
  0.1, half up, and the score is `min(99, ⌊E + D + B + U + 0.5⌋)` of the stored values. (3) No inflow
  in the trace window → `U` = 0; a partial trace → `U` = 0 and `E` from what was attributed (the check
  is INCOMPLETE and the score a lower bound).
- **Alternatives:** A plain mean of confidences; scoring from unrounded components (a stored breakdown
  could then miss its score by 1).
- **Consequences:** Every stored breakdown reproduces its score. AT-42 unchanged.

### D-053 · The case report is built from stored records only (Q-24)
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P7
- **Context:** PRD F10.4 lists the contents of the case report PDF but not its source.
- **Decision:** `amlcheck cp report <addr>` uses the counterparty's latest check (`--check ID` for
  another), the trace linked to it and the stored classifications. No network. "No decision recorded"
  until P9 adds decisions. Marked internal use only (D-023). Same records ⇒ same PDF bytes.
- **Alternatives:** Re-screening while building the report (the report would no longer match the
  recorded check).
- **Consequences:** A report always shows what was recorded at the time of the check.

### D-054 · Watch notifications: macOS, plus an optional local webhook (Q-11)
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P8
- **Context:** `watch run` must tell the operator when a verdict changes (PRD F11.3).
- **Decision:** A macOS notification (`osascript`) when anything changed. When `[monitor] webhook_url`
  is set, the same changes also go there as one JSON POST. No notification elsewhere (Linux, Windows):
  the run's output and exit code 6 carry it.
- **Alternatives:** Webhook only; e-mail (needs credentials).
- **Consequences:** A scheduled run reaches the operator without an extra service.

### D-055 · The web UI uses no JavaScript (Q-25)
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P8
- **Context:** Architecture §2 names HTMX, vendored. The pages need only form posts and a progress view
  for running traces.
- **Decision:** Server-rendered pages without scripts. A running trace's page refreshes itself every 2 s
  until the trace ends. The CSP is `default-src 'none'` with only same-origin styles, images and form
  actions, so no script can run at all.
- **Alternatives:** Vendored HTMX (a third-party file to download, review and keep updated).
- **Consequences:** Supersedes the HTMX line of architecture §2 for P8. A later page that truly needs
  scripts adds them with its own decision.

### D-056 · Batch file format and exit code (Q-26)
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P8
- **Context:** PRD F11.2 names `batch` from CSV but no columns.
- **Decision:** Input header `address,chain,amount,client,note`, only `address` required, extra columns
  refused. Every row is validated before any is screened; one invalid row ⇒ nothing screened, exit 1,
  each problem listed by row. Output CSV, streamed row by row: `row,address,chain,verdict,score,band,
  rules,check_id,record_hash`. Exit code: the most serious verdict (5 BLOCK, 4 INCOMPLETE, 3 REVIEW, 0).
- **Alternatives:** Screen the valid rows and skip the rest (a silently partial batch).
- **Consequences:** A batch is all or nothing at the validation step, like `labels import`.

### D-057 · `watch run` re-screens without a trace (Q-27)
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P8
- **Context:** PRD F11.3: changed verdicts are reported and recorded.
- **Decision:** Every watched address is checked like `check` without an amount (no trace), one at a
  time. A change is a verdict different from the watchlist's last verdict for it; the first run of a new
  address is not a change. Each check is in the audit log; the watchlist row keeps the last verdict and
  check id. Exit 6 when anything changed.
- **Alternatives:** Trace every watched address (budget); compare scores (the score is uncalibrated
  until P11).
- **Consequences:** A scheduled daily run costs one check per watched address.

### D-058 · Every decision carries a name (Q-12)
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P9
- **Context:** PRD F12.1: a decision is closed "with a note and the operator name".
- **Decision:** The name comes from `[operator] name`; `--by` overrides it for one decision. With
  neither, the decision is refused with a message to set `[operator] name`. (Labels and entity names
  keep recording `operator` when unnamed, P4.)
- **Alternatives:** Ask each time (scripts can't); record `operator` (an anonymous decision trail).
- **Consequences:** The decision chain always says who decided.

### D-059 · Confirming and rejecting inferences, per type (Q-28)
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P9
- **Context:** PRD F12.3: confirm → operator label; reject → suppressed until the classifier version
  changes. The inferred categories (`suspicious_collector`, `service_unattributed`,
  `contract_unattributed`) may only be assigned by inference (methodology §8), so a confirmation can't
  simply copy them.
- **Decision:** DEPOSIT: the address's entity membership becomes `operator`; naming the entity
  (name and kind) then makes it a terminal at trace test 4 without a read (AT-51). HUB: confirmed by
  naming its entity and kind. COLLECTOR, DISTRIBUTOR, PASS_THROUGH: an operator label in the category the
  operator chooses (`--category`, a human category such as `scam` or `high_risk`). Rejecting a type
  records it in `inference_feedback` for the current `classifier_version`; the classifier then drops
  that type for that address (not saved, no R-HEU-07, not a trace terminal) until the version changes.
- **Alternatives:** A fixed category per type (`COLLECTOR` → `high_risk` is not always true).
- **Consequences:** Every confirmation and rejection is stored with the case, operator and classifier
  version.

### D-060 · Case lifecycle (Q-29)
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P9
- **Context:** PRD F12.1 opens cases from REVIEW/BLOCK checks; INCOMPLETE says "treat as REVIEW".
- **Decision:** A case opens from a REVIEW, BLOCK or INCOMPLETE check (latest by default). One open case
  per address: opening again returns it. `approved` and `rejected` close the case; `escalated` is
  recorded and keeps it open. A decision is linked to the check it was made on (by default the case's
  check) and to that check's record hash. Decisions are never changed; a new question is a new case.
- **Alternatives:** Cases for NO_HITS (nothing to decide); several open cases per address (confusing).
- **Consequences:** The latest decision on an address is easy to find and always tied to evidence.

### D-061 · `case export --jsonl` (Q-30)
- **Status:** Accepted
- **Date:** 2026-10-03 · **Phase:** P9
- **Context:** PRD F12.4: an export "for future model training".
- **Decision:** One JSON line per decision: case and decision (verdict given, note, operator, time),
  the check it was made on (verdict, rule IDs, score components, classifier types and profile, trace
  partition and coverage) and the inference feedback in the case. Addresses included: internal use
  only (D-023). Built from stored records, same order every time.
- **Alternatives:** Raw transfers (large, and re-derivable from the cache).
- **Consequences:** A later model can be trained on what operators actually decided.

### D-062 · Monitoring scope and pace (Q-13)
- **Status:** Accepted
- **Date:** 2026-10-04 · **Phase:** P10
- **Context:** PRD F13: own wallets are registered and their new senders screened.
- **Decision:** `monitor run` covers every active registered wallet. It is meant to run every 10
  minutes (docs/scheduling.md). Each wallet's run reads inbound USDT since the last transfer it has
  seen; the first run looks back 24 hours. An own wallet is labelled `own_or_trusted` (F13.1), so the
  look-alike guard also protects it.
- **Alternatives:** Monitor only chosen wallets (one more list to keep); a full history on the first
  run (budget).
- **Consequences:** A new counterparty is screened within about 10 minutes of paying in.

### D-063 · At most 50 new senders per monitor run (Q-31)
- **Status:** Accepted
- **Date:** 2026-10-04 · **Phase:** P10
- **Context:** Each new sender costs a full check; a busy wallet can bring hundreds at once.
- **Decision:** `[monitor] max_senders_per_run` = 50, taken oldest first. When the cap is reached, the
  wallet's position is set to the last transfer screened, so the next run starts exactly there; the run
  says it was capped.
- **Alternatives:** No cap (one run could take hours and exhaust the free budgets).
- **Consequences:** A burst is worked off over a few runs instead of one long one.

### D-064 · `monitor run` exits 6 when a sender needs attention (Q-32)
- **Status:** Accepted
- **Date:** 2026-10-04 · **Phase:** P10
- **Context:** PRD F13.3: reports like `watch run`; D-019 reserves 6 for "needs attention".
- **Decision:** Exit 6 when any sender screened in the run is REVIEW, BLOCK or INCOMPLETE; 0 otherwise.
  The same alerts as `watch run` (D-054). Each sender is checked like `check`, client = the wallet's
  name, note `monitor run`, amount = its largest transfer in the window, so a transfer of
  `[monitor] trace_amount_usdt` or more is traced (F13.2).
- **Alternatives:** Exit 6 only for BLOCK (a REVIEW would go unnoticed).
- **Consequences:** A scheduler or script can react to one exit code.

### D-065 · The local API's token and port (Q-33)
- **Status:** Accepted
- **Date:** 2026-10-04 · **Phase:** P10
- **Context:** PRD F14.2: Bearer token of at least 32 characters from `.env`, 127.0.0.1, host
  allow-list.
- **Decision:** `amlcheck api [--port 8766]` reads `AMLCHECK_API_TOKEN` (environment or `.env`, never
  config) and refuses to start without one of at least 32 characters. Every request, reads included,
  needs `Authorization: Bearer <token>` (401 otherwise); the token is compared in constant time. The web
  UI keeps its own per-start token and port.
- **Alternatives:** Reads without a token (the API exposes counterparty data).
- **Consequences:** The corridor keeps one secret; rotating it is a restart.

### D-066 · Golden-set sources (Q-14)
- **Status:** Accepted
- **Date:** 2026-10-04 · **Phase:** P11
- **Context:** Methodology §10 needs ≥ 40 golden addresses per chain with expected verdict, type and
  band; no third-party AML data may be used (D-033), and label data needs a recorded licence.
- **Decision:** BLOCK expectations from our own synced OFAC and Tether TRON lists. HUB expectations
  from exchanges' own published proof-of-reserves wallet lists, read with the owner's permission, terms
  checked and the source URL recorded per address; they are test expectations only and are never
  imported as labels. DEPOSIT expectations derived on-chain (addresses sweeping ≥ 99% to those hubs).
  COLLECTOR candidates found on-chain and confirmed or rejected by the owner. Known-clean
  counterparties (≥ 10 per chain) and decided cases come from the owner. The owner approves the list
  before the live run.
- **Alternatives:** Owner picks everything (slow); a commercial label set (excluded by D-033).
- **Consequences:** Every expectation in `tests/golden/` says where it came from.

### D-067 · What the golden set records for replay (Q-34)
- **Status:** Accepted
- **Date:** 2026-10-04 · **Phase:** P11
- **Context:** Thresholds are tuned against the golden set; CI must recompute precision without the
  network.
- **Decision:** For each golden address the classifier's inputs (profile features and context) and
  the live outcome (verdict, types, score and band, versions) are stored in `tests/golden/`. Raw
  transfer histories are not stored: they are large and can be re-read from the providers.
- **Alternatives:** Store raw histories for full offline re-screening (megabytes in the repository).
- **Consequences:** Classifier changes are measured offline; score and verdict changes need a live
  re-run, which the report states.

### D-068 · The v1 JSON contract (Q-35)
- **Status:** Accepted
- **Date:** 2026-10-04 · **Phase:** P11
- **Context:** At 1.0 the corridor and scripts rely on the check JSON and the API.
- **Decision:** For all of v1, the check JSON (`check --json`, `investigate --json`, the API) and the
  API endpoints keep their fields and meanings: fields may be added, never removed, renamed or given a
  new meaning. A test pins the field names. A breaking change means v2 (`/v2`).
- **Alternatives:** No promise (integrations break silently).
- **Consequences:** Additions are safe; anything else waits for v2.

### D-069 · v1.0.0 ships with the golden set partly measured
- **Status:** Accepted
- **Date:** 2026-10-04 · **Phase:** P11
- **Context:** The P11 exit criteria include HUB and DEPOSIT precision ≥ 0.9, COLLECTOR ≥ 0.8 and no
  clean golden address scored high or severe. Their ground truth needs addresses only the owner knows
  (D-066): the exchanges' proof-of-reserves pages are blocked from this machine, and collector
  candidates from public data turned out to be busy services. The owner chose to release now.
- **Decision:** Release v1.0.0 with what is measured: 20 BLOCK golden entries (20/20 verdicts as
  expected) and the AT-59 budget run (passed). The clean band and HUB, DEPOSIT and COLLECTOR precision
  are stated as **not measured**, in the calibration report and the release notes. No threshold was
  changed without evidence. The owner signs off knowing this; the measurements follow in a v1.0.x
  when the owner's addresses arrive (Q-36).
- **Alternatives:** Wait for the owner's addresses (open-ended); label addresses ourselves (circular,
  and third-party labels are excluded).
- **Consequences:** The classifier and score keep their designed defaults (classifier version 1,
  score version 1). The known calibration notes (unattributed services push E; very busy deposits and
  collectors are primary HUB; coverage often low) are listed for the v1.0.x calibration.

