# amlcheck — Risk methodology

> Every rule, algorithm, threshold and formula the product uses. Every threshold here is a default in
> `config.toml`. Every formula has a version number stored with its results: changing a formula means
> bumping its version, never editing it in place.

| Component | Version | Stored with |
|---|---|---|
| Screening + behaviour rules | `rules_version = 1` | each check |
| Profiler + classifier | `classifier_version = 1` | each classification |
| Source-of-funds trace | `trace_version = 1` | each trace |
| Category list + weights | `category_version = 1` | each label, score components |
| Score | `score_version = 2` from v2 (§11); `1` for checks made by v1 (§9) | each check |
| Risk policy (exposures, levels, verdict defaults) | `risk_version = 2` (§11) | each check |

---

## 1. Principles

| # | Principle | Why |
|---|---|---|
| P1 | Lists and operator labels are facts with a source. Classifications are inferences with a confidence | An inference must never look like a list hit |
| P2 | Proportional (haircut) attribution gives an **estimated share**. Every path also shows its **bottleneck amount**, an absolute number | Money is fungible inside a wallet: no method traces it "dollar by dollar". The share ranks, the amount proves |
| P3 | Every unit of inflow value ends in exactly one bucket: a category or an untraced reason | The operator always sees what the tool doesn't know |
| P4 | Only data already read is used for nodes at the edge of a trace; neighbours are flagged from local data only | Keeps checks and traces inside their budgets |
| P5 | Money can only come from inflows **before** it was sent on | Otherwise later deposits get attributed to earlier payments |
| P6 | A required source that failed or is stale can never give a clean result | A gap is not evidence of safety |

---

## 2. Screening (P2)

### 2.1 Sources in a check

| Source | Chains | Required | `stale` when | Notes |
|---|---|---|---|---|
| Sanctions list (OFAC SDN, local) | both | yes | last successful download > 48 h ago | Age is from **our download**, not OFAC's publish date (OFAC does not publish daily) |
| TRON USDT freeze index (local) | TRON | yes | index cannot be refreshed and lags the chain > 60 min | Refreshed incrementally at the start of every TRON check |
| TRON `isBlackListed` spot-check | TRON | yes | — | One contract call for the target |
| BEP20 USDT token freeze | BSC | — | — | Always `skipped`: the contract has no freeze function ([data sources](04-data-sources.md)). Every BSC result also says freezes of the same `0x` address on other chains are not checked in v1 (D-039) |
| Exposure (USDT history) | both | yes | history could not be read in full (§3.1) | |

A `skipped` source is not a gap. An `error` or `stale` required source adds **R-SYS-01**.

### 2.2 R-SAN-01 · sanctioned address

The normalised target equals a digital-currency address in the latest accepted sanctions snapshot,
whatever the list's currency label (an address listed as "USDT", "ETH" or "TRX" matches on whichever
chain its format belongs to). Evidence: list, entry ID, entity name, program, snapshot date and hash.

A listed address that fails its own checksum is kept (a typo must not hide an entry) and logged.

### 2.3 R-FRZ-01 / R-FRZ-02 · issuer freezes

| Status of the target | Rule |
|---|---|
| TRON index: latest blacklist event is `AddedBlackList`, or a `DestroyedBlackFunds` exists | R-FRZ-01 |
| `isBlackListed` returns true | R-FRZ-01 |
| TRON index: `AddedBlackList` followed by `RemovedBlackList`, and no later add | R-FRZ-02 |

Evidence: issuer, chain, token, event transaction, block time, amount destroyed if any.

### 2.4 Network failures

| Event | Effect |
|---|---|
| HTTP 429 with `Retry-After` ≤ 10 s | Wait and retry once |
| TronGrid 429/403 **without** `Retry-After` (key suspended, 30 s with a key) | Check: source `error` at once. Traces and syncs: wait through the pacer for the "suspended for N s" seconds, else 30 s, within 65 s (D-036). The limiter at 10 req/s (key limit 15) should prevent it |
| `Retry-After` > 10 s, timeout, 5xx after retries | Source `error` → R-SYS-01 |
| Indexer budget pacer needs to wait ≤ 65 s | Wait (it is pacing, not failing) |
| Error answers | Never cached |

### 2.5 Verdict

```python
def decide(findings: list[Finding]) -> Verdict:
    severities = {f.severity for f in findings}
    if "BLOCK" in severities:      return BLOCK
    if "INCOMPLETE" in severities: return INCOMPLETE
    if "REVIEW" in severities:     return REVIEW
    return NO_HITS
```

Severity overrides from config apply before `decide`. R-SYS-01 is fixed at INCOMPLETE. R-HEU-07 and
R-TRC-05 may never be raised to BLOCK (config refused at load).

---

## 3. Exposure & behaviour (P3)

### 3.1 Reading the target's history

| Rule | Default |
|---|---|
| Lookback window | 180 days (`[exposure] lookback_days`) |
| Most transfers read | 5,000, newest first (`[exposure] max_transfers`). More in the window → source `stale` → INCOMPLETE, never a clean result over part of the history |
| 0-USDT transfers | Dropped and counted: anyone can send them to any address (address poisoning) |
| First activity | TRON: earlier of account activation and first USDT transfer. BSC: first transaction sent or received, or first token `Transfer` log naming the address |

### 3.2 Counterparties

For each other address: USDT received from it, USDT sent to it, and its transfers. Self-transfers are
ignored.

### 3.3 Flags on counterparties (local data only, no API quota)

| Flag | Source |
|---|---|
| `sanctioned` | latest sanctions snapshot |
| `frozen` | TRON index: latest blacklist event is `AddedBlackList` |
| `label:<tag>` | `labels.csv` and the intelligence store |

### 3.4 Exposure rules

| Rule | Condition | Evidence |
|---|---|---|
| R-EXP-01 | A counterparty is `sanctioned` or `frozen` | counterparty, flag, amounts both ways, 3 largest transfers |
| R-EXP-02 | `Σ received from flagged counterparties / Σ received` ≥ 5% (`[exposure] flagged_inflow_share`) | share, amounts, flagged list |

At most 10 R-EXP-01 findings, largest counterparties first (by received + sent, ties by address).

### 3.5 Behaviour rules

| Rule | Condition (defaults in `[heuristics]`) | Notes |
|---|---|---|
| R-HEU-01 | First activity < 7 days ago, or no activity at all | low priority |
| R-HEU-02 | Pass-through: of USDT received, ≥ 90% left within 24 h of arriving | FIFO lots; money already there before the window is not counted |
| R-HEU-03 | Fan-in: > 50 distinct senders, each sending < 100 USDT, within any 24 h | sliding window |
| R-HEU-04 | Fan-out: > 50 distinct recipients within any 24 h | sliding window |
| R-HEU-05 | A counterparty carries a label in `risky_tags` (`mixer`, `bridge`, `high_risk`) | at most 10 findings |

Counterparties labelled `allowlist` (own or known wallets) are excluded from R-HEU-02 to R-HEU-04. They
never cancel a sanctions or freeze finding.

**FIFO pass-through:**

```python
def pass_through(address, transfers, window):
    lots, received, passed = deque(), 0, 0
    for t in sorted(transfers, key=time):
        if t.recipient == address:
            received += t.amount; lots.append([t.time, t.amount]); continue
        need = t.amount
        while need > 0 and lots:
            arrived, left = lots[0]
            take = min(need, left)
            if t.time - arrived <= window: passed += take
            need -= take; lots[0][1] -= take
            if lots[0][1] == 0: lots.popleft()
    return received, passed          # R-HEU-02 when passed / received >= 0.9
```

---

## 4. Look-alike guard (P4) · R-HEU-06

| Step | Rule |
|---|---|
| Body | TRON: the address without its leading `T`. EVM: lowercase hex without `0x` |
| Key | first 4 + last 4 characters of the body, stored as `lookalike_key` on every registry row, own wallet and trusted label |
| Match | Target ≠ a registry address, own wallet or `own_or_trusted` address with the same `lookalike_key` on the same chain |
| Finding | REVIEW. Evidence: both full addresses with the differing middle marked, when the real one was last screened and for which client |

This catches address poisoning: an attacker sends 0 or dust USDT from an address that looks like a
real counterparty, hoping the operator copies the wrong address from history. Graph and UI labels show
at least the first 8 and last 6 characters so such pairs stay distinguishable.

---

## 5. Profile features

Computed by a pure function over cached transfers in a window (default `[classifier] window_days` = 90,
or the trace window), capped at `[trace] hub_transfers` transfers.

| Feature | Definition |
|---|---|
| `n_in`, `n_out` | Number of inbound / outbound USDT transfers (0-value dropped, §3.1) |
| `distinct_senders`, `distinct_recipients` | Distinct counterparties per direction |
| `volume_in`, `volume_out` | USDT summed per direction |
| `retained_share` | `max(0, volume_in − volume_out) / volume_in` |
| `first_seen`, `last_seen` | From history; `first_seen` uses the §3.1 first-activity definition when the full history is read |
| `median_hold_hours` | Amount-weighted median time a received lot stays before leaving (FIFO, as `pass_through`) |
| `pass_through_share_24h` | Existing `heuristics.pass_through` with 24 h |
| `top_recipient`, `top_recipient_share_out` | Largest recipient by value and its share of `volume_out` |
| `top_sender_share_in` | Largest sender's share of `volume_in` |
| `small_in_share` | Share of inbound transfers below `[heuristics] fan_in_small_usdt` (100) |
| `round_share` | Share of transfers whose amount is a multiple of 100 USDT |
| `max_senders_24h`, `max_recipients_24h` | Existing `heuristics.busiest_window` |
| `capped` | `true` when the window held more than `hub_transfers` transfers |
| `is_contract` | From the contract lookup (cached forever) |

---

## 6. Classifier (version 1)

An address may get several types. The **primary type** is the first in the table order that applies.
`FRESH` is a tag added next to the primary type. Confidence starts at the base and adds each bonus that
holds, capped at 1.0. A type is assigned only at confidence ≥ 0.6, except `PERSONAL` (0.5, informational).

| Type | Required (all) | Base | Bonuses (+0.1 each unless stated) | Terminal category |
|---|---|---|---|---|
| `CONTRACT` | `is_contract` | 1.0 | — | `contract_unattributed` (unless labelled) |
| `HUB` | `capped`, or `distinct_senders + distinct_recipients ≥ 500` | 0.7 | +0.25 if `capped` | entity kind, else `service_unattributed` |
| `DEPOSIT` | `distinct_senders ≥ 2`; `top_recipient_share_out ≥ 0.9`; `top_recipient` is `HUB` or labelled exchange/service; `median_hold_hours ≤ 72`; `retained_share ≤ 0.05`; `n_out ≤ n_in` | 0.6 | `distinct_senders ≥ 5`; `median_hold_hours ≤ 12`; `top_recipient_share_out ≥ 0.99`; hub's entity named by operator | hub's entity kind, else `service_unattributed` |
| `COLLECTOR` | `distinct_senders ≥ 30`; `small_in_share ≥ 0.7`; `top_recipient_share_out ≥ 0.8`; not `DEPOSIT` | 0.6 | `distinct_senders ≥ 100`; `small_in_share ≥ 0.9`; `median_hold_hours ≤ 24`; top recipient is `FRESH` or `PASS_THROUGH` | `suspicious_collector` |
| `DISTRIBUTOR` | `max_recipients_24h ≥ 30`; `distinct_senders ≤ 3` | 0.6 | +0.2 `max_recipients_24h ≥ 100`; +0.2 `round_share ≥ 0.5` | none (expanded) |
| `PASS_THROUGH` | `pass_through_share_24h ≥ 0.9`; `volume_in ≥ 1000`; not `DEPOSIT` | 0.6 | +0.2 share ≥ 0.98; +0.2 `median_hold_hours ≤ 2` | none (expanded) |
| `PERSONAL` | none of the above; `distinct_senders + distinct_recipients ≤ 50` | 0.5 | — | none (expanded) |
| `FRESH` (tag) | `first_seen` < 7 days before analysis time | 1.0 | — | — |

**Entity linking.** A `DEPOSIT` address joins the entity of its `top_recipient`. If that hub has no
entity, one is created with name `hub-<first 8 of address>` and kind `unknown`. The link stores the
features that justified it.

**R-HEU-07.** When the checked address itself has primary type `COLLECTOR` or `DISTRIBUTOR` with
confidence ≥ 0.7, R-HEU-07 fires: REVIEW, low priority, evidence = the features that held. It can never
be raised to BLOCK.

**Operator precedence.** An operator label or a rejection recorded in a case (PRD F12.3) wins over any
inference for that address until `classifier_version` changes.

### 6.1 Peel-chain annotation (`layering`)

Checked on every trace path after the trace finishes. A **segment** of ≥ 3 consecutive expanded nodes
on one path is a peel chain when every node in it:

- has `n_in ≤ 3` in its window,
- has `first_seen` within 30 days before its edge,
- sent ≥ 60% and < 98% of its window inflow to the next node on the path,
- and edge amounts along the segment never increase.

The weight that passed through a peel segment is added to `annotations.layering`. It is **not** part
of the partition (a peel chain ends somewhere, and that terminal keeps the share).

---

## 7. Source-of-funds trace

### 7.1 Settings (`[trace]`)

| Key | Default | Meaning |
|---|---|---|
| `auto_amount_usdt` | 10000 | A check with `--amount` at least this runs a trace |
| `max_hops` | 3 | Deepest hop whose identity is checked. Nodes at hops `< max_hops` may be read and expanded |
| `branch` | 5 | Most senders followed from one node |
| `coverage_share` | 0.8 | Senders are followed until they cover this share of the node's inflow |
| `min_attributed_usdt` | 100 | A sender whose estimated attributed amount is below this is pruned |
| `hop_window_days` | 30 | How far before its outgoing payment a node's inflows are considered |
| `max_nodes` | 40 | Most distinct addresses read in one trace (target included) |
| `time_budget_seconds` | 300 | Wall-clock limit. Running out makes the trace `stale` → `INCOMPLETE` |
| `hub_transfers` | 1000 | A node with more transfers than this in its window is a hub (cheap to detect: the capped read stops early) |
| `min_flagged_usdt` | 1000 | R-TRC-01/02 bottleneck threshold  |
| `high_risk_share` | 0.05 | R-TRC-03 |
| `min_coverage` | 0.5 | R-TRC-04 |
| `inferred_share` | 0.10 | R-TRC-05 |

With the defaults, at most 1 + 5 + 25 = **31 addresses are read**. Hop-3 nodes are only matched against
local data (P4). That fits `max_nodes` = 40 with room for window extensions.

### 7.2 Windows

| Node | Window of inflows considered |
|---|---|
| Target (hop 0) | `[as_of − [exposure] lookback_days, as_of]` (180 days, same as 1-hop) |
| Node N at hop ≥ 1 | `[t_first − hop_window_days, t_last]`, where `t_first`/`t_last` are the first and last transfers N sent to the node it was reached from, inside that node's window |

Inflows from any address already on the current path are excluded from N's window (cycle rule, §7.6).

### 7.3 Weights (haircut)

For the target: `in_T` = total USDT received in its window. A sender `k` that sent `a_k` gets weight
`w_k = a_k / in_T`.

For a node N with weight `w_N` and window inflow `in_N`, its sender `j` that sent `a_j` gets
`w_j = w_N × a_j / in_N`.

The **estimated attributed amount** of any node is `w × in_T`.

The **edge amount** of `j → N` is `a_j`. The **bottleneck** of a path `T ← N1 ← N2 ← X` is the
smallest edge amount on it.

### 7.4 Pruning (per parent node)

1. Sort the parent's senders by amount sent, descending, ties by address ascending.
2. Walk the list. Keep a sender while (a) fewer than `branch` are kept, (b) the kept senders cover
   less than `coverage_share` of the parent's inflow *before* adding it, and (c) its estimated
   attributed amount is ≥ `min_attributed_usdt`.
3. Everything not kept adds its weight to the `pruned` bucket.

### 7.5 Terminal tests (in this order, first match wins)

| # | Test | Uses | Bucket / category |
|---|---|---|---|
| 1 | Address on the current path | — | `untraced:cycle` |
| 2 | On the OFAC snapshot | local | `sanctioned` |
| 3 | Latest Tether event is `AddedBlackList` (TRON), or `DestroyedBlackFunds` | local | `frozen` |
| 4 | Has an active label or entity kind with a terminal category (§8), lists/operator/import first, then highest confidence, ties by category order in §8 | local | that category |
| 5 | Cached unexpired classification `CONTRACT`, `HUB`, `DEPOSIT` or `COLLECTOR` | local | §6 mapping |
| 6 | `hop == max_hops` | — | `untraced:depth` |
| 7 | `max_nodes` already read | — | `untraced:budget` |
| 8 | Read history for the window. Too many transfers → classify `HUB` | read | `service_unattributed` (or entity kind) |
| 9 | Profile + classify. `CONTRACT`/`DEPOSIT`/`COLLECTOR` | read | §6 mapping |
| 10 | No inflow in the window | read | `untraced:no_inflow` |
| 11 | Otherwise **expand**: apply §7.3 and §7.4 to its senders | read | — |

Hop-1 senders that end at tests 2–4 are R-EXP-01's territory (§3): they still take their share of the
partition, but the trace emits no R-TRC finding for them.

### 7.6 Revisits and cycles

- The same address reached through different parents is a separate item each time (different windows
  and weights). Its history is read once and extended if a later window needs more.
- `max_nodes` counts **distinct addresses read**.
- A sender that is already on the item's own path goes to `untraced:cycle`. A sender seen on a
  *different* path is not a cycle.

### 7.7 Failures

| Event | Effect |
|---|---|
| A node's history cannot be read (error, 429 beyond the pacer) | Trace source `stale`, verdict `INCOMPLETE` (R-SYS-01). The partial trace is kept for display |
| `time_budget_seconds` reached | Same |
| `max_nodes` reached | Not a failure. Remaining weight → `untraced:budget` |
| Target has no inflow in its window | Trace `ok`, `coverage = null`, no R-TRC findings |

### 7.8 Outputs and invariants

```json
{
  "trace_version": 1,
  "direction": "in",
  "as_of": "2026-10-01T10:00:00Z",
  "target_inflow_usdt": "20000",
  "nodes": [{"address": "…", "hop": 1, "weight": "0.6", "terminal": "exchange_regulated",
             "classification": {"type": "HUB", "confidence": 0.95}, "read": true}],
  "edges": [{"from": "…", "to": "…", "amount_usdt": "12000", "first": "…", "last": "…",
             "tx_sample": ["…"]}],
  "partition": {"exchange_regulated": "0.60", "sanctioned": "0.20",
                "suspicious_collector": "0.10", "untraced:pruned": "0.10"},
  "annotations": {"layering": "0.00"},
  "coverage": "0.90",
  "paths": [{"to_category": "sanctioned", "hops": 2, "addresses": ["T…", "…", "…"],
             "bottleneck_usdt": "4000", "estimated_usdt": "4000"}],
  "budget": {"nodes_read": 4, "queries": 11, "cache_hits": 2, "seconds": 9.4}
}
```

| Invariant | Test |
|---|---|
| Σ `partition` = 1 ± 0.001 | property test on random graphs |
| `coverage` = Σ of non-`untraced:*` buckets | unit |
| Same cache + config ⇒ identical JSON | golden test |
| Every `paths[]` entry's addresses are connected by `edges[]` | unit |

Amounts and weights are `Decimal` and serialised as strings, like the rest of amlcheck.

### 7.9 Forward trace (`--direction out`)

Mirror image: outflows instead of inflows, windows `[t_first, t_last + hop_window_days]`, recipients
instead of senders. Used to answer "where did the money we paid go". Same buckets, same invariants. R-TRC
rules apply only to inbound traces.

### 7.10 Pseudocode

```python
def trace(target, cfg, as_of):
    hist = cache.history(target, window=(as_of - cfg.lookback, as_of))     # incremental read
    senders = group_inflows(hist, target, exclude=set())
    in_T = sum(s.amount for s in senders)
    if in_T == 0:
        return Trace.empty(target)
    buckets, items = Buckets(), []
    items += prune(senders, parent_weight=Decimal(1), parent_in=in_T, in_T=in_T,
                   hop=1, path=(target,), buckets=buckets)
    read = {target}
    for item in ordered(items):            # hop asc, weight desc, address asc; new items appended
        cat = local_terminal(item)         # tests 1–6
        if cat:
            buckets.add(cat, item.weight); continue
        if item.address not in read and len(read) >= cfg.max_nodes:
            buckets.add("untraced:budget", item.weight); continue
        hist = cache.history(item.address, window=item.window(cfg))  # raises → stale
        read.add(item.address)
        cat = read_terminal(item, hist)    # tests 8–10
        if cat:
            buckets.add(cat, item.weight); continue
        senders = group_inflows(hist, item.address, exclude=set(item.path))
        in_N = sum(s.amount for s in senders)
        items += prune(senders, item.weight, in_N, in_T, item.hop + 1,
                       item.path + (item.address,), buckets)
    return Trace(buckets, items, …)
```

### 7.11 Worked example

Target T received **20,000 USDT** in its window: A 12,000, B 6,000, C 2,000.

| Step | What happens | Bucket |
|---|---|---|
| Prune T's senders | A (60%) kept, B kept (60% < 80% before adding), C dropped (90% ≥ 80%) | `untraced:pruned` 0.10 |
| A, hop 1 | Member of the entity the operator named "Binance", kind `exchange_regulated`: test 4, no read (D-047) | `exchange_regulated` 0.60 |
| B, hop 1 | Read: in window B received 6,000: D 4,000, E 2,000 | expand |
| D, hop 2 | On OFAC | `sanctioned` 0.20 |
| E, hop 2 | Read, classified `COLLECTOR` (confidence 0.8) | `suspicious_collector` 0.10 |

Result: coverage 0.90. Path T ← B ← D has bottleneck min(6,000, 4,000) = **4,000 ≥ 1,000** →
**R-TRC-01**. High-risk share 20% ≥ 5% → **R-TRC-03**. Collector share 10% ≥ 10% → **R-TRC-05**
(low). Verdict REVIEW. Reads: T, B, E = 3 addresses (D-047).

---

## 8. Categories (version 1)

Order matters: it breaks ties in terminal test 4.

| # | Category | Weight `w` | Allowed provenance | High-risk set (R-TRC-03) | Notes |
|---|---|---|---|---|---|
| 1 | `sanctioned` | 1.0 | list | yes | OFAC, plus any verified extra list |
| 2 | `frozen` | 0.9 | list | yes | Tether TRON index |
| 3 | `stolen_funds` | 0.9 | operator, import | yes | |
| 4 | `darknet` | 0.9 | operator, import | yes | |
| 5 | `mixer` | 0.8 | operator, import | yes | `labels.csv` tag `mixer` |
| 6 | `scam` | 0.7 | operator, import | yes | |
| 7 | `high_risk` | 0.6 | operator, import | yes | `labels.csv` tag `high_risk` |
| 8 | `suspicious_collector` | 0.5 | inferred | no (R-TRC-05) | weighted by confidence in the score |
| 9 | `gambling` | 0.3 | operator, import | no | |
| 10 | `exchange_nokyc` | 0.3 | operator, import | no | |
| 11 | `bridge` | 0.2 | operator, import | no | `labels.csv` tag `bridge`. Cross-chain is out of scope |
| 12 | `otc_desk` | 0.1 | operator, import | no | |
| 13 | `service_unattributed` | 0.1 | inferred | no | unnamed hubs and their deposits |
| 14 | `contract_unattributed` | 0.1 | inferred | no | |
| 15 | `exchange_regulated` | 0.0 | operator, import | no | |
| 16 | `payment_processor` | 0.0 | operator, import | no | |
| 17 | `own_or_trusted` | 0.0 | operator, import | no | `labels.csv` tag `allowlist`, own wallets |
| — | `layering` | 0.5 | inferred | no (R-TRC-05) | annotation only (§6.1) |

Entity kinds are the categories 3–17 that the operator may assign.

---

## 9. Score (version 1)

> Checks made by v1 keep this score as stored. New checks use §11 (D-071).

| Part | Formula | Range |
|---|---|---|
| Hazard `H` | `Σ w_c × share_c` over the partition, where inferred categories also multiply by the mean confidence of the terminals behind them, `+ 0.5 × annotations.layering` | 0–1 |
| Exposure `E` | `60 × (1 − e^(−20·H))` | 0–60 |
| Direct `D` | +25 if R-EXP-01, +15 if R-EXP-02, capped at 30 | 0–30 |
| Behaviour `B` | R-HEU-01 +5, R-HEU-02 +10, R-HEU-03 +10, R-HEU-04 +5, R-HEU-05 +15, R-HEU-06 +20, R-HEU-07 +15, R-FRZ-02 +15, capped at 30 | 0–30 |
| Uncertainty `U` | `10 × (1 − coverage)` when a trace ran, else 0 | 0–10 |
| **Score** | `100` if verdict is BLOCK; otherwise `min(99, ⌊E + D + B + U + 0.5⌋)` (half up, not banker's rounding) | 0–100 |

| Band | Score |
|---|---|
| `low` | 0–19 |
| `medium` | 20–49 |
| `high` | 50–79 |
| `severe` | 80–100 |

If the verdict is `INCOMPLETE`, the score is shown as a lower bound (`≥ 34`) and its band carries a
`+` (`medium+`).

**Feel of `E`** (one category, everything else zero):

| Exposure | `H` | `E` |
|---|---|---|
| 1% scam | 0.007 | 8 |
| 5% mixer | 0.04 | 33 |
| 2% sanctioned | 0.02 | 20 |
| 10% sanctioned | 0.10 | 52 |
| 60% regulated exchange | 0 | 0 |

**§7.11 example:** `H = 1.0 × 0.20 + 0.5 × 0.10 × 0.8 = 0.24` → `E = 59.5`. No R-EXP. R-HEU-01 (fresh)
→ `B = 5`. `U = 10 × 0.10 = 1`. Score **66, high**, verdict REVIEW.

The components are stored with the check, so an old score can be explained after the formula changes.

---

## 10. Calibration (P11)

| Step | Output |
|---|---|
| Build a golden set of ≥ 40 addresses per chain: OFAC-listed, Tether-frozen, operator-known exchange deposits, known clean personal wallets, own counterparties with decided cases | `tests/golden/*.json` with expected verdict, primary type and band |
| Run every golden address live; record classifier precision per type and band agreement | table in `docs/acceptance.md` |
| Adjust thresholds in config only. A change to a formula bumps its version | D-entries for every change |
| Target: `DEPOSIT` and `HUB` precision ≥ 0.9, `COLLECTOR` ≥ 0.8, no golden-clean address in `high`/`severe` | exit criteria of P11 |

---

## 11. Risk policy (version 2, from P12)

> Built so that results read like a professional screening platform's (MistTrack's published levels,
> risk types and hop decay, D-070), from our own data only (D-033). Replaces §9 for new checks and
> the §2.5 default severities. Verdict precedence (§2.5) is unchanged.

### 11.1 Exposures

An **exposure** is money the checked address exchanged with a risk category, directly or through
others. Every exposure is shown and stored with the check.

| Field | Meaning |
|---|---|
| `direction` | `in` (money it received) or `out` (money it sent) |
| `exposure_type` | `direct`: hop 1, a counterparty. `indirect`: hop 2 or more, found by a trace |
| `hop` | 1 = a counterparty; 2 = a counterparty's counterparty; and so on |
| `entity` | Who it is: the list entry (`OFAC SDN: …`), `Tether-frozen`, the label or entity name, or the inferred type |
| `category` | Its §8 category |
| `risk_type` | §11.2 |
| `volume_usdt` | Direct: the USDT exchanged with it in that direction (exact). Indirect: the **path volume**, the smallest edge on the path, which every hop moved; paths through one first-hop counterparty are capped together at what it sent (D-078) |
| `estimated_usdt` | Indirect only: the trace's proportional estimate, `weight × flow` (D-016) |
| `percent` | `volume_usdt` over the address's whole flow in that direction (0–1) |
| `inferred`, `confidence` | Inferred categories only: the classification's confidence |
| `address`, `path` | The risk end; the path from the checked address to it |

- **Direct exposures** come from the whole history read (§3.1), every counterparty, not only the 20
  shown. Counterparties are flagged from local data only (§3.3). Both directions.
- **Indirect exposures** come from the trace's terminals at hop ≥ 2, and from hop-1 terminals decided
  by a classification (tests 5, 8, 9), which local flags can't see. Without a trace there are none.
- A category with weight 0 (§11.2) is not an exposure.

### 11.2 Risk types and weights

The six risk types MistTrack publishes, plus `frozen`: Tether's blacklist is a fact we read directly
from the chain, so it is shown as itself.

| Category (§8) | `risk_type` | Weight `w` |
|---|---|---|
| `sanctioned` | `sanctioned_entity` | 1.0 |
| `frozen` | `frozen` | 0.9 |
| `stolen_funds`, `darknet` | `illicit_activity` | 0.9 |
| `mixer` | `mixer` | 0.8 |
| `scam` | `illicit_activity` | 0.7 |
| `high_risk` | `illicit_activity` | 0.6 |
| `suspicious_collector` (inferred) | `illicit_activity` | 0.5 × confidence |
| `gambling` | `gambling` | 0.3 |
| `exchange_nokyc` | `risk_exchange` | 0.3 |
| `bridge` | `bridge` | 0.2 |
| `otc_desk`, `service_unattributed`, `contract_unattributed`, `exchange_regulated`, `payment_processor`, `own_or_trusted` | — | 0 (not a risk) |

An unknown service is not a risk in itself. v1 counted it at 0.1, which raised scores for wallets
that only dealt with unlabelled exchanges.

### 11.3 Score (version 2)

```
c(e)    = w(category) × (1 − decay)^(hop − 1) × percent × confidence   (confidence 1 for facts)
H_dir   = min(1, Σ c(e) over the exposures in that direction)           dir = in, out
H       = 1 − (1 − H_in) × (1 − H_out)
X       = 100 × (1 − e^(−k·H))                                          exposure points, 0–100
B       = min(30, Σ behaviour points)                                   behaviour points, 0–30
score   = 100 if BLOCK, else min(99, ⌊X + (100 − X) × B / 100 + 0.5⌋)
```

| Setting | Default | Meaning |
|---|---|---|
| `[score] decay` | 0.4 | Each extra hop counts 40% less (MistTrack's documented risk decay); 0 turns decay off |
| `[score] k` | 8 | How fast exposure turns into points |
| `[score] review_at` | 31 | R-SCR-01: REVIEW from this score (the Moderate floor) |
| `[score] indirect` | `path` | An indirect exposure's volume: the path volume, or `proportional` (the estimate; less sensitive) (D-078) |

Behaviour points: R-HEU-01 +5, R-HEU-02 +10, R-HEU-03 +10, R-HEU-04 +5, R-HEU-06 +20, R-HEU-07 +15,
R-FRZ-02 +15. R-HEU-05 is no longer counted: its counterparties are direct exposures now. Behaviour
alone stays Low (at most 30). `X` and `B` are stored to 0.1, half up, and the score is computed from
the stored values.

**Feel** (one exposure, nothing else):

| Exposure | `H` | `X` | Level |
|---|---|---|---|
| 1% scam, direct | 0.007 | 5.4 | Low |
| 2% sanctioned, direct | 0.02 | 14.8 | Low |
| 5% mixer, direct | 0.04 | 27.4 | Low |
| 5% sanctioned, direct | 0.05 | 33.0 | Moderate |
| 10% sanctioned, direct | 0.10 | 55.1 | Moderate |
| 10% sanctioned, hop 2 | 0.06 | 38.1 | Moderate |
| 10% sanctioned, hop 3 | 0.036 | 25.0 | Low |
| 20% sanctioned, direct | 0.20 | 79.8 | High |
| 30% sanctioned, direct | 0.30 | 90.9 | Severe |
| 60% regulated exchange | 0 | 0 | Low |

`k`, `decay` and the weights are calibrated against the owner's benchmark set in P15. A change of
formula bumps `score_version`; a change of default is a config change with a decision.

### 11.4 Levels

| Level | Score | Suggested action |
|---|---|---|
| `low` | 0–30 | Minimal supervision. Proceed per policy; not a clearance |
| `moderate` | 31–70 | Review by hand before transacting |
| `high` | 71–90 | Investigate before transacting; keep under close watch |
| `severe` | 91–100 | Do not transact; escalate |

`INCOMPLETE` shows the score as a lower bound with a `+` on the level (`≥ 34 · moderate+`).

### 11.5 Verdict defaults (version 2)

A new severity, `INFO`: the finding is shown and explains the score, but never changes the verdict.

| Rule | v1 default | v2 default |
|---|---|---|
| R-SAN-01, R-FRZ-01 | BLOCK | BLOCK |
| R-SYS-01 | INCOMPLETE (fixed) | INCOMPLETE (fixed) |
| R-EXP-01 (a counterparty is sanctioned or frozen), R-FRZ-02, R-HEU-06 (look-alike) | REVIEW | REVIEW |
| R-SCR-01 (score ≥ `review_at`) | off | REVIEW from 31 |
| R-EXP-02, R-HEU-01 to R-HEU-05, R-HEU-07, R-TRC-01 to R-TRC-05 | REVIEW | INFO |

The owner can set any rule back with `[rules] severity`. Inferences and the score still never BLOCK.

### 11.6 The checked address's own label

The first that applies: the sanctions entry (`OFAC SDN: <name>`); `Tether-frozen`; an own wallet's
name; the intelligence store's entity name and kind, or label category; otherwise the classifier's
primary type with its confidence, marked inferred. None of these is a source of new findings: they
say who the address is.
